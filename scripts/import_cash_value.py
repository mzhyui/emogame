#!/usr/bin/env python3
"""Import estimated iPhone revenue and run release-window attribution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.cash_value import CashValueService
from data.skin_repository import DEFAULT_DB_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import WZRY iPhone revenue and attribute release-window uplift")
    parser.add_argument("csv", help="Revenue CSV path")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="Skin SQLite database")
    parser.add_argument("--currency", choices=["CNY", "USD"], default="CNY", help="Revenue currency (default: CNY)")
    parser.add_argument("--cny-per-usd", type=float, help="Required only when --currency USD")
    parser.add_argument("--market", default="CN", help="Revenue geography (default: CN)")
    parser.add_argument("--source-name", default="WZRY_IPHONE_revenue.csv")
    parser.add_argument("--import-only", action="store_true", help="Import facts without attribution")
    parser.add_argument("--verbose-records", action="store_true", help="Print every attributed record and daily chart")
    parser.add_argument("--allocation-weights-json", help="JSON object mapping same-day source_key to positive weight")
    parser.add_argument("--gacha-overrides-json", help="JSON object mapping source_key to manual CNY spend/pity")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = CashValueService(args.db)
    imported = service.import_revenue_csv(
        args.csv, currency=args.currency, cny_per_usd=args.cny_per_usd,
        market=args.market, source_name=args.source_name,
    )
    result: dict = {"import": imported}
    if not args.import_only:
        weights = _load_number_map(args.allocation_weights_json)
        gacha_overrides = _load_number_map(args.gacha_overrides_json)
        attribution = service.attribute_releases(
            import_batch=imported["import_batch"], cny_per_usd=args.cny_per_usd,
            allocation_weights=weights, gacha_overrides=gacha_overrides,
        )
        result["attribution"] = attribution if args.verbose_records else {
            "import_batch": attribution["import_batch"],
            "eligible_records": attribution["eligible_records"],
            "insufficient_coverage_count": len(attribution["insufficient_coverage"]),
            "eligible_source_keys": [record["source_key"] for record in attribution["records"]],
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _load_number_map(path: str | None) -> dict[str, float] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return {str(key): float(value) for key, value in payload.items()}


if __name__ == "__main__":
    raise SystemExit(main())
