#!/usr/bin/env python3
"""Reporter-GRPO pipeline for the report-generation agent.

Two modes:

``--prepare``
    Compact ``reporter_states.jsonl`` records into RLAIFDataset-compatible prompts.
    The full state stays in ``reporter_states.jsonl`` for audit; only the prompt
    payload is compacted, because the trainer truncates prompts to
    ``--max_seq_len`` (768 by default) from the left.

``launch``
    Run minimind's GRPO trainer with the task verifier blended into the reward.
    ``minimind/trainer/train_grpo.py`` is a top-level script with no ``main()`` and
    no reward hook, so this mode imports its reward-model class first, replaces
    ``LMForRewardModel.get_score`` with :class:`VerifierReward`, then executes the
    trainer via :func:`runpy.run_path`. The local trainer exposes task-versus-generic
    reward accounting and a cold-start signal gate for this workflow.

    ``--verifier-only`` skips loading the 1.8B reward model entirely and trains on
    :func:`verify_report` alone. It also defaults the inherited generic
    length/think/repetition reward to zero, so proxy variance cannot masquerade as
    task learning. It is the working configuration here: the shipped reward model's
    remote code targets transformers 4.41 and scores NaN on 5.x.

Both modes are deliberately offline: neither runs a VLM nor writes to the catalog DB.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.emotion_evidence import SUBJECTIVE_ASPECTS  # noqa: E402

MINIMIND = ROOT / "minimind"
TRAINER_CWD = MINIMIND / "trainer"
TRAIN_GRPO = TRAINER_CWD / "train_grpo.py"

REQUIRED = ("情绪溢价总览", "五维度雷达解读", "定价区间建议", "风险提示", "竞品参照", "运营策略建议")
SYSTEM_PROMPT = (
    "你是游戏商业化分析师。根据给定状态用中文生成报告，包含指定的所有章节。"
    "区分目录估计、规则评分和实际观测。没有定价区间、竞品或视觉证据时，"
    "明确说明资料不足，不编造价格、五维度得分或竞品；建议必须与证据一致。"
)

# Each available task component is normalized against its own attainable maximum
# before mapping to [-1, 1].  These are report-task checks, not generic fluency
# proxies such as raw length or n-gram repetition.
_REWARD_WEIGHTS = {
    "section": 2.0,
    "heading_progress": 0.5,
    "grounding": 1.0,
    "evidence_caveat": 0.75,
    "numeric": 0.5,
    "business_anchor": 0.75,
}

# Identity is repeated in feature_vector / premium_result / business_analysis in the
# source records; the prompt carries it once under "subject".
_CATALOG_SCALARS = (
    "quality", "acquire_method", "price_text",
    "official_tier", "quality_score", "skin_age_days", "hero_skin_count",
)
# The six is_* acquisition booleans are not carried separately: every record that has
# them also has acquire_method, whose Chinese text encodes the same facts (verified
# across the current dataset), so a flags list would only restate it.
# The two evidence gates are kept explicitly: they gate every "资料不足" statement
# the system prompt asks for.
_EVIDENCE_GATES = ("has_detail_record", "has_primary_asset")
_MARKET_SIGNALS = (
    "visual_score", "feel_score", "craftsmanship_score", "collection_score",
    "value_score", "purchase_intent_score", "sentiment_score", "discussion_count",
    "video_views", "marketing_volume", "sales_volume", "avg_spend_to_obtain",
    "ownership_rate",
)
_PREMIUM_SCALARS = ("evaluation_score", "confidence", "validation_status")
# The one part of premium_result.evidence the system prompt actually needs.
# scoring_contract is deliberately absent: it is the identical enum string in every
# record of the current dataset, so it would be a per-prompt constant.
_PREMIUM_SCOPE = ("score_status", "observed_aspects")
_BUSINESS_FIELDS = (
    "decision", "sales_readiness", "purchase_drivers",
    "recommended_actions", "evidence_gaps",
)
# The six radar dimensions come from the shared contract; market_heat is the one
# rule-engine aspect outside it and is usually null.
_ASPECT_FIELDS = SUBJECTIVE_ASPECTS + ("market_heat",)

# Fields the source records carry but the prompt does not, each because a cheaper
# field already states the same fact. Kept as prose so the omission is reviewable:
#   premium.warnings            -> every entry restates catalog.has_detail_record /
#                                  has_primary_asset, or duplicates evidence_gaps
#   premium.official_prior_score-> duplicates catalog.official_tier
#   premium.evidence_coverage   -> derivable from the observed/estimated split
#   premium_scope.estimated_aspects -> exactly the keys present in aspect_scores
#   premium_scope.scoring_contract  -> the identical string in every record
#   business.conversion_blockers-> restates evidence_gaps
#   business.decision_reason    -> restates decision + conversion_blockers
#   business.recommended_actions[].detail -> restates evidence_gaps / purchase_drivers
#   pricing_guidance.official_tier -> duplicates catalog.official_tier
#   catalog.online_date         -> superseded by skin_age_days
#   catalog.is_*                -> encoded by catalog.acquire_method
_DROPPED_PRICING_KEYS = ("official_tier",)


def _normalized_text(value) -> str:
    return "".join(str(value or "").lower().split())


def _subject(state: dict) -> dict:
    subject = state.get("subject") if isinstance(state, dict) else None
    return subject if isinstance(subject, dict) else (state if isinstance(state, dict) else {})


def _coverage(report: str, values) -> float | None:
    targets = {_normalized_text(value) for value in values if _normalized_text(value)}
    if not targets:
        return None
    text = _normalized_text(report)
    return sum(target in text for target in targets) / len(targets)


def _heading_progress(report: str) -> float:
    """Give partial, auditable credit for a multi-character heading prefix."""
    text = _normalized_text(report)
    progresses = []
    for heading in REQUIRED:
        normalized_heading = _normalized_text(heading)
        matched = 0
        # Single characters are too common to constitute structural progress.
        for length in range(len(normalized_heading), 1, -1):
            if normalized_heading[:length] in text:
                matched = length
                break
        progresses.append(matched / len(normalized_heading))
    return sum(progresses) / len(progresses)


def _numeric_targets(state: dict) -> list[str]:
    premium = state.get("premium") or {}
    business = state.get("business") or {}
    values = (premium.get("evaluation_score"), business.get("sales_readiness"))
    # One-digit IDs/tier values have far too many accidental matches in prose.
    return list({str(value) for value in values if value is not None and len(str(value)) >= 2})


def _business_anchors(state: dict) -> list[str]:
    business = state.get("business") or {}
    anchors = []
    for value in business.get("purchase_drivers") or []:
        if isinstance(value, str):
            anchors.append(value)
    for action in business.get("recommended_actions") or []:
        if isinstance(action, dict) and isinstance(action.get("action"), str):
            anchors.append(action["action"])
    return anchors


def verify_report(state: dict, report: str) -> dict[str, float | None]:
    """Return auditable, task-specific reward components for one report.

    The legacy ``total`` remains for audit compatibility.  ``task_total`` is the
    normalized-reward numerator: beyond exact headings it credits subject grounding,
    required evidence caveats, values from the compact state, and business anchors.
    These checks provide a denser scaffold but are still mechanical; they do not
    establish semantic report quality or replace held-out evaluation.
    """
    state = state if isinstance(state, dict) else {}
    subject = _subject(state)
    section = sum(heading in report for heading in REQUIRED) / len(REQUIRED)
    progress = _heading_progress(report)
    grounding = _coverage(report, (
        subject.get("skin_id", ""), subject.get("hero_name", ""), subject.get("skin_name", ""),
    ))
    grounding = 0.0 if grounding is None else grounding

    catalog = state.get("catalog") or {}
    needs_caveat = (catalog.get("has_detail_record") is False or
                    catalog.get("has_primary_asset") is False)
    caveat = None
    if needs_caveat:
        caveat = float(any(phrase in _normalized_text(report)
                           for phrase in ("资料不足", "证据不足", "信息不足", "暂无")))

    numeric = _coverage(report, _numeric_targets(state))
    business_anchor = _coverage(report, _business_anchors(state))
    components = {
        "section": section,
        "heading_progress": progress,
        "grounding": grounding,
        "evidence_caveat": caveat,
        "numeric": numeric,
        "business_anchor": business_anchor,
    }
    task_maximum = sum(_REWARD_WEIGHTS[name] for name, value in components.items()
                       if value is not None)
    task_total = sum(_REWARD_WEIGHTS[name] * value for name, value in components.items()
                     if value is not None)
    skin = str(subject.get("skin_id", state.get("skin_id", "")))
    legacy_grounding = 1.0 if skin and skin in report else 0.0
    return {
        **components,
        "total": 2.0 * section + legacy_grounding,
        "task_total": task_total,
        "task_maximum": task_maximum,
    }


def _present(value) -> bool:
    """False for values that carry no information in a prompt; True for 0 and False."""
    return value is not None and value != "" and value != [] and value != {}


def _pick(source: dict, fields) -> dict:
    return {f: source[f] for f in fields if _present(source.get(f))}


def compact_state(state: dict) -> dict:
    """Reduce one reporter state to the prompt payload.

    Selects fields explicitly rather than pruning generically, so what the model
    sees is reviewable against this list.
    """
    features = state.get("feature_vector") or {}
    premium = state.get("premium_result") or {}
    evidence = premium.get("evidence") or {}
    business = state.get("business_analysis") or {}

    catalog = _pick(features, _CATALOG_SCALARS)
    catalog.update(_pick(features, _EVIDENCE_GATES))

    # The identity triple is repeated across feature_vector / premium_result /
    # business_analysis in the source records; the prompt carries it once.
    payload = {
        "subject": {
            "skin_id": state["skin_id"],
            "hero_name": state.get("hero_name", ""),
            "skin_name": state.get("skin_name", ""),
        },
        "catalog": catalog,
        "premium": _pick(premium, _PREMIUM_SCALARS),
        "premium_scope": _pick(evidence, _PREMIUM_SCOPE),
        "business": _pick(business, _BUSINESS_FIELDS),
        "required_sections": list(REQUIRED),
    }
    # score_status stays: it is what tells the model the aspect scores are catalog
    # estimates rather than observations, which the system prompt requires it to
    # distinguish. observed_aspects is kept only when something actually was observed;
    # an empty list says nothing score_status does not already say.
    if not payload["premium_scope"].get("observed_aspects"):
        payload["premium_scope"].pop("observed_aspects", None)

    aspects = _pick(premium.get("aspect_scores") or {}, _ASPECT_FIELDS)
    if aspects:
        payload["premium"]["aspect_scores"] = aspects

    guidance = {k: v for k, v in (business.get("pricing_guidance") or {}).items()
                if _present(v) and k not in _DROPPED_PRICING_KEYS}
    if guidance:
        payload["business"]["pricing_guidance"] = guidance

    # The advisor's prose 'detail' is the text the report is meant to generate, and it
    # restates evidence_gaps and purchase_drivers; only its intent label is carried.
    actions = [{k: v for k, v in action.items() if k != "detail"}
               for action in business.get("recommended_actions") or []]
    if actions and _present(payload["business"].get("recommended_actions")):
        payload["business"]["recommended_actions"] = actions

    signals = _pick(features.get("market_signals") or {}, _MARKET_SIGNALS)
    if signals:
        payload["market_signals"] = signals

    competitors = state.get("competitor_data")
    if _present(competitors):
        payload["competitors"] = competitors

    return payload


def prepare(input_path: Path, output_path: Path) -> int:
    """Convert state records to the RLAIFDataset prompt schema."""
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input and output must differ")
    records = []
    for line in input_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            state = json.loads(line)
            if not isinstance(state, dict) or not state.get("skin_id"):
                raise ValueError("each state must be an object with a nonempty skin_id")
            records.append(state)
    if not records:
        raise ValueError("input contains no reporter states")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with output_path.open("w", encoding="utf-8") as dst:
        for state in records:
            conversations = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(compact_state(state), ensure_ascii=False)},
                # RLAIFDataset drops the final assistant turn before generation.
                {"role": "assistant", "content": ""},
            ]
            dst.write(json.dumps({"conversations": conversations}, ensure_ascii=False) + "\n")
            n += 1
    return n


def state_from_messages(messages) -> dict:
    """Recover the compact report state from a trainer-parsed prompt, or {}."""
    if not messages:
        return {}
    try:
        payload = json.loads(messages[-1].get("content", ""))
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def subject_from_messages(messages) -> dict:
    """Recover the report subject from a trainer-parsed prompt, or {} if unreadable."""
    payload = state_from_messages(messages)
    if not payload:
        return {}
    subject = payload.get("subject")
    if isinstance(subject, dict):
        return subject
    # Tolerate the pre-compaction payload shape.
    return {"skin_id": payload.get("skin_id", "")}


class VerifierReward:
    """Blend the deterministic report verifier into the reward model's score.

    Installed over ``LMForRewardModel.get_score``, which
    ``train_grpo.calculate_rewards`` calls once per generated sample. It is invoked
    inside ``torch.no_grad()``, so it returns a plain float and builds no graph.

    ``original=None`` selects verifier-only mode: no reward model is consulted, so
    the reward is entirely the auditable section/grounding verifier.
    """

    def __init__(self, original, verifier_weight: float = 1.0,
                 reward_model_weight: float = 0.5, audit_path: Path | None = None):
        self._original = original
        self.verifier_weight = verifier_weight
        self.reward_model_weight = reward_model_weight
        self.audit_path = audit_path
        self._audit = None
        self.calls = 0

    @property
    def verifier_only(self) -> bool:
        return self._original is None

    def _log(self, row: dict) -> None:
        if self.audit_path is None:
            return
        if self._audit is None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            self._audit = self.audit_path.open("a", encoding="utf-8")
        self._audit.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._audit.flush()

    def close(self) -> None:
        if self._audit is not None:
            self._audit.close()
            self._audit = None

    def __call__(self, model, messages, response) -> float:
        rm_score = None if self._original is None else float(self._original(model, messages, response))
        state = state_from_messages(messages)
        parts = verify_report(state, response)
        normalized = parts["task_total"] / parts["task_maximum"] * 2.0 - 1.0
        combined = self.verifier_weight * normalized
        if rm_score is not None:
            combined += self.reward_model_weight * rm_score
        self.calls += 1
        self._log({"call": self.calls,
                   "skin_id": _subject(state).get("skin_id", state.get("skin_id", "")),
                   "mode": "verifier_only" if rm_score is None else "blended",
                   "reward_model": rm_score, "verifier_total": parts["task_total"],
                   "verifier_maximum": parts["task_maximum"], **parts,
                   "verifier_normalized": normalized, "combined": combined})
        return combined


def _skip_reward_model_init(self, model_path=None, device="cuda", dtype=None):
    """Verifier-only stand-in for LMForRewardModel.__init__ that loads no weights.

    The trainer constructs the reward model unconditionally, and the shipped
    internlm2 remote code targets transformers 4.41 while this environment runs 5.x,
    so loading it fails (or, if shimmed, silently returns NaN). Nothing is read here
    because the installed get_score never consults the model.
    """
    self.tokenizer = None
    self.model = None
    self.device = device


def install_checkpoint_redirect(trainer_utils, out_dir: Path) -> None:
    """Keep resume state inside the gitignored ``out/``.

    ``train_grpo`` passes ``save_dir='../checkpoints'`` at both its save and its
    resume site, and minimind's .gitignore covers ``out`` but not ``checkpoints``,
    so an unredirected run leaves ~650MB of untracked files inside the submodule.
    Reading and writing are redirected together. The reporter launcher deliberately
    rejects resume flags because every corrected objective must begin from a fresh
    named run.
    """
    original = trainer_utils.lm_checkpoint

    def lm_checkpoint(lm_config, *args, save_dir=None, **kwargs):
        return original(lm_config, *args, save_dir=str(out_dir), **kwargs)

    trainer_utils.lm_checkpoint = lm_checkpoint


def install_reward_hook(trainer_utils, verifier_only: bool = False, **kwargs) -> VerifierReward:
    """Replace the reward model scorer in-process without changing its class."""
    original = None if verifier_only else trainer_utils.LMForRewardModel.get_score
    hook = VerifierReward(original, **kwargs)

    # Must be a function, not the hook object: only functions are bound when looked up
    # on an instance, so a callable attribute would be invoked as
    # get_score(messages, response) and fail on arity.
    def get_score(model, messages, response):
        return hook(model, messages, response)

    trainer_utils.LMForRewardModel.get_score = get_score
    if verifier_only:
        trainer_utils.LMForRewardModel.__init__ = _skip_reward_model_init
    return hook


def _check_reward_model_supported() -> None:
    """Fail loudly instead of surfacing a remote-code traceback from deep in transformers.

    The internlm2 reward model's remote code targets transformers 4.41 and uses APIs
    removed in transformers 5 (``rope_scaling["type"]``, ``DynamicCache.from_legacy_cache``,
    ``DynamicCache.to_legacy_cache``). Shimmed, it loads but scores every response as NaN,
    which would silently poison the reward instead of erroring.
    """
    import transformers

    major = int(transformers.__version__.split(".")[0])
    if major >= 5:
        raise SystemExit(
            f"the reward model cannot run on transformers {transformers.__version__}: its "
            "remote code targets 4.41 and scores NaN when shimmed onto 5.x.\n"
            "Use --verifier-only, or pin transformers to a 4.4x release in an isolated venv.")


def _split_args(argv):
    """Split launcher flags from the trainer flags we forward verbatim."""
    launcher = argparse.ArgumentParser(
        prog="train_reporter_grpo.py launch",
        description="Run minimind GRPO with the reporter verifier in the reward.")
    launcher.add_argument("--data", type=Path, default=ROOT / "data" / "reporter_grpo.jsonl",
                          help="prepared prompts (run --prepare first)")
    # There is deliberately no --save_dir: init_model reads the base weights from a
    # hardcoded '../out', so an overridable output dir would split weight input and
    # output across two paths. Both live in minimind/out/, which is gitignored.
    launcher.add_argument("--reward-model-path", type=str,
                          default=str(ROOT / "internlm2-1_8b-reward"))
    launcher.add_argument("--verifier-only", action="store_true",
                          help="reward from verify_report alone; do not load the reward model")
    launcher.add_argument("--verifier-weight", type=float, default=1.0)
    launcher.add_argument("--reward-model-weight", type=float, default=None,
                          help="weight on the reward model's score "
                               "(default 0.5; 0 with --verifier-only)")
    launcher.add_argument("--audit-path", type=Path, default=None,
                          help="per-sample reward audit JSONL "
                               "(default: <save_dir>/grpo_reward_audit_<utc>.jsonl)")
    launcher.add_argument("--run-name", type=str, default=None,
                          help="fresh checkpoint prefix (default: reporter_grpo_<UTC timestamp>)")
    launcher.add_argument("--generic-reward-weight", type=float, default=0.0,
                          help="weight for MiniMind's legacy length/think/repetition reward; 0 is task-only")
    launcher.add_argument("--task-signal-groups", type=int, default=32,
                          help="number of initial GRPO groups inspected by the cold-start gate")
    launcher.add_argument("--min-task-signal-fraction", type=float, default=0.30,
                          help="minimum task-varying group fraction required by the cold-start gate")
    launcher.add_argument("--task-signal-epsilon", type=float, default=1e-6,
                          help="minimum within-group task-reward std counted as signal")
    return launcher.parse_known_args(argv)


def launch(argv) -> None:
    args, forwarded = _split_args(argv)
    if not args.data.is_file():
        raise SystemExit(f"prompts not found: {args.data} (run --prepare first)")
    if not args.verifier_only and not Path(args.reward_model_path).is_dir():
        raise SystemExit(f"reward model not found: {args.reward_model_path}")
    if not TRAIN_GRPO.is_file():
        raise SystemExit(f"trainer not found: {TRAIN_GRPO} (is the minimind submodule initialised?)")
    if args.task_signal_groups < 1:
        raise SystemExit("--task-signal-groups must be at least 1 for a reporter rerun")
    if not 0.0 <= args.min_task_signal_fraction <= 1.0:
        raise SystemExit("--min-task-signal-fraction must be in [0, 1]")
    if args.task_signal_epsilon < 0.0:
        raise SystemExit("--task-signal-epsilon must be nonnegative")
    forwarded_options = {item.split("=", 1)[0] for item in forwarded}
    if {"--save_weight", "--save-weight", "--from_resume", "--from-resume"} & forwarded_options:
        raise SystemExit(
            "the reporter launcher always starts a fresh named run; use --run-name "
            "and do not pass --save-weight or --from-resume")
    if args.verifier_only:
        if args.reward_model_weight is not None:
            raise SystemExit("--verifier-only ignores --reward-model-weight; drop one of them")
        reward_model_weight = 0.0
    else:
        _check_reward_model_supported()
        reward_model_weight = 0.5 if args.reward_model_weight is None else args.reward_model_weight

    # minimind's defaults ('../out', '../model', '../../internlm2-1_8b-reward') all assume
    # this cwd; the emogame repo root silently breaks every one of them.
    out_dir = TRAINER_CWD / ".." / "out"
    # Timestamped so a relaunch cannot clobber or interleave a previous run's evidence.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = args.audit_path or (out_dir / f"grpo_reward_audit_{stamp}.jsonl")
    out_dir.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or f"reporter_grpo_{stamp}"
    if not run_name.replace("_", "").replace("-", "").isalnum():
        raise SystemExit("--run-name may contain only letters, digits, underscores, and hyphens")
    if any(out_dir.glob(f"{run_name}_*")):
        raise SystemExit(f"fresh run name already has artifacts: {run_name}")

    # A 23:17 run at MiniMind's own defaults (--batch_size 2 --num_generations 4, so
    # eight concurrent rollouts) OOMed during the forward pass, so these reporter
    # defaults pin batch_size to 1. Four concurrent rollouts then measured 5.6 GiB
    # at --max_gen_len 768 and 6.2 GiB at 1024 (about 30% of a 20 GiB card), so the
    # generation budget stays at MiniMind's 1024 rather than being cut to fit: at 384
    # the report was truncated before it could reach its six required sections.
    # Callers may override any of these deliberately.
    safe_trainer_defaults = (
        ("--max_gen_len", "1024"),
        ("--batch_size", "1"),
        ("--num_generations", "4"),
        ("--thinking_ratio", "0"),
        ("--loss_type", "grpo"),
        ("--save_interval", "50"),
    )
    injected_defaults = [item for option, value in safe_trainer_defaults
                         if option not in forwarded_options for item in (option, value)]

    os.chdir(TRAINER_CWD)
    sys.path.insert(0, str(MINIMIND))
    import trainer.trainer_utils as trainer_utils

    install_checkpoint_redirect(trainer_utils, out_dir)
    hook = install_reward_hook(trainer_utils, verifier_only=args.verifier_only,
                               verifier_weight=args.verifier_weight,
                               reward_model_weight=reward_model_weight,
                               audit_path=audit_path)

    sys.argv = [str(TRAIN_GRPO), "--data_path", str(args.data.resolve()),
                "--save_dir", "../out", "--save_weight", run_name,
                "--reward_model_path", args.reward_model_path,
                "--generic-reward-weight", str(args.generic_reward_weight),
                "--task-signal-groups", str(args.task_signal_groups),
                "--min-task-signal-fraction", str(args.min_task_signal_fraction),
                "--task-signal-epsilon", str(args.task_signal_epsilon),
                *injected_defaults,
                *forwarded]
    print(f"[reporter-grpo] cwd={TRAINER_CWD}")
    print(f"[reporter-grpo] argv={' '.join(sys.argv[1:])}")
    print(f"[reporter-grpo] run={run_name}; weights in/out: {TRAINER_CWD / '..' / 'out'}")
    print(f"[reporter-grpo] reward: "
          + (f"verify_report only (weight {args.verifier_weight})" if hook.verifier_only
             else f"verify_report {args.verifier_weight} + reward model {reward_model_weight}"))
    print(f"[reporter-grpo] generic reward weight={args.generic_reward_weight}; "
          f"task-signal gate={args.task_signal_groups} groups / "
          f"{args.min_task_signal_fraction:.0%}", flush=True)
    print(f"[reporter-grpo] audit: {audit_path}", flush=True)
    try:
        runpy.run_path(str(TRAIN_GRPO), run_name="__main__")
    finally:
        hook.close()
        print(f"[reporter-grpo] reward audit rows written: {hook.calls}")


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Dispatched on the raw token: 'launch' would be coerced to a Path by argparse.
    if argv and argv[0] == "launch":
        launch(argv[1:])
        return

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path, help="JSONL report states")
    p.add_argument("output", type=Path, help="RLAIFDataset-compatible JSONL")
    p.add_argument("--prepare", action="store_true", help="only prepare prompts")
    args = p.parse_args(argv)
    if not args.prepare:
        raise SystemExit(
            "usage: train_reporter_grpo.py <input> <output> --prepare\n"
            "       train_reporter_grpo.py launch [--verifier-weight W] [trainer args...]")
    print(f"prepared {prepare(args.input, args.output)} records")


if __name__ == "__main__":
    main()
