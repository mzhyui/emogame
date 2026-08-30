"""L3: AutoDL GPT-5.4-mini semantic / cultural analysis via OpenAI-compatible API.

Supports two modes:
1. Standard L3 — semantic analysis only (design style, cultural references, etc.)
2. Combined L2+L3 — when Ollama L2 is unavailable, AutoDL handles both
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from loguru import logger
from openai import AsyncOpenAI

from vlm.cache import CacheManager
from vlm.config import get_settings
from vlm.degradation import DegradationHandler
from vlm.prompts import L2_PROMPT, L3_SYSTEM_PROMPT, L3_USER_PROMPT_TEMPLATE
from vlm.provenance import (
    canonical_json_sha256,
    producer_signature,
    sha256_text,
    tier_provenance,
)
from vlm.schemas import L3Output
from vlm.utils import encode_image, image_content_hash, parse_jsonish


L3_SCHEMA_VERSION = "l3-output-v1"
L3_COMBINED_SCHEMA_VERSION = "l2-l3-combined-output-v1"
L3_COMBINED_SYSTEM_PROMPT = "你是游戏美术与文化分析专家。输出严格 JSON 格式。"


class L3Semantic:
    """L3: AutoDL GPT-5.4-mini semantic understanding."""

    def __init__(
        self,
        cache: CacheManager,
        degradation: DegradationHandler,
    ) -> None:
        self.settings = get_settings()
        self.cache = cache
        self.degradation = degradation
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI | None:
        """Return an OpenAI-compatible client pointed at AutoDL, or ``None``
        when no token is configured."""
        if self._client is not None:
            return self._client
        if not self.settings.autodl_token:
            logger.warning("No AUTODL_TOKEN set — L3 semantic analysis disabled")
            return None
        kwargs: dict[str, Any] = {
            "api_key": self.settings.autodl_token,
            "base_url": self.settings.autodl_base_url,
            "http_client": httpx.AsyncClient(trust_env=False),
        }
        self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def analyze(
        self,
        image_path: Path,
        l1_result: dict[str, Any],
        l2_result: dict[str, Any] | None = None,
        needs_l2_fallback: bool = False,
    ) -> dict[str, Any]:
        """Run L3 semantic analysis.

        When ``needs_l2_fallback`` is True, AutoDL produces both L2
        scores and L3 semantics in a single combined call.

        Returns an empty dict with ``_source: "no_autodl_token"`` when no
        AutoDL token is configured.
        """
        client = self._get_client()
        if client is None:
            return {"_source": "no_autodl_token"}

        # ── Cache hit ──
        cache_level = "l3_combined" if needs_l2_fallback else "l3"
        content_hash = image_content_hash(image_path)
        if needs_l2_fallback:
            user_prompt = self._combined_user_prompt(l1_result)
            schema_version = L3_COMBINED_SCHEMA_VERSION
        else:
            user_prompt = self._standard_user_prompt(l1_result, l2_result)
            schema_version = L3_SCHEMA_VERSION
        upstream_hash = canonical_json_sha256(
            {
                "l1": self._public_inputs(l1_result),
                "l2": self._public_inputs(l2_result or {}),
                "needs_l2_fallback": needs_l2_fallback,
            }
        )
        system_prompt = (
            L3_COMBINED_SYSTEM_PROMPT if needs_l2_fallback else L3_SYSTEM_PROMPT
        )
        prompt_hash = sha256_text(f"{system_prompt}\n{user_prompt}")
        signature = producer_signature(
            tier=cache_level,
            model=self.settings.l3_model,
            prompt_sha256=prompt_hash,
            schema_version=schema_version,
            upstream_sha256=upstream_hash,
        )
        cached = self.cache.get(
            image_path,
            cache_level,
            content_hash,
            producer_signature=signature,
        )
        if cached is not None:
            cached["_source"] = "cache"
            return cached

        if needs_l2_fallback:
            return await self._combined_l2_l3(
                image_path,
                l1_result,
                prompt_hash=prompt_hash,
                signature=signature,
                upstream_hash=upstream_hash,
                content_hash=content_hash,
            )

        return await self._standard_l3(
            image_path,
            user_prompt,
            prompt_hash=prompt_hash,
            signature=signature,
            upstream_hash=upstream_hash,
            content_hash=content_hash,
        )

    @staticmethod
    def _public_inputs(result: dict[str, Any]) -> dict[str, Any]:
        """Exclude runtime metadata from an upstream producer signature."""
        return {
            key: value for key, value in result.items() if not str(key).startswith("_")
        }

    @staticmethod
    def _standard_user_prompt(
        l1_result: dict[str, Any], l2_result: dict[str, Any] | None
    ) -> str:
        return L3_USER_PROMPT_TEMPLATE.format(
            rarity_tier=l1_result.get("rarity_tier", "未知"),
            dominant_colors=", ".join(l1_result.get("dominant_colors", [])),
            scene_type=l1_result.get("scene_type", "未知"),
            effect_density=l1_result.get("effect_density", "未知"),
            model_detail=l2_result.get("model_detail", "N/A") if l2_result else "N/A",
            color_scheme=l2_result.get("color_scheme", "N/A") if l2_result else "N/A",
        )

    @classmethod
    def _combined_user_prompt(cls, l1_result: dict[str, Any]) -> str:
        user_text = cls._standard_user_prompt(l1_result, None)
        return f"""{L2_PROMPT}

Additionally, after the aesthetic scoring, provide semantic analysis:

{user_text}

Output a single JSON object with BOTH the 8 L2 scoring dimensions (as top-level keys) AND the L3 semantic fields (also as top-level keys).
"""

    async def _standard_l3(
        self,
        image_path: Path,
        user_prompt: str,
        *,
        prompt_hash: str,
        signature: str,
        upstream_hash: str,
        content_hash: str,
    ) -> dict[str, Any]:
        """Standard L3: semantic analysis only."""
        image_b64 = encode_image(image_path)
        client = self._get_client()
        if client is None:
            return {"_source": "no_autodl_token"}

        try:
            response = await client.chat.completions.create(
                model=self.settings.l3_model,
                messages=[
                    {"role": "system", "content": L3_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_b64}"
                                },
                            },
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=1000,
                timeout=self.settings.l3_timeout,
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:
            logger.error(f"L3 analysis failed: {exc}")
            return {"_source": "error", "_error": str(exc)}

        parsed = parse_jsonish(content) or {}
        try:
            validated = L3Output(**parsed).model_dump()
        except Exception:
            logger.warning(f"L3 output validation failed, using raw: {parsed}")
            validated = parsed

        validated["_source"] = "autodl"
        validated["_provenance"] = tier_provenance(
            tier="l3",
            model=self.settings.l3_model,
            prompt_sha256=prompt_hash,
            producer_signature_value=signature,
            producer_source="autodl",
            schema_version=L3_SCHEMA_VERSION,
            upstream_sha256=upstream_hash,
        )
        self.cache.set(
            image_path,
            "l3",
            validated,
            content_hash,
            producer_signature=signature,
        )
        return validated

    async def _combined_l2_l3(
        self,
        image_path: Path,
        l1_result: dict[str, Any],
        *,
        prompt_hash: str,
        signature: str,
        upstream_hash: str,
        content_hash: str,
    ) -> dict[str, Any]:
        """Combined L2+L3: delegate to ``DegradationHandler``."""
        client = self._get_client()
        if client is None:
            return {"_source": "no_autodl_token"}
        result = await self.degradation.run_combined_l2_l3_api(
            image_path, l1_result, client
        )
        remote_attempts = result.pop("_attempts", [])
        if result.get("_source") == "error":
            result["_provenance"] = {
                "tier": "l3_combined",
                "model": self.settings.l3_model,
                "prompt_sha256": prompt_hash,
                "producer_signature": signature,
                "producer_source": "remote_fallback_failed",
                "retrieval_source": "live",
                "schema_version": L3_COMBINED_SCHEMA_VERSION,
                "upstream_sha256": upstream_hash,
                "attempts": remote_attempts,
            }
        # Cache the combined result
        if result.get("_source") != "error":
            result["_provenance"] = tier_provenance(
                tier="l3_combined",
                model=self.settings.l3_model,
                prompt_sha256=prompt_hash,
                producer_signature_value=signature,
                producer_source="autodl_combined",
                schema_version=L3_COMBINED_SCHEMA_VERSION,
                upstream_sha256=upstream_hash,
            )
            result["_provenance"]["attempts"] = remote_attempts
            self.cache.set(
                image_path,
                "l3_combined",
                result,
                content_hash,
                producer_signature=signature,
            )
        return result
