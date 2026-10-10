#!/usr/bin/env python3
"""Does the decision model verify reports, or only classify short claims?

The first live JEV run (outputs/systemone/jev-demo/) showed the report-level
checks failing to separate a clean report from one with seeded violations; three
checks inverted. Both reports contain the same domain keywords, and the clean one
explicitly *denies* the claims, so the hypothesis is that a loaded question over a
long JSON state degenerates into keyword triggering without negation handling or
evidence grounding.

This probe compares two units of analysis on the same three defects:

* ``report`` - one loaded question over all six sections plus the evidence blob
* ``claim``  - one narrow question over a single sentence plus only the evidence
               field that adjudicates it

For each defect it prints P(yes) on the clean and the violating text and the
separation delta. A usable verifier needs the delta to be large and positive.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.decision_verifier import MODEL, http_transport, select_gateway  # noqa: E402
from scripts.verify_report_with_decision_model import build_state, load_api_key  # noqa: E402

EVIDENCE = {
    "catalog": {"price_text": "888点券", "quality": "史诗", "online_date": "2024-06-01"},
    "reference_date": "2026-10-04",
    "chart_data": {"five_dimension_scores": None},
    "vlm_features": {"status": "completed", "vlm_art_quality": "精致"},
}

DEFECTS = {
    "gameplay_overclaim": {
        "report_question": "报告是否仅凭壁纸或静态图片断言局内手感、动态特效、购买意愿或销量？",
        "claim_question": "这句话是否把局内手感说成已经实测确认的结论？",
        "clean_sentence": "图片模型判断不能替代局内手感观测，本报告不给出局内手感结论。",
        "defect_sentence": "局内手感实测顺滑，可直接作为卖点。",
        "claim_evidence": {"vlm_features": EVIDENCE["vlm_features"]},
    },
    "fabricated_radar_scores": {
        "report_question": "在 five_dimension_scores 为 null 时，报告是否给出了具体五维度分数？",
        "claim_question": "在 five_dimension_scores 为 null 的前提下，这句话是否给出了具体的五维度分数？",
        "clean_sentence": "five_dimension_scores 为 null，无法给出雷达分数，只能说明特征覆盖率。",
        "defect_sentence": "五维度得分为 belonging 8.8、showing_off 9.1、collection 8.4、surprise 7.9、aesthetic 9.3。",
        "claim_evidence": {"five_dimension_scores": None},
    },
    "currency_unit_error": {
        "report_question": "报告是否把点券/水晶价格换算成人民币，或改变了官方价格的单位？",
        "claim_question": "已知官方价格原文为 888点券，这句话是否把它换算成了人民币金额？",
        "clean_sentence": "官方价格为 888点券，保留原始单位，不换算为人民币。",
        "defect_sentence": "888点券约合人民币 176 元，建议溢价定价区间为 220-260 元人民币。",
        "claim_evidence": {"price_text": EVIDENCE["catalog"]["price_text"]},
    },
}

REPORTS = {
    "clean": Path("outputs/systemone/jev-demo/clean.json"),
    "defect": Path("outputs/systemone/jev-demo/defect.json"),
}


def noul(question: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": question,
            "criteria": {"true": "存在该问题", "false": "不存在该问题"}}


def ask(send, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, float]:
    status, body, elapsed = send({"model": MODEL, "state": state, "questions": questions})
    if status != 200 or not isinstance(body, dict):
        raise SystemExit(f"HTTP {status}: {str(body)[:300]}")
    out = {qid: float(a.get("noul")) for qid, a in (body.get("answers") or {}).items()}
    print(f"    request_id={body.get('request_id')} tokens={(body.get('usage') or {}).get('input_tokens')} "
          f"latency={body.get('latency_ms')}ms wall={elapsed:.3f}s")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api-key")
    parser.add_argument("--base-url")
    parser.add_argument("--json", type=Path, help="Write the raw comparison to this path")
    args = parser.parse_args(argv)

    api_key = load_api_key(args.api_key)
    base_url, reason = select_gateway(api_key, args.base_url)
    send = http_transport(base_url, api_key, timeout=60.0)
    print(f"endpoint: {base_url}/v1/systemone ({reason})\n")

    results: dict[str, Any] = {"report_level": {}, "claim_level": {}}

    print("[1/2] report-level: loaded questions over all six sections + evidence blob")
    for label, path in REPORTS.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = build_state(payload)
        questions = {name: noul(spec["report_question"]) for name, spec in DEFECTS.items()}
        results["report_level"][label] = ask(send, state, questions)

    print("[2/2] claim-level: one narrow question per sentence + the adjudicating field")
    for label, key in (("clean", "clean_sentence"), ("defect", "defect_sentence")):
        questions = {name: noul(spec["claim_question"]) for name, spec in DEFECTS.items()}
        states = {
            name: {"evidence": spec["claim_evidence"], "sentence": spec[key]} for name, spec in DEFECTS.items()
        }
        # one request per defect: the state differs per claim, questions cannot share a state
        answers: dict[str, float] = {}
        for name, state in states.items():
            answers.update(ask(send, state, {name: questions[name]}))
        results["claim_level"][label] = answers

    print("\n" + "=" * 78)
    print(f"{'check':26s} {'level':7s} {'clean':>7s} {'defect':>7s} {'delta':>8s}  separates")
    print("-" * 78)
    summary = []
    for level in ("report_level", "claim_level"):
        for name in DEFECTS:
            clean = results[level]["clean"][name]
            defect = results[level]["defect"][name]
            delta = defect - clean
            ok = delta >= 0.5
            summary.append({"level": level, "check": name, "clean": clean, "defect": defect,
                            "delta": round(delta, 4), "separates": ok})
            print(f"{name:26s} {level.replace('_level',''):7s} {clean:7.2f} {defect:7.2f} {delta:+8.2f}  {'YES' if ok else 'no'}")
    print("-" * 78)
    for level in ("report_level", "claim_level"):
        rows = [r for r in summary if r["level"] == level]
        inverted = [r["check"] for r in rows if r["delta"] < 0]
        print(f"{level:14s} separated {sum(1 for r in rows if r['separates'])}/{len(rows)}"
              f"   inverted: {inverted or 'none'}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({"raw": results, "summary": summary}, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"\nwritten to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
