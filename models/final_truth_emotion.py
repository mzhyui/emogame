"""Direct emotional scoring for a project-owner-declared truth artifact.

This scorer does not infer missing labels. It aggregates the human polarities
that are present in the declared artifact and reports coverage alongside every
number. A partial observed-aspect score is kept distinct from the locked
six-aspect composite used by the publication pipeline.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Iterable, Mapping

from models.emotion_evidence import (
    SUBJECTIVE_ASPECTS,
    SUBJECTIVE_ASPECT_WEIGHTS,
    polarity_to_score,
    weighted_subjective_score,
)


FINAL_TRUTH_SCORER_VERSION = "human-final-truth-scorer-v1"
FINAL_TRUTH_POLICY = "single_human_final_truth_v1"
FINAL_TRUTH_ANNOTATOR_KIND = "final_truth"


@dataclass(slots=True)
class FinalTruthSkinScore:
    """One skin's direct score and its observed-label coverage."""

    source_key: str
    score_status: str
    review_row_count: int
    relevant_row_count: int
    aspect_scores: dict[str, int | None]
    aspect_counts: dict[str, int]
    aspect_coverage: float
    observed_emotion_score: int | None
    complete_six_aspect_score: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def final_truth_annotation_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """Hash immutable label semantics independently of database metadata."""
    payload = [
        {
            "evidence_id": int(row["evidence_id"]),
            "source_key": str(row["source_key"]),
            "phase": str(row["phase"]),
            "relevance": str(row["relevance"]),
            "aspects": sorted(row.get("aspects") or []),
            "polarities": dict(sorted((row.get("polarities") or {}).items())),
            "actual_use": bool(row.get("actual_use")),
            "confidence": float(row.get("confidence") or 0.0),
            "notes": str(row.get("notes") or ""),
        }
        for row in rows
    ]
    payload.sort(key=lambda row: (row["phase"], row["evidence_id"]))
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observed_weighted_score(
    aspect_scores: Mapping[str, int | float | None],
) -> int | None:
    available = [
        aspect for aspect in SUBJECTIVE_ASPECTS if aspect_scores.get(aspect) is not None
    ]
    if not available:
        return None
    weight = sum(SUBJECTIVE_ASPECT_WEIGHTS[aspect] for aspect in available)
    value = sum(
        float(aspect_scores[aspect]) * SUBJECTIVE_ASPECT_WEIGHTS[aspect]
        for aspect in available
    ) / weight
    return min(max(round(value), 0), 100)


def score_observed_annotation_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_kind: str,
) -> dict[str, FinalTruthSkinScore]:
    """Aggregate supplied annotations over only their observed aspects."""
    if source_kind not in {"final_truth", "model_comments"}:
        raise ValueError(f"unsupported observed-score source: {source_kind}")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in rows:
        row = dict(raw)
        source_key = str(row.get("source_key") or "")
        if not source_key:
            raise ValueError("final-truth row is missing source_key")
        grouped[source_key].append(row)

    results: dict[str, FinalTruthSkinScore] = {}
    for source_key, skin_rows in grouped.items():
        relevant = [row for row in skin_rows if row.get("relevance") == "relevant"]
        values: dict[str, list[float]] = {aspect: [] for aspect in SUBJECTIVE_ASPECTS}
        for row in relevant:
            polarities = dict(row.get("polarities") or {})
            for aspect in SUBJECTIVE_ASPECTS:
                if aspect in polarities:
                    values[aspect].append(polarity_to_score(polarities[aspect]))
        aspect_scores = {
            aspect: (round(mean(scores)) if scores else None)
            for aspect, scores in values.items()
        }
        aspect_counts = {aspect: len(scores) for aspect, scores in values.items()}
        observed_aspects = sum(score is not None for score in aspect_scores.values())
        complete = weighted_subjective_score(aspect_scores)
        observed = _observed_weighted_score(aspect_scores)
        if complete is not None:
            status = f"complete_{source_kind}"
        elif observed is not None:
            status = f"partial_{source_kind}"
        else:
            status = f"no_relevant_{source_kind}"
        results[source_key] = FinalTruthSkinScore(
            source_key=source_key,
            score_status=status,
            review_row_count=len(skin_rows),
            relevant_row_count=len(relevant),
            aspect_scores=aspect_scores,
            aspect_counts=aspect_counts,
            aspect_coverage=round(observed_aspects / len(SUBJECTIVE_ASPECTS), 4),
            observed_emotion_score=observed,
            complete_six_aspect_score=complete,
        )
    return results


def score_final_truth_rows(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, FinalTruthSkinScore]:
    """Aggregate exactly the supplied final-truth rows by target skin."""
    return score_observed_annotation_rows(rows, source_kind="final_truth")
