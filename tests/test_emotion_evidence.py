"""Regression tests for the fail-closed P1 emotion-evidence contract."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from data.emotion_evidence_repository import EmotionEvidenceRepository
from feature_engineering.features import MarketValidationSignals, SkinFeatureVector
from models.emotion_evidence import (
    SUBJECTIVE_ASPECTS,
    EvidenceQualificationProfile,
    aggregate_adjudicated_annotations,
    evidence_content_hash,
    sanitize_public_text,
    signal_values_from_profile,
)
from models.emotion_workflow import (
    MODEL_GENERATION_OPTIONS,
    build_cohort_manifest,
    build_sanitized_evidence_item,
    calibration_metrics,
    sha256_json,
    validate_annotation,
    validate_cohort_manifest,
    write_review_pack,
)
from models.final_truth_emotion import (
    score_final_truth_rows,
    score_observed_annotation_rows,
)
from models.rule_engine import RuleEngine
from scripts import run_emotion_evidence


PROTOCOL_HASH = "a" * 64


def cohort_manifest(run_id: str = "run-1") -> dict:
    records = []
    for index in range(100):
        records.append(
            {
                "source_key": f"skin-{index:03d}",
                "hero_name": f"hero-{index:03d}",
                "skin_name": f"name-{index:03d}",
                "online_date": "2024-01-01",
                "cohort_role": "warm_start" if index < 50 else "coverage_extension",
                "release_era": "pre_2021",
            }
        )
    return {
        "schema_version": 1,
        "protocol_version": "emotion-evidence-v1",
        "run_id": run_id,
        "records_sha256": sha256_json(records),
        "records": records,
    }


def eligible_profile(source_key: str, run_id: str = "run-1") -> EvidenceQualificationProfile:
    return EvidenceQualificationProfile(
        run_id=run_id,
        source_key=source_key,
        published=False,
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
        protocol_hash=PROTOCOL_HASH,
    )


def qualifying_rows(*, actual_use_after: int = 20) -> list[dict]:
    rows = []
    for index in range(20):
        rows.append(
            {
                "evidence_id": index + 1,
                "source_key": "skin-000",
                "platform": "weibo" if index < 10 else "bilibili",
                "parent_external_id": "parent-a" if index < 10 else "parent-b",
                "mapping_scope": "exact_skin",
                "author_hash": f"author-{index:02d}",
                "is_synthetic": False,
                "quarantine_reason": None,
                "input_class": "public_comment",
                "relevance": "relevant",
                "aspects": list(SUBJECTIVE_ASPECTS),
                "polarities": {aspect: 1 for aspect in SUBJECTIVE_ASPECTS},
                "actual_use": index < actual_use_after,
                "published_at": "2025-01-01" if index < actual_use_after else "2023-01-01",
                "online_date": "2024-01-01",
                "confidence": 1.0,
            }
        )
    return rows


class EmotionEvidenceContractTests(unittest.TestCase):
    def test_final_truth_scorer_separates_partial_and_complete_scores(self):
        complete = {
            "evidence_id": 1,
            "source_key": "complete",
            "relevance": "relevant",
            "aspects": list(SUBJECTIVE_ASPECTS),
            "polarities": {aspect: 1 for aspect in SUBJECTIVE_ASPECTS},
        }
        partial = {
            "evidence_id": 2,
            "source_key": "partial",
            "relevance": "relevant",
            "aspects": ["visual_appeal"],
            "polarities": {"visual_appeal": -1},
        }
        missing = {
            "evidence_id": 3,
            "source_key": "missing",
            "relevance": "irrelevant",
            "aspects": [],
            "polarities": {},
        }

        scores = score_final_truth_rows([complete, partial, missing])
        self.assertEqual(scores["complete"].score_status, "complete_final_truth")
        self.assertEqual(scores["complete"].complete_six_aspect_score, 75)
        self.assertEqual(scores["complete"].observed_emotion_score, 75)
        self.assertEqual(scores["partial"].score_status, "partial_final_truth")
        self.assertEqual(scores["partial"].observed_emotion_score, 25)
        self.assertIsNone(scores["partial"].complete_six_aspect_score)
        self.assertEqual(scores["missing"].score_status, "no_relevant_final_truth")
        self.assertIsNone(scores["missing"].observed_emotion_score)

    def test_observed_scorer_labels_model_comment_outputs(self):
        scores = score_observed_annotation_rows(
            [
                {
                    "evidence_id": 1,
                    "source_key": "comment-skin",
                    "relevance": "relevant",
                    "aspects": ["in_game_feel"],
                    "polarities": {"in_game_feel": 1},
                }
            ],
            source_kind="model_comments",
        )
        score = scores["comment-skin"]
        self.assertEqual(score.score_status, "partial_model_comments")
        self.assertEqual(score.observed_emotion_score, 75)

    def test_cohort_is_deterministic_and_extension_quotas_are_exact(self):
        skins = []
        warm = []
        for index in range(50):
            key = f"warm-{index:02d}"
            warm.append(key)
            skins.append(
                {
                    "source_key": key,
                    "hero_name": f"warm-hero-{index:02d}",
                    "skin_name": f"warm-skin-{index:02d}",
                    "online_date": "2025-01-01",
                }
            )
        for era, count, date in (
            ("old", 20, "2020-01-01"),
            ("middle", 20, "2022-01-01"),
            ("missing", 10, ""),
        ):
            for index in range(count):
                skins.append(
                    {
                        "source_key": f"{era}-{index:02d}",
                        "hero_name": f"{era}-hero-{index:02d}",
                        "skin_name": f"{era}-skin-{index:02d}",
                        "online_date": date,
                    }
                )
        first = build_cohort_manifest(skins, warm, seed=7)
        second = build_cohort_manifest(reversed(skins), warm, seed=7)
        validate_cohort_manifest(first)
        self.assertEqual(first, second)
        extension = [r for r in first["records"] if r["cohort_role"] == "coverage_extension"]
        self.assertEqual(
            Counter(r["release_era"] for r in extension),
            Counter({"pre_2021": 20, "2021_2024": 20, "missing_date": 10}),
        )
        self.assertEqual(len({r["hero_name"] for r in extension}), 50)

    def test_privacy_sanitizer_removes_handles_and_long_ids_not_prices(self):
        sanitized = sanitize_public_text("@some_user 来看 UID 123456789，价格 1788 点券")
        self.assertNotIn("some_user", sanitized)
        self.assertNotIn("123456789", sanitized)
        self.assertIn("1788", sanitized)

    def test_evidence_builder_quarantines_fallback_and_out_of_window(self):
        base = dict(
            run_id="run-1",
            source_key="skin-000",
            platform="weibo",
            parent_external_id="post-1",
            external_id="comment-1",
            author_value="user-1",
            text="这个皮肤特效很好看",
            parent_title="英雄 name-000 皮肤",
            skin_name="name-000",
            url=None,
        )
        accepted = build_sanitized_evidence_item(
            **base, mapping_scope="exact_skin", published_at="2025-01-01"
        )
        self.assertIsNone(accepted["quarantine_reason"])
        fallback = build_sanitized_evidence_item(
            **base, mapping_scope="hero_fallback", published_at="2025-01-01"
        )
        self.assertEqual(fallback["quarantine_reason"], "hero_only_or_fallback_mapping")
        old = build_sanitized_evidence_item(
            **base, mapping_scope="exact_skin", published_at="2024-01-01"
        )
        self.assertEqual(old["quarantine_reason"], "outside_observation_window")

    def test_qualification_requires_every_gate_and_post_release_feel(self):
        profile = aggregate_adjudicated_annotations(
            qualifying_rows(),
            run_id="run-1",
            source_key="skin-000",
            protocol_hash=PROTOCOL_HASH,
            calibration_passed=True,
            audit_passed=True,
        )
        self.assertTrue(profile.is_eligible_for_publication)
        self.assertEqual(profile.score, 75)
        self.assertEqual(profile.parent_document_count, 2)
        self.assertEqual(profile.qualified_aspect_count, 6)
        self.assertEqual((profile.score_ci_low, profile.score_ci_high), (75.0, 75.0))

        insufficient = aggregate_adjudicated_annotations(
            qualifying_rows(actual_use_after=4),
            run_id="run-1",
            source_key="skin-000",
            protocol_hash=PROTOCOL_HASH,
            calibration_passed=True,
            audit_passed=True,
        )
        self.assertIn("insufficient_actual_use_feel_evidence", insufficient.validation_reasons)

    def test_forbidden_lineage_cannot_qualify(self):
        rows = qualifying_rows()
        rows.append({**rows[0], "evidence_id": 99, "input_class": "official_prior"})
        profile = aggregate_adjudicated_annotations(
            rows,
            run_id="run-1",
            source_key="skin-000",
            protocol_hash=PROTOCOL_HASH,
            calibration_passed=True,
            audit_passed=True,
        )
        self.assertIn(
            "forbidden_input_lineage:official_prior", profile.validation_reasons
        )

    def test_rule_engine_validates_only_a_published_profile(self):
        profile = eligible_profile("skin-000")
        features = SkinFeatureVector(
            source_key="skin-000",
            hero_name="hero",
            skin_name="skin",
            has_detail_record=True,
            has_primary_asset=True,
            market_signals=MarketValidationSignals.from_dict(
                signal_values_from_profile(profile)
            ),
        )
        audit_only = RuleEngine().evaluate(features, profile)
        self.assertEqual(audit_only.validation_status, "insufficient_market_evidence")
        profile.published = True
        validated = RuleEngine().evaluate(features, profile)
        self.assertEqual(validated.validation_status, "evidence_validated")
        self.assertEqual(validated.evaluation_score, 75)

    def test_calibration_metrics_apply_locked_thresholds(self):
        truth = []
        predictions = []
        for index in range(10):
            row = {
                "evidence_id": index,
                "relevance": "relevant",
                "aspects": list(SUBJECTIVE_ASPECTS),
                "polarities": {aspect: 1 for aspect in SUBJECTIVE_ASPECTS},
            }
            truth.append(row)
            predictions.append(dict(row))
        metrics = calibration_metrics(truth, predictions)
        self.assertTrue(metrics["passed"])
        self.assertEqual(metrics["aspect_macro_f1"], 1.0)

    def test_local_model_annotation_disables_thinking_and_bounds_output(self):
        row = {
            "evidence_id": 7,
            "source_key": "skin-000",
            "hero_name": "hero-000",
            "skin_name": "name-000",
            "parent_title": "name-000 发布",
            "text": "这个皮肤特效很好看",
        }
        response = {
            "done": True,
            "done_reason": "stop",
            "message": {
                "content": (
                    '{"relevance":"relevant","aspects":["visual_appeal"],'
                    '"polarities":{"visual_appeal":2},"actual_use":false,'
                    '"confidence":0.9}'
                )
            },
        }
        with patch.object(
            run_emotion_evidence, "_ollama_json", return_value=response
        ) as request:
            result = run_emotion_evidence._model_annotation(
                row,
                model="qwen3.5:4b",
                host="http://127.0.0.1:11434",
                timeout=120,
            )

        payload = request.call_args.args[1]
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["options"], MODEL_GENERATION_OPTIONS)
        self.assertEqual(result["relevance"], "relevant")

    def test_local_annotator_identity_is_versioned_by_prompt(self):
        first = run_emotion_evidence._extractor_annotator_id(
            "qwen3.5:4b", prompt_hash="a" * 64
        )
        second = run_emotion_evidence._extractor_annotator_id(
            "qwen3.5:4b", prompt_hash="b" * 64
        )
        self.assertEqual(first, "qwen3.5:4b@aaaaaaaaaaaa")
        self.assertNotEqual(first, second)
        self.assertEqual(
            run_emotion_evidence._extractor_annotator_id("deterministic-v1"),
            "deterministic-v1",
        )

    def test_local_model_annotation_reports_empty_content_with_evidence_id(self):
        row = {
            "evidence_id": 8,
            "source_key": "skin-000",
            "hero_name": "hero-000",
            "skin_name": "name-000",
            "parent_title": "name-000 发布",
            "text": "这个皮肤如何",
        }
        response = {
            "done": True,
            "done_reason": "length",
            "eval_count": 256,
            "message": {"content": "", "thinking": "unfinished reasoning"},
        }
        with patch.object(
            run_emotion_evidence, "_ollama_json", return_value=response
        ):
            with self.assertRaisesRegex(
                ValueError, "empty structured content for evidence 8"
            ):
                run_emotion_evidence._model_annotation(
                    row,
                    model="qwen3.5:4b",
                    host="http://127.0.0.1:11434",
                    timeout=120,
                )

    def test_local_model_annotation_drops_only_unlabelled_zero_defaults(self):
        raw = {
            "relevance": "relevant",
            "aspects": ["in_game_feel"],
            "polarities": {
                "visual_appeal": 0,
                "in_game_feel": 1,
                "craftsmanship_quality": 0,
                "collection_value": 0,
                "value_for_money": 0,
                "purchase_intent": 0,
            },
            "actual_use": False,
            "confidence": 0.9,
        }
        normalized, notes = run_emotion_evidence._normalize_model_annotation(raw)
        self.assertEqual(normalized["aspects"], ["in_game_feel"])
        self.assertEqual(normalized["polarities"], {"in_game_feel": 1})
        self.assertTrue(any("dropped_zero_default_polarities" in note for note in notes))

    def test_local_model_annotation_adds_explicit_nonzero_polarity_aspects(self):
        raw = {
            "relevance": "relevant",
            "aspects": ["in_game_feel"],
            "polarities": {"in_game_feel": 1, "visual_appeal": 2},
            "actual_use": False,
            "confidence": 0.9,
        }
        normalized, notes = run_emotion_evidence._normalize_model_annotation(raw)
        self.assertEqual(
            normalized["aspects"], ["in_game_feel", "visual_appeal"]
        )
        self.assertEqual(
            normalized["polarities"], {"in_game_feel": 1, "visual_appeal": 2}
        )
        self.assertIn("added_nonzero_polarity_aspects=visual_appeal", notes)
        validate_annotation(normalized)

    def test_local_model_annotation_keeps_malformed_extras_invalid(self):
        raw = {
            "relevance": "relevant",
            "aspects": ["in_game_feel"],
            "polarities": {"in_game_feel": 1, "not_an_aspect": 2},
            "actual_use": False,
            "confidence": 0.9,
        }
        normalized, notes = run_emotion_evidence._normalize_model_annotation(raw)
        self.assertEqual(normalized, raw)
        self.assertEqual(notes, ())
        with self.assertRaisesRegex(
            ValueError, "polarities must contain exactly the labelled aspects"
        ):
            validate_annotation(normalized)


class EmotionEvidenceRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "emotion.sqlite3"
        self.repo = EmotionEvidenceRepository(self.db)
        manifest = cohort_manifest()
        self.repo.create_run(
            run_id="run-1",
            protocol_version="emotion-evidence-v1",
            protocol_hash=PROTOCOL_HASH,
            cohort_hash=manifest["records_sha256"],
            manifest=manifest,
            observation_start="2024-09-02",
            observation_end="2026-09-01",
            annotation_schema_version=1,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _evidence(self, *, raw=None) -> dict:
        author_hash = "b" * 32
        text = "name-000 的局内手感很好"
        return {
            "run_id": "run-1",
            "source_key": "skin-000",
            "platform": "weibo",
            "external_id": "comment-1",
            "parent_external_id": "post-1",
            "mapping_scope": "exact_skin",
            "content_hash": evidence_content_hash(
                "skin-000", "weibo", "post-1", author_hash, text
            ),
            "author_hash": author_hash,
            "text": text,
            "title": "name-000 发布",
            "published_at": "2025-01-01T00:00:00+00:00",
            "raw": raw or {"collector": "test"},
            "is_synthetic": False,
            "input_class": "public_comment",
        }

    def test_read_path_is_fail_closed_and_does_not_create_database(self):
        ghost = Path(self.tmp.name) / "ghost.sqlite3"
        self.assertIsNone(EmotionEvidenceRepository(ghost).latest_cohort_status())
        self.assertFalse(ghost.exists())

    def test_writer_rejects_raw_identity_and_out_of_cohort_target(self):
        with self.assertRaisesRegex(ValueError, "private identity"):
            self.repo.add_evidence(self._evidence(raw={"user_id": "123"}))
        outside = self._evidence()
        outside["source_key"] = "not-in-cohort"
        with self.assertRaisesRegex(ValueError, "outside"):
            self.repo.add_evidence(outside)

    def test_additive_schema_and_evidence_resume_are_idempotent(self):
        first = self.repo.add_evidence(self._evidence())
        second = self.repo.add_evidence(self._evidence())
        self.assertEqual(first, second)
        with sqlite3.connect(self.db) as conn:
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(opinion_evidence_items)")
            }
            count = conn.execute(
                "SELECT COUNT(*) FROM opinion_evidence_items WHERE collection_run_id = 'run-1'"
            ).fetchone()[0]
        self.assertTrue(
            {"collection_run_id", "mapping_scope", "content_hash", "author_hash"}
            <= columns
        )
        self.assertEqual(count, 1)

    def test_declared_final_truth_is_immutable_and_preferred(self):
        evidence_id = self.repo.add_evidence(self._evidence())
        review = Path(self.tmp.name) / "final-truth.csv"
        write_review_pack(
            review,
            [
                {
                    "review_id": "truth-1",
                    "evidence_id": evidence_id,
                    "phase": "development",
                    "source_key": "skin-000",
                    "relevance": "relevant",
                    "aspects": "in_game_feel",
                    "polarities": "in_game_feel:1",
                    "actual_use": "yes",
                    "confidence": "1",
                }
            ],
        )
        args = SimpleNamespace(
            input=review,
            db=self.db,
            run_id="run-1",
            reviewer_id="declared-truth-v1",
            kind=run_emotion_evidence.FINAL_TRUTH_KIND,
            apply=True,
        )
        run_emotion_evidence.command_import_review(args)

        truth, truth_kind = run_emotion_evidence._truth_annotations(
            self.repo, "run-1", phase="development"
        )
        self.assertEqual(truth_kind, run_emotion_evidence.FINAL_TRUTH_KIND)
        self.assertEqual(len(truth), 1)
        self.assertEqual(truth[0]["polarities"], {"in_game_feel": 1})
        self.assertEqual(
            run_emotion_evidence._development_minimum(truth_kind), 200
        )
        input_hashes = self.repo.get_run("run-1")["input_hashes"]
        self.assertIn("final-truth:development", input_hashes)
        self.assertIn("final-truth-annotations:development", input_hashes)

        conflicting = SimpleNamespace(**vars(args))
        conflicting.reviewer_id = "different-truth"
        with self.assertRaisesRegex(ValueError, "already bound"):
            run_emotion_evidence.command_import_review(conflicting)

        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE emotion_evidence_annotations SET confidence = 0.5 "
                "WHERE annotator_kind = 'final_truth'"
            )
            conn.commit()
        with self.assertRaisesRegex(ValueError, "differs from its bound digest"):
            run_emotion_evidence._truth_annotations(
                self.repo, "run-1", phase="development"
            )

    def _prepare_release(self, count: int) -> None:
        self.repo.record_ethics_status("run-1", status="ready", record_hash="ethics")
        self.repo.freeze_model_selection(
            "run-1",
            model_name="deterministic-v1",
            model_digest="digest",
            prompt_hash="prompt",
            metrics={"development": "passed"},
        )
        self.repo.set_review_gate("run-1", gate="calibration", passed=True, metrics={})
        self.repo.set_review_gate("run-1", gate="audit", passed=True, metrics={})
        self.repo.bind_validation_artifact("run-1", "artifact")
        for index in range(count):
            self.repo.save_validation_result(eligible_profile(f"skin-{index:03d}"))

    def test_publication_rolls_back_below_80(self):
        self._prepare_release(79)
        with self.assertRaisesRegex(ValueError, "79/80"):
            self.repo.publish_run("run-1")
        self.assertEqual(self.repo.get_run("run-1")["release_status"], "staged")
        self.assertEqual(self.repo.list_latest_published_profiles(), {})

    def test_publication_exposes_only_qualified_active_run(self):
        self._prepare_release(80)
        result = self.repo.publish_run("run-1")
        self.assertEqual(result["validated_count"], 80)
        self.assertEqual(len(self.repo.list_latest_published_profiles()), 80)

        second = cohort_manifest("run-2")
        self.repo.create_run(
            run_id="run-2",
            protocol_version="emotion-evidence-v1",
            protocol_hash=PROTOCOL_HASH,
            cohort_hash=second["records_sha256"],
            manifest=second,
            observation_start="2024-09-02",
            observation_end="2026-09-01",
            annotation_schema_version=1,
        )
        self.assertEqual(self.repo.list_latest_published_profiles(), {})


if __name__ == "__main__":
    unittest.main()
