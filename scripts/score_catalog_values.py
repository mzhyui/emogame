"""Generate full value-present scores for the local skin catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.emotion_evidence_repository import EmotionEvidenceRepository  # noqa: E402
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository  # noqa: E402
from models.value_present_scoring import (  # noqa: E402
    VALUE_PRESENT_SCORER_VERSION,
    score_value_present_skin,
)


DEFAULT_OUTPUT = Path("data/value_scores/value-present-scores.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_report(db_path: Path, *, minimum_full: int = 100) -> dict[str, Any]:
    if not db_path.is_file():
        raise ValueError(f"database not found: {db_path}")
    skins = SkinRepository(db_path).list_skins(limit=None)
    profiles = EmotionEvidenceRepository(db_path).list_active_run_profiles()
    rows: list[dict[str, Any]] = []
    for skin in skins:
        source_key = str(skin["source_key"])
        profile = profiles.get(source_key)
        observed = profile.aspect_scores if profile is not None else {}
        result = score_value_present_skin(
            skin,
            observed,
            observed_source="community_observation",
        )
        rows.append(
            {
                **result.to_dict(),
                "quality": str(skin.get("quality") or ""),
                "online_date": skin.get("online_date"),
                "evidence_run_id": profile.run_id if profile is not None else None,
            }
        )
    rows.sort(key=lambda row: row["source_key"])
    full_count = sum(
        row["valid"]
        and row["score"] is not None
        and len(row["aspect_scores"]) == 6
        and all(value is not None for value in row["aspect_scores"].values())
        for row in rows
    )
    if full_count < minimum_full:
        raise ValueError(
            f"full-score minimum not met: {full_count}/{minimum_full}"
        )
    statuses = Counter(row["score_status"] for row in rows)
    return {
        "schema_version": 1,
        "scorer_version": VALUE_PRESENT_SCORER_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database": str(db_path),
        "database_sha256": sha256_file(db_path),
        "catalog_skins": len(skins),
        "full_scores": full_count,
        "minimum_full_required": minimum_full,
        "minimum_full_satisfied": full_count >= minimum_full,
        "publication_required": False,
        "quality_gate_required": False,
        "human_audit_required": False,
        "status_counts": dict(sorted(statuses.items())),
        "score_semantics": (
            "Observed community aspects are retained; missing aspects are "
            "completed by deterministic catalog estimates. Every catalog "
            "result is operationally valid and contains six aspect scores."
        ),
        "rows": rows,
    }


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--minimum-full", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args.db, minimum_full=args.minimum_full)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
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
