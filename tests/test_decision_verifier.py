"""Offline tests for the decision-model judge/verifier (no network calls)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from agents.decision_verifier import (
    MAX_QUESTIONS_PER_REQUEST,
    MODEL,
    Check,
    DecisionVerifier,
    Thresholds,
    VerifierSpec,
    classify,
    evaluate,
    report_spec,
    select_gateway,
    summarise_answer,
    sweep_thresholds,
)
from agents.prompts import REPORT_SECTIONS, validate_report


def noul(p_yes: float) -> dict[str, Any]:
    return {"type": "noul", "noul": p_yes}


def choice(label: str, probs: dict[str, float], confidence: float) -> dict[str, Any]:
    return {"type": "choice", "choice": label, "probabilities": probs, "confidence": confidence}


def score(value: float, probs: dict[str, float], confidence: float, levels: int) -> dict[str, Any]:
    return {
        "type": "score", "score": value, "confidence": confidence,
        "legend": {str(i): f"level{i}" for i in range(levels)}, "probabilities": probs,
    }


def fake_transport(answers: dict[str, dict[str, Any]], status: int = 200, body: Any = None):
    """Deterministic stand-in for POST /v1/systemone."""
    calls: list[dict[str, Any]] = []

    def transport(payload: dict[str, Any]) -> tuple[int, Any, float]:
        calls.append(payload)
        if status != 200:
            return status, body if body is not None else {"error": "boom"}, 0.05
        selected = {qid: answers[qid] for qid in payload["questions"]}
        return 200, {
            "model": MODEL, "request_id": "req-fixed", "answers": selected,
            "usage": {"input_tokens": 120}, "latency_ms": 41.0,
        }, 0.05

    transport.calls = calls  # type: ignore[attr-defined]
    return transport


def calibrated_for(spec: VerifierSpec, batch_size: int = 1) -> Thresholds:
    return Thresholds(
        spec_hash=spec.spec_hash(), batch_size=batch_size, violation_yes_max=0.10,
        violation_yes_fail=0.80, requirement_yes_min=0.70, requirement_yes_fail=0.20,
        choice_confidence_min=0.85, severity_max=0.5, severity_fail=2.0,
        calibrated_at="2026-10-04T00:00:00Z", dev_set_id="dev-fake",
    )


class GatewaySelectionTests(unittest.TestCase):
    def test_token_plan_key_selects_token_plan_gateway(self) -> None:
        url, reason = select_gateway("sk-sp-abc")

        self.assertIn("token-plan.cn-beijing.maas.aliyuncs.com", url)
        self.assertIn("Token Plan", reason)

    def test_override_wins(self) -> None:
        url, _ = select_gateway("sk-sp-abc", "https://example.test/compatible-mode/")

        self.assertEqual(url, "https://example.test/compatible-mode")


class SpecValidationTests(unittest.TestCase):
    def test_single_level_score_is_rejected_client_side(self) -> None:
        spec = VerifierSpec("s", "v", (Check("c", "score", "等级？", ["只有一个等级"]),))

        with self.assertRaises(ValueError) as ctx:
            spec.validate()

        self.assertIn("silently answers", str(ctx.exception))

    def test_question_budget_is_enforced(self) -> None:
        checks = tuple(Check(f"c{i}", "noul", "问题", None) for i in range(MAX_QUESTIONS_PER_REQUEST + 1))

        with self.assertRaises(ValueError):
            VerifierSpec("s", "v", checks).validate()

    def test_duplicate_ids_and_bad_polarity_are_rejected(self) -> None:
        dup = VerifierSpec("s", "v", (Check("c", "noul", "a", None), Check("c", "noul", "b", None)))
        bad = VerifierSpec("s", "v", (Check("c", "noul", "a", None, polarity="maybe"),))

        with self.assertRaises(ValueError):
            dup.validate()
        with self.assertRaises(ValueError):
            bad.validate()

    def test_choice_labels_must_exist_in_criteria(self) -> None:
        spec = VerifierSpec("s", "v", (
            Check("c", "choice", "哪一类？", {"a": "A", "b": "B"}, allowed=frozenset({"a", "ghost"})),
        ))

        with self.assertRaises(ValueError) as ctx:
            spec.validate()

        self.assertIn("ghost", str(ctx.exception))

    def test_spec_hash_changes_with_wording(self) -> None:
        base = report_spec().spec_hash()
        edited = VerifierSpec(
            "emogame_report_verification", "report-jev-v0",
            tuple([Check("official_attribution", "noul", "改了一个字的提问", None)]),
        ).spec_hash()

        self.assertNotEqual(base, edited)

    def test_report_spec_fits_one_request(self) -> None:
        spec = report_spec()
        spec.validate()

        self.assertLessEqual(len(spec.checks), MAX_QUESTIONS_PER_REQUEST)
        self.assertEqual(len(spec.questions()), len(spec.checks))


class ThresholdBindingTests(unittest.TestCase):
    def test_uncalibrated_thresholds_never_auto_pass(self) -> None:
        spec = report_spec()
        thresholds = Thresholds.uncalibrated(spec)
        clean = {c.id: noul(0.0) for c in spec.checks if c.kind == "noul"}
        clean["evidence_provenance"] = choice("official_catalog", {"official_catalog": 1.0}, 1.0)
        clean["severity"] = score(0.0, {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}, 1.0, 4)
        verifier = DecisionVerifier(spec, thresholds, transport=fake_transport(clean))

        result = verifier.verify({"report": "全部合规"})

        self.assertEqual(result.action, "escalate")
        self.assertTrue(all(v.action == "escalate" for v in result.verdicts))
        self.assertFalse(thresholds.calibrated)

    def test_batch_size_mismatch_is_refused(self) -> None:
        spec = report_spec()

        with self.assertRaises(ValueError) as ctx:
            DecisionVerifier(spec, calibrated_for(spec, batch_size=1), transport=fake_transport({}), batch_size=10)

        self.assertIn("0.63", str(ctx.exception))

    def test_spec_hash_mismatch_is_refused(self) -> None:
        spec = report_spec()
        stale = Thresholds(spec_hash="deadbeefdeadbeef", batch_size=1)

        with self.assertRaises(ValueError):
            DecisionVerifier(spec, stale, transport=fake_transport({}))

    def test_thresholds_round_trip_through_disk(self) -> None:
        spec = report_spec()
        thresholds = calibrated_for(spec)

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "calibration.json"
            thresholds.save(path)
            loaded = Thresholds.load(path, spec, batch_size=1)

        self.assertEqual(loaded.violation_yes_fail, thresholds.violation_yes_fail)
        self.assertEqual(loaded.dev_set_id, "dev-fake")
        self.assertTrue(loaded.calibrated)


class ClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = report_spec()
        self.thresholds = calibrated_for(self.spec)
        self.checks = {c.id: c for c in self.spec.checks}

    def test_violation_bands(self) -> None:
        check = self.checks["official_attribution"]

        self.assertEqual(classify(check, {"p_yes": 0.02}, self.thresholds)[0], "pass")
        self.assertEqual(classify(check, {"p_yes": 0.45}, self.thresholds)[0], "escalate")
        self.assertEqual(classify(check, {"p_yes": 0.95}, self.thresholds)[0], "fail")

    def test_requirement_polarity_is_inverted(self) -> None:
        check = self.checks["gap_disclosure"]

        self.assertEqual(classify(check, {"p_yes": 0.95}, self.thresholds)[0], "pass")
        self.assertEqual(classify(check, {"p_yes": 0.05}, self.thresholds)[0], "fail")
        self.assertEqual(classify(check, {"p_yes": 0.45}, self.thresholds)[0], "escalate")

    def test_forbidden_choice_label_fails_regardless_of_confidence(self) -> None:
        check = self.checks["evidence_provenance"]
        summary = {"label": "unsupported", "confidence": 1.0, "top_p": 1.0}

        action, reason = classify(check, summary, self.thresholds)

        self.assertEqual(action, "fail")
        self.assertIn("declared defect", reason)

    def test_low_confidence_choice_escalates_instead_of_passing(self) -> None:
        check = self.checks["evidence_provenance"]
        summary = {"label": "official_catalog", "confidence": 0.63, "top_p": 0.81}

        self.assertEqual(classify(check, summary, self.thresholds)[0], "escalate")
        # top_p would have passed the same threshold: the routing field is a real choice
        routed = Check("evidence_provenance", "choice", check.instructions, check.criteria,
                       allowed=check.allowed, forbidden=check.forbidden, routing_field="top_p")
        self.assertEqual(classify(routed, summary, self.thresholds)[0], "escalate")

    def test_severity_bands(self) -> None:
        check = self.checks["severity"]

        self.assertEqual(classify(check, {"score": 0.0}, self.thresholds)[0], "pass")
        self.assertEqual(classify(check, {"score": 1.2}, self.thresholds)[0], "escalate")
        self.assertEqual(classify(check, {"score": 2.75}, self.thresholds)[0], "fail")


class AnswerInvariantTests(unittest.TestCase):
    def test_two_decimal_quantisation_is_tolerated(self) -> None:
        summary, errors = summarise_answer(
            score(2.76, {"0": 0.0, "1": 0.0, "2": 0.22, "3": 0.77}, 0.92, 4)
        )

        self.assertEqual(errors, [])
        self.assertEqual(summary["score"], 2.76)

    def test_large_probability_deficit_is_an_error(self) -> None:
        _, errors = summarise_answer(choice("a", {"a": 0.7, "b": 0.1}, 0.7))

        self.assertTrue(any("sum deviates" in e for e in errors))

    def test_choice_must_be_argmax(self) -> None:
        _, errors = summarise_answer(choice("a", {"a": 0.2, "b": 0.8}, 0.8))

        self.assertTrue(any("argmax" in e for e in errors))

    def test_score_must_match_weighted_mean(self) -> None:
        _, errors = summarise_answer(score(0.5, {"0": 0.01, "1": 0.0, "2": 0.73, "3": 0.26}, 0.9, 4))

        self.assertTrue(any("weighted mean" in e for e in errors))

    def test_out_of_range_noul_is_an_error(self) -> None:
        _, errors = summarise_answer({"type": "noul", "noul": 1.4})

        self.assertTrue(any("out of range" in e for e in errors))


class VerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = report_spec()
        self.thresholds = calibrated_for(self.spec)

    def answers(self, defects: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
        defects = defects or {}
        out: dict[str, dict[str, Any]] = {}
        for check in self.spec.checks:
            if check.kind == "noul":
                value = defects.get(check.id, 0.95 if check.polarity == "requirement" else 0.01)
                out[check.id] = noul(value)
            elif check.kind == "choice":
                out[check.id] = choice("official_catalog", {"official_catalog": 1.0}, 1.0)
            else:
                out[check.id] = score(0.0, {"0": 1.0, "1": 0.0, "2": 0.0, "3": 0.0}, 1.0, 4)
        return out

    def test_clean_report_passes_and_audits(self) -> None:
        seen: list[dict[str, Any]] = []
        verifier = DecisionVerifier(
            self.spec, self.thresholds, transport=fake_transport(self.answers()), audit=seen.append
        )

        result = verifier.verify({"report": "全部合规", "price_text": "888点券"})

        self.assertEqual(result.action, "pass")
        self.assertEqual(result.invariant_errors, [])
        self.assertEqual(result.audit["request_id"], "req-fixed")
        self.assertEqual(result.audit["spec_hash"], self.spec.spec_hash())
        self.assertEqual(len(result.audit["state_hash"]), 16)
        self.assertEqual(result.audit["input_tokens"], 120)
        self.assertEqual(result.audit["counts"], {"pass": 12, "escalate": 0, "fail": 0})
        self.assertEqual(len(seen), 1)

    def test_one_defect_fails_the_whole_report(self) -> None:
        verifier = DecisionVerifier(
            self.spec, self.thresholds,
            transport=fake_transport(self.answers({"currency_unit_error": 0.97, "severity": 0.0})),
        )
        verifier.transport(self.spec.questions() and {"model": MODEL, "state": {}, "questions": {}})

        result = verifier.verify({"report": "888点券约合人民币200元"})

        self.assertEqual(result.action, "fail")
        self.assertEqual(result.by_check()["currency_unit_error"].action, "fail")

    def test_severity_gate_can_fail_on_its_own(self) -> None:
        verifier = DecisionVerifier(
            self.spec, self.thresholds,
            transport=fake_transport({**self.answers(),
                                      "severity": score(3.0, {"0": 0.0, "1": 0.0, "2": 0.0, "3": 1.0}, 1.0, 4)}),
        )

        result = verifier.verify({"report": "虚构销量"})

        self.assertEqual(result.action, "fail")
        self.assertEqual(result.by_check()["severity"].action, "fail")

    def test_transport_failure_escalates_every_check(self) -> None:
        verifier = DecisionVerifier(self.spec, self.thresholds,
                                    transport=fake_transport({}, status=500, body={"error": "upstream"}))

        result = verifier.verify({"report": "x"})

        self.assertEqual(result.action, "escalate")
        self.assertEqual(len(result.verdicts), len(self.spec.checks))
        self.assertTrue(any("HTTP 500" in e for e in result.invariant_errors))

    def test_missing_answer_escalates_that_check(self) -> None:
        answers = self.answers()
        answers.pop("gameplay_overclaim")
        transport = fake_transport({})

        def partial(payload: dict[str, Any]) -> tuple[int, Any, float]:
            return 200, {"model": MODEL, "request_id": "r", "answers": answers,
                         "usage": {"input_tokens": 10}, "latency_ms": 5.0}, 0.01

        verifier = DecisionVerifier(self.spec, self.thresholds, transport=partial)
        _ = transport  # kept to document that the fake is unused here

        result = verifier.verify({"report": "x"})

        self.assertEqual(result.action, "escalate")
        self.assertIn("gameplay_overclaim", " ".join(e for e in result.invariant_errors))

    def test_identical_state_is_reproducible(self) -> None:
        state = {"report": "同样的输入", "price_text": "888点券"}
        first = DecisionVerifier(self.spec, self.thresholds, transport=fake_transport(self.answers())).verify(state)
        second = DecisionVerifier(self.spec, self.thresholds, transport=fake_transport(self.answers())).verify(state)

        self.assertEqual(first.audit["state_hash"], second.audit["state_hash"])
        self.assertEqual([v.action for v in first.verdicts], [v.action for v in second.verdicts])


class EvaluatorTests(unittest.TestCase):
    def test_gate_metrics_are_computed_from_labels(self) -> None:
        rows = [
            {"label": "defect", "action": "fail"},    # true rejection
            {"label": "defect", "action": "pass"},    # false acceptance
            {"label": "defect", "action": "escalate"},
            {"label": "correct", "action": "pass"},
            {"label": "correct", "action": "fail"},   # false rejection
            {"label": "correct", "action": "pass"},
        ]

        metrics = evaluate(rows)

        self.assertEqual(metrics["n"], 6)
        self.assertEqual(metrics["resolved"], 5)
        self.assertEqual(metrics["escalated"], 1)
        self.assertEqual(metrics["false_accept_rate"], 0.3333)
        self.assertEqual(metrics["false_reject_rate"], 0.3333)
        self.assertEqual(metrics["accuracy_on_resolved"], 0.6)

    def test_sweep_prefers_the_widest_pass_band_within_budget(self) -> None:
        scores = [0.02, 0.05, 0.4, 0.92, 0.99]
        labels = ["correct", "correct", "correct", "defect", "defect"]

        best = sweep_thresholds(scores, labels, budget=0.0)

        self.assertTrue(best["calibrated"])
        self.assertEqual(best["false_accept_rate"], 0.0)
        self.assertEqual(best["auto_rate"], 1.0)
        self.assertEqual(best["resolved_correct_rate"], 1.0)
        self.assertLess(best["violation_yes_max"], best["violation_yes_fail"])

    def test_sweep_does_not_reward_a_reject_everything_gate(self) -> None:
        scores = [0.05, 0.1, 0.9, 0.95]
        labels = ["correct", "correct", "defect", "defect"]

        best = sweep_thresholds(scores, labels, budget=0.0)

        self.assertTrue(best["calibrated"])
        self.assertEqual(best["resolved_correct_rate"], 1.0)
        self.assertGreaterEqual(best["violation_yes_max"], 0.1)
        self.assertEqual(best["accuracy_on_resolved"], 1.0)

    def test_sweep_reports_uncalibratable_sets(self) -> None:
        # a defect the judge scores 0.0 cannot be separated from correct rows at any budget of 0
        result = sweep_thresholds([0.0, 1.0], ["defect", "correct"], budget=0.0)

        self.assertFalse(result["calibrated"])
        self.assertIn("no threshold", result["reason"])

    def test_sweep_grid_is_two_decimal_wide(self) -> None:
        best = sweep_thresholds([0.005, 0.995], ["correct", "defect"], budget=0.0)

        self.assertTrue(best["calibrated"])
        self.assertEqual(round(best["violation_yes_max"] * 100) % 1, 0)


class CoverageOfExistingValidatorTests(unittest.TestCase):
    """The semantic checks must cover what validate_report can only catch lexically."""

    def test_banned_phrase_classes_have_a_semantic_check(self) -> None:
        spec = report_spec()
        instructions = " ".join(c.instructions for c in spec.checks)

        for term in ("官方评分", "官方审核", "官方审计"):
            with self.subTest(term=term):
                self.assertIn(term, instructions)
        # "经社区观测验证" is a provenance claim, covered by provenance_mismatch
        self.assertIn("社区实测", instructions)

    def test_docs20_error_classes_have_a_check(self) -> None:
        ids = {c.id for c in report_spec().checks}

        for expected in (
            "provenance_mismatch",       # wrong-skin / wrong-source citation
            "stale_as_current",          # stale feedback presented as current
            "currency_unit_error",       # official price with the wrong unit
            "hypothetical_as_observed",  # scenario presented as observed revenue
            "aggregate_inconsistent",    # mathematically inconsistent aggregate
            "gameplay_overclaim",        # unsupported in-game behaviour claim
            "gap_disclosure",            # appropriately uncertain reports must still pass
        ):
            self.assertIn(expected, ids)

    def test_structural_validator_still_owns_shape(self) -> None:
        with self.assertRaises(ValueError):
            validate_report({"情绪溢价总览": "只有一节"})
        sections = {name: "内容" for name in REPORT_SECTIONS}

        self.assertEqual(len(validate_report(sections)), len(REPORT_SECTIONS))


if __name__ == "__main__":
    unittest.main()
