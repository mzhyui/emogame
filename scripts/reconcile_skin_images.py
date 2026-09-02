"""Verify and optionally bind the existing legacy skin-image cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.image_reconciliation import (
    DEFAULT_EXPECTED_PRIMARY_URLS,
    DEFAULT_EXPECTED_SKINS,
    DEFAULT_EXPECTED_VERIFIED,
    ReconciliationError,
    SnapshotExpectation,
    reconcile_skin_images,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/wzry_skins/skins.sqlite3")
    parser.add_argument("--image-dir", default="data/wzry_skins/images")
    parser.add_argument(
        "--report-dir", default="data/wzry_skins/reconciliation_reports"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create a SQLite backup and atomically update pristine bindings.",
    )
    parser.add_argument("--expected-skins", type=int, default=DEFAULT_EXPECTED_SKINS)
    parser.add_argument(
        "--expected-primary-urls", type=int, default=DEFAULT_EXPECTED_PRIMARY_URLS
    )
    parser.add_argument(
        "--expected-verified", type=int, default=DEFAULT_EXPECTED_VERIFIED
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    expectation = SnapshotExpectation(
        skins=args.expected_skins,
        primary_urls=args.expected_primary_urls,
        verified_images=args.expected_verified,
    )
    try:
        report, report_path = reconcile_skin_images(
            Path(args.db),
            Path(args.image_dir),
            Path(args.report_dir),
            apply=args.apply,
            expectation=expectation,
        )
    except ReconciliationError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": report["status"],
                "preflight": report["preflight"],
                "summary": report["summary"],
                "updated": report["updated"],
                "backup_path": report["backup_path"],
                "report_path": str(report_path.resolve()),
                "mismatches": report["mismatches"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["status"] != "aborted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
