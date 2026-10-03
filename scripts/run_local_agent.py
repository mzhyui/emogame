#!/usr/bin/env python3
"""Run the local six-stage skin evaluation agent with Ollama."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.local_pipeline import LocalAgentPipeline, PipelineConfig, select_skin
from data.skin_repository import SkinRepository


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group()
    selector.add_argument("--source-key", help="Unique catalog key, e.g. 106-03")
    selector.add_argument("--search", help="Skin search; ambiguous matches require --source-key")
    parser.add_argument("--question", help="User request; prompted interactively when omitted")
    parser.add_argument("--db", type=Path, default=ROOT / "data/wzry_skins/skins.sqlite3")
    parser.add_argument("--asset-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="New run directory (must not already exist)")
    parser.add_argument("--ollama-host", default=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--text-model", default="qwen3.5:4b")
    parser.add_argument("--vision-model", default="qwen3.5:4b")
    parser.add_argument("--timeout", type=float, default=180, help="Seconds per Ollama request")
    parser.add_argument("--attempts", type=int, default=3, help="Total attempts per LLM call (1-3)")
    parser.add_argument("--num-ctx", type=int, default=16384)
    parser.add_argument("--report-tokens", type=int, default=2400)
    parser.add_argument("--reference-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--competitors", type=int, default=3)
    parser.add_argument("--skip-vlm", action="store_true", help="Report from local metadata/signals only")
    parser.add_argument("--strict-vlm", action="store_true", help="Stop when local image analysis fails")
    parser.add_argument("--dry-run", action="store_true", help="Run deterministic stages and save prompts without Ollama")
    args = parser.parse_args(argv)
    try:
        if not args.db.is_file():
            raise ValueError(f"database not found: {args.db}")
        if not args.source_key and not args.search and sys.stdin.isatty():
            args.search = input("皮肤名称 / 英雄关键词: ").strip()
        source_key = select_skin(SkinRepository(args.db), args.source_key, args.search)
        if args.question is None:
            if not sys.stdin.isatty():
                raise ValueError("provide --question when stdin is not interactive")
            args.question = input("你希望分析什么？ ").strip()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        args.output = args.output or ROOT / "outputs/local_agent" / timestamp
        values = vars(args).copy()
        values.pop("search")
        values["source_key"] = source_key
        config = PipelineConfig(**values)
        state = asyncio.run(LocalAgentPipeline(
            config, progress=lambda message: print(message, file=sys.stderr, flush=True),
        ).run())
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"status: {state['status']}\nartifacts: {config.output.resolve()}")
    if state["status"] == "failed":
        for error in state["errors"]:
            print(f"{error['stage']}: {error['message']}", file=sys.stderr)
        return 1
    print(state["report"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
