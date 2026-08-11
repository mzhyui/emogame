"""Generate synthetic Weibo comments using local Ollama.

Examples:
    # Generate 20 comments per labeled skin (default)
    python scripts/synthesize_weibo_comments.py --per-skin 20

    # Smoke test with small model
    python scripts/synthesize_weibo_comments.py --per-skin 3 --model qwen2.5vl:3b --seed 42

    # Dry run: see what would be generated without calling Ollama
    python scripts/synthesize_weibo_comments.py --dry-run --per-skin 5

    # Inspect batches
    python scripts/synthesize_weibo_comments.py --list-batches
    python scripts/synthesize_weibo_comments.py --summary synth-wc-20260808-42

    # Export to CSV/JSONL
    python scripts/synthesize_weibo_comments.py --summary synth-wc-20260808-42 \
        --export-csv outputs/synth_weibo.csv --export-jsonl outputs/synth_weibo.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.weibo_comment_synthesizer import (  # noqa: E402
    WeiboCommentSynthesizer,
    SyntheticWeiboRepository,
    DEFAULT_WEIBO_DB,
)

DEFAULT_SEED = 20260808


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic Weibo comments using local Ollama"
    )
    parser.add_argument(
        "--per-skin",
        type=int,
        default=20,
        help="Number of comments to generate per skin (default: 20)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="RNG seed for deterministic output (default: 20260808)",
    )
    parser.add_argument(
        "--model",
        help="Ollama model name (default: from config, qwen2.5vl:3b)",
    )
    parser.add_argument(
        "--skin-keys",
        nargs="+",
        help="Restrict to specific skin_keys (default: all labeled skins)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.8,
        help="Sampling temperature (default: 0.8)",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Quick test: 3 comments per skin with default model",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_WEIBO_DB),
        help="Weibo SQLite database path",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan generation without calling Ollama",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Print sample comments after generation",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print generation report as JSON",
    )
    parser.add_argument(
        "--list-batches",
        action="store_true",
        help="List existing synthetic batches",
    )
    parser.add_argument(
        "--summary",
        metavar="BATCH",
        help="Print comments/stats for a batch",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max rows to display in --summary (default: 20)",
    )
    parser.add_argument(
        "--export-csv",
        metavar="PATH",
        help="Export comments to CSV",
    )
    parser.add_argument(
        "--export-jsonl",
        metavar="PATH",
        help="Export comments to JSONL",
    )

    return parser.parse_args(argv)


def list_batches(repo: SyntheticWeiboRepository) -> None:
    """Print all synth batches."""
    batches = repo.list_batches()
    if not batches:
        print("No synthetic batches found.")
        return

    print(f"{'Batch ID':<30} {'Seed':<12} {'Model':<20} {'Skins':<8} {'Rows':<8} {'Date'}")
    print("-" * 100)
    for b in batches:
        from datetime import datetime

        created = datetime.fromtimestamp(b["created_at"]).strftime("%Y-%m-%d %H:%M")
        print(
            f"{b['synth_batch']:<30} {b['seed']:<12} {b['model']:<20} "
            f"{b['skin_count']:<8} {b['row_count']:<8} {created}"
        )


def show_summary(
    repo: SyntheticWeiboRepository,
    batch_id: str,
    limit: int,
    export_csv: str | None,
    export_jsonl: str | None,
) -> None:
    """Print summary of a batch and optionally export."""
    batch = repo.get_batch(batch_id)
    if not batch:
        print(f"Batch '{batch_id}' not found.")
        return

    print(f"Batch: {batch['synth_batch']}")
    print(f"Seed: {batch['seed']}")
    print(f"Model: {batch['model']}")
    print(f"Skins: {batch['skin_count']}")
    print(f"Per-skin: {batch['per_skin']}")
    print(f"Rows: {batch['row_count']}")
    print(f"Rejections: {batch['rejection_count']}")
    print(f"Elapsed: {batch['elapsed_seconds']}s")
    print()

    comments = repo.comments(batch_id)
    print(f"Sample comments (showing {min(limit, len(comments))} of {len(comments)}):")
    for c in comments[:limit]:
        print(f"  [{c['skin_key']}] {c['text']}")

    # Export
    if export_csv:
        with open(export_csv, "w", newline="", encoding="utf-8") as f:
            if comments:
                writer = csv.DictWriter(f, fieldnames=comments[0].keys())
                writer.writeheader()
                writer.writerows(comments)
        print(f"\n✅ Exported {len(comments)} rows to {export_csv}")

    if export_jsonl:
        with open(export_jsonl, "w", encoding="utf-8") as f:
            for c in comments:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"✅ Exported {len(comments)} rows to {export_jsonl}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    db_path = Path(args.db)
    repo = SyntheticWeiboRepository(db_path)

    # List batches
    if args.list_batches:
        list_batches(repo)
        return 0

    # Show summary
    if args.summary:
        show_summary(repo, args.summary, args.limit, args.export_csv, args.export_jsonl)
        return 0

    # Smoke test overrides
    if args.smoke_test:
        args.per_skin = 3
        if not args.model:
            args.model = "qwen2.5vl:3b"

    # Generate
    synth = WeiboCommentSynthesizer(db_path=db_path)

    print(f"🚀 Starting Weibo comment synthesis")
    print(f"   Per-skin: {args.per_skin}")
    print(f"   Seed: {args.seed}")
    print(f"   Model: {args.model or '(default)'}")
    print(f"   Skin keys: {args.skin_keys or '(all labeled)'}")
    print(f"   Temperature: {args.temperature}")
    print(f"   Dry run: {args.dry_run}")
    print()

    report = synth.generate(
        per_skin=args.per_skin,
        seed=args.seed,
        skin_keys=args.skin_keys,
        temperature=args.temperature,
        model=args.model,
        dry_run=args.dry_run,
    )

    # Print report
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"\n✅ Generation complete")
        print(f"   Batch ID: {report.get('synth_batch')}")
        print(f"   Status: {report.get('status')}")
        print(f"   Skins: {report.get('skin_count', 0)}")
        print(f"   Rows: {report.get('row_count', 0)}")
        if "rejection_count" in report:
            print(f"   Rejections: {report.get('rejection_count', 0)}")
        if "elapsed_seconds" in report:
            print(f"   Elapsed: {report.get('elapsed_seconds', 0)}s")

    # Show sample if requested
    if args.report and report.get("row_count", 0) > 0:
        print("\n📝 Sample comments:")
        comments = repo.comments(report["synth_batch"])
        for c in comments[:10]:
            print(f"  [{c['skin_key']}] {c['text']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
