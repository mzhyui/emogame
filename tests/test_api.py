import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from crawlers.wzry_skin_crawler import AssetRecord, HeroRecord, SkinRecord, ensure_schema
from crawlers.wzry_skin_crawler import save_asset, save_hero, save_skin
from data.market_signal_repository import MarketSignalRepository
from data.emotion_evidence_repository import EmotionEvidenceRepository
from models.emotion_evidence import EvidenceQualificationProfile, SUBJECTIVE_ASPECTS
from models.emotion_workflow import sha256_json


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "skins.sqlite3"
        conn = sqlite3.connect(self.db_path)
        try:
            ensure_schema(conn)
            save_hero(
                conn,
                HeroRecord(
                    hero_id="107",
                    hero_name="赵云",
                    id_name="zhaoyun",
                    title="苍天翔龙",
                    hero_type_code=4,
                    hero_type="刺客",
                    skin_names=["龙胆"],
                    raw_json={},
                ),
            )
            save_skin(
                conn,
                SkinRecord(
                    source_key="107-08",
                    source_index=8,
                    hero_id="107",
                    hero_name="赵云",
                    skin_index=8,
                    skin_id="10708",
                    skin_name="龙胆",
                    quality="史诗限定",
                    online_date="2020-05-05",
                    intro="sample",
                    acquire_method="商城限时直售获取",
                    price_text="888点券",
                    image_url="https://example.com/skin.jpg",
                    detail_url="https://example.com/detail.html",
                    mobile_url="",
                    video_id="",
                    catalog_source="herolist",
                    detail_source="heroskinlist",
                    has_detail_record=True,
                    raw_catalog_json={},
                    raw_detail_json={},
                ),
                Path("data/wzry_skins/images/107-08.jpg"),
            )
            save_asset(
                conn,
                AssetRecord(
                    source_key="107-08",
                    asset_type="skin_primary",
                    remote_url="https://example.com/skin.jpg",
                    local_path="data/wzry_skins/images/107-08.jpg",
                    content_hash="abc123",
                    download_status="downloaded",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.client = TestClient(app)

    def tearDown(self):
        self.tmp.cleanup()

    def test_health(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_list_skins(self):
        response = self.client.get("/api/skins", params={"db": str(self.db_path), "search": "龙胆"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["skins"][0]["source_key"], "107-08")

    def test_evaluate_with_inline_signals(self):
        response = self.client.post(
            "/api/evaluate",
            params={"db": str(self.db_path)},
            json={
                "source_key": "107-08",
                "signals": {
                    "visual_score": 0.8,
                    "feel_score": 0.75,
                    "craftsmanship_score": 0.78,
                    "collection_score": 0.72,
                    "value_score": 0.7,
                    "purchase_intent_score": 0.76,
                    "discussion_count": 8000,
                },
            },
        )

        self.assertEqual(response.status_code, 200)
        evaluation = response.json()["evaluation"]
        self.assertEqual(evaluation["validation_status"], "insufficient_market_evidence")
        self.assertIn("missing_published_evidence_profile", evaluation["validation_reasons"])

    def test_evaluate_reads_only_published_cohort_profile(self):
        records = [
            {
                "source_key": "107-08" if index == 0 else f"cohort-{index:03d}",
                "hero_name": "赵云" if index == 0 else f"hero-{index:03d}",
                "skin_name": "龙胆" if index == 0 else f"skin-{index:03d}",
                "online_date": "2020-05-05",
                "cohort_role": "warm_start" if index < 50 else "coverage_extension",
                "release_era": "pre_2021",
            }
            for index in range(100)
        ]
        manifest = {
            "protocol_version": "emotion-evidence-v1",
            "records_sha256": sha256_json(records),
            "records": records,
        }
        repo = EmotionEvidenceRepository(self.db_path)
        repo.create_run(
            run_id="api-run",
            protocol_version="emotion-evidence-v1",
            protocol_hash="p" * 64,
            cohort_hash=manifest["records_sha256"],
            manifest=manifest,
            observation_start="2024-09-02",
            observation_end="2026-09-01",
            annotation_schema_version=1,
        )
        repo.record_ethics_status("api-run", status="ready", record_hash="ethics")
        repo.freeze_model_selection(
            "api-run",
            model_name="deterministic-v1",
            model_digest="digest",
            prompt_hash="prompt",
            metrics={},
        )
        repo.set_review_gate("api-run", gate="calibration", passed=True, metrics={})
        repo.set_review_gate("api-run", gate="audit", passed=True, metrics={})
        repo.bind_validation_artifact("api-run", "artifact")
        for row in records[:80]:
            repo.save_validation_result(
                EvidenceQualificationProfile(
                    run_id="api-run",
                    source_key=row["source_key"],
                    calibration_passed=True,
                    audit_passed=True,
                    relevant_comment_count=20,
                    unique_author_count=20,
                    parent_document_count=2,
                    platform_count=2,
                    aspect_author_counts={aspect: 5 for aspect in SUBJECTIVE_ASPECTS},
                    feel_actual_use_author_count=5,
                    aspect_scores={aspect: 75 for aspect in SUBJECTIVE_ASPECTS},
                    score=75,
                    score_ci_low=70,
                    score_ci_high=80,
                    protocol_hash="p" * 64,
                )
            )
        repo.publish_run("api-run")

        response = self.client.post(
            "/api/evaluate",
            params={"db": str(self.db_path)},
            json={"source_key": "107-08"},
        )
        self.assertEqual(response.status_code, 200)
        evaluation = response.json()["evaluation"]
        self.assertEqual(evaluation["validation_status"], "evidence_validated")
        self.assertEqual(evaluation["evaluation_score"], 75)
        self.assertEqual(evaluation["evidence_run_id"], "api-run")

    def test_sales_report(self):
        response = self.client.post(
            "/api/sales-report",
            params={"db": str(self.db_path)},
            json={"source_key": "107-08", "ignore_db_signals": True},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sales_report"]["decision"], "collect_more_evidence")

    def test_sales_gap(self):
        market_repo = MarketSignalRepository(self.db_path)
        market_repo.add_evidence(
            "107-08",
            platform="sales_public",
            external_id="sales-demo",
            metrics={"estimated_sales_volume": 1_000_000, "sales_volume_relation": "estimated"},
        )
        market_repo.aggregate_evidence_signals("107-08")

        response = self.client.post(
            "/api/sales-gap",
            params={"db": str(self.db_path), "official_only": False},
            json={"source_key": "107-08"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("sales_gap", response.json())
        self.assertEqual(response.json()["sales_gap"]["sales_basis"], "estimated_sales_volume")

    def test_sales_gap_defaults_to_official_only(self):
        market_repo = MarketSignalRepository(self.db_path)
        market_repo.add_evidence(
            "107-08",
            platform="sales_public",
            external_id="sales-demo",
            metrics={"estimated_sales_volume": 1_000_000, "sales_volume_relation": "estimated"},
        )
        market_repo.aggregate_evidence_signals("107-08")

        response = self.client.post(
            "/api/sales-gap",
            params={"db": str(self.db_path)},
            json={"source_key": "107-08"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sales_gap"]["sales_basis"], None)
        self.assertEqual(response.json()["sales_gap"]["gap_direction"], "insufficient_sales_data")


if __name__ == "__main__":
    unittest.main()
