#!/usr/bin/env python3
"""Live smoke test for the decision-model (systemone) classification API.

Calls ``POST {base_url}/v1/systemone`` with ``model=decision-model-preview`` and
verifies that one forward pass returns typed answers (``choice`` / ``noul`` /
``score``) with probability distributions and confidence values instead of
generated text, then measures ``choice`` accuracy against known labels.

Endpoint selection follows the platform docs:

* ``sk-sp-`` (Token Plan)   -> https://token-plan.cn-beijing.maas.aliyuncs.com
* ``sk-ws-`` (workspace)    -> https://maas.qianwenaiapi.com
* anything else             -> https://trial.cn-beijing.maas.aliyuncs.com

The credential is read from ``DASHSCOPE_API_KEY`` (falling back to the repo
``.env``) and is never printed.  Per AGENTS.md this is an explicit live smoke
test; ``tests/test_systemone_decision_model.py`` is the offline counterpart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODEL = "decision-model-preview"
BASE_URLS = {
    "sp": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode",
    "ws": "https://maas.qianwenaiapi.com/compatible-mode",
    "default": "https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode",
}
FALLBACK_PROXY = "socks5h://127.0.0.1:7890"
PROB_TOL = 2e-3
PROB_ROUND_TOL_PER_LEVEL = 5e-3  # probabilities come back rounded to 2 decimals
SCORE_TOL = 5e-2


# --------------------------------------------------------------------------- #
# scenarios
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Expectation:
    """Expected outcome for one question id.

    ``choice`` -> ``label``; ``noul`` -> ``side`` (yes/no);
    ``score`` -> closed band ``[index_min, index_max]``.
    """

    kind: str
    label: str | None = None
    side: str | None = None
    index_min: float | None = None
    index_max: float | None = None


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    state: dict[str, Any]
    questions: dict[str, dict[str, Any]]
    expectations: dict[str, Expectation] = field(default_factory=dict)
    informational: tuple[str, ...] = ()


TICKET_QUESTIONS: dict[str, dict[str, Any]] = {
    "department": {
        "type": "choice",
        "instructions": "应该由哪个团队处理这张工单？",
        "criteria": {
            "billing": "支付、退款、账单和发票问题",
            "technical": "产品故障、报错和接口集成问题",
        },
    },
    "escalate": {
        "type": "noul",
        "instructions": "是否需要立即通知值班人员？",
        "criteria": {"true": "核心服务已受影响，需要马上介入", "false": "可以按常规队列处理"},
    },
    "severity": {
        "type": "score",
        "instructions": "这个问题有多严重？",
        "criteria": [
            "轻微问题，不影响功能",
            "部分功能受影响，但存在替代方案",
            "核心功能不可用，没有替代方案",
            "造成严重业务或安全影响",
        ],
    },
}

# Same ticket taxonomy plus a third plausible team, so a typo/content ticket is
# not forced into billing or technical.
TICKET_QUESTIONS_3WAY: dict[str, dict[str, Any]] = {
    **TICKET_QUESTIONS,
    "department": {
        "type": "choice",
        "instructions": "应该由哪个团队处理这张工单？",
        "criteria": {
            **TICKET_QUESTIONS["department"]["criteria"],
            "content": "文档、帮助中心文案等内容问题",
        },
    },
}

SKIN_QUESTIONS: dict[str, dict[str, Any]] = {
    "sentiment": {
        "type": "choice",
        "instructions": "这条王者荣耀皮肤相关评论的整体情感倾向是什么？",
        "criteria": {
            "positive": "认可、喜爱或推荐该皮肤",
            "negative": "失望、抱怨或批评该皮肤",
            "mixed": "同时包含明确的正面与负面评价",
            "neutral": "陈述事实或提问，情感不明显",
        },
    },
    "mentions_purchase": {
        "type": "noul",
        "instructions": "评论中是否出现与该皮肤购买或付费相关的表述？",
        "criteria": {
            "true": "提到想买、已经入手、已购买、退款或不再购买等任意一种表述",
            "false": "全文没有涉及购买或付费",
        },
    },
    "future_paid_intent": {
        "type": "noul",
        "instructions": "评论作者对该皮肤是否持正面付费态度（愿意买或认为值得买）？",
        "criteria": {
            "true": "已购买且满意、表示想买、推荐他人购买",
            "false": "认为不值、后悔购买或表示不再购买",
        },
    },
    "intensity": {
        "type": "score",
        "instructions": "评论的情感强度有多高？",
        "criteria": [
            "几乎没有情感色彩",
            "轻度情感，语气平和",
            "中度情感，用词明显带倾向",
            "强烈情感，出现感叹、夸张或激烈措辞",
        ],
    },
}

# Comment-moderation batch: verbatim sample data and criteria from
# platform.qianwenai.com/docs/token-plan/best-practices/decision-model.md, whose
# published output table gives the documented labels used as ground truth here.
MODERATION_COMMENTS: tuple[tuple[int, str, str], ...] = (
    (1, "这篇文章把异步和并发的区别讲得很清楚，收藏了。", "pass"),
    (2, "加微信 teacher888 领取内部资料，免费带你做副业月入过万。", "spam"),
    (3, "请问示例里为什么用线程池而不是协程？", "pass"),
    (4, "前排出售各类平台账号，需要的私聊。", "spam"),
    (5, "写得太差了，看了半天没看懂。", "pass"),
    (6, "点击链接领取新款手机 t.cn/abc123。", "spam"),
    (7, "感谢分享，已按文中方法跑通。", "pass"),
    (8, "有人知道这个库的中文文档在哪吗？我博客里有整理，需要的自取 t.cn/abc。", "spam"),
    (9, "第二节的示例代码在我机器上报 ImportError。", "pass"),
    (10, "风口项目日入五百，稳赚不赔，带新人入场。", "spam"),
)

MODERATION_CRITERIA = {
    "pass": "正常讨论，包括批评、质疑和报错反馈",
    "spam": "广告导流、诈骗引流、批量营销话术、买卖账号或课程资源",
}


def moderation_scenario() -> Scenario:
    questions: dict[str, dict[str, Any]] = {}
    expectations: dict[str, Expectation] = {}
    for cid, text, label in MODERATION_COMMENTS:
        qid = f"c{cid}"
        questions[qid] = {
            "type": "choice",
            "instructions": f"`comments` 中 id 为 {cid} 的评论属于哪一类？",
            "criteria": dict(MODERATION_CRITERIA),
        }
        expectations[qid] = Expectation("choice", label=label)
    return Scenario(
        key="moderation_batch10",
        title="评论审核：单请求并行判定 10 条（官方文档样本，标签来自文档输出表）",
        state={"comments": [{"id": cid, "text": text} for cid, text, _ in MODERATION_COMMENTS]},
        questions=questions,
        expectations=expectations,
        informational=("c8",),  # doc reports confidence 0.69 -> 转人工; tracked, not asserted
    )


def scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(
            key="ticket_billing",
            title="工单分流：支付未到账（API 参考示例场景）",
            state={
                "ticket_id": "T-1001",
                "content": "订单支付后超过 24 小时仍未到账，用户无法继续使用核心服务，要求立即处理。",
            },
            questions=TICKET_QUESTIONS,
            expectations={
                "department": Expectation("choice", label="billing"),
                "escalate": Expectation("noul", side="yes"),
                "severity": Expectation("score", index_min=1.5, index_max=3.0),
            },
        ),
        Scenario(
            key="ticket_technical",
            title="工单分流：接口 500 / 连接池耗尽",
            state={
                "ticket_id": "T-1002",
                "content": "调用 /v1/orders 接口稳定返回 500，服务端日志显示数据库连接池耗尽；账单页面正常。",
            },
            questions=TICKET_QUESTIONS_3WAY,
            expectations={
                "department": Expectation("choice", label="technical"),
                "escalate": Expectation("noul", side="yes"),
                "severity": Expectation("score", index_min=1.5, index_max=3.0),
            },
        ),
        Scenario(
            key="ticket_low_priority",
            title="工单分流：文案错别字（低严重度对照）",
            state={
                "ticket_id": "T-1003",
                "content": "帮助中心有一处错别字，“立既生效”应为“立即生效”，不影响任何功能，方便时修一下即可。",
            },
            questions=TICKET_QUESTIONS_3WAY,
            expectations={
                "department": Expectation("choice", label="content"),
                "escalate": Expectation("noul", side="no"),
                "severity": Expectation("score", index_min=0.0, index_max=1.0),
            },
        ),
        Scenario(
            key="skin_negative",
            title="皮肤评论：负面（换色骗氪）",
            state={
                "source": "weibo",
                "comment": "这皮肤特效跟原皮有什么区别？168 块钱就换了个配色，纯纯割韭菜，再也不买了。",
            },
            questions=SKIN_QUESTIONS,
            expectations={
                "sentiment": Expectation("choice", label="negative"),
                "mentions_purchase": Expectation("noul", side="yes"),
                "future_paid_intent": Expectation("noul", side="no"),
                "intensity": Expectation("score", index_min=2.0, index_max=3.0),
            },
        ),
        Scenario(
            key="skin_positive",
            title="皮肤评论：正面（音效手感好评）",
            state={
                "source": "weibo",
                "comment": "新皮肤的音效太好听了！技能手感也很轻，已经入手了，强烈推荐给大家。",
            },
            questions=SKIN_QUESTIONS,
            expectations={
                "sentiment": Expectation("choice", label="positive"),
                "mentions_purchase": Expectation("noul", side="yes"),
                "future_paid_intent": Expectation("noul", side="yes"),
                "intensity": Expectation("score", index_min=2.0, index_max=3.0),
            },
        ),
        Scenario(
            key="skin_mixed",
            title="皮肤评论：褒贬并存（置信度观察，不做硬性断言）",
            state={
                "source": "weibo",
                "comment": "建模确实精致，回城动画也用心，但技能特效太暗了，打团根本看不清，有点失望。",
            },
            questions=SKIN_QUESTIONS,
            informational=("sentiment", "mentions_purchase", "future_paid_intent", "intensity"),
        ),
        Scenario(
            key="choice_other_fallback",
            title="choice 兜底：无匹配选项时应落到 other",
            state={
                "ticket_id": "T-1004",
                "content": "我的账号被判定违规封禁了 7 天，申诉入口在哪里？我没有使用任何外挂。",
            },
            questions={
                "business_line": {
                    "type": "choice",
                    "instructions": "这张工单属于哪个业务线？",
                    "criteria": {
                        "billing": "支付、退款、账单和发票问题",
                        "logistics": "配送、物流和收货地址问题",
                        "integration": "开放接口、SDK 和技术集成问题",
                        "other": "不属于以上任何一类的问题",
                    },
                }
            },
            expectations={"business_line": Expectation("choice", label="other")},
        ),
        Scenario(
            key="skin_wide_topic",
            title="6 选 1 宽标签分类：评论主题（回城动画 -> visual_effects）",
            state={
                "source": "weibo",
                "comment": "这个皮肤的回城动画做得太好看了，光看回城我就能看一整局，特效粒子也做得很细。",
            },
            questions={
                "topic": {
                    "type": "choice",
                    "instructions": "这条评论主要在讨论皮肤的哪个方面？",
                    "criteria": {
                        "visual_effects": "技能特效、回城动画、建模、外观等视觉表现",
                        "gameplay_feel": "手感、打击感、技能释放体验",
                        "audio": "音效、配音、背景音乐",
                        "price_value": "价格、性价比、是否值得购买",
                        "defect": "BUG、显示异常、闪退等技术问题",
                        "other": "与以上方面都无关的内容",
                    },
                }
            },
            expectations={"topic": Expectation("choice", label="visual_effects")},
        ),
        Scenario(
            key="moderation_c8_isolated",
            title="批量干扰对照：单独发送文档 #8 评论（文档标注 spam / confidence 0.69）",
            state={"comments": [{"id": 8, "text": MODERATION_COMMENTS[7][1]}]},
            questions={
                "c8": {
                    "type": "choice",
                    "instructions": "`comments` 中 id 为 8 的评论属于哪一类？",
                    "criteria": dict(MODERATION_CRITERIA),
                }
            },
            expectations={"c8": Expectation("choice", label="spam")},
            informational=("c8",),
        ),
        moderation_scenario(),
    )


# Documented limits that the probe checks against actual server behaviour.
CONTRACT_PROBES: tuple[dict[str, Any], ...] = (
    {
        "name": "score_single_level",
        "note": "docs say score levels must be 2-255; send only 1 level",
        "payload": {
            "model": MODEL,
            "state": {"content": "参数校验用例：score 只给 1 个等级。"},
            "questions": {
                "broken": {
                    "type": "score",
                    "instructions": "这个等级设置是否合法？",
                    "criteria": ["只有一个等级"],
                }
            },
        },
    },
    {
        "name": "unknown_question_type",
        "note": "docs enumerate choice/noul/score; send an undeclared type",
        "payload": {
            "model": MODEL,
            "state": {"content": "参数校验用例：未定义的问题类型。"},
            "questions": {"weird": {"type": "ranking", "instructions": "这个类型合法吗？"}},
        },
    },
)


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
def load_api_key(explicit: str | None) -> str:
    """Return the API key from --api-key, the environment, or the repo .env."""
    if explicit:
        return explicit.strip()
    key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if key:
        return key
    try:
        from dotenv import dotenv_values

        key = (dotenv_values(ROOT / ".env").get("DASHSCOPE_API_KEY") or "").strip()
    except Exception:  # pragma: no cover - dotenv is optional at runtime
        key = ""
    if not key:
        raise SystemExit("DASHSCOPE_API_KEY not found in env or .env; refusing to call the API.")
    return key


def resolve_base_url(api_key: str, explicit: str | None) -> tuple[str, str]:
    """Pick the documented gateway for this key family; return (url, reason)."""
    if explicit:
        return explicit.rstrip("/"), "explicit --base-url"
    if api_key.startswith("sk-sp-"):
        return BASE_URLS["sp"], "key prefix sk-sp- (Token Plan)"
    if api_key.startswith("sk-ws-"):
        return BASE_URLS["ws"], "key prefix sk-ws- (workspace)"
    return BASE_URLS["default"], "default trial gateway"


def key_fingerprint(api_key: str) -> str:
    """Non-reversible key description safe to print."""
    import hashlib

    return f"prefix={api_key[:6]!r} len={len(api_key)} sha256[:8]={hashlib.sha256(api_key.encode()).hexdigest()[:8]}"


def build_session(proxy: str | None, no_proxy: bool) -> requests.Session:
    session = requests.Session()
    session.trust_env = not no_proxy and proxy is None
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    return session


def post_systemone(
    session: requests.Session,
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[int, Any, float]:
    """POST one payload; return (status_code, parsed_body_or_text, wall_seconds)."""
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


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def _as_float(value: Any) -> float:
    return float(value)


def validate_answer(
    question_id: str,
    question: dict[str, Any],
    answer: Any,
    expectation: Expectation | None,
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Check schema invariants and expectations for one answer.

    Returns ``(errors, observations, summary)``.
    """
    errors: list[str] = []
    observations: list[str] = []
    summary: dict[str, Any] = {"id": question_id, "type": question.get("type")}

    if not isinstance(answer, dict):
        return [f"{question_id}: answer is not an object ({type(answer).__name__})"], observations, summary

    qtype = question.get("type")
    if answer.get("type") != qtype:
        errors.append(f"{question_id}: answer.type={answer.get('type')!r} != question.type={qtype!r}")

    probs_raw = answer.get("probabilities")
    probs: dict[str, float] = {}
    if isinstance(probs_raw, dict):
        try:
            probs = {str(k): _as_float(v) for k, v in probs_raw.items()}
        except (TypeError, ValueError):
            errors.append(f"{question_id}: probabilities contain non-numeric values")
            probs = {}
        total = sum(probs.values())
        if probs:
            deviation = abs(total - 1.0)
            allowed = max(PROB_TOL, PROB_ROUND_TOL_PER_LEVEL * len(probs))
            if deviation > allowed:
                errors.append(
                    f"{question_id}: probabilities sum to {total:.6f}, expected 1.0 (allowed deviation {allowed:.4f})"
                )
            elif deviation > 1e-9:
                observations.append(
                    f"{question_id}: probabilities are quantised to 2 decimals and sum to {total:.4f} "
                    f"(deviation {deviation:.4f} within rounding tolerance {allowed:.4f})"
                )
        if any(v < 0.0 for v in probs.values()):
            errors.append(f"{question_id}: negative probability present")
        summary["probabilities"] = dict(sorted(probs.items(), key=lambda kv: kv[1], reverse=True))
    elif qtype in {"choice", "score"}:
        # docs: choice and score always return a distribution
        errors.append(f"{question_id}: {qtype} answer missing probabilities object")
    else:
        # docs example returns only {"type": "noul", "noul": 0.99} for noul
        observations.append(f"{question_id}: noul returned P(yes) without a probabilities object (matches docs)")

    if qtype == "choice":
        allowed = {str(k) for k in (question.get("criteria") or {})}
        picked = answer.get("choice")
        summary["prediction"] = picked
        if not isinstance(picked, str):
            errors.append(f"{question_id}: choice answer is not a string")
        elif allowed and picked not in allowed:
            errors.append(f"{question_id}: choice {picked!r} not among declared options")
        if probs:
            top = max(probs, key=lambda k: probs[k])
            if picked is not None and str(top) != str(picked):
                errors.append(f"{question_id}: choice {picked!r} != argmax(probabilities) {top!r}")
            ranked = sorted(probs.values(), reverse=True)
            margin = ranked[0] - ranked[1] if len(ranked) > 1 else ranked[0]
            summary["top_p"] = round(probs.get(str(picked), 0.0), 4)
            summary["margin"] = round(margin, 4)
            observations.append(f"{question_id}: top-1 {picked!r} p={probs.get(str(picked), 0.0):.4f} margin={margin:.4f}")
        if expectation is not None and expectation.label is not None:
            summary["expected"] = expectation.label
            summary["correct"] = picked == expectation.label
            if picked != expectation.label:
                errors.append(f"{question_id}: expected choice {expectation.label!r}, got {picked!r}")
    elif qtype == "noul":
        value = answer.get("noul")
        summary["prediction"] = value
        if not isinstance(value, (int, float)):
            errors.append(f"{question_id}: noul answer is not numeric")
        else:
            prob_yes = _as_float(value)
            if not 0.0 <= prob_yes <= 1.0:
                errors.append(f"{question_id}: noul {prob_yes} outside [0, 1]")
            side = "yes" if prob_yes >= 0.5 else "no"
            summary["side"] = side
            observations.append(f"{question_id}: P(yes)={prob_yes:.4f} -> {side}")
            if expectation is not None and expectation.side is not None:
                summary["expected"] = expectation.side
                summary["correct"] = side == expectation.side
                if side != expectation.side:
                    errors.append(
                        f"{question_id}: expected noul side {expectation.side!r}, got {side!r} (P={prob_yes:.4f})"
                    )
    elif qtype == "score":
        value = answer.get("score")
        summary["prediction"] = value
        criteria = question.get("criteria") or []
        legend = answer.get("legend")
        if isinstance(legend, dict) and criteria:
            expected_legend = {str(i): str(text) for i, text in enumerate(criteria)}
            if {str(k): str(v) for k, v in legend.items()} != expected_legend:
                errors.append(f"{question_id}: legend does not echo the requested score levels")
            summary["legend"] = legend
        if not isinstance(value, (int, float)):
            errors.append(f"{question_id}: score answer is not numeric")
        else:
            expected_value = _as_float(value)
            if criteria and not -1e-9 <= expected_value <= len(criteria) - 1 + 1e-9:
                errors.append(f"{question_id}: score {expected_value} outside [0, {len(criteria) - 1}]")
            if probs:
                weights = {int(k): v for k, v in probs.items() if str(k).lstrip("-").isdigit()}
                if weights:
                    mean = sum(idx * p for idx, p in weights.items()) / max(sum(weights.values()), 1e-12)
                    if abs(mean - expected_value) > SCORE_TOL:
                        errors.append(
                            f"{question_id}: score {expected_value:.4f} != probability-weighted mean {mean:.4f}"
                        )
                    summary["weighted_mean"] = round(mean, 4)
            observations.append(f"{question_id}: score={expected_value:.3f} over {len(criteria)} levels")
            if expectation is not None:
                lo = expectation.index_min if expectation.index_min is not None else float("-inf")
                hi = expectation.index_max if expectation.index_max is not None else float("inf")
                summary["expected"] = f"[{lo}, {hi}]"
                summary["correct"] = lo <= expected_value <= hi
                if not lo <= expected_value <= hi:
                    errors.append(f"{question_id}: score {expected_value:.3f} outside expected band [{lo}, {hi}]")
    else:
        errors.append(f"{question_id}: unsupported question type {qtype!r}")

    confidence = answer.get("confidence")
    if qtype in {"choice", "score"}:
        if not isinstance(confidence, (int, float)):
            errors.append(f"{question_id}: {qtype} answer missing numeric confidence")
        else:
            conf = _as_float(confidence)
            if not 0.0 <= conf <= 1.0:
                errors.append(f"{question_id}: confidence {conf} outside [0, 1]")
            summary["confidence"] = conf
            if qtype == "choice" and probs:
                # confidence is documented as "判定把握"; compare with top-1 mass
                top_p = max(probs.values())
                if abs(top_p - conf) > 1e-9:
                    observations.append(
                        f"{question_id}: confidence={conf:.4f} differs from top-1 p={top_p:.4f}"
                    )
    elif confidence is not None:
        observations.append(f"{question_id}: noul answer also carries confidence={confidence}")

    text_fields = [k for k in ("text", "output_text", "message", "content") if k in answer]
    if text_fields:
        observations.append(f"{question_id}: answer carries generated-text field(s) {text_fields}")

    return errors, observations, summary


def validate_response(
    payload: dict[str, Any],
    status: int,
    body: Any,
    elapsed: float,
    expectations: dict[str, Expectation],
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Validate one systemone response envelope plus every answer it carries."""
    errors: list[str] = []
    observations: list[str] = []
    info: dict[str, Any] = {"http_status": status, "wall_seconds": round(elapsed, 3), "answers": {}}

    if status != 200:
        errors.append(f"HTTP {status} (expected 200)")
        info["body"] = body
        return errors, observations, info
    if not isinstance(body, dict):
        errors.append("response body is not a JSON object")
        info["body"] = body
        return errors, observations, info

    if body.get("model") != MODEL:
        errors.append(f"model echoed as {body.get('model')!r}, expected {MODEL!r}")
    info["request_id"] = body.get("request_id")
    if not info["request_id"]:
        errors.append("response has no request_id")

    input_tokens = (body.get("usage") or {}).get("input_tokens")
    info["input_tokens"] = input_tokens
    if not isinstance(input_tokens, int) or input_tokens <= 0:
        errors.append(f"usage.input_tokens missing or non-positive: {input_tokens!r}")

    latency = body.get("latency_ms")
    info["latency_ms"] = latency
    if not isinstance(latency, (int, float)) or latency < 0:
        errors.append(f"latency_ms missing or negative: {latency!r}")
    elif latency / 1000.0 > elapsed + 1.0:
        errors.append(f"latency_ms {latency} exceeds measured wall time {elapsed * 1000:.1f} ms")

    answers = body.get("answers")
    questions = payload.get("questions", {})
    if not isinstance(answers, dict):
        errors.append("response has no answers object")
        return errors, observations, info
    if set(answers) != set(questions):
        errors.append(f"answer ids {sorted(answers)} != question ids {sorted(questions)}")

    for qid, question in questions.items():
        if qid not in answers:
            continue
        q_errors, q_obs, summary = validate_answer(qid, question, answers[qid], expectations.get(qid))
        errors.extend(q_errors)
        observations.extend(q_obs)
        info["answers"][qid] = summary

    return errors, observations, info


def check_stability(runs: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    """Compare repeated identical requests for answer-level determinism."""
    notes: list[str] = []
    if len(runs) < 2:
        return False, ["fewer than two successful calls to compare"]
    ok = True
    baseline = runs[0]
    for run in runs[1:]:
        base_answers = (baseline.get("response") or {}).get("answers") or {}
        other_answers = (run.get("response") or {}).get("answers") or {}
        if set(base_answers) != set(other_answers):
            ok = False
            notes.append("answer id set changed between calls")
            continue
        for qid, base in base_answers.items():
            other = other_answers.get(qid, {})
            for field_name in ("choice", "noul", "score", "confidence"):
                if field_name in base and base.get(field_name) != other.get(field_name):
                    ok = False
                    notes.append(f"{qid}.{field_name}: {base.get(field_name)!r} != {other.get(field_name)!r}")
            base_probs = base.get("probabilities") or {}
            other_probs = other.get("probabilities") or {}
            drift = max(
                (abs(_as_float(base_probs.get(k, 0)) - _as_float(other_probs.get(k, 0))) for k in base_probs),
                default=0.0,
            )
            if drift > 1e-9:
                notes.append(f"{qid}: max probability drift {drift:.6f}")
                if drift > 1e-6:
                    ok = False
    if not notes:
        notes.append("identical labels, scores, confidences and probability vectors across calls")
    return ok, notes


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def format_probabilities(summary: dict[str, Any], limit: int = 4) -> str:
    probs = summary.get("probabilities") or {}
    if not probs:
        return "n/a"
    return " ".join(f"{k}={v:.3f}" for k, v in list(probs.items())[:limit])


def render_scenario(scenario: Scenario, info: dict[str, Any], errors: list[str], observations: list[str]) -> str:
    lines = [f"[{scenario.key}] {scenario.title}"]
    lines.append(
        f"  request_id={info.get('request_id')} http={info.get('http_status')} "
        f"server_latency={info.get('latency_ms')}ms wall={info.get('wall_seconds')}s "
        f"input_tokens={info.get('input_tokens')} questions={len(info.get('answers', {}))}"
    )
    for qid, summary in info.get("answers", {}).items():
        qtype = summary.get("type")
        prediction = summary.get("prediction")
        if qtype == "noul":
            rendered = (
                f"{summary.get('side')} (P(yes)={prediction:.4f})"
                if isinstance(prediction, (int, float))
                else str(prediction)
            )
        elif qtype == "score":
            rendered = f"{prediction:.3f}" if isinstance(prediction, (int, float)) else str(prediction)
        else:
            rendered = str(prediction)
        tag = "info" if qid in scenario.informational else "chk"
        verdict = ""
        if "correct" in summary:
            verdict = " OK" if summary["correct"] else " MISMATCH"
        conf = summary.get("confidence")
        conf_txt = f" confidence={conf:.4f}" if isinstance(conf, (int, float)) else ""
        lines.append(f"  - {qid} [{qtype}/{tag}]{verdict} -> {rendered}{conf_txt}")
        if qtype != "noul" or summary.get("probabilities"):
            lines.append(f"      probs: {format_probabilities(summary)}")
    shown_obs = observations if len(observations) <= 12 else observations[:12] + [
        f"... {len(observations) - 12} more observations in --json transcript"
    ]
    for note in shown_obs:
        lines.append(f"    note: {note}")
    for err in errors:
        lines.append(f"    FAIL: {err}")
    lines.append(f"  result: {'PASS' if not errors else 'FAIL'}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #
def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.getenv("SYSTEMONE_BASE_URL"), help="Override gateway selection")
    parser.add_argument("--api-key", help="Override DASHSCOPE_API_KEY (avoid on shared shells)")
    parser.add_argument("--scenario", action="append", help="Run only these scenario keys (repeatable)")
    parser.add_argument("--repeats", type=int, default=1, help="Repeat each request to check answer stability")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--proxy", help="Force a proxy, e.g. socks5h://127.0.0.1:7890")
    parser.add_argument("--no-proxy", action="store_true", help="Ignore environment proxy settings")
    parser.add_argument("--skip-contract-probes", action="store_true", help="Skip documented-limit probes")
    parser.add_argument("--json", type=Path, help="Write raw requests/responses to this path")
    parser.add_argument("--list", action="store_true", help="List scenario keys and exit")
    args = parser.parse_args(argv)

    all_scenarios = scenarios()
    if args.list:
        for scenario in all_scenarios:
            print(f"{scenario.key:22s} {len(scenario.questions):2d} questions  {scenario.title}")
        return 0

    selected = all_scenarios
    if args.scenario:
        wanted = set(args.scenario)
        selected = tuple(s for s in all_scenarios if s.key in wanted)
        missing = wanted - {s.key for s in selected}
        if missing:
            raise SystemExit(f"unknown scenario key(s): {sorted(missing)}; use --list")
    if not selected:
        raise SystemExit("no scenarios selected")

    api_key = load_api_key(args.api_key)
    base_url, reason = resolve_base_url(api_key, args.base_url)
    url = base_url.rstrip("/") + "/v1/systemone"
    repeats = max(1, args.repeats)

    session = build_session(args.proxy, args.no_proxy)
    transcript: list[dict[str, Any]] = []
    hard_failures = 0
    checks = 0
    tokens = 0
    latencies: list[float] = []
    choice_rows: list[dict[str, Any]] = []
    discrepancies: list[str] = []

    print(f"endpoint:  {url}")
    print(f"model:     {MODEL}")
    print(f"selection: {reason}")
    print(f"credential: {key_fingerprint(api_key)} (value redacted)")
    print(f"scenarios: {len(selected)}  repeats: {repeats}  "
          f"started: {datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}\n")

    for scenario in selected:
        wire_payload = {"model": MODEL, "state": scenario.state, "questions": scenario.questions}
        runs: list[dict[str, Any]] = []

        for attempt in range(1, repeats + 1):
            try:
                status, body, elapsed = post_systemone(session, url, api_key, wire_payload, args.timeout)
            except requests.RequestException as exc:
                if attempt == 1 and args.proxy is None and not args.no_proxy:
                    print(f"[{scenario.key}] direct request failed ({exc}); retrying via {FALLBACK_PROXY}")
                    session = build_session(FALLBACK_PROXY, no_proxy=True)
                    try:
                        status, body, elapsed = post_systemone(session, url, api_key, wire_payload, args.timeout)
                    except requests.RequestException as retry_exc:
                        print(f"[{scenario.key}] FAIL: request error via proxy: {retry_exc}\n")
                        hard_failures += 1
                        checks += 1
                        continue
                else:
                    print(f"[{scenario.key}] FAIL: request error: {exc}\n")
                    hard_failures += 1
                    checks += 1
                    continue

            errors, observations, info = validate_response(
                wire_payload, status, body, elapsed, scenario.expectations
            )
            record = {
                "scenario": scenario.key,
                "attempt": attempt,
                "request": wire_payload,
                "http_status": status,
                "response": body,
                "validation": info,
                "errors": errors,
                "observations": observations,
            }
            runs.append(record)
            transcript.append(record)

            scored_errors = [e for e in errors]
            if scenario.informational:
                # expectation mismatches on informational ids are observations, not failures
                scored_errors = [
                    e for e in scored_errors
                    if not any(e.startswith(f"{qid}:") and "expected" in e for qid in scenario.informational)
                ]
            print(render_scenario(scenario, info, scored_errors, observations))
            if repeats > 1:
                print(f"  attempt: {attempt}/{repeats}")
            print()

            checks += 1
            hard_failures += len(scored_errors)
            if isinstance(info.get("input_tokens"), int):
                tokens += info["input_tokens"]
            if isinstance(info.get("latency_ms"), (int, float)):
                latencies.append(float(info["latency_ms"]))
            for qid, summary in info.get("answers", {}).items():
                if summary.get("type") == "choice" and "correct" in summary:
                    choice_rows.append(
                        {
                            "scenario": scenario.key,
                            "id": qid,
                            "expected": summary.get("expected"),
                            "predicted": summary.get("prediction"),
                            "correct": bool(summary.get("correct")),
                            "confidence": summary.get("confidence"),
                            "top_p": summary.get("top_p"),
                            "margin": summary.get("margin"),
                            "informational": qid in scenario.informational,
                        }
                    )

        if repeats > 1:
            stable, notes = check_stability(runs)
            checks += 1
            if not stable:
                hard_failures += 1
            print(f"[{scenario.key}] determinism over {len(runs)} identical calls: {'PASS' if stable else 'FAIL'}")
            for note in notes:
                print(f"    {note}")
            print()

    if not args.skip_contract_probes:
        for probe in CONTRACT_PROBES:
            checks += 1
            print(f"[probe:{probe['name']}] {probe['note']}")
            try:
                status, body, elapsed = post_systemone(session, url, api_key, probe["payload"], args.timeout)
            except requests.RequestException as exc:
                print(f"    request error: {exc}")
                discrepancies.append(f"{probe['name']}: transport error {exc}")
                hard_failures += 1
                transcript.append({"probe": probe["name"], "request": probe["payload"], "error": str(exc)})
                continue
            snippet = json.dumps(body, ensure_ascii=False)[:300] if isinstance(body, dict) else str(body)[:300]
            print(f"    HTTP {status} in {elapsed:.2f}s body: {snippet}")
            transcript.append(
                {"probe": probe["name"], "request": probe["payload"], "http_status": status, "response": body}
            )
            if 400 <= status < 500:
                print("    documented limit is enforced: PASS\n")
            elif status == 200:
                msg = f"{probe['name']}: server accepted input outside documented limits (HTTP 200)"
                discrepancies.append(msg)
                print(f"    DISCREPANCY: {msg}\n")
            else:
                hard_failures += 1
                print(f"    FAIL: unexpected HTTP {status}\n")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "endpoint": url,
                    "model": MODEL,
                    "gateway_selection": reason,
                    "key_fingerprint": key_fingerprint(api_key),
                    "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "records": transcript,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"transcript written to {args.json}")

    print("=" * 76)
    asserted = [r for r in choice_rows if not r["informational"]]
    correct = [r for r in asserted if r["correct"]]
    info_rows = [r for r in choice_rows if r["informational"]]
    if asserted:
        print(f"choice classification accuracy: {len(correct)}/{len(asserted)} asserted labels correct")
        wrong = [r for r in asserted if not r["correct"]]
        for r in wrong:
            print(f"    MISMATCH {r['scenario']}.{r['id']}: expected {r['expected']} got {r['predicted']} "
                  f"(confidence={r['confidence']})")
        confs = [r["confidence"] for r in asserted if isinstance(r["confidence"], (int, float))]
        if confs:
            print(f"    confidence: min={min(confs):.4f} median={sorted(confs)[len(confs) // 2]:.4f} max={max(confs):.4f}")
    for r in info_rows:
        print(f"informational {r['scenario']}.{r['id']}: predicted {r['predicted']} "
              f"(expected-doc {r['expected']}, confidence={r['confidence']}, margin={r['margin']})")
    mean_latency = sum(latencies) / len(latencies) if latencies else float("nan")
    print(f"requests: {checks}  hard failures: {hard_failures}  input_tokens: {tokens}  "
          f"mean_server_latency: {mean_latency:.1f}ms")
    if discrepancies:
        print(f"doc-vs-server discrepancies: {len(discrepancies)}")
        for item in discrepancies:
            print(f"    - {item}")
    print("OVERALL:", "PASS" if hard_failures == 0 else "FAIL")
    return 0 if hard_failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
