"""Tests for the reporter-GRPO prompt compaction, verifier reward and launch wiring.

The prompt-budget test is the regression net for the defect that made the first
GRPO run meaningless: prompts were left at the source size (~1974 tokens) and the
trainer truncated them to ``--max_seq_len`` (768) from the left, discarding the
system prompt and most of the state.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models.emotion_evidence import SUBJECTIVE_ASPECTS
from scripts.train_reporter_grpo import (
    REQUIRED,
    SYSTEM_PROMPT,
    VerifierReward,
    compact_state,
    install_reward_hook,
    _split_args,
    prepare,
    state_from_messages,
    subject_from_messages,
    verify_report,
)

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "data" / "reporter_grpo.jsonl"
TOKENIZER_DIR = ROOT / "minimind" / "model"
MAX_SEQ_LEN = 768

SKIN_ID = "105-01"


def gold_report(skin_id: str = SKIN_ID) -> str:
    """A report with every required heading and a grounded skin id."""
    body = "".join(f"{heading}：内容。\n" for heading in REQUIRED)
    return body + f"（依据皮肤 {skin_id}）\n"


GOLD_REPORT = gold_report()


def make_state(**overrides) -> dict:
    """A state shaped like one record of data/reporter_states.jsonl."""
    state = {
        "skin_id": "105-01",
        "source_key": "105-01",
        "hero_name": "廉颇",
        "skin_name": "正义爆轰",
        "feature_vector": {
            "source_key": "105-01",
            "hero_name": "廉颇",
            "skin_name": "正义爆轰",
            "skin_id": "",
            "quality": "",
            "online_date": "",
            "acquire_method": "限时活动&碎片商店&商城直售获取",
            "price_text": None,
            "official_tier": 1,
            "quality_score": 0.2,
            "is_limited": True,
            "is_gacha": False,
            "is_direct_sale": True,
            "has_detail_record": False,
            "has_primary_asset": True,
            "skin_age_days": 147,
            "hero_skin_count": 1,
            "market_signals": {"visual_score": None, "sales_volume": 22790},
        },
        "premium_result": {
            "source_key": "105-01",
            "hero_name": "廉颇",
            "skin_name": "正义爆轰",
            "evaluation_score": 52,
            "official_prior_score": 0,
            "aspect_scores": {a: 53 for a in SUBJECTIVE_ASPECTS} | {"market_heat": None},
            "confidence": 0.25,
            "validation_status": "value_scored",
            "evidence_coverage": 0.0,
            "warnings": ["missing_official_detail"],
            "evidence": {
                "scoring_contract": "value_present",
                "score_status": "full_catalog_estimate",
                "observed_aspects": [],
                "estimated_aspects": list(SUBJECTIVE_ASPECTS),
            },
        },
        "business_analysis": {
            "decision": "hold_and_research",
            "decision_reason": "当前证据虽可评估，但销售准备度不足。",
            "sales_readiness": 52,
            "purchase_drivers": ["收藏与稀缺属性可用。"],
            "conversion_blockers": ["证据覆盖不足。"],
            "recommended_actions": [
                {"action": "接销量验证", "detail": "上线后按日回填销量，用来校准评分。"}
            ],
            "pricing_guidance": {
                "posture": "hold",
                "rationale": "证据不足以建议改变官方价格。",
                "official_tier": 1,
            },
            "evidence_gaps": ["missing_purchase_intent"],
        },
        "competitor_data": [],
        "provenance": {"source_db": "/abs/path/skins.sqlite3", "vlm_run": False},
    }
    state.update(overrides)
    return state


class VerifyReportTests(unittest.TestCase):
    def test_gold_report_scores_full_marks(self):
        result = verify_report({"skin_id": SKIN_ID}, GOLD_REPORT)
        self.assertEqual(result["section"], 1.0)
        self.assertEqual(result["grounding"], 1.0)
        self.assertEqual(result["total"], 3.0)

    def test_section_score_scales_with_missing_headings(self):
        half = "".join(f"{h}：内容。\n" for h in REQUIRED[:3])
        result = verify_report({"skin_id": SKIN_ID}, half)
        self.assertEqual(result["section"], 0.5)
        self.assertEqual(result["grounding"], 0.0)
        self.assertEqual(result["total"], 1.0)

    def test_ungrounded_report_loses_only_grounding(self):
        result = verify_report({"skin_id": SKIN_ID}, GOLD_REPORT)
        other = verify_report({"skin_id": "999-99"}, GOLD_REPORT)
        self.assertEqual(result["section"], 1.0)
        self.assertEqual(other["section"], 1.0)
        self.assertEqual(other["grounding"], 0.0)
        self.assertEqual(other["total"], 2.0)

    def test_blank_skin_id_is_not_grounded(self):
        self.assertEqual(verify_report({"skin_id": ""}, GOLD_REPORT)["grounding"], 0.0)

    def test_task_scaffold_rewards_grounding_and_evidence_before_a_full_heading(self):
        state = compact_state(make_state())
        report = "情绪：廉颇的正义爆轰（105-01）资料不足，当前评分为52。收藏与稀缺属性可用。接销量验证。"
        result = verify_report(state, report)
        self.assertEqual(result["section"], 0.0)
        self.assertGreater(result["heading_progress"], 0.0)
        self.assertEqual(result["grounding"], 1.0)
        self.assertEqual(result["evidence_caveat"], 1.0)
        self.assertEqual(result["numeric"], 1.0)
        self.assertEqual(result["business_anchor"], 1.0)
        self.assertGreater(result["task_total"], 0.0)


class CompactStateTests(unittest.TestCase):
    def test_identity_is_carried_once_for_the_grounding_check(self):
        payload = compact_state(make_state())
        self.assertEqual(payload["subject"]["skin_id"], "105-01")
        self.assertNotIn("source_key", payload["subject"])

    def test_required_sections_match_the_verifier(self):
        payload = compact_state(make_state())
        self.assertEqual(tuple(payload["required_sections"]), REQUIRED)

    def test_provenance_is_not_sent_to_the_model(self):
        payload = compact_state(make_state())
        self.assertNotIn("provenance", payload)
        self.assertNotIn("source_db", json.dumps(payload, ensure_ascii=False))

    def test_documented_redundancies_are_absent(self):
        payload = compact_state(make_state())
        self.assertNotIn("warnings", payload["premium"])
        self.assertNotIn("official_prior_score", payload["premium"])
        self.assertNotIn("evidence_coverage", payload["premium"])
        self.assertNotIn("flags", payload["catalog"])
        self.assertNotIn("conversion_blockers", payload["business"])
        self.assertNotIn("decision_reason", payload["business"])
        self.assertNotIn("official_tier", payload["business"]["pricing_guidance"])

    def test_score_status_survives_but_the_constant_contract_does_not(self):
        payload = compact_state(make_state())
        self.assertEqual(payload["premium_scope"]["score_status"], "full_catalog_estimate")
        self.assertNotIn("scoring_contract", payload["premium_scope"])
        self.assertNotIn("estimated_aspects", payload["premium_scope"])

    def test_observed_aspects_are_carried_when_present(self):
        state = make_state()
        state["premium_result"]["evidence"]["observed_aspects"] = ["visual_appeal"]
        payload = compact_state(state)
        self.assertEqual(payload["premium_scope"]["observed_aspects"], ["visual_appeal"])

    def test_evidence_gates_survive_because_they_drive_insufficiency_claims(self):
        payload = compact_state(make_state())
        self.assertIs(payload["catalog"]["has_detail_record"], False)
        self.assertIs(payload["catalog"]["has_primary_asset"], True)

    def test_all_six_radar_aspects_are_carried_and_nulls_dropped(self):
        payload = compact_state(make_state())
        self.assertEqual(set(payload["premium"]["aspect_scores"]), set(SUBJECTIVE_ASPECTS))

    def test_null_market_signals_are_dropped_but_real_ones_kept(self):
        payload = compact_state(make_state())
        self.assertEqual(payload["market_signals"], {"sales_volume": 22790})

    def test_recommended_action_keeps_its_label_not_its_prose(self):
        payload = compact_state(make_state())
        self.assertEqual(payload["business"]["recommended_actions"], [{"action": "接销量验证"}])

    def test_empty_competitors_and_market_signals_are_omitted(self):
        state = make_state()
        state["feature_vector"]["market_signals"] = {"visual_score": None}
        payload = compact_state(state)
        self.assertNotIn("market_signals", payload)
        self.assertNotIn("competitors", payload)

    def test_payload_is_json_serialisable(self):
        payload = compact_state(make_state())
        self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False)), payload)


class SubjectFromMessagesTests(unittest.TestCase):
    def test_recovers_subject_from_a_compact_prompt(self):
        payload = compact_state(make_state())
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
        self.assertEqual(subject_from_messages(messages)["skin_id"], "105-01")
        self.assertEqual(state_from_messages(messages)["business"]["sales_readiness"], 52)

    def test_tolerates_the_pre_compaction_payload_shape(self):
        messages = [{"role": "user", "content": json.dumps({"skin_id": "7-7"})}]
        self.assertEqual(subject_from_messages(messages)["skin_id"], "7-7")

    def test_returns_empty_for_unreadable_content(self):
        self.assertEqual(subject_from_messages([]), {})
        self.assertEqual(subject_from_messages([{"role": "user", "content": "not json"}]), {})
        self.assertEqual(subject_from_messages([{"role": "user", "content": "[1,2]"}]), {})


class VerifierRewardTests(unittest.TestCase):
    def messages(self, payload=None) -> list[dict]:
        payload = payload or compact_state(make_state())
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]

    def stub(self, value: float):
        return lambda model, messages, response: value

    def test_verifier_raises_the_score_of_a_complete_grounded_report(self):
        hook = VerifierReward(self.stub(0.0))
        self.assertAlmostEqual(hook(None, self.messages({"skin_id": SKIN_ID}), GOLD_REPORT), 1.0)

    def test_verifier_lowers_the_score_of_an_empty_report(self):
        hook = VerifierReward(self.stub(0.0))
        self.assertAlmostEqual(hook(None, self.messages(), "no sections here"), -1.0)

    def test_reward_model_is_weighted_separately(self):
        hook = VerifierReward(self.stub(3.0), reward_model_weight=0.5)
        self.assertAlmostEqual(hook(None, self.messages({"skin_id": SKIN_ID}), GOLD_REPORT), 2.5)

    def test_combined_score_never_reaches_the_trainer_clamp(self):
        # train_grpo.calculate_rewards clamps to [-3, 3]; the blend must stay inside
        # it so a correct report is never silently downgraded to a saturated reward.
        for rm in (-3.0, 0.0, 3.0):
            for report in (GOLD_REPORT, "", "情绪溢价总览：x"):
                hook = VerifierReward(self.stub(rm))
                self.assertLess(abs(hook(None, self.messages(), report)), 3.0)

    def test_audit_row_is_written_and_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit" / "rows.jsonl"
            hook = VerifierReward(self.stub(1.0), audit_path=path)
            hook(None, self.messages({"skin_id": SKIN_ID}), GOLD_REPORT)
            hook.close()
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["skin_id"], "105-01")
        self.assertEqual(rows[0]["reward_model"], 1.0)
        self.assertEqual(rows[0]["section"], 1.0)
        self.assertEqual(rows[0]["grounding"], 1.0)
        self.assertAlmostEqual(rows[0]["combined"], 1.5)

    def test_unreadable_prompt_falls_back_to_the_reward_model_alone(self):
        hook = VerifierReward(self.stub(0.4))
        self.assertAlmostEqual(hook(None, [{"role": "user", "content": "oops"}], "x"),
                               0.5 * 0.4 - 1.0)


class InstallRewardHookTests(unittest.TestCase):
    def test_install_replaces_get_score_without_touching_the_class(self):
        class FakeRewardModel:
            def get_score(self, messages, response):
                return 0.0

        class FakeTrainerUtils:
            LMForRewardModel = FakeRewardModel

        original = FakeRewardModel.get_score
        hook = install_reward_hook(FakeTrainerUtils, verifier_weight=1.0,
                                   reward_model_weight=0.5)
        self.assertIs(FakeTrainerUtils.LMForRewardModel, FakeRewardModel)
        self.assertIsNot(FakeRewardModel.get_score, original)
        self.assertIsInstance(hook, VerifierReward)
        # Looked up on an instance the replacement is bound like any method, so it
        # still receives the model as its first argument. An unbound callable stored
        # as the attribute would be invoked as get_score(messages, response) instead.
        self.assertEqual(FakeRewardModel().get_score([], "x"), -1.0)

    def test_installed_hook_scores_a_real_prompt(self):
        class FakeRewardModel:
            def get_score(self, messages, response):
                return 1.0

        class FakeTrainerUtils:
            LMForRewardModel = FakeRewardModel

        install_reward_hook(FakeTrainerUtils, verifier_weight=1.0, reward_model_weight=0.5)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({"skin_id": SKIN_ID})}]
        self.assertEqual(FakeRewardModel().get_score(messages, GOLD_REPORT), 1.5)


class VerifierOnlyModeTests(unittest.TestCase):
    """Verifier-only mode must not touch the reward model, which cannot load here."""

    def fake_utils(self, explode_on_load: bool = True):
        class FakeRewardModel:
            def __init__(self, model_path, device="cuda", dtype=None):
                if explode_on_load:
                    raise AssertionError("reward model must not be loaded in verifier-only mode")

            def get_score(self, messages, response):
                raise AssertionError("the reward model must not be scored in verifier-only mode")

        class FakeTrainerUtils:
            LMForRewardModel = FakeRewardModel

        return FakeTrainerUtils

    def messages(self) -> list[dict]:
        return [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"skin_id": SKIN_ID})}]

    def test_constructing_the_reward_model_is_a_no_op(self):
        utils = self.fake_utils()
        install_reward_hook(utils, verifier_only=True, reward_model_weight=0.0)
        model = utils.LMForRewardModel("/nonexistent/path")
        self.assertIsNone(model.model)
        self.assertIsNone(model.tokenizer)

    def test_reward_is_the_verifier_alone(self):
        utils = self.fake_utils()
        hook = install_reward_hook(utils, verifier_only=True, reward_model_weight=0.0)
        self.assertTrue(hook.verifier_only)
        self.assertEqual(utils.LMForRewardModel("/x").get_score(self.messages(), GOLD_REPORT), 1.0)

    def test_an_ungrounded_report_is_penalised_without_any_model(self):
        utils = self.fake_utils()
        install_reward_hook(utils, verifier_only=True, reward_model_weight=0.0)
        score = utils.LMForRewardModel("/x").get_score(self.messages(), "no sections")
        self.assertEqual(score, -1.0)

    def test_audit_row_records_the_mode_and_a_null_reward_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.jsonl"
            hook = VerifierReward(None, audit_path=path)
            hook(None, self.messages(), GOLD_REPORT)
            hook.close()
            row = json.loads(path.read_text(encoding="utf-8").strip())
        self.assertEqual(row["mode"], "verifier_only")
        self.assertIsNone(row["reward_model"])
        self.assertEqual(row["combined"], 1.0)

    def test_non_verifier_only_still_installs_a_blend(self):
        class FakeRewardModel:
            def get_score(self, messages, response):
                return 2.0

        class FakeTrainerUtils:
            LMForRewardModel = FakeRewardModel

        hook = install_reward_hook(FakeTrainerUtils, reward_model_weight=0.5)
        self.assertFalse(hook.verifier_only)
        self.assertEqual(FakeRewardModel().get_score(self.messages(), GOLD_REPORT), 2.0)


class RewardModelSupportGuardTests(unittest.TestCase):
    """Blend mode must fail fast rather than surface a remote-code traceback."""

    def test_refuses_on_transformers_5(self):
        from unittest import mock

        import transformers
        from scripts.train_reporter_grpo import _check_reward_model_supported

        with mock.patch.object(transformers, "__version__", "5.10.2"):
            with self.assertRaises(SystemExit) as caught:
                _check_reward_model_supported()
        self.assertIn("--verifier-only", str(caught.exception))

    def test_allows_the_transformers_the_remote_code_targets(self):
        from unittest import mock

        import transformers
        from scripts.train_reporter_grpo import _check_reward_model_supported

        with mock.patch.object(transformers, "__version__", "4.41.2"):
            _check_reward_model_supported()


class TaskSignalTests(unittest.TestCase):
    @staticmethod
    def trainer_module():
        minimind = str(ROOT / "minimind")
        if minimind not in sys.path:
            sys.path.insert(0, minimind)
        from trainer import train_grpo

        return train_grpo

    def test_task_signal_stats_uses_task_reward_groups_not_total_advantage(self):
        import torch

        trainer = self.trainer_module()
        stats = trainer.task_signal_stats(
            torch.tensor([-1.0, -1.0, -1.0, -1.0, -1.0, -0.5, -1.0, -1.0]),
            num_generations=4,
        )
        self.assertEqual(stats["groups"], 2)
        self.assertEqual(stats["signal_groups"], 1)
        self.assertEqual(stats["signal_fraction"], 0.5)

    def test_zero_generic_weight_cannot_change_task_rewards(self):
        import torch

        trainer = self.trainer_module()
        existed = hasattr(trainer, "args")
        previous = getattr(trainer, "args", None)
        trainer.args = SimpleNamespace(device="cpu", num_generations=2,
                                       generic_reward_weight=0.0)

        class FixedTaskReward:
            def get_score(self, messages, response):
                return 0.25

        try:
            total, task, generic = trainer.calculate_rewards(
                ["<|im_start|>user\nstate<|im_end|>"],
                ["brief response", "another brief response"],
                FixedTaskReward(),
            )
        finally:
            if existed:
                trainer.args = previous
            else:
                del trainer.args
        self.assertTrue(torch.equal(total, task))
        self.assertTrue(torch.equal(generic, torch.zeros_like(generic)))


class LaunchDefaultsTests(unittest.TestCase):
    def test_launcher_defaults_to_task_only_with_a_signal_gate(self):
        args, forwarded = _split_args([])
        self.assertEqual(forwarded, [])
        self.assertEqual(args.generic_reward_weight, 0.0)
        self.assertEqual(args.task_signal_groups, 32)
        self.assertEqual(args.min_task_signal_fraction, 0.30)


class PromptBudgetTests(unittest.TestCase):
    """The regression the first GRPO run failed: prompts must fit max_seq_len."""

    def load_prompts(self) -> list[dict]:
        return [json.loads(line) for line in PROMPTS.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    @unittest.skipUnless(PROMPTS.is_file(), "data/reporter_grpo.jsonl not prepared")
    def test_every_prompt_carries_a_subject_and_the_required_sections(self):
        for record in self.load_prompts():
            payload = json.loads(record["conversations"][1]["content"])
            self.assertTrue(payload["subject"]["skin_id"])
            self.assertEqual(tuple(payload["required_sections"]), REQUIRED)

    @unittest.skipUnless(PROMPTS.is_file(), "data/reporter_grpo.jsonl not prepared")
    def test_prompts_fit_within_the_trainer_truncation_window(self):
        if not TOKENIZER_DIR.is_dir():
            self.skipTest("minimind tokenizer not available")
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_DIR)
        lengths = []
        for record in self.load_prompts():
            text = tokenizer.apply_chat_template(
                record["conversations"][:-1], tokenize=False,
                open_thinking=False, add_generation_prompt=True)
            lengths.append(len(tokenizer(text)["input_ids"]))
        lengths.sort()
        p95 = lengths[int(0.95 * len(lengths)) - 1]
        self.assertLessEqual(
            lengths[-1], MAX_SEQ_LEN,
            f"prompt of {lengths[-1]} tokens would be truncated by train_grpo")
        self.assertLessEqual(p95, 700, f"p95 prompt length {p95} leaves no headroom")

    def test_prepare_round_trips_a_state_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "states.jsonl"
            target = Path(tmp) / "prompts.jsonl"
            source.write_text(json.dumps(make_state(), ensure_ascii=False) + "\n",
                              encoding="utf-8")
            self.assertEqual(prepare(source, target), 1)
            record = json.loads(target.read_text(encoding="utf-8").strip())
            self.assertEqual(record["conversations"][0]["content"], SYSTEM_PROMPT)
            self.assertEqual(record["conversations"][-1]["content"], "")
            payload = json.loads(record["conversations"][1]["content"])
            self.assertEqual(payload["subject"]["skin_id"], "105-01")

    def test_prepare_rejects_a_state_without_a_skin_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "states.jsonl"
            source.write_text(json.dumps({"skin_id": ""}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare(source, Path(tmp) / "out.jsonl")

    def test_prepare_refuses_to_overwrite_its_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "states.jsonl"
            source.write_text(json.dumps(make_state(), ensure_ascii=False) + "\n",
                              encoding="utf-8")
            with self.assertRaises(ValueError):
                prepare(source, source)


if __name__ == "__main__":
    unittest.main()
