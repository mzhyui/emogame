"""Offline tests for the systemone decision-model smoke script.

These exercise response validation, gateway selection and the determinism
comparator with synthetic payloads only; no network calls are made.  The live
counterpart is ``scripts/smoke_systemone_decision_model.py``.
"""

from __future__ import annotations

import unittest
from typing import Any

from scripts.smoke_systemone_decision_model import (
    BASE_URLS,
    CONTRACT_PROBES,
    MODERATION_COMMENTS,
    MODERATION_CRITERIA,
    Expectation,
    check_stability,
    resolve_base_url,
    scenarios,
    validate_answer,
    validate_response,
)

CHOICE_QUESTION: dict[str, Any] = {
    "type": "choice",
    "instructions": "应该由哪个团队处理？",
    "criteria": {"billing": "支付、退款和账单问题", "technical": "产品故障和集成问题"},
}
NOUL_QUESTION: dict[str, Any] = {"type": "noul", "instructions": "是否需要立即通知值班人员？"}
SCORE_QUESTION: dict[str, Any] = {
    "type": "score",
    "instructions": "这个问题有多严重？",
    "criteria": ["轻微", "部分受影响", "核心不可用", "严重业务影响"],
}


def doc_response(**overrides: Any) -> dict[str, Any]:
    """The response envelope shape published in the decision-model API reference."""
    body: dict[str, Any] = {
        "model": "decision-model-preview",
        "request_id": "7b986c65-b223-9341-b5f0-b988e27ecaac",
        "answers": {
            "department": {
                "type": "choice",
                "choice": "billing",
                "confidence": 0.88,
                "probabilities": {"billing": 0.94, "technical": 0.06},
            },
            "escalate": {"type": "noul", "noul": 0.99},
            "severity": {
                "type": "score",
                "score": 2.25,
                "confidence": 0.91,
                "legend": {"0": "轻微", "1": "部分受影响", "2": "核心不可用", "3": "严重业务影响"},
                "probabilities": {"0": 0.0, "1": 0.01, "2": 0.73, "3": 0.26},
            },
        },
        "usage": {"input_tokens": 125},
        "latency_ms": 52.9,
    }
    body.update(overrides)
    return body


def payload() -> dict[str, Any]:
    return {
        "model": "decision-model-preview",
        "state": {"content": "支付后 24 小时未到账"},
        "questions": {
            "department": CHOICE_QUESTION,
            "escalate": NOUL_QUESTION,
            "severity": SCORE_QUESTION,
        },
    }


class DocShapedResponseTests(unittest.TestCase):
    def test_published_example_passes_every_check(self) -> None:
        errors, observations, info = validate_response(payload(), 200, doc_response(), 0.2, {})

        self.assertEqual(errors, [])
        self.assertEqual(info["request_id"], "7b986c65-b223-9341-b5f0-b988e27ecaac")
        self.assertEqual(info["input_tokens"], 125)
        self.assertEqual(info["answers"]["department"]["prediction"], "billing")
        self.assertEqual(info["answers"]["severity"]["weighted_mean"], 2.25)
        self.assertTrue(any("probabilities object" in note for note in observations))

    def test_noul_answer_without_probabilities_is_accepted(self) -> None:
        errors, _, summary = validate_answer("escalate", NOUL_QUESTION, {"type": "noul", "noul": 0.99}, None)

        self.assertEqual(errors, [])
        self.assertEqual(summary["side"], "yes")
        self.assertNotIn("probabilities", summary)

    def test_generated_text_field_is_reported(self) -> None:
        answer = {"type": "noul", "noul": 0.4, "text": "模型顺带生成了一段解释"}

        _, observations, _ = validate_answer("escalate", NOUL_QUESTION, answer, None)

        self.assertTrue(any("generated-text" in note for note in observations))


class ChoiceValidationTests(unittest.TestCase):
    def test_label_outside_declared_criteria_is_rejected(self) -> None:
        answer = {"type": "choice", "choice": "logistics", "confidence": 0.9,
                  "probabilities": {"logistics": 0.9, "billing": 0.1}}

        errors, _, _ = validate_answer("department", CHOICE_QUESTION, answer, None)

        self.assertTrue(any("not among declared options" in e for e in errors))

    def test_choice_must_equal_probability_argmax(self) -> None:
        answer = {"type": "choice", "choice": "billing", "confidence": 0.9,
                  "probabilities": {"billing": 0.2, "technical": 0.8}}

        errors, _, _ = validate_answer("department", CHOICE_QUESTION, answer, None)

        self.assertTrue(any("argmax" in e for e in errors))

    def test_missing_confidence_on_choice_is_rejected(self) -> None:
        answer = {"type": "choice", "choice": "billing", "probabilities": {"billing": 1.0, "technical": 0.0}}

        errors, _, _ = validate_answer("department", CHOICE_QUESTION, answer, None)

        self.assertTrue(any("missing numeric confidence" in e for e in errors))

    def test_expected_label_mismatch_is_reported(self) -> None:
        answer = {"type": "choice", "choice": "technical", "confidence": 0.9,
                  "probabilities": {"technical": 0.9, "billing": 0.1}}
        expectation = Expectation("choice", label="billing")

        errors, _, summary = validate_answer("department", CHOICE_QUESTION, answer, expectation)

        self.assertTrue(any("expected choice 'billing'" in e for e in errors))
        self.assertFalse(summary["correct"])


class ProbabilityQuantisationTests(unittest.TestCase):
    def test_two_decimal_rounding_deviation_is_an_observation_not_a_failure(self) -> None:
        answer = {"type": "score", "score": 2.76, "confidence": 0.92,
                  "legend": {"0": "轻微", "1": "部分受影响", "2": "核心不可用", "3": "严重业务影响"},
                  "probabilities": {"0": 0.0, "1": 0.0, "2": 0.22, "3": 0.77}}

        errors, observations, _ = validate_answer("severity", SCORE_QUESTION, answer, None)

        self.assertEqual(errors, [])
        self.assertTrue(any("quantised to 2 decimals" in note for note in observations))

    def test_deviation_beyond_rounding_budget_is_rejected(self) -> None:
        answer = {"type": "choice", "choice": "billing", "confidence": 0.8,
                  "probabilities": {"billing": 0.7, "technical": 0.1}}

        errors, _, _ = validate_answer("department", CHOICE_QUESTION, answer, None)

        self.assertTrue(any("probabilities sum to 0.800000" in e for e in errors))

    def test_choice_without_probabilities_is_rejected(self) -> None:
        answer = {"type": "choice", "choice": "billing", "confidence": 0.8}

        errors, _, _ = validate_answer("department", CHOICE_QUESTION, answer, None)

        self.assertTrue(any("missing probabilities object" in e for e in errors))


class ScoreValidationTests(unittest.TestCase):
    def test_score_must_match_probability_weighted_mean(self) -> None:
        answer = {"type": "score", "score": 0.5, "confidence": 0.9,
                  "legend": {"0": "轻微", "1": "部分受影响", "2": "核心不可用", "3": "严重业务影响"},
                  "probabilities": {"0": 0.0, "1": 0.01, "2": 0.73, "3": 0.26}}

        errors, _, _ = validate_answer("severity", SCORE_QUESTION, answer, None)

        self.assertTrue(any("probability-weighted mean" in e for e in errors))

    def test_legend_must_echo_requested_levels(self) -> None:
        answer = {"type": "score", "score": 2.25, "confidence": 0.9,
                  "legend": {"0": "完全不同的描述", "1": "部分受影响", "2": "核心不可用", "3": "严重业务影响"},
                  "probabilities": {"0": 0.0, "1": 0.01, "2": 0.73, "3": 0.26}}

        errors, _, _ = validate_answer("severity", SCORE_QUESTION, answer, None)

        self.assertTrue(any("legend does not echo" in e for e in errors))

    def test_score_outside_expected_band_is_reported(self) -> None:
        answer = {"type": "score", "score": 0.0, "confidence": 1.0,
                  "legend": {"0": "轻微", "1": "部分受影响", "2": "核心不可用", "3": "严重业务影响"},
                  "probabilities": {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}}
        expectation = Expectation("score", index_min=2.0, index_max=3.0)

        errors, _, summary = validate_answer("severity", SCORE_QUESTION, answer, expectation)

        self.assertTrue(any("outside expected band" in e for e in errors))
        self.assertFalse(summary["correct"])

    def test_noul_side_expectation_uses_half_threshold(self) -> None:
        errors, _, summary = validate_answer(
            "escalate", NOUL_QUESTION, {"type": "noul", "noul": 0.32}, Expectation("noul", side="yes")
        )

        self.assertEqual(summary["side"], "no")
        self.assertFalse(summary["correct"])
        self.assertTrue(any("expected noul side" in e for e in errors))


class EnvelopeValidationTests(unittest.TestCase):
    def test_non_200_status_is_a_hard_failure(self) -> None:
        errors, _, info = validate_response(payload(), 401, {"code": "InvalidApiKey"}, 0.1, {})

        self.assertEqual(info["http_status"], 401)
        self.assertTrue(any("HTTP 401" in e for e in errors))

    def test_answer_ids_must_match_question_ids(self) -> None:
        body = doc_response()
        body["answers"].pop("severity")

        errors, _, _ = validate_response(payload(), 200, body, 0.2, {})

        self.assertTrue(any("answer ids" in e for e in errors))

    def test_missing_request_id_and_tokens_are_reported(self) -> None:
        body = doc_response(request_id=None, usage={"input_tokens": 0})

        errors, _, _ = validate_response(payload(), 200, body, 0.2, {})

        self.assertTrue(any("request_id" in e for e in errors))
        self.assertTrue(any("input_tokens" in e for e in errors))

    def test_reported_latency_must_fit_measured_wall_time(self) -> None:
        body = doc_response(latency_ms=5000.0)

        errors, _, _ = validate_response(payload(), 200, body, 0.2, {})

        self.assertTrue(any("exceeds measured wall time" in e for e in errors))


class GatewaySelectionTests(unittest.TestCase):
    def test_token_plan_key_routes_to_token_plan_gateway(self) -> None:
        url, reason = resolve_base_url("sk-sp-tokenplankey", None)

        self.assertEqual(url, BASE_URLS["sp"])
        self.assertIn("sk-sp-", reason)

    def test_workspace_key_routes_to_platform_gateway(self) -> None:
        url, _ = resolve_base_url("sk-ws-workspacekey", None)

        self.assertEqual(url, BASE_URLS["ws"])

    def test_explicit_base_url_wins_and_trailing_slash_is_stripped(self) -> None:
        url, reason = resolve_base_url("sk-sp-tokenplankey", "https://example.test/compatible-mode/")

        self.assertEqual(url, "https://example.test/compatible-mode")
        self.assertEqual(reason, "explicit --base-url")

    def test_unknown_prefix_falls_back_to_trial_gateway(self) -> None:
        url, _ = resolve_base_url("sk-plainkey", None)

        self.assertEqual(url, BASE_URLS["default"])


class DeterminismTests(unittest.TestCase):
    @staticmethod
    def run_with(answers: dict[str, Any]) -> dict[str, Any]:
        return {"response": {"answers": answers}, "http_status": 200}

    def test_identical_repeats_are_stable(self) -> None:
        answers = {"department": {"type": "choice", "choice": "billing", "confidence": 0.85,
                                  "probabilities": {"billing": 0.93, "technical": 0.07}}}
        runs = [self.run_with(answers), self.run_with(answers)]

        stable, notes = check_stability(runs)

        self.assertTrue(stable)
        self.assertTrue(any("identical" in note for note in notes))

    def test_label_drift_is_detected(self) -> None:
        first = self.run_with({"department": {"type": "choice", "choice": "billing", "confidence": 0.85,
                                             "probabilities": {"billing": 0.93, "technical": 0.07}}})
        second = self.run_with({"department": {"type": "choice", "choice": "technical", "confidence": 0.85,
                                              "probabilities": {"billing": 0.07, "technical": 0.93}}})

        stable, notes = check_stability([first, second])

        self.assertFalse(stable)
        self.assertTrue(any("department.choice" in note for note in notes))

    def test_single_run_cannot_prove_determinism(self) -> None:
        stable, notes = check_stability([self.run_with({"department": {"type": "choice", "choice": "billing"}})])

        self.assertFalse(stable)
        self.assertTrue(any("fewer than two" in note for note in notes))


class ScenarioDefinitionTests(unittest.TestCase):
    def test_scenario_keys_are_unique(self) -> None:
        keys = [s.key for s in scenarios()]

        self.assertEqual(len(keys), len(set(keys)))

    def test_expectations_and_informational_ids_exist_in_questions(self) -> None:
        for scenario in scenarios():
            with self.subTest(scenario=scenario.key):
                self.assertTrue(scenario.questions)
                self.assertLessEqual(set(scenario.expectations), set(scenario.questions))
                self.assertLessEqual(set(scenario.informational), set(scenario.questions))
                for question in scenario.questions.values():
                    self.assertIn(question["type"], {"choice", "noul", "score"})
                    if question["type"] == "score":
                        self.assertGreaterEqual(len(question["criteria"]), 2)
                    elif question["type"] == "choice":
                        self.assertGreaterEqual(len(question["criteria"]), 2)

    def test_expected_labels_are_declared_options(self) -> None:
        for scenario in scenarios():
            for qid, expectation in scenario.expectations.items():
                if expectation.kind != "choice":
                    continue
                with self.subTest(scenario=scenario.key, question=qid):
                    self.assertIn(expectation.label, scenario.questions[qid]["criteria"])

    def test_moderation_batch_reproduces_documented_sample(self) -> None:
        batch = next(s for s in scenarios() if s.key == "moderation_batch10")

        self.assertEqual(len(MODERATION_COMMENTS), 10)
        self.assertEqual(len(batch.questions), 10)
        self.assertEqual([c["id"] for c in batch.state["comments"]], [cid for cid, _, _ in MODERATION_COMMENTS])
        for question in batch.questions.values():
            self.assertEqual(question["criteria"], MODERATION_CRITERIA)
        self.assertEqual(batch.expectations["c5"].label, "pass")  # doc: negative tone still passes
        self.assertEqual(batch.expectations["c10"].label, "spam")
        self.assertEqual(batch.informational, ("c8",))  # doc's 转人工 sample, tracked not asserted

    def test_contract_probes_are_well_formed(self) -> None:
        self.assertTrue(CONTRACT_PROBES)
        for probe in CONTRACT_PROBES:
            with self.subTest(probe=probe["name"]):
                self.assertEqual(probe["payload"]["model"], "decision-model-preview")
                self.assertIn("questions", probe["payload"])
                self.assertIn("state", probe["payload"])


if __name__ == "__main__":
    unittest.main()
