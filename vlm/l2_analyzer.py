"""L2: Qwen2.5VL-3B fine aesthetic analysis via Ollama.

Cached, with deferral to AutoDL when Ollama L2 is unavailable.

Phase 1 intentionally uses the same local model family as L1 because the
audit in ``progress/2026-06-20.md`` showed that it satisfies the JSON
contract, while ``llama3.2-vision:11b`` needs prompt and token-budget work
before it can be promoted.
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
from vlm.output_validation import LocalOutputValidationError, validate_l2_payload, validate_l2_response
from vlm.prompts import L2_PROMPT
from vlm.provenance import producer_signature, sha256_text, tier_provenance
from vlm.utils import encode_image, image_content_hash


L2_SCHEMA_VERSION = "l2-output-v1"


class L2Analyzer:
    """L2: fine aesthetic analysis (8 dimensions, 1-10 scoring)."""

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

    async def analyze(self, image_path: Path) -> dict[str, Any]:
        """Run L2 aesthetic analysis.

        Returns a dict matching ``L2Output`` schema plus ``_source`` metadata.
        When Ollama L2 is unavailable, returns ``{"_source": "deferred_to_l3"}``
        so the pipeline can produce L2 scores via AutoDL GPT-5.4-mini instead.
        """
        tier = await self.degradation.detect_tier()

        # ── Defer to AutoDL GPT-5.4-mini ──
        if tier in (
            PipelineTier.API_ONLY,
            PipelineTier.DEGRADED_API_L2,
            PipelineTier.DEGRADED_CV_L1,
        ):
            return {
                "_source": "deferred_to_l3",
                "_provenance": {
                    "tier": "l2",
                    "producer_source": "deferred_to_l3",
                    "retrieval_source": "live",
                },
            }

        content_hash = image_content_hash(image_path)
        prompt_hash = sha256_text(L2_PROMPT)
        try:
            installed = await self.ollama.list_models()
        except Exception:
            installed = set()
        primary = self.settings.l2_model
        fallback = getattr(self.settings, "l2_fallback_model", "")
        models = [primary] if primary in installed or not installed else []
        if fallback and fallback in installed and fallback not in models:
            models.append(fallback)
        if not models:
            models = [primary]

        # ── Signed cache hit ──
        for model in models:
            signature = producer_signature(
                tier="l2",
                model=model,
                prompt_sha256=prompt_hash,
                schema_version=L2_SCHEMA_VERSION,
            )
            cached = self.cache.get(
                image_path,
                "l2",
                content_hash,
                producer_signature=signature,
            )
            if cached is not None:
                try:
                    validate_l2_payload(cached)
                except LocalOutputValidationError as exc:
                    logger.warning(
                        "Discarding invalid cached L2 output for {}: {}",
                        image_path.name,
                        exc.reason,
                    )
                    self.cache.invalidate_level(image_path, "l2")
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
                    "L2 model {} returned invalid local output for {}: {}",
                    model,
                    image_path.name,
                    exc.reason,
                )
                attempts.append({"model": model, "status": "invalid", "reason": exc.reason})
                last_validation_error = exc
                continue
            except Exception as exc:
                logger.error("L2 model {} failed for {}: {}", model, image_path.name, exc)
                attempts.append({"model": model, "status": "error", "reason": type(exc).__name__})
                last_runtime_error = exc
                continue

            attempts.append({"model": model, "status": "accepted"})
            signature = producer_signature(
                tier="l2",
                model=model,
                prompt_sha256=prompt_hash,
                schema_version=L2_SCHEMA_VERSION,
            )
            validated["_source"] = "ollama"
            validated["_elapsed"] = response.get("elapsed_seconds", 0)
            validated["_provenance"] = tier_provenance(
                tier="l2",
                model=model,
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="ollama",
                schema_version=L2_SCHEMA_VERSION,
            )
            validated["_provenance"]["attempts"] = attempts
            self.cache.set(
                image_path,
                "l2",
                validated,
                content_hash,
                producer_signature=signature,
            )
            return validated

        if last_validation_error is not None:
            exc = last_validation_error
            return {
                "_source": "error",
                "_error": "invalid_local_output",
                "_diagnostic": {"reason": exc.reason, **exc.diagnostic},
                "_provenance": {"tier": "l2", "attempts": attempts},
            }
        if last_runtime_error is not None:
            return {
                "_source": "error",
                "_error": str(last_runtime_error),
                "_provenance": {"tier": "l2", "attempts": attempts},
            }
        raise RuntimeError("L2 model chain completed without a result")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _call_with_retry(self, model: str, image_b64: str) -> tuple[dict[str, Any], dict[str, Any]]:
        response = await self.ollama.chat(
            model,
            image_b64,
            L2_PROMPT,
            timeout=self.settings.l2_timeout,
        )
        return response, validate_l2_response(response)
