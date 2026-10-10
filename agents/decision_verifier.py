"""Discriminative judge/verifier for agent artifacts, built on decision-model-preview.

Role split using the ``docs/20-cs329a-robust-verification.md`` vocabulary:

* **judge** - the decision model answers typed questions (``choice`` / ``noul`` /
  ``score``) about an artifact in one forward pass and returns probabilities.
* **verifier** - calibrated thresholds plus a polarity rule turn judge answers
  into ``pass`` / ``escalate`` / ``fail`` and write an audit record.
* **evaluator** - labelled dev-set metrics (``evaluate``, ``sweep_thresholds``).
  A judge's own confidence cannot establish its accuracy; only labels can.

Measured constraints that shaped this module (evidence:
``progress/2026-10-04-systemone-decision-model-smoke.md``,
``outputs/systemone/smoke-20261004-v2.json``):

1. Probabilities are quantised to 0.01 and can sum to 0.99/1.01, so thresholds
   live on a 0.01 grid and the sum check allows ``0.005 * levels``.
2. The same item scored ``confidence=0.63`` alone and ``0.89`` inside a 10-item
   batch. Thresholds are therefore bound to the batch size they were calibrated
   at, and ``Thresholds`` refuses to gate a differently shaped call.
3. ``confidence`` is not top-1 ``p`` (equal in 18/38 measured answers, otherwise
   lower by up to 0.18), so the routing field is declared explicitly instead of
   being assumed interchangeable.
4. Published doc confidences did not reproduce (#8 flipped spam -> pass), so no
   threshold is hardcoded; uncalibrated thresholds can only escalate.
5. The service accepts a 1-level ``score`` (HTTP 200, ``confidence=1.0``,
   ``input_tokens=0``) even though the docs require 2-255 levels, so specs are
   validated client-side before any request is sent.
6. Repeated identical calls were bit-identical, so ``(spec_hash, state_hash)``
   is a safe cache key and audit records are replayable.

Prompt-injection resistance of ``state`` was **not** measured; until an
adversarial dev subset exists this gate is advisory, not a security boundary.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

MODEL = "decision-model-preview"
MAX_QUESTIONS_PER_REQUEST = 16  # documented recommendation; latency grows ~linearly
MIN_LEVELS = 2                  # docs require 2-255 score levels; server does not enforce it
MIN_OPTIONS = 2
PROB_ROUND_TOL_PER_LEVEL = 5e-3
SCORE_TOL = 5e-2

GATEWAYS = {
    "sp": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode",
    "ws": "https://maas.qianwenaiapi.com/compatible-mode",
    "default": "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode",
}

VIOLATION = "violation"    # P(yes) high means the artifact is wrong
REQUIREMENT = "requirement"  # P(yes) high means the artifact satisfies a rule

Transport = Callable[[dict[str, Any]], tuple[int, Any, float]]


def select_gateway(api_key: str, override: str | None = None) -> tuple[str, str]:
    """Pick the documented gateway for a key family; return (base_url, reason)."""
    if override:
        return override.rstrip("/"), "explicit override"
    if api_key.startswith("sk-sp-"):
        return GATEWAYS["sp"], "key prefix sk-sp- (Token Plan)"
    if api_key.startswith("sk-ws-"):
        return GATEWAYS["ws"], "key prefix sk-ws- (workspace)"
    return GATEWAYS["default"], "default trial gateway"


def http_transport(base_url: str, api_key: str, timeout: float = 60.0) -> Transport:
    """Build the live transport; imported lazily so offline use needs no requests."""
    import requests

    url = base_url.rstrip("/") + "/v1/systemone"
    session = requests.Session()
    session.trust_env = False

    def send(payload: dict[str, Any]) -> tuple[int, Any, float]:
        started = time.perf_counter()
        response = session.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
        )
        elapsed = time.perf_counter() - started
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return response.status_code, body, elapsed

    return send


# --------------------------------------------------------------------------- #
# spec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Check:
    """One typed question about an artifact."""

    id: str
    kind: str                                     # choice | noul | score
    instructions: str
    criteria: Any                                 # dict for choice, list for score, optional dict for noul
    polarity: str = VIOLATION                     # how P(yes)/label maps to a defect
    allowed: frozenset[str] = frozenset()         # choice: labels that are not defects
    forbidden: frozenset[str] = frozenset()       # choice: labels that are always defects
    routing_field: str = "confidence"             # confidence | top_p (they are not interchangeable)

    def as_question(self) -> dict[str, Any]:
        question: dict[str, Any] = {"type": self.kind, "instructions": self.instructions}
        if self.kind == "score":
            question["criteria"] = list(self.criteria)
        elif self.criteria:
            question["criteria"] = dict(self.criteria)
        return question


@dataclass(frozen=True)
class VerifierSpec:
    """A frozen, hashable set of checks. Changing a check changes the hash."""

    name: str
    version: str
    checks: tuple[Check, ...]

    def validate(self) -> None:
        """Enforce documented limits client-side; the service does not enforce all of them."""
        if not self.checks:
            raise ValueError(f"{self.name}: spec has no checks")
        if len(self.checks) > MAX_QUESTIONS_PER_REQUEST:
            raise ValueError(
                f"{self.name}: {len(self.checks)} checks exceed the {MAX_QUESTIONS_PER_REQUEST}-question budget"
            )
        ids = [c.id for c in self.checks]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.name}: duplicate check ids")
        for check in self.checks:
            if check.kind not in {"choice", "noul", "score"}:
                raise ValueError(f"{check.id}: unsupported kind {check.kind!r} (service rejects it with HTTP 400)")
            if check.polarity not in {VIOLATION, REQUIREMENT}:
                raise ValueError(f"{check.id}: polarity must be {VIOLATION} or {REQUIREMENT}")
            if check.kind == "score":
                levels = list(check.criteria or [])
                if not MIN_LEVELS <= len(levels) <= 255:
                    raise ValueError(
                        f"{check.id}: score needs {MIN_LEVELS}-255 levels, got {len(levels)}; "
                        "the service silently answers a 1-level score with confidence 1.0"
                    )
            if check.kind == "choice":
                options = list((check.criteria or {}).keys())
                if not MIN_OPTIONS <= len(options) <= 255:
                    raise ValueError(f"{check.id}: choice needs {MIN_OPTIONS}-255 options, got {len(options)}")
                if check.forbidden and not check.allowed:
                    raise ValueError(f"{check.id}: declare allowed labels when forbidden labels are used")
                unknown = (set(check.allowed) | set(check.forbidden)) - set(options)
                if unknown:
                    raise ValueError(f"{check.id}: allowed/forbidden labels not in criteria: {sorted(unknown)}")
            if check.routing_field not in {"confidence", "top_p"}:
                raise ValueError(f"{check.id}: routing_field must be confidence or top_p")

    def questions(self) -> dict[str, dict[str, Any]]:
        return {check.id: check.as_question() for check in self.checks}

    def canonical(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "checks": [
                {
                    "id": c.id, "kind": c.kind, "instructions": c.instructions, "criteria": c.criteria,
                    "polarity": c.polarity, "allowed": sorted(c.allowed), "forbidden": sorted(c.forbidden),
                    "routing_field": c.routing_field,
                }
                for c in self.checks
            ],
        }

    def spec_hash(self) -> str:
        return hashlib.sha256(
            json.dumps(self.canonical(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# thresholds
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Thresholds:
    """Calibration artifact binding thresholds to a spec hash and batch size.

    ``Uncalibrated`` leaves every numeric bound at ``None``; routing then always
    escalates, so nothing is auto-accepted before labels exist.
    """

    spec_hash: str
    batch_size: int
    routing_field: str = "confidence"
    violation_yes_max: float | None = None   # <= this -> pass
    violation_yes_fail: float | None = None  # >= this -> fail
    requirement_yes_min: float | None = None
    requirement_yes_fail: float | None = None
    choice_confidence_min: float | None = None
    severity_max: float | None = None
    severity_fail: float | None = None
    calibrated_at: str | None = None
    dev_set_id: str | None = None
    evidence: str | None = None

    @property
    def calibrated(self) -> bool:
        return self.calibrated_at is not None

    @classmethod
    def uncalibrated(cls, spec: VerifierSpec, batch_size: int = 1) -> "Thresholds":
        return cls(spec_hash=spec.spec_hash(), batch_size=batch_size)

    @classmethod
    def load(cls, path: Path, spec: VerifierSpec, batch_size: int) -> "Thresholds":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        thresholds = cls(**{k: v for k, v in payload.items() if k in cls.__dataclass_fields__})
        thresholds.assert_compatible(spec, batch_size)
        return thresholds

    def assert_compatible(self, spec: VerifierSpec, batch_size: int) -> None:
        """Refuse to gate a call whose shape differs from the calibrated one."""
        if self.spec_hash != spec.spec_hash():
            raise ValueError(
                f"thresholds were calibrated for spec {self.spec_hash}, not {spec.spec_hash()}; recalibrate"
            )
        if self.batch_size != batch_size:
            raise ValueError(
                f"thresholds were calibrated at batch_size={self.batch_size}; measured confidence for one "
                f"item moved 0.63 -> 0.89 between single and 10-item batches, so batch_size={batch_size} "
                "needs its own calibration"
            )

    def save(self, path: Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({k: getattr(self, k) for k in self.__dataclass_fields__}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def route_value(summary: dict[str, Any], routing_field: str) -> float | None:
    """Return the declared routing signal for one answer."""
    value = summary.get(routing_field)
    return float(value) if isinstance(value, (int, float)) else None


def classify(check: Check, summary: dict[str, Any], thresholds: Thresholds) -> tuple[str, str]:
    """Map one judge answer to (action, reason); escalate whenever unsure."""
    kind = check.kind
    if kind == "noul":
        p_yes = summary.get("p_yes")
        if not isinstance(p_yes, (int, float)):
            return "escalate", "noul answer missing a numeric probability"
        if check.polarity == VIOLATION:
            hi, lo = thresholds.violation_yes_fail, thresholds.violation_yes_max
            if hi is None or lo is None:
                return "escalate", f"P(yes)={p_yes:.2f} but violation thresholds are uncalibrated"
            if p_yes >= hi:
                return "fail", f"P(yes)={p_yes:.2f} >= {hi:.2f} violation threshold"
            if p_yes <= lo:
                return "pass", f"P(yes)={p_yes:.2f} <= {lo:.2f} violation threshold"
            return "escalate", f"P(yes)={p_yes:.2f} between {lo:.2f} and {hi:.2f}"
        hi, lo = thresholds.requirement_yes_min, thresholds.requirement_yes_fail
        if hi is None or lo is None:
            return "escalate", f"P(yes)={p_yes:.2f} but requirement thresholds are uncalibrated"
        if p_yes >= hi:
            return "pass", f"P(yes)={p_yes:.2f} >= {hi:.2f} requirement threshold"
        if p_yes <= lo:
            return "fail", f"P(yes)={p_yes:.2f} <= {lo:.2f} requirement threshold"
        return "escalate", f"P(yes)={p_yes:.2f} between {lo:.2f} and {hi:.2f}"

    if kind == "score":
        score = summary.get("score")
        if not isinstance(score, (int, float)):
            return "escalate", "score answer missing a numeric value"
        if thresholds.severity_max is None or thresholds.severity_fail is None:
            return "escalate", f"score={score:.2f} but severity thresholds are uncalibrated"
        if score >= thresholds.severity_fail:
            return "fail", f"score={score:.2f} >= {thresholds.severity_fail:.2f}"
        if score <= thresholds.severity_max:
            return "pass", f"score={score:.2f} <= {thresholds.severity_max:.2f}"
        return "escalate", f"score={score:.2f} between calibrated bounds"

    label = summary.get("label")
    if not isinstance(label, str):
        return "escalate", "choice answer missing a label"
    if label in check.forbidden:
        return "fail", f"label {label!r} is a declared defect"
    signal = route_value(summary, check.routing_field)
    minimum = thresholds.choice_confidence_min
    if minimum is None:
        return "escalate", f"label {label!r} but choice threshold is uncalibrated"
    if signal is None:
        return "escalate", f"label {label!r} has no {check.routing_field!r} value"
    if label in check.allowed and signal >= minimum:
        return "pass", f"label {label!r} with {check.routing_field}={signal:.2f} >= {minimum:.2f}"
    if label in check.allowed:
        return "escalate", f"label {label!r} but {check.routing_field}={signal:.2f} < {minimum:.2f}"
    return "fail", f"label {label!r} is outside the allowed set"


# --------------------------------------------------------------------------- #
# answer parsing
# --------------------------------------------------------------------------- #
def summarise_answer(answer: Any) -> tuple[dict[str, Any], list[str]]:
    """Normalise one answer and return (summary, invariant_errors)."""
    errors: list[str] = []
    if not isinstance(answer, dict):
        return {}, [f"answer is not an object ({type(answer).__name__})"]
    summary: dict[str, Any] = {"type": answer.get("type")}

    probs_raw = answer.get("probabilities")
    probs: dict[str, float] = {}
    if isinstance(probs_raw, dict):
        try:
            probs = {str(k): float(v) for k, v in probs_raw.items()}
        except (TypeError, ValueError):
            errors.append("probabilities contain non-numeric values")
        deviation = abs(sum(probs.values()) - 1.0)
        allowed = max(2e-3, PROB_ROUND_TOL_PER_LEVEL * max(len(probs), 1))
        if probs and deviation > allowed:
            errors.append(f"probabilities sum deviates {deviation:.4f} > {allowed:.4f}")
        summary["probabilities"] = probs
        summary["top_p"] = max(probs.values()) if probs else None

    kind = answer.get("type")
    if kind == "noul":
        value = answer.get("noul")
        if isinstance(value, (int, float)) and 0.0 <= float(value) <= 1.0:
            summary["p_yes"] = float(value)
        else:
            errors.append(f"noul value out of range: {value!r}")
    elif kind == "choice":
        label = answer.get("choice")
        summary["label"] = label if isinstance(label, str) else None
        if probs and isinstance(label, str) and label in probs:
            if max(probs, key=lambda k: probs[k]) != label:
                errors.append(f"choice {label!r} is not the probability argmax")
    elif kind == "score":
        value = answer.get("score")
        if isinstance(value, (int, float)):
            summary["score"] = float(value)
            weights = {int(k): v for k, v in probs.items() if str(k).lstrip("-").isdigit()}
            if weights:
                total = sum(weights.values())
                mean = sum(i * p for i, p in weights.items()) / total if total else 0.0
                if abs(mean - float(value)) > SCORE_TOL + 1e-9:
                    errors.append(f"score {value} != probability-weighted mean {mean:.4f}")
        else:
            errors.append(f"score value is not numeric: {value!r}")
    else:
        errors.append(f"unsupported answer type {kind!r}")

    confidence = answer.get("confidence")
    if isinstance(confidence, (int, float)):
        summary["confidence"] = float(confidence)
    return summary, errors


# --------------------------------------------------------------------------- #
# verifier
# --------------------------------------------------------------------------- #
@dataclass
class Verdict:
    check_id: str
    kind: str
    action: str
    reason: str
    summary: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    action: str                      # pass | escalate | fail
    verdicts: list[Verdict]
    audit: dict[str, Any]
    invariant_errors: list[str] = field(default_factory=list)

    def by_check(self) -> dict[str, Verdict]:
        return {v.check_id: v for v in self.verdicts}


class DecisionVerifier:
    """Send one typed-question request per artifact and gate the answers."""

    def __init__(
        self,
        spec: VerifierSpec,
        thresholds: Thresholds,
        *,
        transport: Transport | None = None,
        batch_size: int = 1,
        audit: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        spec.validate()
        self.spec = spec
        self.batch_size = batch_size
        thresholds.assert_compatible(spec, batch_size)
        self.thresholds = thresholds
        self.transport = transport
        self.audit = audit or (lambda record: None)

    @classmethod
    def from_credentials(
        cls,
        spec: VerifierSpec,
        thresholds: Thresholds,
        api_key: str,
        *,
        base_url: str | None = None,
        batch_size: int = 1,
        timeout: float = 60.0,
        audit: Callable[[dict[str, Any]], None] | None = None,
    ) -> "DecisionVerifier":
        """Build a live verifier; call ``select_gateway`` separately to log the reason."""
        url, _ = select_gateway(api_key, base_url)
        return cls(
            spec, thresholds, transport=http_transport(url, api_key, timeout),
            batch_size=batch_size, audit=audit,
        )

    def verify(self, state: dict[str, Any]) -> VerificationResult:
        if self.transport is None:
            raise ValueError("no transport configured; pass transport= or use from_credentials")
        payload = {"model": MODEL, "state": state, "questions": self.spec.questions()}
        status, body, elapsed = self.transport(payload)
        state_hash = hashlib.sha256(
            json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        invariant_errors: list[str] = []
        verdicts: list[Verdict] = []
        audit: dict[str, Any] = {
            "spec": self.spec.name, "spec_version": self.spec.version,
            "spec_hash": self.spec.spec_hash(), "batch_size": self.batch_size,
            "state_hash": state_hash, "http_status": status,
            "wall_seconds": round(elapsed, 4), "thresholds_calibrated": self.thresholds.calibrated,
            "thresholds_calibrated_at": self.thresholds.calibrated_at,
            "routing_field": self.thresholds.routing_field,
        }

        if status != 200 or not isinstance(body, dict):
            invariant_errors.append(f"HTTP {status}: {str(body)[:200]}")
            for check in self.spec.checks:
                verdicts.append(Verdict(check.id, check.kind, "escalate", f"request failed (HTTP {status})"))
            audit["action"] = "escalate"
            audit["invariant_errors"] = invariant_errors
            self.audit(audit)
            return VerificationResult("escalate", verdicts, audit, invariant_errors)

        audit["request_id"] = body.get("request_id")
        audit["input_tokens"] = (body.get("usage") or {}).get("input_tokens")
        audit["latency_ms"] = body.get("latency_ms")
        answers = body.get("answers") or {}
        if set(answers) != set(self.spec.questions()):
            invariant_errors.append(f"answer ids {sorted(answers)} != check ids {sorted(self.spec.questions())}")

        for check in self.spec.checks:
            summary, errors = summarise_answer(answers.get(check.id))
            for error in errors:
                invariant_errors.append(f"{check.id}: {error}")
            if errors or not summary:
                verdicts.append(Verdict(check.id, check.kind, "escalate",
                                        "answer failed invariant checks" if errors else "no answer returned",
                                        summary))
                continue
            action, reason = classify(check, summary, self.thresholds)
            verdicts.append(Verdict(check.id, check.kind, action, reason, summary))

        order = {"fail": 2, "escalate": 1, "pass": 0}
        overall = max((v.action for v in verdicts), key=lambda a: order[a]) if verdicts else "escalate"
        audit["action"] = overall
        audit["counts"] = {a: sum(1 for v in verdicts if v.action == a) for a in ("pass", "escalate", "fail")}
        audit["invariant_errors"] = invariant_errors
        self.audit(audit)
        return VerificationResult(overall, verdicts, audit, invariant_errors)


# --------------------------------------------------------------------------- #
# evaluator (labels, never judge confidence)
# --------------------------------------------------------------------------- #
def evaluate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Gate metrics over labelled rows: {"label": "correct"|"defect", "action": ...}.

    Escalations are counted separately; they are neither accepted nor rejected.
    """
    rows = list(rows)
    resolved = [r for r in rows if r["action"] in {"pass", "fail"}]
    defects = [r for r in rows if r["label"] == "defect"]
    correct = [r for r in rows if r["label"] == "correct"]
    false_accept = [r for r in defects if r["action"] == "pass"]
    false_reject = [r for r in correct if r["action"] == "fail"]
    decided_right = [r for r in resolved if (r["label"] == "correct") == (r["action"] == "pass")]

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    return {
        "n": len(rows),
        "resolved": len(resolved),
        "escalated": len(rows) - len(resolved),
        "auto_rate": rate(len(resolved), len(rows)),
        "accuracy_on_resolved": rate(len(decided_right), len(resolved)),
        "resolved_correct_rate": rate(len(decided_right), len(rows)),
        "false_accept_rate": rate(len(false_accept), len(defects)),
        "false_reject_rate": rate(len(false_reject), len(correct)),
        "defects": len(defects),
        "correct": len(correct),
    }


def sweep_thresholds(
    scores: Sequence[float],
    labels: Sequence[str],
    *,
    budget: float = 0.0,
    grid: Sequence[float] | None = None,
) -> dict[str, Any]:
    """Pick the violation threshold that resolves the most rows correctly.

    ``scores`` are judge P(yes) values for a violation check; ``labels`` are
    ``correct``/``defect``. The grid is 0.01-wide because the service quantises
    probabilities to two decimals. Maximising correctly resolved rows (not the
    automation rate) keeps a reject-everything gate from winning the sweep.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have equal length")
    if not scores:
        raise ValueError("no labelled rows to calibrate")
    grid = tuple(round(0.01 * i, 2) for i in range(101)) if grid is None else tuple(grid)
    best: dict[str, Any] | None = None
    for pass_max in grid:
        fail_min = min((g for g in grid if g > pass_max), default=None)
        if fail_min is None:
            continue
        actions = ["pass" if s <= pass_max else "fail" if s >= fail_min else "escalate" for s in scores]
        metrics = evaluate([{"label": l, "action": a} for l, a in zip(labels, actions)])
        false_accept = metrics["false_accept_rate"]
        if false_accept is None or false_accept > budget:
            continue
        candidate = {"violation_yes_max": pass_max, "violation_yes_fail": fail_min, **metrics}
        key = (candidate["resolved_correct_rate"] or 0.0, candidate["auto_rate"] or 0.0)
        if best is None or key > (best["resolved_correct_rate"] or 0.0, best["auto_rate"] or 0.0):
            best = candidate
    if best is None:
        return {"calibrated": False, "reason": f"no threshold on the grid meets false_accept budget {budget}"}
    return {"calibrated": True, **best}


# --------------------------------------------------------------------------- #
# EmoGame report spec (proposed; labels do not exist yet)
# --------------------------------------------------------------------------- #
SEVERITY_LEVELS = [
    "无问题：表述与证据一致，并已说明资料缺口",
    "轻微：措辞不精确，但不改变结论或不会被误读为已验证事实",
    "严重：把估计、假设或图片模型判断表述为已验证事实、官方结论或实测数据",
    "致命：虚构数字、虚构价格单位换算，或给出会被直接执行且与证据矛盾的建议",
]

EVIDENCE_PROVENANCE = {
    "official_catalog": "来自官方目录字段（价格原文、上线日期、品质）",
    "image_model_judgment": "来自本地 VLM 对图片的判断，非社区或局内观测",
    "community_observation": "来自已入库的社区/情绪证据档案",
    "catalog_estimate": "由目录派生的估计值或规则评分",
    "insufficient": "明确说明资料不足，未给出结论",
    "unsupported": "在所提供的证据中找不到依据",
}


def report_checks() -> tuple[Check, ...]:
    """Checks derived from REPORT_SYSTEM_PROMPT rules and the docs/20 error list."""
    yes_no = {"true": "存在该问题", "false": "不存在该问题"}
    return (
        Check("official_attribution", "noul",
              "报告是否把项目评分、项目审核或规则结果说成官方评分、官方审核、官方审计？",
              yes_no, VIOLATION),
        Check("provenance_mismatch", "noul",
              "报告是否把图片模型判断、目录估计或规则评分说成社区实测或局内观测？",
              yes_no, VIOLATION),
        Check("currency_unit_error", "noul",
              "报告是否把点券/水晶价格换算成人民币，或改变了官方价格的单位？",
              yes_no, VIOLATION),
        Check("hypothetical_as_observed", "noul",
              "报告是否把假设情景、合成数据或测算示例说成已发生的收入、销量或用户行为？",
              yes_no, VIOLATION),
        Check("stale_as_current", "noul",
              "报告是否把过期证据或未审核档案表述为当前有效结论？",
              yes_no, VIOLATION),
        Check("aggregate_inconsistent", "noul",
              "报告中的数字是否与所提供证据在算术上不一致（求和、比例、区间越界）？",
              yes_no, VIOLATION),
        Check("gameplay_overclaim", "noul",
              "报告是否仅凭壁纸或静态图片断言局内手感、动态特效、购买意愿或销量？",
              yes_no, VIOLATION),
        Check("fabricated_radar_scores", "noul",
              "在 five_dimension_scores 为 null 时，报告是否给出了具体五维度分数？",
              yes_no, VIOLATION),
        Check("competitor_out_of_scope", "noul",
              "报告是否引用了 competitor_data 之外的皮肤，或声称同英雄只有这些皮肤？",
              yes_no, VIOLATION),
        Check("gap_disclosure", "noul",
              "在资料不足处，报告是否明确写出资料不足而不是给出结论？",
              {"true": "已明确说明资料不足", "false": "未说明资料不足"}, REQUIREMENT),
        Check("evidence_provenance", "choice",
              "报告结论主要依赖哪一类依据？", EVIDENCE_PROVENANCE, VIOLATION,
              allowed=frozenset(EVIDENCE_PROVENANCE) - {"unsupported"},
              forbidden=frozenset({"unsupported"})),
        Check("severity", "score", "若存在上述问题，最严重的一条属于哪个等级？",
              SEVERITY_LEVELS, VIOLATION),
    )


def report_spec(version: str = "report-jev-v0") -> VerifierSpec:
    """12 checks -> one request per report, inside the 16-question budget."""
    return VerifierSpec(name="emogame_report_verification", version=version, checks=report_checks())
