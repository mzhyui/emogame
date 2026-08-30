"""L1: Qwen2.5VL-3B fast skin classification via Ollama.

Cached, with automatic CV fallback when Ollama is unavailable or errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from vlm.cache import CacheManager
from vlm.config import get_settings
from vlm.degradation import DegradationHandler, PipelineTier
from vlm.ollama_client import OllamaClient
from vlm.output_validation import LocalOutputValidationError, validate_l1_payload, validate_l1_response
from vlm.preprocess import PreprocessResult, Preprocessor
from vlm.prompts import L1_PROMPT
from vlm.provenance import producer_signature, sha256_text, tier_provenance
from vlm.utils import encode_image, image_content_hash


L1_SCHEMA_VERSION = "l1-output-v1"


class L1Classifier:
    """L1: fast classification (rarity, colours, scene, effect density)."""

    def __init__(
        self,
        ollama: OllamaClient,
        cache: CacheManager,
        degradation: DegradationHandler,
    ) -> None:
        self.settings = get_settings()
        self.ollama = ollama
        self.cache = cache
        self.degradation = degradation
        self.preprocessor = Preprocessor()

    async def classify(
        self, image_path: Path, preprocess_result: PreprocessResult | None = None
    ) -> dict[str, Any]:
        """Run L1 classification.

        Returns a dict matching ``L1Output`` schema plus ``_source`` metadata.
        """
        tier = await self.degradation.detect_tier()
        content_hash = image_content_hash(image_path)

        # ── CV fallback path ──
        if tier in (PipelineTier.DEGRADED_CV_L1, PipelineTier.API_ONLY):
            if preprocess_result is None:
                _, preprocess_result = self.preprocessor.process(image_path)
            result = await self.degradation.run_l1_cv_fallback(
                preprocess_result, str(image_path)
            )
            result["_source"] = "cv_fallback"
            prompt_hash = sha256_text("cv_l1_edge_density_colour_heuristic_v1")
            signature = producer_signature(
                tier="l1",
                model="cv_heuristic_v1",
                prompt_sha256=prompt_hash,
                schema_version=L1_SCHEMA_VERSION,
            )
            result["_provenance"] = tier_provenance(
                tier="l1",
                model="cv_heuristic_v1",
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="cv_fallback",
                schema_version=L1_SCHEMA_VERSION,
            )
            self.cache.set(
                image_path,
                "l1",
                result,
                content_hash,
                producer_signature=signature,
            )
            return result

        # ── Select primary and validated fallback models ──
        try:
            installed = await self.ollama.list_models()
        except Exception:
            installed = set()
        primary = self.settings.l1_model
        fallback = getattr(self.settings, "l1_fallback_model", "")
        models = [primary] if primary in installed or not installed else []
        if fallback and fallback in installed and fallback not in models:
            models.append(fallback)
        if not models:
            models = [primary]
        prompt_hash = sha256_text(L1_PROMPT)

        # ── Signed cache hit ──
        for model in models:
            signature = producer_signature(
                tier="l1",
                model=model,
                prompt_sha256=prompt_hash,
                schema_version=L1_SCHEMA_VERSION,
            )
            cached = self.cache.get(
                image_path,
                "l1",
                content_hash,
                producer_signature=signature,
            )
            if cached is not None:
                try:
                    validate_l1_payload(cached)
                except LocalOutputValidationError as exc:
                    logger.warning(
                        "Discarding invalid cached L1 output for {}: {}",
                        image_path.name,
                        exc.reason,
                    )
                    self.cache.invalidate_level(image_path, "l1")
                else:
                    cached["_source"] = "cache"
                    return cached

        # ── Ollama call with a different-family fallback ──
        image_b64 = encode_image(image_path)
        attempts: list[dict[str, str]] = []
        last_validation_error: LocalOutputValidationError | None = None
        last_runtime_error: Exception | None = None
        for model in models:
            try:
                response, validated = await self._call_with_retry(model, image_b64)
            except LocalOutputValidationError as exc:
                logger.warning(
                    "L1 model {} returned invalid local output for {}: {}",
                    model,
                    image_path.name,
                    exc.reason,
                )
                attempts.append({"model": model, "status": "invalid", "reason": exc.reason})
                last_validation_error = exc
                continue
            except Exception as exc:
                logger.error("L1 model {} failed for {}: {}", model, image_path.name, exc)
                attempts.append({"model": model, "status": "error", "reason": type(exc).__name__})
                last_runtime_error = exc
                continue

            attempts.append({"model": model, "status": "accepted"})
            signature = producer_signature(
                tier="l1",
                model=model,
                prompt_sha256=prompt_hash,
                schema_version=L1_SCHEMA_VERSION,
            )
            validated["_source"] = "ollama"
            validated["_elapsed"] = response.get("elapsed_seconds", 0)
            validated["_provenance"] = tier_provenance(
                tier="l1",
                model=model,
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="ollama",
                schema_version=L1_SCHEMA_VERSION,
            )
            validated["_provenance"]["attempts"] = attempts
            self.cache.set(
                image_path,
                "l1",
                validated,
                content_hash,
                producer_signature=signature,
            )
            return validated

        model = models[-1]
        signature = producer_signature(
            tier="l1",
            model=model,
            prompt_sha256=prompt_hash,
            schema_version=L1_SCHEMA_VERSION,
        )
        if last_validation_error is not None:
            exc = last_validation_error
            if preprocess_result is None:
                _, preprocess_result = self.preprocessor.process(image_path)
            result = await self.degradation.run_l1_cv_fallback(
                preprocess_result, str(image_path)
            )
            result["_source"] = "cv_fallback_after_invalid_local_output"
            result["_error"] = "invalid_local_output"
            result["_diagnostic"] = {"reason": exc.reason, **exc.diagnostic}
            result["_provenance"] = tier_provenance(
                tier="l1",
                model=model,
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="cv_fallback_after_invalid_local_output",
                schema_version=L1_SCHEMA_VERSION,
            )
            result["_provenance"]["attempts"] = attempts
            return result
        if last_runtime_error is not None:
            if preprocess_result is None:
                _, preprocess_result = self.preprocessor.process(image_path)
            result = await self.degradation.run_l1_cv_fallback(
                preprocess_result, str(image_path)
            )
            result["_source"] = "cv_fallback_after_error"
            result["_provenance"] = tier_provenance(
                tier="l1",
                model=model,
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="cv_fallback_after_error",
                schema_version=L1_SCHEMA_VERSION,
            )
            result["_provenance"]["attempts"] = attempts
            return result
        raise RuntimeError("L1 model chain completed without a result")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _call_with_retry(self, model: str, image_b64: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = await self.ollama.chat(
            model, image_b64, L1_PROMPT, timeout=self.settings.l1_timeout
        )
        return response, validate_l1_response(response)
