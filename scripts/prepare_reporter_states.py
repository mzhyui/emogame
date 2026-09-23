#!/usr/bin/env python3
"""Export catalog-based reporter inputs; does not run VLMs or train a model."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from business.sales_advisor import SalesAdvisor
from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from feature_engineering.pipeline import FeatureBuilder
from models.rule_engine import RuleEngine


def export_states(db: Path, output: Path, reference_date: date,
                  limit: int | None = None) -> int:
    """Read existing catalog/signals and preserve evaluation provenance."""
    if not db.is_file():
        raise ValueError(f"database not found: {db}")
    if output.resolve() == db.resolve():
        raise ValueError("output must not replace the database")
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    repo = SkinRepository(db)
    signals = MarketSignalRepository(db)
    builder = FeatureBuilder(repo, reference_date=reference_date)
    engine, advisor = RuleEngine(), SalesAdvisor()
    records = []
    for skin in repo.list_skins(limit=limit):
        key = skin["source_key"]
        features = builder.build(key, signals.get_signals(key))
        evaluation = engine.evaluate(features)
        records.append({
            "skin_id": skin.get("skin_id") or key,
            "source_key": key,
            "hero_name": skin["hero_name"],
            "skin_name": skin["skin_name"],
            "feature_vector": features.to_dict(),
            "premium_result": evaluation.to_dict(),
            "business_analysis": advisor.advise(features, evaluation).to_dict(),
            "competitor_data": [],
            "provenance": {
                "source_db": str(db.resolve()),
                "reference_date": reference_date.isoformat(),
                "evaluation_method": "RuleEngine (not a learned premium predictor)",
                "business_method": "SalesAdvisor",
                "vlm_run": False,
                "competitor_lookup_run": False,
                "intended_use": "baseline_inputs_require_review_and_dataset_split",
            },
        })
    if not records:
        raise ValueError("catalog contains no readable skins")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records),
                      encoding="utf-8")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--output", type=Path, default=Path("data/reporter_states.jsonl"))
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    count = export_states(args.db, args.output, args.reference_date, args.limit)
    print(f"exported {count} reporter states to {args.output}")


if __name__ == "__main__":
    main()
