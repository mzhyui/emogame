#!/usr/bin/env python3
"""Build and run the evidence-aware perceived-premium pilot.

The script deliberately keeps production dashboard state untouched.  It writes
only a manifest, reproducible pilot artifacts, and validation reports beneath
its output directory.  Run a three-item smoke test before the 50-skin batch:

    python scripts/run_premium_pilot.py --limit 3 --run-vlm --output-dir /tmp/premium-smoke

For the real pilot, first create and manually review a media map from the
tracked example in ``docs/``, then use ``--media-map`` and a completed blind
reviewer CSV.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from models.premium_pilot import (
    DEFAULT_PILOT_SIZE,
    DEFAULT_REVENUE_TARGET,
    DEFAULT_SEED,
    MediaEvidence,
    PilotCandidate,
    aggregate_media_labels,
    build_feature_trace,
    build_candidates,
    canonical_payload_sha256,
    load_frozen_manifest,
    load_media_mapping,
    load_reviewer_ratings,
    load_signed_uplifts,
    manifest_payload,
    resolve_cached_images,
    revenue_source_keys,
    revenue_validation,
    reviewer_validation,
    score_premium,
    select_pilot_manifest,
    sha256_file,
)

EXECUTION_MODES = ("l1_l2", "full")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the first-stage perceived/emotional premium pilot."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--image-dir", type=Path, default=Path("data/wzry_skins/images"),
        help="Legacy local skin-image cache; it is reconciled, never trusted by path.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/premium_pilot"))
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Reuse and verify a previously frozen manifest instead of selecting again.",
    )
    parser.add_argument("--media-map", type=Path, help="Verified real-post-to-skin JSON map.")
    parser.add_argument(
        "--weibo-db", type=Path, default=Path("data/weibo_comments/weibo.sqlite3"),
        help="Local public Weibo post/comment database.",
    )
    parser.add_argument("--reviewer-csv", type=Path, help="Completed blind reviewer ratings.")
    parser.add_argument("--limit", type=int, default=DEFAULT_PILOT_SIZE)
    parser.add_argument("--revenue-target", type=int, default=DEFAULT_REVENUE_TARGET)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--run-vlm", action="store_true",
        help="Run local L1/L2 and the selected L3 mode instead of loading saved VLM JSONL.",
    )
    parser.add_argument(
        "--vlm-results", type=Path,
        help="Prior VLM JSONL, one object per line with source_key and vlm fields.",
    )
    parser.add_argument(
        "--resume-vlm-results",
        action="store_true",
        help="With --run-vlm and --vlm-results, reuse complete signed rows and rerun only missing/incomplete rows.",
    )
    parser.add_argument(
        "--execution-mode", choices=EXECUTION_MODES,
        default="full",
        help="full uses remote semantic L3; l1_l2 is allowed only for local smoke checks.",
    )
    parser.add_argument(
        "--require-remote", action="store_true",
        help="Fail before the batch when full mode lacks an AutoDL token.",
    )
    parser.add_argument(
        "--media-use-llm", action="store_true",
        help="Use the remote API to summarize de-identified linked comments; otherwise use the rule baseline.",
    )
    parser.add_argument("--force-media", action="store_true", help="Ignore cached media labels.")
    parser.add_argument("--dry-run", action="store_true", help="Print the deterministic cohort without writes or model calls.")
    return parser.parse_args()


def build_manifest(args: argparse.Namespace) -> tuple[list[PilotCandidate], dict[str, Any]]:
    frozen_manifest = getattr(args, "manifest", None)
    if frozen_manifest is not None:
        return load_frozen_manifest(frozen_manifest)
    if not args.db.exists():
        raise ValueError(f"skin database not found: {args.db}")
    skins = SkinRepository(args.db).list_skins(limit=None)
    resolutions = resolve_cached_images(skins, args.image_dir)
    candidates, excluded = build_candidates(
        skins, resolutions, revenue_source_keys(args.db)
    )
    selected = select_pilot_manifest(
        candidates,
        limit=args.limit,
        revenue_target=args.revenue_target,
        seed=args.seed,
    )
    if len(selected) < args.limit:
        raise ValueError(
            f"only {len(selected)} unambiguous locally resolved images are available; "
            f"cannot build requested cohort of {args.limit}"
        )
    return selected, manifest_payload(
        selected,
        excluded,
        seed=args.seed,
        limit=args.limit,
        revenue_target=args.revenue_target,
    )


def load_vlm_results(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            source_key = str(row.get("source_key") or "").strip()
            vlm = row.get("vlm") if isinstance(row.get("vlm"), dict) else row
            if not source_key:
                raise ValueError(f"{path}:{line_number} has no source_key")
            if source_key in rows:
                raise ValueError(f"{path}:{line_number} duplicates {source_key}")
            rows[source_key] = dict(vlm)
    return rows


def reusable_vlm_result(row: Mapping[str, Any], execution_mode: str) -> bool:
    """Return whether a prior row has usable features and complete tier lineage."""
    if row.get("status") not in ("ok", "degraded"):
        return False
    lineage = row.get("tier_provenance", {})
    if not isinstance(lineage, Mapping):
        return False
    required = ["l1", "l2"]
    if execution_mode == "full":
        required.append("l3")
    return all(isinstance(lineage.get(tier), Mapping) for tier in required)


async def run_vlm(
    candidates: Iterable[PilotCandidate], mode: Any
) -> dict[str, dict[str, Any]]:
    """Run sequentially to avoid overcommitting the local Ollama GPU."""
    from vlm.pipeline import VlmPipeline

    pipeline = VlmPipeline()
    outputs: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        try:
            outputs[candidate.source_key] = await pipeline.analyze(
                candidate.image_path, mode=mode
            )
        except Exception as exc:  # preserve the other pilot items
            outputs[candidate.source_key] = {
                "status": "error",
                "error": str(exc),
                "execution_mode": mode.value,
            }
    return outputs


def _load_post(conn: sqlite3.Connection, external_id: str) -> tuple[str, list[dict[str, Any]]] | None:
    post = conn.execute(
        "SELECT title FROM weibo_posts WHERE mid = ?", (external_id,)
    ).fetchone()
    if post is None:
        return None
    comments = conn.execute(
        "SELECT text, like_count FROM weibo_comments WHERE mid = ?", (external_id,)
    ).fetchall()
    # Do not pass user IDs, user names, or raw crawler metadata to the remote API.
    sanitized = [
        {"text": str(row[0] or ""), "like_count": int(row[1] or 0)}
        for row in comments
    ]
    return str(post[0] or ""), sanitized


async def label_linked_media(
    mapping: dict[str, list[MediaEvidence]],
    weibo_db: Path,
    *,
    use_llm: bool,
    force: bool,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Score only manually linked real Weibo posts and preserve map provenance."""
    labels_by_skin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    provenance: list[dict[str, Any]] = []
    if not mapping:
        return labels_by_skin, provenance
    from models.community_labels import CommunitySignalLabeler

    if not weibo_db.exists():
        raise ValueError(f"Weibo database not found: {weibo_db}")
    labeler = CommunitySignalLabeler()
    with sqlite3.connect(weibo_db) as conn:
        for source_key, entries in mapping.items():
            for entry in entries:
                if entry.platform != "weibo":
                    raise ValueError(
                        f"unsupported media platform for {source_key}: {entry.platform}"
                    )
                post = _load_post(conn, entry.external_id)
                if post is None:
                    row = {
                        "source_key": source_key,
                        "mid": entry.external_id,
                        "error": "mapped_post_not_found",
                    }
                else:
                    title, comments = post
                    label = await labeler.label_post(
                        entry.external_id,
                        title,
                        comments,
                        skin_index=None,
                        force=force,
                        use_llm=use_llm,
                    )
                    row = label.to_record()
                    row["source_key"] = source_key
                    row["remote_enrichment_requested"] = use_llm
                row["platform"] = entry.platform
                row["url"] = entry.url
                row["published_at"] = entry.published_at
                row["mapping_rationale"] = entry.mapping_rationale
                labels_by_skin[source_key].append(row)
                provenance.append(row)
    return labels_by_skin, provenance


def reviewer_cards(candidates: Iterable[PilotCandidate]) -> list[dict[str, str]]:
    """Create blind-review cards with no pilot score, media, or revenue fields."""
    return [
        {
            "source_key": candidate.source_key,
            "hero_name": candidate.hero_name,
            "skin_name": candidate.skin_name,
            "quality": candidate.quality,
            "acquire_method": candidate.acquire_method,
            "price_text": candidate.price_text,
            "online_date": candidate.online_date,
            "image_path": candidate.image_path,
            "perceived_premium": "",
            "rationale": "",
        }
        for candidate in candidates
    ]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@contextmanager
def output_run_lock(output_dir: Path):
    """Prevent concurrent scorers from overwriting the same run directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = output_dir / ".run.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"another premium pilot process owns output directory: {output_dir}"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                },
                sort_keys=True,
            )
        )
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _optional_file_sha256(path: Path | None) -> str | None:
    return sha256_file(path) if path is not None and path.exists() else None


def _git_metadata() -> dict[str, Any]:
    """Return advisory Git state without assuming a clean worktree."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        return {"head": head, "dirty": dirty, "role": "advisory"}
    except (OSError, subprocess.CalledProcessError):
        return {"head": None, "dirty": None, "role": "advisory_unavailable"}


def build_run_metadata(
    args: argparse.Namespace,
    *,
    manifest_sha256: str,
    settings_info: dict[str, str],
    started_at: str,
) -> dict[str, Any]:
    """Bind one run to its inputs, prompts, models, and executed source files."""
    from vlm.prompts import L1_PROMPT, L2_PROMPT, L3_SYSTEM_PROMPT, L3_USER_PROMPT_TEMPLATE
    from vlm.provenance import sha256_text

    source_paths = [
        Path("scripts/run_premium_pilot.py"),
        Path("models/premium_pilot.py"),
        Path("vlm/pipeline.py"),
        Path("vlm/prompts.py"),
        Path("vlm/provenance.py"),
    ]
    return {
        "run_metadata_version": 1,
        "run_id": args.output_dir.name,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": manifest_sha256,
        "selection_source": (
            str(getattr(args, "manifest", None))
            if getattr(args, "manifest", None) is not None
            else "deterministic_selection"
        ),
        "execution_mode": args.execution_mode if args.run_vlm else "precomputed_or_missing",
        "models": dict(settings_info),
        "prompt_sha256": {
            "l1": sha256_text(L1_PROMPT),
            "l2": sha256_text(L2_PROMPT),
            "l3_system": sha256_text(L3_SYSTEM_PROMPT),
            "l3_user_template": sha256_text(L3_USER_PROMPT_TEMPLATE),
        },
        "inputs": {
            "skin_db": {"path": str(args.db), "sha256": _optional_file_sha256(args.db)},
            "weibo_db": {
                "path": str(args.weibo_db),
                "sha256": _optional_file_sha256(args.weibo_db),
            },
            "input_manifest_sha256": _optional_file_sha256(
                getattr(args, "manifest", None)
            ),
            "vlm_results_sha256": _optional_file_sha256(args.vlm_results),
            "media_map_sha256": _optional_file_sha256(args.media_map),
            "reviewer_csv_sha256": _optional_file_sha256(args.reviewer_csv),
        },
        "source_sha256": {
            str(path): sha256_file(ROOT / path) for path in source_paths
        },
        "git": _git_metadata(),
        "evidence_policy": {
            "synthetic_media_allowed": False,
            "l3_score_role": "non_scoring_rationale",
            "validation_only_fields": ["reviewer_rating", "signed_revenue_uplift"],
        },
    }


def render_report(report: dict[str, Any]) -> str:
    selection = report["selection"]
    score_summary = report["score_summary"]
    return "\n".join(
        [
            "# Perceived-premium pilot report",
            "",
            "## Cohort",
            "",
            f"- Selected skins: {selection['selected']}",
            f"- Revenue-evaluable skins: {selection['revenue_selected']}",
            f"- Seed: {selection['seed']}",
            "",
            "## Score evidence",
            "",
            f"- Complete-evidence scores: {score_summary['complete']}",
            f"- Partial-evidence scores: {score_summary['partial']}",
            f"- Insufficient-evidence records: {score_summary['insufficient']}",
            "- Revenue and reviewer values were excluded from score construction.",
            "",
            "## VLM execution",
            "",
            f"- VLM execution state: `{report['vlm_execution']['status']}`",
            f"- Local-output rejections: {report['vlm_execution']['invalid_local_output_count']}",
            f"- Local rejection events: {report['vlm_execution']['local_output_rejection_count']}",
            f"- Recovered local fallbacks: {report['vlm_execution']['recovered_local_fallback_count']}",
            f"- Feature/provenance trace: `{report['trace_summary']['status']}`",
            f"- Incomplete provenance rows: {report['trace_summary']['incomplete_provenance_rows']}",
            "",
            "## Validation",
            "",
            f"- Reviewer audit: `{report['reviewer_validation']['status']}` "
            f"(n={report['reviewer_validation']['n']})",
            f"- Revenue check: `{report['revenue_validation']['status']}` "
            f"(n={report['revenue_validation']['n']})",
            "",
            "## Boundary",
            "",
            "This is a first-stage, evidence-weighted pilot. Partial and complete "
            "evidence groups must not be used as one unrestricted ranking, and the "
            "validation summaries are descriptive rather than causal or predictive claims.",
            "",
        ]
    )


def summarize_vlm_execution(
    candidates: Iterable[PilotCandidate], outputs: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Surface invalid local output as a review gate, never a normal success."""
    status_counts: Counter[str] = Counter()
    invalid_keys: list[str] = []
    rejection_keys: list[str] = []
    recovered_keys: list[str] = []
    missing_keys: list[str] = []
    for candidate in candidates:
        row = outputs.get(candidate.source_key)
        if row is None:
            missing_keys.append(candidate.source_key)
            continue
        status_counts[str(row.get("status", "missing_status"))] += 1
        diagnostics = row.get("tier_diagnostics", {})
        has_invalid_diagnostic = isinstance(diagnostics, dict) and any(
            isinstance(value, dict) and value.get("_error") == "invalid_local_output"
            for value in diagnostics.values()
        )
        lineage = row.get("tier_provenance", {})
        recovered = False
        has_rejected_attempt = False
        if isinstance(lineage, dict):
            for tier in ("l1", "l2"):
                provenance = lineage.get(tier, {})
                attempts = (
                    provenance.get("attempts", [])
                    if isinstance(provenance, dict)
                    else []
                )
                rejected = isinstance(attempts, list) and any(
                    isinstance(attempt, dict)
                    and attempt.get("status") in ("invalid", "error")
                    for attempt in attempts
                )
                accepted = isinstance(attempts, list) and any(
                    isinstance(attempt, dict) and attempt.get("status") == "accepted"
                    for attempt in attempts
                )
                has_rejected_attempt = has_rejected_attempt or rejected
                if rejected and accepted:
                    recovered = True
        if has_invalid_diagnostic or has_rejected_attempt:
            rejection_keys.append(candidate.source_key)
        if recovered:
            recovered_keys.append(candidate.source_key)
        elif has_invalid_diagnostic:
            invalid_keys.append(candidate.source_key)
    has_execution_issue = bool(invalid_keys or missing_keys or status_counts["error"] or status_counts["partial"])
    return {
        "status": "review_required" if has_execution_issue else "completed",
        "status_counts": dict(sorted(status_counts.items())),
        "invalid_local_output_count": len(invalid_keys),
        "invalid_local_output_source_keys": invalid_keys,
        "local_output_rejection_count": len(rejection_keys),
        "local_output_rejection_source_keys": rejection_keys,
        "recovered_local_fallback_count": len(recovered_keys),
        "recovered_local_fallback_source_keys": recovered_keys,
        "missing_output_source_keys": missing_keys,
    }


async def _async_main_unlocked(args: argparse.Namespace) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    candidates, manifest = build_manifest(args)
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    resume_vlm = bool(getattr(args, "resume_vlm_results", False))
    if args.run_vlm and args.vlm_results and not resume_vlm:
        raise ValueError(
            "use either --run-vlm or --vlm-results, unless --resume-vlm-results is set"
        )
    if resume_vlm and (not args.run_vlm or args.vlm_results is None):
        raise ValueError(
            "--resume-vlm-results requires both --run-vlm and --vlm-results"
        )
    settings_info = {
        "local_l1": "not_loaded",
        "local_l2": "not_loaded",
        "local_l1_fallback": "not_loaded",
        "local_l2_fallback": "not_loaded",
        "remote_l3": "not_loaded",
    }
    mode: Any = args.execution_mode
    if args.run_vlm or args.require_remote:
        from vlm.config import get_settings
        from vlm.pipeline import ExecutionMode

        mode = ExecutionMode(args.execution_mode)
        settings = get_settings()
        settings_info = {
            "local_l1": settings.l1_model,
            "local_l2": settings.l2_model,
            "local_l1_fallback": settings.l1_fallback_model,
            "local_l2_fallback": settings.l2_fallback_model,
            "remote_l3": settings.l3_model,
        }
    if args.require_remote and args.execution_mode == "full" and not settings.autodl_token:
        raise ValueError("--require-remote was set but AUTODL_TOKEN is not configured")
    if args.run_vlm:
        vlm_results = load_vlm_results(args.vlm_results) if resume_vlm else {}
        pending = [
            candidate
            for candidate in candidates
            if not reusable_vlm_result(
                vlm_results.get(candidate.source_key, {}), args.execution_mode
            )
        ]
        if pending:
            vlm_results.update(await run_vlm(pending, mode))
    else:
        vlm_results = load_vlm_results(args.vlm_results)
    candidate_keys = {candidate.source_key for candidate in candidates}
    unknown_vlm_keys = set(vlm_results) - candidate_keys
    if unknown_vlm_keys:
        raise ValueError(
            "VLM results contain source keys outside the selected cohort: "
            + ", ".join(sorted(unknown_vlm_keys))
        )

    mapping = load_media_mapping(args.media_map)
    unknown_media_keys = set(mapping) - {candidate.source_key for candidate in candidates}
    if unknown_media_keys:
        raise ValueError(
            "media map contains source keys outside the selected cohort: "
            + ", ".join(sorted(unknown_media_keys))
        )
    media_labels, media_provenance = await label_linked_media(
        mapping, args.weibo_db, use_llm=args.media_use_llm, force=args.force_media
    )

    scored_rows: list[dict[str, Any]] = []
    trace_rows: list[dict[str, Any]] = []
    score_objects = []
    manifest_hash = canonical_payload_sha256(manifest)
    for candidate in candidates:
        media_score, meaningful_count, media_ids = aggregate_media_labels(
            media_labels.get(candidate.source_key, [])
        )
        score = score_premium(
            candidate, vlm=vlm_results.get(candidate.source_key), media_score=media_score
        )
        score_objects.append(score)
        media_status = (
            "linked" if media_score is not None else
            "linked_insufficient" if candidate.source_key in mapping else "missing"
        )
        scored_rows.append(
            {
                **candidate.to_dict(),
                **score.to_dict(),
                "media_status": media_status,
                "meaningful_media_comments": meaningful_count,
                "linked_media_ids": media_ids,
                "vlm": vlm_results.get(candidate.source_key),
            }
        )
        trace_rows.append(
            build_feature_trace(
                candidate,
                vlm=vlm_results.get(candidate.source_key),
                media_score=media_score,
                media_status=media_status,
                meaningful_media_comments=meaningful_count,
                linked_media_ids=media_ids,
                score=score,
                manifest_sha256=manifest_hash,
            )
        )

    reviewer = load_reviewer_ratings(args.reviewer_csv, candidate_keys)
    signed_uplifts = load_signed_uplifts(args.db, candidate_keys)
    counts = Counter(score.evidence_status for score in score_objects)
    report = {
        "pilot_version": 1,
        "selection": {
            "selected": len(candidates),
            "revenue_selected": sum(c.has_revenue_evidence for c in candidates),
            "seed": int(manifest.get("selection", {}).get("seed", args.seed)),
        },
        "models": {
            **settings_info,
            "execution_mode": args.execution_mode if args.run_vlm else "precomputed_or_missing",
            "remote_media_enrichment_requested": args.media_use_llm,
        },
        "score_summary": {
            "complete": counts["complete"],
            "partial": sum(value for key, value in counts.items() if key.startswith("partial_")),
            "insufficient": counts["insufficient_evidence"],
        },
        "vlm_execution": summarize_vlm_execution(candidates, vlm_results),
        "trace_summary": {
            "status": (
                "complete"
                if all(row["provenance_status"] == "complete" for row in trace_rows)
                else "review_required"
            ),
            "rows": len(trace_rows),
            "incomplete_provenance_rows": sum(
                row["provenance_status"] != "complete" for row in trace_rows
            ),
            "manifest_sha256": manifest_hash,
        },
        "reviewer_validation": reviewer_validation(score_objects, reviewer),
        "revenue_validation": revenue_validation(score_objects, signed_uplifts),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "manifest.json", manifest)
    write_jsonl(args.output_dir / "scores.jsonl", scored_rows)
    write_jsonl(args.output_dir / "feature_trace.jsonl", trace_rows)
    write_jsonl(
        args.output_dir / "vlm_outputs.jsonl",
        [{"source_key": key, "vlm": value} for key, value in sorted(vlm_results.items())],
    )
    write_jsonl(args.output_dir / "media_labels.jsonl", media_provenance)
    write_csv(args.output_dir / "reviewer_cards.csv", reviewer_cards(candidates))
    write_json(args.output_dir / "report.json", report)
    write_json(
        args.output_dir / "run_metadata.json",
        build_run_metadata(
            args,
            manifest_sha256=manifest_hash,
            settings_info=settings_info,
            started_at=started_at,
        ),
    )
    (args.output_dir / "report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


async def async_main(args: argparse.Namespace) -> int:
    """Run a dry selection or one output-directory-locked pilot execution."""
    if args.dry_run:
        return await _async_main_unlocked(args)
    with output_run_lock(args.output_dir):
        return await _async_main_unlocked(args)


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(async_main(args))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
