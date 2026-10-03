"""Offline regression tests for graph routing, evidence custody, and Ollama failures."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from agents.local_ollama import LocalOllamaClient
from agents.local_pipeline import LocalAgentPipeline, PipelineConfig, STAGES, select_skin
from agents.prompts import REPORT_SECTIONS, REPORT_SYSTEM_PROMPT, validate_report
from crawlers.wzry_skin_crawler import SkinRecord, ensure_schema, save_skin
from data.skin_repository import SkinRepository
from feature_engineering.features import FEATURE_FIELD_NAMES
from models.emotion_evidence import EvidenceQualificationProfile

L1 = {
    "rarity_tier": "史诗", "dominant_colors": ["#AABBCC", "#112233", "#445566"],
    "scene_type": "自然", "character_ratio": 0.65, "effect_density": "high", "confidence": 0.8,
}
L2 = {
    "model_detail": 8, "effect_quality": 7, "color_scheme": 9, "composition": 6,
    "uniqueness": 8, "costume_design": 8, "background_quality": 7, "ui_elements": {},
}
REPORT = {name: "根据本地资料分析；证据不足的部分需补充验证。" for name in REPORT_SECTIONS}


def reply(payload):
    return httpx.Response(200, json={
        "done": True, "done_reason": "stop",
        "message": {"content": json.dumps(payload, ensure_ascii=False)},
    })


class LocalAgentPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "skins.sqlite3"
        (self.root / "skin.jpg").write_bytes(b"mock-image-content")
        with sqlite3.connect(self.db) as conn:
            ensure_schema(conn)
            for index, name in enumerate(("测试皮肤", "对照皮肤"), start=1):
                save_skin(conn, SkinRecord(
                    source_key=f"106-0{index}", source_index=index, hero_id="106",
                    hero_name="小乔", skin_index=index, skin_id="duplicate-id",
                    skin_name=name, quality="史诗", online_date="2024-06-01", intro="",
                    acquire_method="商城直售", price_text="888点券",
                    image_url="https://example.invalid/skin.jpg", detail_url="", mobile_url="",
                    video_id="", catalog_source="herolist", detail_source="heroskinlist",
                    has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
                ), Path("skin.jpg"))
        self.requests = []

    def tearDown(self):
        self.tmp.cleanup()

    def pipeline(self, **overrides):
        values = {
            "db": self.db, "asset_root": self.root, "output": self.root / "run",
            "source_key": "106-01", "question": "请分析视觉卖点和缺少的定价证据。",
            "reference_date": date(2024, 7, 1), "attempts": 1,
        }
        values.update(overrides)
        pipeline = LocalAgentPipeline(PipelineConfig(**values))

        def handler(request):
            data = json.loads(request.content)
            self.requests.append(data)
            if data["messages"][0]["role"] == "system":
                return reply(REPORT)
            return reply(L1 if "rarity_tier" in data["messages"][0]["content"] else L2)

        pipeline.client.transport = httpx.MockTransport(handler)
        return pipeline

    async def test_full_workflow_visits_each_stage_once_and_preserves_db(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        state = await self.pipeline().run()
        self.assertEqual(state["status"], "completed")
        self.assertEqual([e["stage"] for e in state["events"]], list(STAGES))
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        self.assertEqual(state["feature_vector"]["vlm_art_quality"], 8)
        self.assertEqual(state["feature_vector"]["vlm_color_harmony"], 0.9)
        self.assertEqual(state["feature_vector"]["skin_age_days"], 30)
        self.assertEqual(len(state["feature_vector"]["availability"]), 33)
        self.assertIsNone(state["feature_vector"]["ownership_rate"])
        self.assertIsNone(state["feature_vector"]["avg_spend_to_obtain"])
        self.assertIsNone(state["chart_data"]["five_dimension_scores"])
        self.assertFalse(state["chart_data"]["vlm_used_in_rule_score"])
        self.assertIsNone(state["business_analysis"]["pricing_guidance"]["recommended_range_cny"])
        self.assertEqual(state["competitor_data"][0]["source_key"], "106-02")
        self.assertEqual(self.requests[-1]["messages"][0]["content"], REPORT_SYSTEM_PROMPT)
        self.assertIn(state["question"], self.requests[-1]["messages"][1]["content"])
        for name in ("state.json", "report.md", "report_prompt.json", "chart_data.json", "config.json"):
            self.assertTrue((self.root / "run" / name).is_file())
        audit = [json.loads(line) for line in (self.root / "run/ollama_calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(audit), 3)
        self.assertNotIn("images", audit[0]["request"]["messages"][0])
        self.assertIn("image_base64_sha256", audit[0]["request"]["messages"][0])

    async def test_dry_run_never_calls_ollama_and_keeps_missing_visuals_null(self):
        state = await self.pipeline(dry_run=True).run()
        self.assertEqual(state["status"], "dry_run")
        self.assertFalse(self.requests)
        self.assertIsNone(state["feature_vector"]["vlm_art_quality"])
        self.assertTrue(set(FEATURE_FIELD_NAMES) <= state["feature_vector"].keys())
        self.assertFalse((self.root / "run/ollama_calls.jsonl").exists())

    async def test_skip_vlm_only_calls_reporter(self):
        state = await self.pipeline(skip_vlm=True).run()
        self.assertEqual(state["status"], "degraded")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(state["vlm_features"]["reason"], "requested_skip")

    async def test_missing_image_degrades_and_continues_once(self):
        (self.root / "skin.jpg").unlink()
        state = await self.pipeline().run()
        self.assertEqual(state["status"], "degraded")
        self.assertEqual(len(state["errors"]), 1)
        self.assertTrue(state["errors"][0]["recoverable"])
        self.assertEqual(len(state["events"]), 6)
        self.assertIsNone(state["feature_vector"]["vlm_art_quality"])
        self.assertEqual(len(self.requests), 1)

    async def test_strict_missing_image_stops_before_feature_stage(self):
        (self.root / "skin.jpg").unlink()
        state = await self.pipeline(strict_vlm=True).run()
        self.assertEqual(state["status"], "failed")
        self.assertEqual(len(state["events"]), 2)
        self.assertNotIn("feature_vector", state)
        self.assertFalse(self.requests)

    async def test_invalid_vlm_is_not_converted_to_zero_scores(self):
        pipeline = self.pipeline()
        pipeline.client.transport = httpx.MockTransport(
            lambda request: reply(REPORT if json.loads(request.content)["messages"][0]["role"] == "system" else {})
        )
        state = await pipeline.run()
        self.assertEqual(state["status"], "degraded")
        self.assertIsNone(state["feature_vector"]["vlm_art_quality"])
        self.assertIn("l1_missing_required_fields", state["errors"][0]["message"])

    async def test_report_failure_preserves_upstream_results(self):
        pipeline = self.pipeline(skip_vlm=True)
        pipeline.client.transport = httpx.MockTransport(lambda request: reply({}))
        state = await pipeline.run()
        self.assertEqual(state["status"], "failed")
        self.assertIn("premium_result", state)
        self.assertEqual(state["errors"][-1]["stage"], "generate_report")
        self.assertNotIn("report", state)
        self.assertTrue((self.root / "run/report_prompt.json").is_file())

    async def test_diagnostic_profile_retains_unpublished_provenance(self):
        profile = EvidenceQualificationProfile.from_mapping({
            "run_id": "fixture", "source_key": "106-01", "published": False,
            "aspect_scores": {"visual_appeal": 80},
        })
        with patch("agents.local_pipeline.EmotionEvidenceRepository.latest_run_profile", return_value=profile):
            state = await self.pipeline(dry_run=True).run()
        self.assertEqual(state["status"], "dry_run")
        self.assertEqual(state["premium_result"]["evidence_run_id"], "fixture")
        self.assertFalse(state["premium_result"]["evidence"]["qualification"]["published"])

    async def test_output_directory_is_never_overwritten(self):
        pipeline = self.pipeline(dry_run=True)
        await pipeline.run()
        with self.assertRaises(FileExistsError):
            await pipeline.run()

    async def test_missing_database_is_not_created(self):
        pipeline = self.pipeline(db=self.root / "missing.sqlite3")
        with self.assertRaisesRegex(ValueError, "database not found"):
            await pipeline.run()
        self.assertFalse(pipeline.config.db.exists())

    def test_ambiguous_search_requires_unique_source_key(self):
        repo = SkinRepository(self.db)
        with self.assertRaisesRegex(ValueError, "106-01.*小乔"):
            select_skin(repo, None, "duplicate-id")
        self.assertEqual(select_skin(repo, None, "测试皮肤"), "106-01")
        self.assertEqual(select_skin(repo, "106-02", None), "106-02")


class LocalOllamaClientTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_false_official_scoring_and_validation_attribution(self):
        for term in ("官方评分", "官方审核", "官方审计", "经社区观测验证"):
            with self.subTest(term=term), self.assertRaisesRegex(ValueError, "准确用语"):
                validate_report({**REPORT, "情绪溢价总览": term})

    async def test_accepts_complete_fenced_json_but_not_partial_or_prose(self):
        for content, accepted in (
            ("```json\n" + json.dumps(REPORT) + "\n```", True),
            ("extra text " + json.dumps(REPORT), False),
            ("```json\n" + json.dumps(REPORT)[:-1] + "\n```", False),
        ):
            with self.subTest(content=content[:30]):
                client = LocalOllamaClient(
                    "http://127.0.0.1:11434", attempts=1,
                    transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                        "done": True, "message": {"content": content},
                    })),
                )
                call = client.generate_json(stage="report", model="fixture", messages=[], validate=validate_report)
                if accepted:
                    self.assertEqual(await call, REPORT)
                else:
                    with self.assertRaises(RuntimeError):
                        await call

    async def test_retry_invalid_json_then_accept_and_audit_attempts(self):
        records = []
        responses = iter([reply({}), reply(REPORT)])
        client = LocalOllamaClient(
            "http://127.0.0.1:11434", attempts=3, audit=records.append,
            transport=httpx.MockTransport(lambda request: next(responses)),
        )
        with patch("agents.local_ollama.asyncio.sleep", new_callable=AsyncMock):
            result = await client.generate_json(
                stage="report", model="fixture", messages=[], validate=validate_report,
            )
        self.assertEqual(result, REPORT)
        self.assertEqual([r["status"] for r in records], ["error", "accepted"])
        self.assertIn("校验失败原因", records[1]["request"]["messages"][-1]["content"])

    async def test_transport_failure_exhausts_exactly_three_attempts(self):
        records = []
        client = LocalOllamaClient(
            "http://127.0.0.1:11434", attempts=3, audit=records.append,
            transport=httpx.MockTransport(lambda request: httpx.Response(503)),
        )
        with patch("agents.local_ollama.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                await client.generate_json(stage="report", model="fixture", messages=[], validate=validate_report)
        self.assertEqual(len(records), 3)

    async def test_rejects_truncated_even_when_json_is_valid(self):
        client = LocalOllamaClient(
            "http://127.0.0.1:11434", attempts=1,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "done": True, "done_reason": "length", "message": {"content": json.dumps(REPORT)},
            })),
        )
        with self.assertRaisesRegex(RuntimeError, "reached num_predict"):
            await client.generate_json(stage="report", model="fixture", messages=[], validate=validate_report)

    async def test_rejects_empty_thinking_only_response(self):
        client = LocalOllamaClient(
            "http://127.0.0.1:11434", attempts=1,
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                "done": True, "message": {"content": "", "thinking": "reasoning only"},
            })),
        )
        with self.assertRaisesRegex(RuntimeError, "empty message.content"):
            await client.generate_json(stage="report", model="fixture", messages=[], validate=validate_report)


if __name__ == "__main__":
    unittest.main()
