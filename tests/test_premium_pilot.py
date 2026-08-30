"""Regression tests for the first-stage perceived-premium pilot."""

from __future__ import annotations

import json
import tempfile
import unittest
from argparse import Namespace
import asyncio
import hashlib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from models.premium_pilot import (
    MediaEvidence,
    PilotCandidate,
    PremiumScore,
    aggregate_media_labels,
    build_feature_trace,
    build_candidates,
    canonical_payload_sha256,
    load_frozen_manifest,
    load_media_mapping,
    load_reviewer_ratings,
    manifest_payload,
    resolve_cached_images,
    revenue_validation,
    reviewer_validation,
    score_premium,
    select_pilot_manifest,
)
from crawlers.wzry_skin_crawler import HeroRecord, SkinRecord, ensure_schema, save_hero, save_skin
from scripts.run_premium_pilot import (
    async_main,
    output_run_lock,
    reusable_vlm_result,
    reviewer_cards,
)


def candidate(
    source_key: str = "128-02",
    *,
    quality: str = "史诗",
    revenue: bool = False,
) -> PilotCandidate:
    return PilotCandidate(
        source_key=source_key,
        hero_name="曹操",
        skin_name="超能战警",
        skin_id="12802",
        quality=quality,
        acquire_method="商城直售",
        price_text="888点券",
        online_date="2024-01-01",
        image_path="/tmp/skin.jpg",
        image_hash="a" * 64,
        has_revenue_evidence=revenue,
        selection_tier=quality,
    )


def vlm_output(value: float = 8.0) -> dict[str, object]:
    return {
        "status": "ok",
        "model_detail": value,
        "effect_quality": value,
        "color_scheme": value,
        "composition": value,
        "uniqueness": value,
        "costume_design": value,
        "background_quality": value,
    }


class CachedImageResolutionTests(unittest.TestCase):
    def test_resolves_using_stable_hero_and_skin_id_not_legacy_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "0752-曹操-12802-超能战警.jpg"
            image.write_bytes(b"image-data")
            skins = [
                {
                    "source_key": "128-02",
                    "source_index": 201,
                    "hero_name": "曹操",
                    "skin_id": "12802",
                    "skin_name": "超能战警",
                }
            ]
            resolution = resolve_cached_images(skins, tmp)["128-02"]
            self.assertEqual(resolution.resolution, "filename_reconciled")
            self.assertEqual(resolution.image_path, str(image))
            self.assertEqual(len(resolution.image_hash or ""), 64)

    def test_ambiguous_database_identity_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "0752-曹操-12802-超能战警.jpg").write_bytes(b"image-data")
            skins = [
                {
                    "source_key": "128-02",
                    "hero_name": "曹操",
                    "skin_id": "12802",
                    "skin_name": "超能战警",
                },
                {
                    "source_key": "other",
                    "hero_name": "曹操",
                    "skin_id": "12802",
                    "skin_name": "超能战警",
                },
            ]
            resolutions = resolve_cached_images(skins, tmp)
            self.assertEqual(resolutions["128-02"].resolution, "no_reconciled_cached_image")
            self.assertEqual(resolutions["other"].resolution, "no_reconciled_cached_image")

    def test_build_candidates_records_unresolved_image_exclusion(self):
        skins = [{"source_key": "128-02", "hero_name": "曹操", "skin_name": "超能战警"}]
        candidates, excluded = build_candidates(skins, {}, set())
        self.assertEqual(candidates, [])
        self.assertEqual(excluded["128-02"], "no_image_resolution")


class PilotManifestTests(unittest.TestCase):
    def test_selection_is_deterministic_and_keeps_revenue_slice(self):
        values = [
            candidate(f"skin-{index}", quality="史诗", revenue=index < 3)
            for index in range(8)
        ]
        first = select_pilot_manifest(values, limit=5, revenue_target=3, seed=42)
        second = select_pilot_manifest(values, limit=5, revenue_target=3, seed=42)
        self.assertEqual(
            [item.source_key for item in first],
            [item.source_key for item in second],
        )
        self.assertEqual(sum(item.has_revenue_evidence for item in first), 3)
        payload = manifest_payload(first, {"bad": "unresolved"}, limit=5, revenue_target=3)
        self.assertEqual(payload["selection"]["revenue_selected"], 3)
        self.assertEqual(payload["excluded"]["bad"], "unresolved")

    def test_frozen_manifest_rejects_changed_image_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "skin.jpg"
            image.write_bytes(b"first")
            frozen = candidate()
            frozen = PilotCandidate(
                **{
                    **frozen.to_dict(),
                    "image_path": str(image),
                    "image_hash": hashlib.sha256(b"first").hexdigest(),
                }
            )
            payload = manifest_payload([frozen], {}, limit=1, revenue_target=0)
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded, loaded_payload = load_frozen_manifest(path)
            self.assertEqual(loaded[0].source_key, frozen.source_key)
            self.assertEqual(canonical_payload_sha256(loaded_payload), canonical_payload_sha256(payload))

            image.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "image hash changed"):
                load_frozen_manifest(path)


class PremiumScoringTests(unittest.TestCase):
    def test_complete_score_uses_only_frozen_modalities(self):
        result = score_premium(candidate(), vlm=vlm_output(), media_score=70.0)
        self.assertEqual(result.evidence_status, "complete")
        self.assertEqual(result.ranking_group, "complete_evidence")
        self.assertEqual(result.visual_score, 80.0)
        self.assertEqual(result.official_context_score, 45.0)
        self.assertEqual(result.media_score, 70.0)
        self.assertEqual(result.perceived_premium_score, 70.5)
        self.assertEqual(result.confidence, 0.9)
        self.assertNotIn("revenue", result.to_dict())

    def test_missing_media_is_partial_with_capped_confidence(self):
        result = score_premium(candidate(), vlm=vlm_output(), media_score=None)
        self.assertEqual(result.evidence_status, "partial_no_media")
        self.assertEqual(result.ranking_group, "partial_evidence")
        self.assertEqual(result.confidence, 0.65)
        self.assertEqual(result.perceived_premium_score, 70.67)
        self.assertIn("missing_linked_media", result.warnings)

    def test_error_vlm_cannot_be_silently_scored_as_zero(self):
        result = score_premium(candidate(), vlm={"status": "error"}, media_score=None)
        self.assertIsNone(result.visual_score)
        self.assertEqual(result.perceived_premium_score, 45.0)
        self.assertEqual(result.evidence_status, "partial_no_visual_media")

    def test_pipeline_composition_alias_is_included_in_visual_score(self):
        output = vlm_output()
        output.pop("composition")
        output["vlm_composition"] = 1.0
        result = score_premium(candidate(), vlm=output, media_score=None)
        self.assertEqual(result.visual_score, 70.0)

    def test_feature_trace_reconstructs_score_and_keeps_l3_non_scoring(self):
        output = {
            **vlm_output(),
            "design_style": "水墨国风",
            "tier_provenance": {
                "l1": {"model": "local", "producer_signature": "l1"},
                "l2": {"model": "local", "producer_signature": "l2"},
                "l3": {"model": "remote", "producer_signature": "l3"},
            },
        }
        result = score_premium(candidate(), vlm=output, media_score=None)
        trace = build_feature_trace(
            candidate(),
            vlm=output,
            media_score=None,
            media_status="missing",
            meaningful_media_comments=0,
            linked_media_ids=[],
            score=result,
            manifest_sha256="manifest",
        )
        self.assertEqual(trace["fusion"]["reconstructed_score"], 70.67)
        self.assertEqual(trace["inputs"]["semantic"]["score_role"], "non_scoring_rationale")
        self.assertEqual(trace["inputs"]["semantic"]["values"]["design_style"], "水墨国风")
        self.assertEqual(trace["provenance_status"], "complete")


class MediaEvidenceTests(unittest.TestCase):
    def test_synthetic_media_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "media.json"
            path.write_text(
                json.dumps(
                    {
                        "entries": [
                            {
                                "source_key": "128-02",
                                "platform": "weibo",
                                "external_id": "1",
                                "mapping_rationale": "test",
                                "is_synthetic": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "synthetic media"):
                load_media_mapping(path)

    def test_media_needs_meaningful_real_comments(self):
        score, count, ids = aggregate_media_labels(
            [{"mid": "1", "community_premium": 90, "n_meaningful": 4}]
        )
        self.assertIsNone(score)
        self.assertEqual(count, 4)
        self.assertEqual(ids, ["1"])
        score, count, ids = aggregate_media_labels(
            [
                {"mid": "1", "community_premium": 80, "n_meaningful": 5},
                {"mid": "2", "community_premium": 60, "n_meaningful": 5},
            ]
        )
        self.assertEqual(score, 70.0)
        self.assertEqual(count, 10)
        self.assertEqual(ids, ["1", "2"])


class ValidationTests(unittest.TestCase):
    def test_reviewer_csv_stays_inside_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ratings.csv"
            path.write_text(
                "source_key,perceived_premium\n128-02,77\noutside,60\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "outside pilot cohort"):
                load_reviewer_ratings(path, {"128-02"})

    def test_reviewer_cards_do_not_leak_scores_or_revenue(self):
        cards = reviewer_cards([candidate()])
        self.assertNotIn("revenue", cards[0])
        self.assertNotIn("media_score", cards[0])
        self.assertNotIn("perceived_premium_score", cards[0])

    def test_reviewer_and_revenue_validation_are_descriptive(self):
        scores = [
            PremiumScore(
                source_key=f"skin-{index}",
                perceived_premium_score=float(index * 10),
                visual_score=50.0,
                official_context_score=50.0,
                media_score=50.0,
                evidence_coverage=1.0,
                confidence=0.9,
                evidence_status="complete",
                ranking_group="complete_evidence",
            )
            for index in range(5)
        ]
        reviewer = reviewer_validation(scores, {f"skin-{i}": float(i * 10) for i in range(5)})
        revenue = revenue_validation(scores, {f"skin-{i}": float(i) for i in range(5)})
        self.assertEqual(reviewer["status"], "single_reviewer_audit")
        self.assertEqual(revenue["status"], "descriptive_held_out_validation")
        self.assertEqual(revenue["spearman_rho"], 1.0)


class PilotRunnerTests(unittest.TestCase):
    def test_output_directory_lock_rejects_concurrent_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            with output_run_lock(output_dir):
                with self.assertRaisesRegex(
                    ValueError,
                    "another premium pilot process owns output directory",
                ):
                    with output_run_lock(output_dir):
                        self.fail("concurrent writer unexpectedly acquired the lock")

    def test_resume_reuses_only_successful_rows_with_required_lineage(self):
        signed = {
            "status": "degraded",
            "tier_provenance": {"l1": {}, "l2": {}, "l3": {}},
        }
        self.assertTrue(reusable_vlm_result(signed, "full"))
        self.assertFalse(
            reusable_vlm_result(
                {"status": "partial", "tier_provenance": signed["tier_provenance"]},
                "full",
            )
        )
        self.assertFalse(
            reusable_vlm_result(
                {"status": "ok", "tier_provenance": {"l1": {}, "l2": {}}},
                "full",
            )
        )

    def test_runner_writes_reproducible_artifacts_without_optional_clients(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "skins.sqlite3"
            image_dir = root / "images"
            image_dir.mkdir()
            (image_dir / "0001-廉颇-10501-地狱岩魂.jpg").write_bytes(b"image")
            import sqlite3

            with sqlite3.connect(db_path) as conn:
                ensure_schema(conn)
                save_hero(
                    conn,
                    HeroRecord(
                        hero_id="105", hero_name="廉颇", id_name="lianpo",
                        title="正义爆轰", hero_type_code=3, hero_type="坦克",
                        skin_names=["地狱岩魂"], raw_json={},
                    ),
                )
                save_skin(
                    conn,
                    SkinRecord(
                        source_key="105-02", source_index=2, hero_id="105",
                        hero_name="廉颇", skin_index=2, skin_id="10501",
                        skin_name="地狱岩魂", quality="史诗", online_date="2024-06-01",
                        intro="sample", acquire_method="商城直售", price_text="888点券",
                        image_url="", detail_url="", mobile_url="", video_id="",
                        catalog_source="test", detail_source="test", has_detail_record=True,
                        raw_catalog_json={}, raw_detail_json={},
                    ),
                    None,
                )
                conn.commit()
            output_dir = root / "output"
            args = Namespace(
                db=db_path, image_dir=image_dir, output_dir=output_dir,
                manifest=None,
                media_map=None, weibo_db=root / "missing-weibo.sqlite3",
                reviewer_csv=None, limit=1, revenue_target=0, seed=42,
                run_vlm=False, vlm_results=None, execution_mode="full",
                require_remote=False, media_use_llm=False, force_media=False,
                dry_run=False,
            )
            with redirect_stdout(StringIO()):
                self.assertEqual(asyncio.run(async_main(args)), 0)
            self.assertTrue((output_dir / "manifest.json").exists())
            self.assertTrue((output_dir / "scores.jsonl").exists())
            self.assertTrue((output_dir / "feature_trace.jsonl").exists())
            self.assertTrue((output_dir / "run_metadata.json").exists())
            report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["score_summary"]["partial"], 1)

            frozen_output = root / "frozen-output"
            args.output_dir = frozen_output
            args.manifest = output_dir / "manifest.json"
            with redirect_stdout(StringIO()):
                self.assertEqual(asyncio.run(async_main(args)), 0)
            frozen_report = json.loads(
                (frozen_output / "report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                frozen_report["trace_summary"]["manifest_sha256"],
                report["trace_summary"]["manifest_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
