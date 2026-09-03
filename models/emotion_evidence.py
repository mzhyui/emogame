"""Provenance-aware contracts for community emotion evidence.

The production dashboard must not infer validation from a handful of numeric
columns.  This module defines the evidence profile that accompanies an emotion
score and the deterministic aggregation used to construct it.
"""

from __future__ import annotations

import hashlib
import html
import math
import random
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Iterable, Mapping


PROTOCOL_VERSION = "emotion-evidence-v1"
ANNOTATION_SCHEMA_VERSION = 1
OBSERVATION_START = "2024-09-02"
OBSERVATION_END = "2026-09-01"
COHORT_SIZE = 100
REQUIRED_VALIDATED_SKINS = 80

SUBJECTIVE_ASPECTS: tuple[str, ...] = (
    "visual_appeal",
    "in_game_feel",
    "craftsmanship_quality",
    "collection_value",
    "value_for_money",
    "purchase_intent",
)

SUBJECTIVE_ASPECT_WEIGHTS: dict[str, float] = {
    "visual_appeal": 0.18,
    "in_game_feel": 0.18,
    "craftsmanship_quality": 0.18,
    "collection_value": 0.16,
    "value_for_money": 0.15,
    "purchase_intent": 0.10,
}

MIN_RELEVANT_COMMENTS = 20
MIN_UNIQUE_AUTHORS = 15
MIN_PARENT_DOCUMENTS = 2
MIN_AUTHORS_PER_ASPECT = 5
MAX_PARENT_SHARE = 0.60
BOOTSTRAP_SAMPLES = 2_000
BOOTSTRAP_SEED = 42

VALID_RELEVANCE = {"relevant", "irrelevant", "uncertain"}
FORBIDDEN_INPUT_CLASSES = {
    "cash",
    "official_prior",
    "premium_pilot_score",
    "revenue",
    "synthetic",
}

ASPECT_TO_SIGNAL_FIELD: dict[str, str] = {
    "visual_appeal": "visual_score",
    "in_game_feel": "feel_score",
    "craftsmanship_quality": "craftsmanship_score",
    "collection_value": "collection_score",
    "value_for_money": "value_score",
    "purchase_intent": "purchase_intent_score",
}


@dataclass(slots=True)
class EvidenceQualificationProfile:
    """Persisted evidence summary required for public validation."""

    run_id: str
    source_key: str
    protocol_version: str = PROTOCOL_VERSION
    published: bool = False
    calibration_passed: bool = False
    audit_passed: bool = False
    relevant_comment_count: int = 0
    unique_author_count: int = 0
    parent_document_count: int = 0
    platform_count: int = 0
    aspect_author_counts: dict[str, int] = field(default_factory=dict)
    feel_actual_use_author_count: int = 0
    aspect_scores: dict[str, int | None] = field(default_factory=dict)
    score: int | None = None
    score_ci_low: float | None = None
    score_ci_high: float | None = None
    evidence_cutoff: str = OBSERVATION_END
    protocol_hash: str = ""
    forbidden_inputs: list[str] = field(default_factory=list)
    validation_reasons: list[str] = field(default_factory=list)

    @property
    def qualified_aspect_count(self) -> int:
        return sum(
            self.aspect_author_counts.get(aspect, 0) >= MIN_AUTHORS_PER_ASPECT
            and self.aspect_scores.get(aspect) is not None
            for aspect in SUBJECTIVE_ASPECTS
        )

    @property
    def evidence_coverage(self) -> float:
        return self.qualified_aspect_count / len(SUBJECTIVE_ASPECTS)

    @property
    def is_validated(self) -> bool:
        return not qualification_reasons(self, require_published=True)

    @property
    def is_eligible_for_publication(self) -> bool:
        return not qualification_reasons(self, require_published=False)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["qualified_aspect_count"] = self.qualified_aspect_count
        payload["evidence_coverage"] = round(self.evidence_coverage, 4)
        payload["is_validated"] = self.is_validated
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvidenceQualificationProfile":
        return cls(
            run_id=str(value.get("run_id") or ""),
            source_key=str(value.get("source_key") or ""),
            protocol_version=str(value.get("protocol_version") or ""),
            published=bool(value.get("published")),
            calibration_passed=bool(value.get("calibration_passed")),
            audit_passed=bool(value.get("audit_passed")),
            relevant_comment_count=int(value.get("relevant_comment_count") or 0),
            unique_author_count=int(value.get("unique_author_count") or 0),
            parent_document_count=int(value.get("parent_document_count") or 0),
            platform_count=int(value.get("platform_count") or 0),
            aspect_author_counts={
                str(key): int(count)
                for key, count in dict(value.get("aspect_author_counts") or {}).items()
            },
            feel_actual_use_author_count=int(
                value.get("feel_actual_use_author_count") or 0
            ),
            aspect_scores={
                str(key): (None if score is None else int(round(float(score))))
                for key, score in dict(value.get("aspect_scores") or {}).items()
            },
            score=None if value.get("score") is None else int(round(float(value["score"]))),
            score_ci_low=(
                None if value.get("score_ci_low") is None else float(value["score_ci_low"])
            ),
            score_ci_high=(
                None if value.get("score_ci_high") is None else float(value["score_ci_high"])
            ),
            evidence_cutoff=str(value.get("evidence_cutoff") or OBSERVATION_END),
            protocol_hash=str(value.get("protocol_hash") or ""),
            forbidden_inputs=[str(item) for item in value.get("forbidden_inputs") or []],
            validation_reasons=[
                str(item) for item in value.get("validation_reasons") or []
            ],
        )


def qualification_reasons(
    profile: EvidenceQualificationProfile,
    *,
    require_published: bool = True,
) -> list[str]:
    """Return stable fail-closed reasons for an evidence profile."""
    reasons: list[str] = []
    if profile.protocol_version != PROTOCOL_VERSION:
        reasons.append("unsupported_protocol_version")
    if require_published and not profile.published:
        reasons.append("evidence_run_not_published")
    if not profile.calibration_passed:
        reasons.append("calibration_not_passed")
    if not profile.audit_passed:
        reasons.append("production_audit_not_passed")
    if profile.relevant_comment_count < MIN_RELEVANT_COMMENTS:
        reasons.append("insufficient_relevant_comments")
    if profile.unique_author_count < MIN_UNIQUE_AUTHORS:
        reasons.append("insufficient_unique_authors")
    if profile.parent_document_count < MIN_PARENT_DOCUMENTS:
        reasons.append("insufficient_parent_documents")
    for aspect in SUBJECTIVE_ASPECTS:
        if profile.aspect_author_counts.get(aspect, 0) < MIN_AUTHORS_PER_ASPECT:
            reasons.append(f"insufficient_aspect_authors:{aspect}")
        if profile.aspect_scores.get(aspect) is None:
            reasons.append(f"missing_aspect_score:{aspect}")
    if profile.feel_actual_use_author_count < MIN_AUTHORS_PER_ASPECT:
        reasons.append("insufficient_actual_use_feel_evidence")
    forbidden = sorted(set(profile.forbidden_inputs) & FORBIDDEN_INPUT_CLASSES)
    if forbidden:
        reasons.append("forbidden_input_lineage:" + ",".join(forbidden))
    if not profile.protocol_hash:
        reasons.append("missing_protocol_hash")
    if profile.score is None:
        reasons.append("missing_composite_score")
    elif weighted_subjective_score(profile.aspect_scores) != profile.score:
        reasons.append("score_reconstruction_mismatch")
    if profile.score_ci_low is None or profile.score_ci_high is None:
        reasons.append("missing_score_interval")
    elif (
        profile.score_ci_low > profile.score_ci_high
        or not 0 <= profile.score_ci_low <= 100
        or not 0 <= profile.score_ci_high <= 100
    ):
        reasons.append("invalid_score_interval")
    return reasons


def sanitize_public_text(value: Any) -> str:
    """Normalize public text and remove common account identifiers.

    Prices and scores remain usable, but public handles and long numeric account
    identifiers are not persisted in the evidence store.
    """
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"(?<!\w)@[\w\u3400-\u9fff.-]{1,64}", "[mention]", text)
    text = re.sub(r"(?<!\d)\d{7,}(?!\d)", "[numeric-id]", text)
    return re.sub(r"\s+", " ", text).strip()


def pseudonymous_author_hash(run_id: str, platform: str, author_value: Any) -> str:
    """Create a run-scoped pseudonym without retaining a public account ID."""
    key = hashlib.sha256(f"{PROTOCOL_VERSION}|{run_id}".encode("utf-8")).digest()
    payload = f"{platform}|{author_value}".encode("utf-8")
    return hashlib.blake2b(payload, key=key, digest_size=16).hexdigest()


def evidence_content_hash(
    source_key: str,
    platform: str,
    parent_external_id: str,
    author_hash: str,
    text: str,
) -> str:
    blob = "|".join(
        (source_key, platform, parent_external_id, author_hash, sanitize_public_text(text))
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def polarity_to_score(value: int | float) -> float:
    """Map the locked -2..2 polarity scale onto 0..100."""
    polarity = max(-2.0, min(2.0, float(value)))
    return (polarity + 2.0) * 25.0


def signal_values_from_profile(
    profile: EvidenceQualificationProfile,
) -> dict[str, float]:
    """Project only six qualified subjective scores into legacy signal names."""
    return {
        ASPECT_TO_SIGNAL_FIELD[aspect]: float(score)
        for aspect, score in profile.aspect_scores.items()
        if aspect in ASPECT_TO_SIGNAL_FIELD and score is not None
    }


def weighted_subjective_score(aspect_scores: Mapping[str, int | float | None]) -> int | None:
    """Compute the six-aspect score; missing aspects are never renormalized."""
    if any(aspect_scores.get(aspect) is None for aspect in SUBJECTIVE_ASPECTS):
        return None
    total_weight = sum(SUBJECTIVE_ASPECT_WEIGHTS.values())
    value = sum(
        float(aspect_scores[aspect]) * SUBJECTIVE_ASPECT_WEIGHTS[aspect]
        for aspect in SUBJECTIVE_ASPECTS
    ) / total_weight
    return min(max(round(value), 0), 100)


def _one_contribution_per_author(
    rows: Iterable[Mapping[str, Any]], aspect: str
) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for row in rows:
        polarities = dict(row.get("polarities") or {})
        if aspect not in polarities:
            continue
        try:
            polarity = int(polarities[aspect])
        except (TypeError, ValueError):
            continue
        if polarity < -2 or polarity > 2:
            continue
        author_hash = str(row.get("author_hash") or "")
        if not author_hash:
            continue
        candidate = {
            "author_hash": author_hash,
            "parent_external_id": str(row.get("parent_external_id") or ""),
            "platform": str(row.get("platform") or ""),
            "polarity": polarity,
            "actual_use": bool(row.get("actual_use")),
            "published_at": row.get("published_at"),
            "online_date": row.get("online_date"),
            "confidence": float(row.get("confidence") or 0.0),
            "evidence_id": int(row.get("evidence_id") or 0),
        }
        previous = candidates.get(author_hash)
        candidate_rank = (
            bool(candidate["actual_use"]) if aspect == "in_game_feel" else False,
            candidate["confidence"],
            -candidate["evidence_id"],
        )
        previous_rank = (
            bool(previous["actual_use"])
            if previous is not None and aspect == "in_game_feel"
            else False,
            previous["confidence"] if previous is not None else -1.0,
            -previous["evidence_id"] if previous is not None else 0,
        )
        if previous is None or candidate_rank > previous_rank:
            candidates[author_hash] = candidate
    return sorted(candidates.values(), key=lambda item: item["author_hash"])


def _cap_parent_share(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop deterministic tail rows until no parent exceeds 60 percent."""
    retained = list(rows)
    parents = {row["parent_external_id"] for row in retained if row["parent_external_id"]}
    if len(parents) < 2:
        return retained
    while retained:
        counts: dict[str, int] = {}
        for row in retained:
            parent = row["parent_external_id"]
            counts[parent] = counts.get(parent, 0) + 1
        largest_parent, largest_count = max(counts.items(), key=lambda item: (item[1], item[0]))
        if largest_count / len(retained) <= MAX_PARENT_SHARE:
            break
        removal_index = max(
            index
            for index, row in enumerate(retained)
            if row["parent_external_id"] == largest_parent
        )
        retained.pop(removal_index)
    return retained


def _public_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2})", text)
    return match.group(1) if match else None


def _is_post_release_experience(row: Mapping[str, Any]) -> bool:
    if not bool(row.get("actual_use")):
        return False
    published_day = _public_day(row.get("published_at"))
    release_day = _public_day(row.get("online_date"))
    return bool(published_day and release_day and published_day >= release_day)


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bootstrap_score_interval(
    contributions: Mapping[str, list[dict[str, Any]]],
    *,
    samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    by_author: dict[str, dict[str, float]] = {}
    for aspect, rows in contributions.items():
        for row in rows:
            by_author.setdefault(row["author_hash"], {})[aspect] = polarity_to_score(
                row["polarity"]
            )
    authors = sorted(by_author)
    if not authors:
        return None, None
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        sampled = [rng.choice(authors) for _ in authors]
        scores: dict[str, float | None] = {}
        for aspect in SUBJECTIVE_ASPECTS:
            values = [
                by_author[author][aspect]
                for author in sampled
                if aspect in by_author[author]
            ]
            scores[aspect] = mean(values) if values else None
        estimate = weighted_subjective_score(scores)
        if estimate is not None:
            estimates.append(float(estimate))
    low = _percentile(estimates, 0.025)
    high = _percentile(estimates, 0.975)
    return (
        None if low is None else round(low, 2),
        None if high is None else round(high, 2),
    )


def aggregate_adjudicated_annotations(
    rows: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
    source_key: str,
    protocol_hash: str,
    calibration_passed: bool,
    audit_passed: bool,
    forbidden_inputs: Iterable[str] = (),
    published: bool = False,
) -> EvidenceQualificationProfile:
    """Aggregate adjudicated, exact-mapped evidence for one skin."""
    materialized = [dict(row) for row in rows]
    derived_forbidden = {
        str(row.get("input_class") or "")
        for row in materialized
        if str(row.get("input_class") or "public_comment") != "public_comment"
    }
    if any(bool(row.get("is_synthetic")) for row in materialized):
        derived_forbidden.add("synthetic")
    accepted = [
        dict(row)
        for row in materialized
        if str(row.get("relevance")) == "relevant"
        and str(row.get("mapping_scope")) == "exact_skin"
        and not bool(row.get("is_synthetic"))
        and not row.get("quarantine_reason")
        and str(row.get("input_class") or "public_comment") == "public_comment"
    ]
    authors = {str(row.get("author_hash") or "") for row in accepted}
    authors.discard("")
    parents = {
        f"{row.get('platform') or ''}:{row.get('parent_external_id') or ''}"
        for row in accepted
        if row.get("parent_external_id")
    }
    parents.discard("")
    platforms = {str(row.get("platform") or "") for row in accepted}
    platforms.discard("")

    contributions: dict[str, list[dict[str, Any]]] = {}
    aspect_scores: dict[str, int | None] = {}
    aspect_counts: dict[str, int] = {}
    for aspect in SUBJECTIVE_ASPECTS:
        aspect_rows = _cap_parent_share(_one_contribution_per_author(accepted, aspect))
        contributions[aspect] = aspect_rows
        aspect_counts[aspect] = len(aspect_rows)
        values = [polarity_to_score(row["polarity"]) for row in aspect_rows]
        aspect_scores[aspect] = round(mean(values)) if values else None

    feel_actual_use = sum(
        _is_post_release_experience(row)
        for row in contributions.get("in_game_feel", [])
    )
    score = weighted_subjective_score(aspect_scores)
    ci_low, ci_high = _bootstrap_score_interval(contributions)
    profile = EvidenceQualificationProfile(
        run_id=run_id,
        source_key=source_key,
        published=published,
        calibration_passed=calibration_passed,
        audit_passed=audit_passed,
        relevant_comment_count=len(accepted),
        unique_author_count=len(authors),
        parent_document_count=len(parents),
        platform_count=len(platforms),
        aspect_author_counts=aspect_counts,
        feel_actual_use_author_count=feel_actual_use,
        aspect_scores=aspect_scores,
        score=score,
        score_ci_low=ci_low,
        score_ci_high=ci_high,
        evidence_cutoff=OBSERVATION_END,
        protocol_hash=protocol_hash,
        forbidden_inputs=sorted(
            derived_forbidden | {str(item) for item in forbidden_inputs}
        ),
    )
    profile.validation_reasons = qualification_reasons(
        profile, require_published=published
    )
    return profile
