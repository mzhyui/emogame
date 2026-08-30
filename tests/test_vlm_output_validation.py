"""Regression tests for fail-closed local VLM structured output handling."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.run_premium_pilot import summarize_vlm_execution
from models.premium_pilot import PilotCandidate
from vlm.cache import CacheManager
from vlm.degradation import DegradationHandler
from vlm.l1_classifier import L1Classifier
from vlm.l2_analyzer import L2Analyzer
from vlm.output_validation import (
    LocalOutputValidationError,
    validate_l1_response,
    validate_l2_response,
)
from vlm.pipeline import tier_diagnostics
from vlm.provenance import producer_signature, sha256_text


def valid_l1() -> dict[str, object]:
    return {
        "rarity_tier": "史诗",
        "dominant_colors": ["#AABBCC", "#112233", "#445566"],
        "scene_type": "战场",
        "character_ratio": 0.6,
        "effect_density": "high",
        "confidence": 0.8,
    }


def valid_l2() -> dict[str, object]:
    return {
        "model_detail": 8,
        "effect_quality": 8,
        "color_scheme": 8,
        "composition": 8,
        "uniqueness": 8,
        "costume_design": 8,
        "background_quality": 8,
        "ui_elements": {},
    }


class MemoryCache:
    def __init__(self, cached: dict | None = None) -> None:
        self.cached = cached
        self.set_calls: list[tuple[str, dict]] = []
        self.invalidated: list[str] = []

    def get(self, _path: Path, level: str, *_args, **_kwargs) -> dict | None:
        return self.cached if level in ("l1", "l2") else None

    def set(self, _path: Path, level: str, value: dict, *_args, **_kwargs) -> None:
        self.set_calls.append((level, value))

    def invalidate_level(self, _path: Path, level: str) -> None:
        self.invalidated.append(level)
        self.cached = None


class FakeOllama:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls = 0

    async def list_models(self) -> set[str]:
        return {"qwen2.5vl:3b"}

    async def chat(self, *_args, **_kwargs) -> dict:
        self.calls += 1
        return dict(self.response)


class FallbackOllama:
    def __init__(self, accepted: dict[str, object]) -> None:
        self.accepted = accepted

    async def list_models(self) -> set[str]:
        return {"qwen2.5vl:3b", "llama3.2-vision:11b"}

    async def chat(self, model: str, *_args, **_kwargs) -> dict:
        if model == "qwen2.5vl:3b":
            return {"content": "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"}
        return {"content": "{}", "parsed_json": dict(self.accepted)}


class SequencedCompletions:
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        self.payloads = list(payloads)

    async def create(self, **_kwargs):
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload))
                )
            ]
        )


class FakeDegradation:
    async def detect_tier(self):
        from vlm.degradation import PipelineTier
        return PipelineTier.FULL

    async def run_l1_cv_fallback(self, _preprocess, _path: str) -> dict:
        return valid_l1()


class OutputValidationTests(unittest.TestCase):
    def test_empty_and_zero_default_replies_are_rejected_with_safe_diagnostics(self):
        with self.assertRaisesRegex(LocalOutputValidationError, "l1_empty_response") as raised:
            validate_l1_response({"content": ""})
        self.assertNotIn("content", raised.exception.diagnostic)
        self.assertEqual(raised.exception.diagnostic["content_chars"], 0)

        response = {"content": "{}", "parsed_json": {}}
        with self.assertRaisesRegex(LocalOutputValidationError, "l2_missing_required_fields"):
            validate_l2_response(response)

        zero = {key: 0 for key in valid_l2() if key != "ui_elements"}
        zero["ui_elements"] = {}
        with self.assertRaisesRegex(LocalOutputValidationError, "l2_scores_outside_prompt_range"):
            validate_l2_response({"content": "{}", "parsed_json": zero})

    def test_complete_prompt_contract_is_accepted(self):
        self.assertEqual(
            validate_l1_response({"content": "ignored", "parsed_json": valid_l1()})["rarity_tier"],
            "史诗",
        )
        self.assertEqual(
            validate_l2_response({"content": "ignored", "parsed_json": valid_l2()})["model_detail"],
            8.0,
        )


class LocalTierHardeningTests(unittest.TestCase):
    def test_remote_combined_fallback_retries_invalid_schema_then_accepts(self):
        handler = DegradationHandler.__new__(DegradationHandler)
        handler.settings = SimpleNamespace(l3_model="remote", l3_timeout=1)
        completions = SequencedCompletions([{}, valid_l2()])
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        with patch("vlm.degradation.encode_image", return_value="image"):
            result = asyncio.run(
                handler.run_combined_l2_l3_api(
                    Path("skin.jpg"), valid_l1(), client
                )
            )
        self.assertEqual(result["_source"], "autodl_combined")
        self.assertEqual(
            [attempt["status"] for attempt in result["_attempts"]],
            ["invalid", "accepted"],
        )

    def test_l1_uses_validated_different_family_fallback_and_records_attempts(self):
        cache = MemoryCache()
        classifier = L1Classifier(FallbackOllama(valid_l1()), cache, FakeDegradation())
        classifier.settings = SimpleNamespace(
            l1_model="qwen2.5vl:3b",
            l1_fallback_model="llama3.2-vision:11b",
            l1_timeout=1,
        )
        with patch("vlm.l1_classifier.encode_image", return_value="image"), patch(
            "vlm.l1_classifier.image_content_hash", return_value="hash"
        ):
            result = asyncio.run(classifier.classify(Path("skin.jpg"), SimpleNamespace()))
        self.assertEqual(result["_provenance"]["model"], "llama3.2-vision:11b")
        self.assertEqual(
            [attempt["status"] for attempt in result["_provenance"]["attempts"]],
            ["invalid", "accepted"],
        )
        self.assertEqual(len(cache.set_calls), 1)

    def test_l2_uses_validated_different_family_fallback_and_records_attempts(self):
        cache = MemoryCache()
        analyzer = L2Analyzer(FallbackOllama(valid_l2()), cache, FakeDegradation())
        analyzer.settings = SimpleNamespace(
            l2_model="qwen2.5vl:3b",
            l2_fallback_model="llama3.2-vision:11b",
            l2_timeout=1,
        )
        with patch("vlm.l2_analyzer.encode_image", return_value="image"), patch(
            "vlm.l2_analyzer.image_content_hash", return_value="hash"
        ):
            result = asyncio.run(analyzer.analyze(Path("skin.jpg")))
        self.assertEqual(result["_provenance"]["model"], "llama3.2-vision:11b")
        self.assertEqual(
            [attempt["status"] for attempt in result["_provenance"]["attempts"]],
            ["invalid", "accepted"],
        )
        self.assertEqual(len(cache.set_calls), 1)

    def test_l1_invalid_reply_falls_back_without_caching_the_bad_output(self):
        cache = MemoryCache()
        classifier = L1Classifier(FakeOllama({"content": ""}), cache, FakeDegradation())
        classifier.settings = SimpleNamespace(
            l1_model="qwen2.5vl:3b", l1_fallback_model="", l1_timeout=1
        )
        preprocess = SimpleNamespace()
        with patch("vlm.l1_classifier.encode_image", return_value="image"), patch(
            "vlm.l1_classifier.image_content_hash", return_value="hash"
        ):
            result = asyncio.run(classifier.classify(Path("skin.jpg"), preprocess))
        self.assertEqual(result["_source"], "cv_fallback_after_invalid_local_output")
        self.assertEqual(result["_error"], "invalid_local_output")
        self.assertEqual(cache.set_calls, [])

    def test_l2_invalid_cached_row_is_evicted_and_never_recached(self):
        bad_cached = {key: 0 for key in valid_l2() if key != "ui_elements"}
        bad_cached["ui_elements"] = {}
        cache = MemoryCache(bad_cached)
        analyzer = L2Analyzer(FakeOllama({"content": ""}), cache, FakeDegradation())
        analyzer.settings = SimpleNamespace(l2_model="qwen2.5vl:3b", l2_timeout=1)
        with patch("vlm.l2_analyzer.encode_image", return_value="image"), patch(
            "vlm.l2_analyzer.image_content_hash", return_value="hash"
        ):
            result = asyncio.run(analyzer.analyze(Path("skin.jpg")))
        self.assertEqual(cache.invalidated, ["l2"])
        self.assertEqual(result["_source"], "error")
        self.assertEqual(result["_error"], "invalid_local_output")
        self.assertEqual(cache.set_calls, [])


class CacheAndReportingTests(unittest.TestCase):
    def test_producer_signature_changes_with_model_prompt_and_upstream_input(self):
        baseline = producer_signature(
            tier="l3",
            model="remote-a",
            prompt_sha256=sha256_text("prompt-a"),
            schema_version="v1",
            upstream_sha256="upstream-a",
        )
        variants = {
            producer_signature(
                tier="l3",
                model="remote-b",
                prompt_sha256=sha256_text("prompt-a"),
                schema_version="v1",
                upstream_sha256="upstream-a",
            ),
            producer_signature(
                tier="l3",
                model="remote-a",
                prompt_sha256=sha256_text("prompt-b"),
                schema_version="v1",
                upstream_sha256="upstream-a",
            ),
            producer_signature(
                tier="l3",
                model="remote-a",
                prompt_sha256=sha256_text("prompt-a"),
                schema_version="v1",
                upstream_sha256="upstream-b",
            ),
        }
        self.assertNotIn(baseline, variants)
        self.assertEqual(len(variants), 3)

    def test_non_object_cache_entry_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = CacheManager.__new__(CacheManager)
            cache.settings = SimpleNamespace(cache_ttl_days=30)
            cache.db_path = Path(tmp) / "cache.sqlite3"
            cache._init_db()
            image = Path(tmp) / "skin.jpg"
            image.write_bytes(b"skin")
            cache.set(image, "l2", ["not", "a", "mapping"])
            self.assertIsNone(cache.get(image, "l2"))
            self.assertIsNone(cache.get(image, "l2"))

    def test_cache_signature_rejects_legacy_and_mismatched_producers(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = CacheManager.__new__(CacheManager)
            cache.settings = SimpleNamespace(cache_ttl_days=30)
            cache.db_path = Path(tmp) / "cache.sqlite3"
            cache._init_db()
            image = Path(tmp) / "skin.jpg"
            image.write_bytes(b"skin")

            cache.set(image, "l2", valid_l2())
            self.assertIsNone(
                cache.get(image, "l2", producer_signature="signed-producer")
            )

            cache.set(
                image,
                "l2",
                valid_l2(),
                producer_signature="signed-producer",
            )
            self.assertIsNone(
                cache.get(image, "l2", producer_signature="changed-producer")
            )
            self.assertIsNotNone(
                cache.get(image, "l2", producer_signature="signed-producer")
            )

            cache.set(
                image,
                "l2",
                {**valid_l2(), "_provenance": {"retrieval_source": "live"}},
                producer_signature="signed-producer",
            )
            loaded = cache.get(
                image, "l2", producer_signature="signed-producer"
            )
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["_provenance"]["retrieval_source"], "cache")
            self.assertIn("cache_created_at", loaded["_provenance"])

    def test_pipeline_and_pilot_report_surface_invalid_local_output(self):
        diagnostics = tier_diagnostics(
            l1={"_source": "cache"},
            l2={"_source": "error", "_error": "invalid_local_output", "_diagnostic": {"reason": "l2_empty_response"}},
            l3={"_source": "autodl"},
        )
        candidate = PilotCandidate(
            source_key="hero-01", hero_name="hero", skin_name="skin", skin_id="1",
            quality="史诗", acquire_method="", price_text="", online_date="",
            image_path="skin.jpg", image_hash="hash", has_revenue_evidence=False,
            selection_tier="史诗",
        )
        summary = summarize_vlm_execution(
            [candidate], {"hero-01": {"status": "partial", "tier_diagnostics": diagnostics}}
        )
        self.assertEqual(summary["status"], "review_required")
        self.assertEqual(summary["invalid_local_output_source_keys"], ["hero-01"])


if __name__ == "__main__":
    unittest.main()
