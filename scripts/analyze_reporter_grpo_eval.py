#!/usr/bin/env python3
"""Analyze paired reporter outputs with hero-clustered bootstrap intervals.

The analysis is row-weighted and keeps each SFT/GRPO output pair together.
Bootstrap replicates resample heroes with replacement, preserving all rows for
each sampled hero.  This accounts for dependence among skins of the same hero.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import fmean


BASE_METRICS = (
    "section",
    "heading_progress",
    "grounding",
    "evidence_caveat",
    "numeric",
    "business_anchor",
    "task_total",
    "verifier_normalized",
    "response_tokens",
    "cap_hit",
    "nonempty_response",
)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object records in {path}")
            rows.append(value)
    if not rows:
        raise ValueError(f"no records found in {path}")
    return rows


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot take a quantile of an empty sequence")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _derived(row: dict, metric: str, max_new_tokens: int) -> float | None:
    if metric == "cap_hit":
        value = row.get("response_tokens")
        return None if value is None else float(value >= max_new_tokens)
    if metric == "nonempty_response":
        response = row.get("response")
        return None if response is None else float(bool(str(response).strip()))
    value = row.get(metric)
    return None if value is None else float(value)


def _load_pairs(eval_dir: Path) -> tuple[list[dict], list[dict]]:
    sft = _jsonl(eval_dir / "sft.jsonl")
    grpo = _jsonl(eval_dir / "grpo.jsonl")
    if len(sft) != len(grpo):
        raise ValueError(f"row counts differ: sft={len(sft)}, grpo={len(grpo)}")
    for position, (left, right) in enumerate(zip(sft, grpo)):
        identity = ("index", "skin_id", "hero_name", "skin_name")
        differences = [key for key in identity if left.get(key) != right.get(key)]
        if differences:
            raise ValueError(f"pair {position} differs on {differences}")
        if left.get("error") is not None or right.get("error") is not None:
            raise ValueError(f"pair {position} contains an evaluator error")
        if not left.get("hero_name"):
            raise ValueError(f"pair {position} has no hero_name for clustering")
    return sft, grpo


def analyze(
    eval_dir: Path,
    *,
    max_new_tokens: int,
    bootstrap_samples: int,
    seed: int,
) -> dict:
    sft, grpo = _load_pairs(eval_dir)
    clusters: dict[str, list[int]] = {}
    for position, row in enumerate(sft):
        clusters.setdefault(str(row["hero_name"]), []).append(position)

    means: dict[str, dict[str, float | None]] = {"sft": {}, "grpo": {}}
    deltas: dict[str, float | None] = {}
    cluster_stats: dict[str, dict[str, tuple[float, int]]] = {}
    eligible_pairs: dict[str, int] = {}
    outcomes: dict[str, dict[str, int]] = {}

    for metric in BASE_METRICS:
        sft_values: list[float] = []
        grpo_values: list[float] = []
        pair_deltas: list[float] = []
        per_cluster: dict[str, tuple[float, int]] = {}
        wins = ties = losses = 0
        for hero, indices in clusters.items():
            cluster_sum = 0.0
            cluster_count = 0
            for index in indices:
                left = _derived(sft[index], metric, max_new_tokens)
                right = _derived(grpo[index], metric, max_new_tokens)
                if left is None or right is None:
                    continue
                sft_values.append(left)
                grpo_values.append(right)
                difference = right - left
                pair_deltas.append(difference)
                if difference > 0:
                    wins += 1
                elif difference < 0:
                    losses += 1
                else:
                    ties += 1
                cluster_sum += difference
                cluster_count += 1
            per_cluster[hero] = (cluster_sum, cluster_count)
        means["sft"][metric] = fmean(sft_values) if sft_values else None
        means["grpo"][metric] = fmean(grpo_values) if grpo_values else None
        deltas[metric] = fmean(pair_deltas) if pair_deltas else None
        eligible_pairs[metric] = len(pair_deltas)
        outcomes[metric] = {"candidate_wins": wins, "ties": ties, "candidate_losses": losses}
        cluster_stats[metric] = per_cluster

    rng = random.Random(seed)
    heroes = sorted(clusters)
    bootstrap_values: dict[str, list[float]] = {metric: [] for metric in BASE_METRICS}
    for _ in range(bootstrap_samples):
        sampled = [rng.choice(heroes) for _ in heroes]
        for metric in BASE_METRICS:
            total = 0.0
            count = 0
            for hero in sampled:
                cluster_sum, cluster_count = cluster_stats[metric][hero]
                total += cluster_sum
                count += cluster_count
            if count:
                bootstrap_values[metric].append(total / count)

    intervals = {
        metric: {
            "low": _quantile(values, 0.025),
            "high": _quantile(values, 0.975),
        }
        for metric, values in bootstrap_values.items()
        if values
    }
    cluster_sizes = [len(indices) for indices in clusters.values()]
    return {
        "scope": "in_sample_diagnostic_only",
        "baseline": "sft",
        "candidate": "grpo",
        "delta_direction": "candidate_minus_baseline",
        "pairs": len(sft),
        "pairing": {
            "aligned": True,
            "identity_fields": ["index", "skin_id", "hero_name", "skin_name"],
            "evaluator_errors": 0,
        },
        "clusters": {
            "field": "hero_name",
            "count": len(clusters),
            "minimum_rows": min(cluster_sizes),
            "maximum_rows": max(cluster_sizes),
        },
        "means": means,
        "mean_candidate_minus_baseline": deltas,
        "eligible_pairs": eligible_pairs,
        "paired_outcomes": outcomes,
        "bootstrap": {
            "method": "paired percentile bootstrap resampling hero clusters with replacement",
            "estimand": "row-weighted paired mean",
            "samples": bootstrap_samples,
            "seed": seed,
            "confidence_level": 0.95,
            "candidate_minus_baseline_ci": intervals,
        },
        "max_new_tokens": max_new_tokens,
        "interpretation_limits": [
            "All evaluated rows were used during GRPO training; this is not held-out generalization.",
            "Verifier metrics are mechanical format and grounding checks, not semantic quality judgments.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("eval_dir", type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=None)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    max_new_tokens = args.max_new_tokens
    summary_path = args.eval_dir / "summary.json"
    if max_new_tokens is None and summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        max_new_tokens = int(summary["max_new_tokens"])
    if max_new_tokens is None:
        parser.error("--max-new-tokens is required when summary.json is unavailable")
    if args.bootstrap_samples <= 0:
        parser.error("--bootstrap-samples must be positive")

    result = analyze(
        args.eval_dir,
        max_new_tokens=max_new_tokens,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    output = args.output or args.eval_dir / "paired_analysis.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
