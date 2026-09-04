"""Build a catalog-wide report from the declared human final-truth CSV."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.skin_repository import DEFAULT_DB_PATH, SkinRepository  # noqa: E402
from models.emotion_workflow import parse_review_pack, sha256_file  # noqa: E402
from models.final_truth_emotion import (  # noqa: E402
    FINAL_TRUTH_POLICY,
    FINAL_TRUTH_SCORER_VERSION,
    final_truth_annotation_digest,
    score_final_truth_rows,
)


DEFAULT_TRUTH = Path(
    "data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv"
)
DEFAULT_OUTPUT = Path(
    "data/emotion_evidence/runs/20260902-current100-v1/final-truth-scores.json"
)


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_report(truth_path: Path, db_path: Path) -> dict[str, Any]:
    truth_rows = parse_review_pack(truth_path)
    if not truth_rows:
        raise ValueError("final-truth artifact is empty")
    truth_scores = score_final_truth_rows(truth_rows)
    catalog = SkinRepository(db_path).list_skins(limit=None)
    catalog_keys = {str(row["source_key"]) for row in catalog}
    unknown = sorted(set(truth_scores) - catalog_keys)
    if unknown:
        raise ValueError("final-truth targets are outside the catalog: " + ",".join(unknown))

    rows: list[dict[str, Any]] = []
    for skin in catalog:
        source_key = str(skin["source_key"])
        truth_score = truth_scores.get(source_key)
        if truth_score is None:
            score_payload = {
                "source_key": source_key,
                "score_status": "no_final_truth_rows",
                "review_row_count": 0,
                "relevant_row_count": 0,
                "aspect_scores": {},
                "aspect_counts": {},
                "aspect_coverage": 0.0,
                "observed_emotion_score": None,
                "complete_six_aspect_score": None,
            }
        else:
            score_payload = truth_score.to_dict()
        rows.append(
            {
                **score_payload,
                "hero_name": str(skin.get("hero_name") or ""),
                "skin_name": str(skin.get("skin_name") or ""),
            }
        )

    status_counts = Counter(row["score_status"] for row in rows)
    return {
        "schema_version": 1,
        "scorer_version": FINAL_TRUTH_SCORER_VERSION,
        "truth_policy": FINAL_TRUTH_POLICY,
        "truth_artifact": str(truth_path),
        "truth_sha256": sha256_file(truth_path),
        "truth_annotations_sha256": final_truth_annotation_digest(truth_rows),
        "truth_rows": len(truth_rows),
        "truth_skins": len(truth_scores),
        "catalog_skins": len(rows),
        "numeric_observed_scores": sum(
            row["observed_emotion_score"] is not None for row in rows
        ),
        "complete_six_aspect_scores": sum(
            row["complete_six_aspect_score"] is not None for row in rows
        ),
        "status_counts": dict(sorted(status_counts.items())),
        "score_semantics": {
            "observed_emotion_score": (
                "Human polarity means combined over observed aspects only; "
                "available aspect weights are renormalized."
            ),
            "complete_six_aspect_score": (
                "Locked six-aspect composite; null when any aspect is missing."
            ),
            "missing_policy": "No label or no relevant label remains null, never neutral.",
            "validation_scope": "Declared final truth only; no out-of-sample claim.",
        },
        "rows": rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth", type=Path, default=DEFAULT_TRUTH)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report(args.truth, args.db)
    summary = {key: value for key, value in report.items() if key != "rows"}
    summary["output"] = str(args.output)
    summary["applied"] = bool(args.apply)
    if args.apply:
        _write_json_atomic(args.output, report)
        summary["output_sha256"] = sha256_file(args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
