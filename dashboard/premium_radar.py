"""Validation and loading for the frozen perceived-premium radar bundle."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CANONICAL_RUN_ID = "20260831-seed42-social-v1"
CANONICAL_COUNTS = (50, 46, 4, 0)
REQUIRED_FILES = ("radar_plots.html", "report.json", "run_metadata.json")


class PremiumRadarBundleError(ValueError):
    """Raised when a premium radar bundle is missing or inconsistent."""


@dataclass(frozen=True)
class PremiumRadarBundle:
    run_id: str
    selected: int
    complete: int
    partial: int
    insufficient: int
    html: str


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PremiumRadarBundleError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise PremiumRadarBundleError(f"{path.name} must contain a JSON object")
    return value


def _integer(mapping: dict[str, Any], key: str, source: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PremiumRadarBundleError(f"{source}.{key} must be an integer")
    return value


def load_premium_radar_bundle(
    run_dir: str | Path,
    *,
    expected_run_id: str | None = CANONICAL_RUN_ID,
    expected_counts: tuple[int, int, int, int] | None = CANONICAL_COUNTS,
) -> PremiumRadarBundle:
    """Load a complete bundle or fail without falling back to another run."""
    root = Path(run_dir)
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise PremiumRadarBundleError(
            "missing premium radar bundle files: " + ", ".join(missing)
        )

    report = _load_object(root / "report.json")
    metadata = _load_object(root / "run_metadata.json")
    try:
        html = (root / "radar_plots.html").read_text(encoding="utf-8")
    except OSError as exc:
        raise PremiumRadarBundleError(f"cannot read radar_plots.html: {exc}") from exc

    run_id = str(metadata.get("run_id") or "")
    if not run_id:
        raise PremiumRadarBundleError("run_metadata.json has no run_id")
    if run_id != root.name:
        raise PremiumRadarBundleError(
            f"run_id {run_id!r} does not match bundle directory {root.name!r}"
        )
    if expected_run_id is not None and run_id != expected_run_id:
        raise PremiumRadarBundleError(
            f"expected run_id {expected_run_id!r}, found {run_id!r}"
        )

    selection = report.get("selection")
    summary = report.get("score_summary")
    trace = report.get("trace_summary")
    policy = metadata.get("evidence_policy")
    if not all(isinstance(item, dict) for item in (selection, summary, trace, policy)):
        raise PremiumRadarBundleError(
            "report selection/score_summary/trace_summary and evidence_policy are required"
        )
    assert isinstance(selection, dict)
    assert isinstance(summary, dict)
    assert isinstance(trace, dict)
    assert isinstance(policy, dict)

    selected = _integer(selection, "selected", "selection")
    complete = _integer(summary, "complete", "score_summary")
    partial = _integer(summary, "partial", "score_summary")
    insufficient = _integer(summary, "insufficient", "score_summary")
    counts = (selected, complete, partial, insufficient)
    if complete + partial + insufficient != selected:
        raise PremiumRadarBundleError(
            "score_summary counts do not sum to selection.selected"
        )
    if expected_counts is not None and counts != expected_counts:
        raise PremiumRadarBundleError(
            f"expected evidence counts {expected_counts}, found {counts}"
        )
    if trace.get("status") != "complete" or trace.get("rows") != selected:
        raise PremiumRadarBundleError("trace_summary is incomplete or row count differs")

    metadata_manifest = str(metadata.get("manifest_sha256") or "")
    trace_manifest = str(trace.get("manifest_sha256") or "")
    if not metadata_manifest or metadata_manifest != trace_manifest:
        raise PremiumRadarBundleError("manifest hashes differ across bundle metadata")
    if policy.get("synthetic_media_allowed") is not False:
        raise PremiumRadarBundleError("synthetic media must be disabled")
    if policy.get("media_input_mode") != "target_aware_comments":
        raise PremiumRadarBundleError("premium bundle is not target-aware social evidence")

    card_count = html.count('<section class="card"')
    if card_count != selected:
        raise PremiumRadarBundleError(
            f"radar HTML contains {card_count} cards for {selected} selected rows"
        )
    return PremiumRadarBundle(
        run_id=run_id,
        selected=selected,
        complete=complete,
        partial=partial,
        insufficient=insufficient,
        html=html,
    )
