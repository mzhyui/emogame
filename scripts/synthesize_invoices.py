"""Synthesize daily per-skin invoice aggregates reconciled to real app revenue.

Examples:
    # Generate a 7-day dataset ending on the last available baseline day
    python scripts/synthesize_invoices.py --preset 7d

    # Explicit window, custom seed, print daily reconciliation report
    python scripts/synthesize_invoices.py --start 2026-01-01 --end 2026-06-30 \\
        --seed 42 --report

    # Inspect an existing batch or export analysis-ready aggregates
    python scripts/synthesize_invoices.py --list-batches
    python scripts/synthesize_invoices.py --summary synth-7d --limit 20
    python scripts/synthesize_invoices.py --summary synth-7d --export-csv outputs/invoice_summary.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.invoice_synthesizer import InvoiceSynthesizer, SyntheticInvoiceRepository  # noqa: E402
from data.skin_repository import DEFAULT_DB_PATH  # noqa: E402

PRESETS = {"7d": 7, "30d": 30, "90d": 90, "180d": 180}
DEFAULT_SEED = 20260807


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Synthesize invoice data for heroskin analysis simulation")
    parser.add_argument("--start", help="window start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="window end date (default: last baseline day)")
    parser.add_argument("--days", type=int, help="window length ending at --end (default end)")
    parser.add_argument("--preset", choices=sorted(PRESETS), help="preset window length")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (deterministic output)")
    parser.add_argument("--baseline-batch", help="revenue import batch to reconcile against")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--dry-run", action="store_true", help="generate and report without writing")
    parser.add_argument("--report", action="store_true", help="print daily reconciliation after generation")
    parser.add_argument("--json", action="store_true", help="print the generation report as JSON")
    parser.add_argument("--list-batches", action="store_true", help="list existing synthetic batches")
    parser.add_argument("--summary", metavar="SYNTH_BATCH", help="print per-skin summary for a batch")
    parser.add_argument("--limit", type=int, default=20, help="max rows for --summary/--list-batches")
    parser.add_argument("--export-csv", metavar="PATH", help="export the --summary aggregates to CSV")
    return parser.parse_args(argv)


def parse_date(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SystemExit(f"invalid {field}: {value!r} (expected YYYY-MM-DD)") from exc


def print_report(report: dict[str, Any]) -> None:
    print(f"batch            {report['synth_batch']}")
    print(f"baseline         {report['baseline_batch']} ({report['currency']})")
    print(f"window           {report['period_start']} .. {report['period_end']} ({report['day_count']} days)")
    print(f"rows             {report['row_count']} ({report['skin_count']} skins/day)")
    print(f"baseline revenue {report['total_baseline_revenue']:,.2f}")
    print(f"synth revenue    {report['total_synthesized_revenue']:,.2f}")
    print(f"max daily dev    {report['max_daily_deviation']:.4%}")
    print(f"mean daily dev   {report['mean_daily_deviation']:.4%}")


def print_reconciliation(repo: SyntheticInvoiceRepository, synth_batch: str) -> None:
    rows = repo.reconciliation(synth_batch)
    if not rows:
        print("no reconciliation rows found")
        return
    print(f"\n{'date':<12}{'baseline':>14}{'synthesized':>14}{'deviation':>11}")
    max_dev = 0.0
    for row in rows:
        baseline = float(row["baseline_revenue"] or 0.0)
        synth = float(row["synthesized_revenue"] or 0.0)
        deviation = abs(synth - baseline) / max(baseline, 1.0)
        max_dev = max(max_dev, deviation)
        print(f"{row['invoice_date']:<12}{baseline:>14,.2f}{synth:>14,.2f}{deviation:>10.4%}")
    print(f"max deviation: {max_dev:.4%}")


def print_summary(repo: SyntheticInvoiceRepository, synth_batch: str, *, limit: int, export_csv: str | None) -> int:
    if repo.get_batch(synth_batch) is None:
        print(f"unknown batch: {synth_batch}")
        return 1
    summary = repo.skin_summary(synth_batch, limit=limit)
    print(f"\n{'source_key':<12}{'skin':<22}{'hero':<14}{'units':>10}{'revenue':>16}{'asp':>10}{'days':>6}")
    for row in summary:
        print(
            f"{row['source_key']:<12}{(row['skin_name'] or '')[:20]:<22}{(row['hero_name'] or '')[:12]:<14}"
            f"{int(row['total_units']):>10,}{float(row['total_revenue']):>16,.2f}"
            f"{float(row['avg_unit_price']):>10.2f}{int(row['active_days']):>6}"
        )
    if export_csv:
        full = repo.skin_summary(synth_batch)
        path = Path(export_csv)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(full[0].keys()) if full else [])
            writer.writeheader()
            writer.writerows(full)
        print(f"\nexported {len(full)} skin aggregates -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = SyntheticInvoiceRepository(args.db)

    if args.list_batches:
        for batch in repo.list_batches():
            print(
                f"{batch['synth_batch']}  {batch['period_start']}..{batch['period_end']}"
                f"  rows={batch['row_count']}  seed={batch['seed']}  max_dev={batch['max_daily_deviation']:.4%}"
            )
        return 0

    if args.summary:
        return print_summary(repo, args.summary, limit=args.limit, export_csv=args.export_csv)

    days = PRESETS[args.preset] if args.preset else args.days
    start = parse_date(args.start, "--start")
    end = parse_date(args.end, "--end")
    if start is None and end is None and days is None:
        print("nothing to do: pass --preset / --days / --start / --end, or --summary / --list-batches")
        return 2

    synthesizer = InvoiceSynthesizer(args.db)
    report = synthesizer.generate(
        start=start,
        end=end,
        days=days,
        seed=args.seed,
        baseline_batch=args.baseline_batch,
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
        if args.dry_run:
            print("(dry run: nothing written)")
    if args.report and not args.dry_run:
        print_reconciliation(repo, report["synth_batch"])
    if args.export_csv and not args.dry_run:
        print_summary(repo, report["synth_batch"], limit=args.limit, export_csv=args.export_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
