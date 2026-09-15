"""Tests for the no-gate full catalog scoring contract."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from crawlers.wzry_skin_crawler import (
    HeroRecord,
    SkinRecord,
    ensure_schema,
    save_hero,
    save_skin,
)
from models.emotion_evidence import SUBJECTIVE_ASPECTS
from models.value_present_scoring import score_value_present_skin
from scripts.score_catalog_values import build_report


class ValuePresentScoringTests(unittest.TestCase):
    def test_catalog_metadata_always_produces_six_scores(self):
        result = score_value_present_skin(
            {
                "source_key": "1-1",
                "hero_name": "测试英雄",
                "skin_name": "测试皮肤",
                "quality": "传说限定",
                "acquire_method": "商城直售获取",
                "has_detail_record": True,
                "has_primary_asset": True,
            }
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.score_status, "full_catalog_estimate")
        self.assertEqual(tuple(result.aspect_scores), SUBJECTIVE_ASPECTS)
        self.assertTrue(all(0 <= value <= 100 for value in result.aspect_scores.values()))
        self.assertTrue(0 <= result.score <= 100)

    def test_observed_aspects_override_estimates_without_gate(self):
        result = score_value_present_skin(
            {
                "source_key": "1-1",
                "hero_name": "测试英雄",
                "skin_name": "测试皮肤",
                "quality": "史诗",
            },
            {"visual_appeal": 91, "value_for_money": 0.77},
        )

        self.assertEqual(result.score_status, "full_hybrid")
        self.assertEqual(result.aspect_scores["visual_appeal"], 91)
        self.assertEqual(result.aspect_scores["value_for_money"], 77)
        self.assertEqual(result.observed_aspects, ["visual_appeal", "value_for_money"])
        self.assertEqual(len(result.estimated_aspects), 4)

    def test_report_satisfies_one_hundred_full_score_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "skins.sqlite3"
            conn = sqlite3.connect(db_path)
            ensure_schema(conn)
            save_hero(
                conn,
                HeroRecord("1", "测试英雄", "test", "测试", 100, "战士", [], {}),
            )
            for index in range(100):
                key = f"1-{index + 1}"
                save_skin(
                    conn,
                    SkinRecord(
                        source_key=key,
                        source_index=index,
                        hero_id="1",
                        hero_name="测试英雄",
                        skin_index=index + 1,
                        skin_id=key,
                        skin_name=f"测试皮肤{index + 1}",
                        quality="史诗",
                        online_date="2026-01-01",
                        intro="",
                        acquire_method="商城直售获取",
                        price_text="888点券",
                        image_url="",
                        detail_url="",
                        mobile_url="",
                        video_id="",
                        catalog_source="test",
                        detail_source="test",
                        has_detail_record=True,
                        raw_catalog_json={},
                        raw_detail_json={},
                    ),
                    None,
                )
            conn.commit()
            conn.close()

            report = build_report(db_path, minimum_full=100)

        self.assertEqual(report["catalog_skins"], 100)
        self.assertEqual(report["full_scores"], 100)
        self.assertTrue(report["minimum_full_satisfied"])
        self.assertFalse(report["publication_required"])
        self.assertFalse(report["quality_gate_required"])
        self.assertFalse(report["human_audit_required"])
        self.assertTrue(all(len(row["aspect_scores"]) == 6 for row in report["rows"]))


if __name__ == "__main__":
    unittest.main()
