"""Reproducible first-stage perceived-premium pilot utilities.

This module intentionally sits beside the production rule engine.  It builds a
small, versioned cohort and produces an evidence-aware *pilot* score from
visual, official-context, and real linked-media evidence.  Revenue and human
ratings are accepted only by the validation helpers, never by
:func:`score_premium`.

The pilot is designed for a reviewable first-stage study, not as a trained
revenue predictor or a replacement for the dashboard's evidence gate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import random
import re
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping


PILOT_VERSION = 1
DEFAULT_SEED = 42
DEFAULT_PILOT_SIZE = 50
DEFAULT_REVENUE_TARGET = 25
MIN_MEANINGFUL_MEDIA_COMMENTS = 5

VISUAL_WEIGHT = 0.55
OFFICIAL_CONTEXT_WEIGHT = 0.20
MEDIA_WEIGHT = 0.25

_WEIGHTS = {
    "visual": VISUAL_WEIGHT,
    "official_context": OFFICIAL_CONTEXT_WEIGHT,
    "media": MEDIA_WEIGHT,
}

_QUALITY_SCORES = {
    "伴生": 15.0,
    "勇者": 25.0,
    "史诗": 45.0,
    "传说": 65.0,
    "无双": 80.0,
    "荣耀典藏": 95.0,
}
_QUALITY_ALIASES = {
    "传说限定": "传说",
    "珍品传说": "传说",
    "无双限定": "无双",
    "史诗限定": "史诗",
    "勇者限定": "勇者",
}
_L2_FIELDS = (
    "model_detail",
    "effect_quality",
    "color_scheme",
    "composition",
    "uniqueness",
    "costume_design",
    "background_quality",
)
_L3_FIELDS = (
    "design_style",
    "cultural_references",
    "target_audience",
    "similar_skins",
    "differentiation",
)
_IMAGE_NAME = re.compile(
    r"^(?P<legacy_index>\d+)-(?P<hero>.+)-(?P<skin_id>\d+)-(?P<skin>.+)$"
)


@dataclass(frozen=True)
class PilotCandidate:
    """One manifest candidate with a reconciled local image."""

    source_key: str
    hero_name: str
    skin_name: str
    skin_id: str | None
    quality: str
    acquire_method: str
    price_text: str
    online_date: str
    image_path: str
    image_hash: str
    has_revenue_evidence: bool
    selection_tier: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImageResolution:
    """A resolved local image or a reason why the row was excluded."""

    source_key: str
    image_path: str | None
    image_hash: str | None
    resolution: str


@dataclass(frozen=True)
class MediaEvidence:
    """A manually verified link from one real public post to a skin."""

    source_key: str
    platform: str
    external_id: str
    mapping_rationale: str
    url: str = ""
    published_at: str = ""
    is_synthetic: bool = False


@dataclass(frozen=True)
class PremiumScore:
    """One frozen pilot score and enough provenance to audit it."""

    source_key: str
    perceived_premium_score: float | None
    visual_score: float | None
    official_context_score: float | None
    media_score: float | None
    evidence_coverage: float
    confidence: float
    evidence_status: str
    ranking_group: str
    warnings: tuple[str, ...] = ()
    score_version: int = PILOT_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["warnings"] = list(self.warnings)
        return payload


def sha256_file(path: str | Path) -> str:
    """Return an image-content digest without loading it all into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_payload_sha256(payload: Any) -> str:
    """Hash a JSON-compatible payload independently of file formatting."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_quality(value: str | None) -> str:
    """Return a supported canonical quality tier, or ``unknown``."""
    raw = (value or "").strip()
    if raw in _QUALITY_SCORES:
        return raw
    if raw in _QUALITY_ALIASES:
        return _QUALITY_ALIASES[raw]
    return "unknown"


def parse_cached_image_name(path: str | Path) -> tuple[str, str, str] | None:
    """Extract ``(hero_name, skin_id, skin_name)`` from the old image cache.

    The cached filename's leading index is from an earlier collector and is
    not trusted as a database key.  Identity is resolved with hero/name/skin
    ID only, and ambiguous matches are intentionally rejected.
    """
    match = _IMAGE_NAME.match(Path(path).stem)
    if match is None:
        return None
    return (
        match.group("hero").strip(),
        match.group("skin_id").strip(),
        match.group("skin").strip(),
    )


def resolve_cached_images(
    skins: Iterable[Mapping[str, Any]], image_dir: str | Path
) -> dict[str, ImageResolution]:
    """Reconcile cached images with current skin rows, fail-closed on ties."""
    by_hero_skin_id: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_hero_skin_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    keys: list[str] = []
    for skin in skins:
        source_key = str(skin["source_key"])
        keys.append(source_key)
        hero = str(skin.get("hero_name") or "").strip()
        skin_id = str(skin.get("skin_id") or "").strip()
        name = str(skin.get("skin_name") or "").strip()
        if hero and skin_id:
            by_hero_skin_id[(hero, skin_id)].append(source_key)
        if hero and name:
            by_hero_skin_name[(hero, name)].append(source_key)

    matches: dict[str, list[Path]] = defaultdict(list)
    root = Path(image_dir)
    if root.exists():
        for image_path in sorted(path for path in root.rglob("*") if path.is_file()):
            parsed = parse_cached_image_name(image_path)
            if parsed is None:
                continue
            hero, skin_id, skin_name = parsed
            candidates = by_hero_skin_id.get((hero, skin_id), [])
            if len(candidates) != 1:
                candidates = by_hero_skin_name.get((hero, skin_name), [])
            if len(candidates) == 1:
                matches[candidates[0]].append(image_path)

    resolved: dict[str, ImageResolution] = {}
    for source_key in keys:
        paths = matches.get(source_key, [])
        if len(paths) == 1:
            path = paths[0]
            resolved[source_key] = ImageResolution(
                source_key, str(path), sha256_file(path), "filename_reconciled"
            )
        elif len(paths) > 1:
            resolved[source_key] = ImageResolution(
                source_key, None, None, "ambiguous_cached_images"
            )
        else:
            resolved[source_key] = ImageResolution(
                source_key, None, None, "no_reconciled_cached_image"
            )
    return resolved


def revenue_source_keys(db_path: str | Path) -> set[str]:
    """Read value-record coverage without creating or migrating any tables."""
    path = Path(db_path)
    if not path.exists():
        return set()
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_key FROM skin_value_records"
            ).fetchall()
    except sqlite3.OperationalError:
        return set()
    return {str(row[0]) for row in rows}


def build_candidates(
    skins: Iterable[Mapping[str, Any]],
    resolutions: Mapping[str, ImageResolution],
    revenue_keys: set[str],
) -> tuple[list[PilotCandidate], dict[str, str]]:
    """Build candidates and retain exclusion reasons for the manifest report."""
    candidates: list[PilotCandidate] = []
    excluded: dict[str, str] = {}
    for skin in skins:
        source_key = str(skin["source_key"])
        resolution = resolutions.get(source_key)
        if resolution is None or not resolution.image_path or not resolution.image_hash:
            excluded[source_key] = (
                resolution.resolution if resolution else "no_image_resolution"
            )
            continue
        candidates.append(
            PilotCandidate(
                source_key=source_key,
                hero_name=str(skin.get("hero_name") or ""),
                skin_name=str(skin.get("skin_name") or ""),
                skin_id=str(skin.get("skin_id") or "") or None,
                quality=str(skin.get("quality") or ""),
                acquire_method=str(skin.get("acquire_method") or ""),
                price_text=str(skin.get("price_text") or ""),
                online_date=str(skin.get("online_date") or ""),
                image_path=resolution.image_path,
                image_hash=resolution.image_hash,
                has_revenue_evidence=source_key in revenue_keys,
                selection_tier=canonical_quality(str(skin.get("quality") or "")),
            )
        )
    return candidates, excluded


def _stratified_sample(
    candidates: Iterable[PilotCandidate], count: int, rng: random.Random
) -> list[PilotCandidate]:
    """Select a deterministic, tier-interleaved cohort without duplicates."""
    groups: dict[str, list[PilotCandidate]] = defaultdict(list)
    for candidate in candidates:
        groups[candidate.selection_tier].append(candidate)
    ordered_tiers = [*(_QUALITY_SCORES.keys()), "unknown"]
    for tier in ordered_tiers:
        rng.shuffle(groups[tier])

    selected: list[PilotCandidate] = []
    positions = {tier: 0 for tier in ordered_tiers}
    while len(selected) < count:
        progressed = False
        for tier in ordered_tiers:
            if len(selected) >= count:
                break
            index = positions[tier]
            if index < len(groups[tier]):
                selected.append(groups[tier][index])
                positions[tier] += 1
                progressed = True
        if not progressed:
            break
    return selected


def select_pilot_manifest(
    candidates: Iterable[PilotCandidate],
    *,
    limit: int = DEFAULT_PILOT_SIZE,
    revenue_target: int = DEFAULT_REVENUE_TARGET,
    seed: int = DEFAULT_SEED,
) -> list[PilotCandidate]:
    """Select a deterministic cohort with a separate revenue-evaluable slice."""
    available = list(candidates)
    if limit <= 0:
        raise ValueError("limit must be positive")
    rng = random.Random(seed)
    revenue = [candidate for candidate in available if candidate.has_revenue_evidence]
    revenue_take = min(max(0, revenue_target), limit, len(revenue))
    selected = _stratified_sample(revenue, revenue_take, rng)
    selected_keys = {candidate.source_key for candidate in selected}
    remainder = [
        candidate for candidate in available if candidate.source_key not in selected_keys
    ]
    selected.extend(_stratified_sample(remainder, limit - len(selected), rng))
    return selected


def manifest_payload(
    selected: Iterable[PilotCandidate],
    excluded: Mapping[str, str],
    *,
    seed: int = DEFAULT_SEED,
    limit: int = DEFAULT_PILOT_SIZE,
    revenue_target: int = DEFAULT_REVENUE_TARGET,
) -> dict[str, Any]:
    """Serialize the immutable manifest with only selection-time evidence."""
    records = [candidate.to_dict() for candidate in selected]
    return {
        "pilot_version": PILOT_VERSION,
        "selection": {
            "seed": seed,
            "limit": limit,
            "revenue_target": revenue_target,
            "revenue_selected": sum(
                1 for record in records if record["has_revenue_evidence"]
            ),
            "method": "tier_interleaved_with_revenue_evaluation_slice",
        },
        "records": records,
        "excluded": dict(sorted(excluded.items())),
    }


def load_frozen_manifest(path: str | Path) -> tuple[list[PilotCandidate], dict[str, Any]]:
    """Load an immutable cohort and verify every selected image by content hash."""
    manifest_path = Path(path)
    raw = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("pilot_version") != PILOT_VERSION:
        raise ValueError("unsupported or malformed premium pilot manifest")
    records = raw.get("records")
    selection = raw.get("selection")
    if not isinstance(records, list) or not isinstance(selection, dict):
        raise ValueError("premium pilot manifest requires selection and records")
    if int(selection.get("limit", -1)) != len(records):
        raise ValueError("premium pilot manifest record count does not match selection limit")

    candidates: list[PilotCandidate] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"premium pilot manifest record {index} is not an object")
        try:
            candidate = PilotCandidate(**record)
        except TypeError as exc:
            raise ValueError(f"premium pilot manifest record {index} is invalid: {exc}") from exc
        if not candidate.source_key or candidate.source_key in seen:
            raise ValueError(
                f"premium pilot manifest has missing or duplicate source_key: {candidate.source_key}"
            )
        image_path = Path(candidate.image_path)
        if not image_path.exists():
            raise ValueError(f"frozen manifest image is missing: {candidate.image_path}")
        if sha256_file(image_path) != candidate.image_hash:
            raise ValueError(f"frozen manifest image hash changed: {candidate.source_key}")
        seen.add(candidate.source_key)
        candidates.append(candidate)
    return candidates, raw


def load_media_mapping(path: str | Path | None) -> dict[str, list[MediaEvidence]]:
    """Load verified media links and reject synthetic or untraceable evidence."""
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    entries = raw.get("entries", raw) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        raise ValueError("media mapping must be a list or an object with entries")
    mapped: dict[str, list[MediaEvidence]] = defaultdict(list)
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("media mapping entries must be objects")
        source_key = str(item.get("source_key") or "").strip()
        platform = str(item.get("platform") or "").strip().lower()
        external_id = str(item.get("external_id") or "").strip()
        rationale = str(item.get("mapping_rationale") or "").strip()
        synthetic = bool(item.get("is_synthetic", False))
        if not source_key or not platform or not external_id or not rationale:
            raise ValueError(
                "media mapping requires source_key, platform, external_id, and mapping_rationale"
            )
        if synthetic or platform.startswith("synthetic"):
            raise ValueError("synthetic media is forbidden in the premium pilot")
        mapped[source_key].append(
            MediaEvidence(
                source_key=source_key,
                platform=platform,
                external_id=external_id,
                mapping_rationale=rationale,
                url=str(item.get("url") or ""),
                published_at=str(item.get("published_at") or ""),
            )
        )
    return dict(mapped)


def aggregate_media_labels(labels: Iterable[Mapping[str, Any]]) -> tuple[float | None, int, list[str]]:
    """Return a meaningful-comment weighted score from real linked posts."""
    valid: list[tuple[float, int, str]] = []
    for label in labels:
        if label.get("error"):
            continue
        score = _as_score(label.get("community_premium"))
        meaningful = int(label.get("n_meaningful", 0) or 0)
        if score is None or meaningful <= 0:
            continue
        valid.append((score, meaningful, str(label.get("mid") or "")))
    total_meaningful = sum(item[1] for item in valid)
    if total_meaningful < MIN_MEANINGFUL_MEDIA_COMMENTS:
        return None, total_meaningful, [item[2] for item in valid if item[2]]
    score = sum(item[0] * item[1] for item in valid) / total_meaningful
    return round(score, 2), total_meaningful, [item[2] for item in valid if item[2]]


def visual_score_from_vlm(vlm: Mapping[str, Any] | None) -> float | None:
    """Convert valid L2 visual dimensions to a 0–100 local-visual score."""
    if not vlm or str(vlm.get("status") or "") == "error":
        return None
    values = [value for _, value, _ in visual_dimension_values(vlm)]
    usable = [value for value in values if value is not None]
    if len(usable) < 4:
        return None
    return round(mean(usable) * 10.0, 2)


def visual_dimension_values(
    vlm: Mapping[str, Any] | None,
) -> list[tuple[str, float | None, str]]:
    """Return all seven visual inputs and their public VLM field origins."""
    values = vlm or {}
    dimensions: list[tuple[str, float | None, str]] = []
    for field in _L2_FIELDS:
        source_field = field
        raw = values.get(field)
        if field == "composition" and raw is None:
            source_field = "vlm_composition"
            raw = values.get(source_field)
        dimensions.append((field, _as_ten_scale(raw), source_field))
    return dimensions


def official_context_score(candidate: PilotCandidate) -> float | None:
    """Build a transparent context score without treating it as real emotion data."""
    return official_context_trace(candidate)["score"]


def official_context_trace(candidate: PilotCandidate) -> dict[str, Any]:
    """Return the official-context derivation used by the frozen scorer."""
    tier = canonical_quality(candidate.quality)
    base = _QUALITY_SCORES.get(tier)
    acquire = candidate.acquire_method
    price = candidate.price_text
    markers = f"{acquire} {price}"
    limited = any(token in markers for token in ("限定", "限时", "典藏"))
    gacha = any(token in markers for token in ("抽奖", "夺宝", "祈愿", "宝箱"))
    if base is None and not (limited or gacha):
        return {
            "quality_raw": candidate.quality,
            "quality_canonical": tier,
            "acquire_method": candidate.acquire_method,
            "price_text": candidate.price_text,
            "base_score": None,
            "limited_marker": limited,
            "gacha_marker": gacha,
            "adjustments": [],
            "pre_cap_score": None,
            "score": None,
        }
    value = base if base is not None else 35.0
    adjustments: list[dict[str, float | str]] = []
    if limited:
        value += 7.0
        adjustments.append({"reason": "limited_marker", "value": 7.0})
    if gacha:
        value += 5.0
        adjustments.append({"reason": "gacha_marker", "value": 5.0})
    return {
        "quality_raw": candidate.quality,
        "quality_canonical": tier,
        "acquire_method": candidate.acquire_method,
        "price_text": candidate.price_text,
        "base_score": base if base is not None else 35.0,
        "limited_marker": limited,
        "gacha_marker": gacha,
        "adjustments": adjustments,
        "pre_cap_score": round(value, 2),
        "score": round(min(value, 100.0), 2),
    }


def score_premium(
    candidate: PilotCandidate,
    *,
    vlm: Mapping[str, Any] | None,
    media_score: float | None,
) -> PremiumScore:
    """Score a skin from frozen non-revenue evidence weights.

    Missing modalities are re-normalized only within a partial-evidence group;
    callers must not mix ``partial_evidence`` and ``complete_evidence`` in one
    unrestricted ranking.
    """
    visual = visual_score_from_vlm(vlm)
    official = official_context_score(candidate)
    media = _as_score(media_score)
    components = {
        "visual": visual,
        "official_context": official,
        "media": media,
    }
    available = {
        name: value for name, value in components.items() if value is not None
    }
    used_weight = sum(_WEIGHTS[name] for name in available)
    warnings: list[str] = []
    if visual is None:
        warnings.append("missing_valid_visual_score")
    if official is None:
        warnings.append("missing_official_context")
    if media is None:
        warnings.append("missing_linked_media")
    if used_weight == 0:
        return PremiumScore(
            source_key=candidate.source_key,
            perceived_premium_score=None,
            visual_score=visual,
            official_context_score=official,
            media_score=media,
            evidence_coverage=0.0,
            confidence=0.0,
            evidence_status="insufficient_evidence",
            ranking_group="not_ranked",
            warnings=tuple(warnings),
        )

    score = sum(value * _WEIGHTS[name] for name, value in available.items()) / used_weight
    coverage = round(used_weight, 2)
    complete = len(available) == len(_WEIGHTS)
    if complete:
        status = "complete"
        ranking_group = "complete_evidence"
        confidence = 0.90
    else:
        missing = "_".join(name for name in _WEIGHTS if name not in available)
        status = f"partial_no_{missing}"
        ranking_group = "partial_evidence"
        confidence = min(0.65, 0.25 + 0.65 * used_weight)
    return PremiumScore(
        source_key=candidate.source_key,
        perceived_premium_score=round(score, 2),
        visual_score=visual,
        official_context_score=official,
        media_score=media,
        evidence_coverage=coverage,
        confidence=round(confidence, 2),
        evidence_status=status,
        ranking_group=ranking_group,
        warnings=tuple(warnings),
    )


def build_feature_trace(
    candidate: PilotCandidate,
    *,
    vlm: Mapping[str, Any] | None,
    media_score: float | None,
    media_status: str,
    meaningful_media_comments: int,
    linked_media_ids: Iterable[str],
    score: PremiumScore,
    manifest_sha256: str,
) -> dict[str, Any]:
    """Build a reconstructable, per-skin feature and provenance record."""
    visual_dimensions = [
        {
            "name": name,
            "value_1_to_10": value,
            "source_tier": "vlm_l2",
            "source_field": source_field,
        }
        for name, value, source_field in visual_dimension_values(vlm)
    ]
    components = {
        "visual": score.visual_score,
        "official_context": score.official_context_score,
        "media": score.media_score,
    }
    used_weight = sum(
        _WEIGHTS[name] for name, value in components.items() if value is not None
    )
    contribution_rows: list[dict[str, Any]] = []
    for name in _WEIGHTS:
        value = components[name]
        available = value is not None
        effective_weight = _WEIGHTS[name] / used_weight if available and used_weight else 0.0
        contribution = value * effective_weight if available else 0.0
        contribution_rows.append(
            {
                "name": name,
                "score": value,
                "available": available,
                "configured_weight": _WEIGHTS[name],
                "effective_weight": round(effective_weight, 8),
                "weighted_contribution": round(contribution, 8),
            }
        )
    reconstructed = (
        round(sum(row["weighted_contribution"] for row in contribution_rows), 2)
        if used_weight
        else None
    )
    if reconstructed != score.perceived_premium_score:
        raise ValueError(
            f"feature trace does not reconstruct premium score for {candidate.source_key}"
        )

    tier_lineage = vlm.get("tier_provenance", {}) if isinstance(vlm, Mapping) else {}
    tier_lineage = tier_lineage if isinstance(tier_lineage, Mapping) else {}
    required_lineage = ["l1", "l2"]
    if isinstance(vlm, Mapping) and vlm.get("execution_mode") == "full":
        required_lineage.append("l3")
    missing_lineage = [tier for tier in required_lineage if tier not in tier_lineage]
    semantic = {
        field: vlm.get(field) if isinstance(vlm, Mapping) else None
        for field in _L3_FIELDS
    }
    return {
        "trace_version": 1,
        "manifest_sha256": manifest_sha256,
        "source_key": candidate.source_key,
        "identity": {
            "hero_name": candidate.hero_name,
            "skin_name": candidate.skin_name,
            "skin_id": candidate.skin_id,
            "image_path": candidate.image_path,
            "image_sha256": candidate.image_hash,
            "selection_tier": candidate.selection_tier,
            "has_revenue_evidence": candidate.has_revenue_evidence,
        },
        "inputs": {
            "visual": {
                "dimensions": visual_dimensions,
                "aggregation": "mean_of_valid_l2_dimensions_x10",
                "minimum_valid_dimensions": 4,
                "score": score.visual_score,
                "pipeline_status": vlm.get("status") if isinstance(vlm, Mapping) else None,
                "tier_provenance": dict(tier_lineage),
            },
            "official_context": official_context_trace(candidate),
            "media": {
                "status": media_status,
                "score": score.media_score,
                "meaningful_comment_count": meaningful_media_comments,
                "linked_real_media_ids": list(linked_media_ids),
                "synthetic_allowed": False,
            },
            "semantic": {
                "score_role": "non_scoring_rationale",
                "values": semantic,
                "provenance": dict(tier_lineage.get("l3", {}))
                if isinstance(tier_lineage.get("l3"), Mapping)
                else {},
            },
        },
        "fusion": {
            "configured_weights": dict(_WEIGHTS),
            "used_weight": round(used_weight, 8),
            "missing_components": [
                name for name, value in components.items() if value is None
            ],
            "components": contribution_rows,
            "reconstructed_score": reconstructed,
        },
        "result": score.to_dict(),
        "provenance_status": "complete" if not missing_lineage else "incomplete",
        "missing_provenance_tiers": missing_lineage,
        "validation_only_fields": ["reviewer_rating", "signed_revenue_uplift"],
    }


def load_reviewer_ratings(
    path: str | Path | None, allowed_source_keys: set[str]
) -> dict[str, float]:
    """Load one blind-reviewer CSV while enforcing the pilot cohort boundary."""
    if path is None:
        return {}
    ratings: dict[str, float] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_key = str(row.get("source_key") or "").strip()
            raw_score = str(row.get("perceived_premium") or "").strip()
            if not source_key or not raw_score:
                continue
            if source_key not in allowed_source_keys:
                raise ValueError(f"reviewer rating is outside pilot cohort: {source_key}")
            if source_key in ratings:
                raise ValueError(f"duplicate reviewer rating for {source_key}")
            score = _as_score(raw_score)
            if score is None:
                raise ValueError(f"invalid reviewer score for {source_key}")
            ratings[source_key] = score
    return ratings


def reviewer_validation(
    scores: Iterable[PremiumScore], ratings: Mapping[str, float]
) -> dict[str, Any]:
    """Descriptively compare frozen pilot scores with one reviewer."""
    pairs = [
        (score.perceived_premium_score, ratings[score.source_key])
        for score in scores
        if score.perceived_premium_score is not None and score.source_key in ratings
    ]
    if len(pairs) < 2:
        return {"status": "not_evaluable", "n": len(pairs)}
    predicted, observed = zip(*pairs)
    return {
        "status": "single_reviewer_audit",
        "n": len(pairs),
        "spearman_rho": round(spearman_rho(predicted, observed), 4),
        "mae": round(mean(abs(a - b) for a, b in pairs), 4),
        "note": "One reviewer is an audit anchor, not a consensus label.",
    }


def load_signed_uplifts(db_path: str | Path, source_keys: set[str]) -> dict[str, float]:
    """Read the pre-existing signed-window outcome without mutating the DB."""
    if not source_keys:
        return {}
    path = Path(db_path)
    if not path.exists():
        return {}
    priorities = {
        "manual_exact": 0,
        "manual_estimated": 1,
        "csv_release_window_uplift": 2,
    }
    placeholders = ", ".join("?" for _ in source_keys)
    query = f"""
        SELECT source_key, signed_uplift, attribution_method, value_id
        FROM skin_value_records
        WHERE source_key IN ({placeholders}) AND signed_uplift IS NOT NULL
    """
    try:
        with sqlite3.connect(path) as conn:
            rows = conn.execute(query, sorted(source_keys)).fetchall()
    except sqlite3.OperationalError:
        return {}
    selected: dict[str, tuple[int, int, float]] = {}
    for source_key, uplift, method, value_id in rows:
        priority = priorities.get(str(method), 99)
        candidate = (priority, -int(value_id), float(uplift))
        current = selected.get(str(source_key))
        if current is None or candidate < current:
            selected[str(source_key)] = candidate
    return {key: value[2] for key, value in selected.items()}


def revenue_validation(
    scores: Iterable[PremiumScore], signed_uplifts: Mapping[str, float]
) -> dict[str, Any]:
    """Describe held-out score/outcome alignment without fitting any model."""
    pairs = [
        (score.perceived_premium_score, signed_uplifts[score.source_key])
        for score in scores
        if score.perceived_premium_score is not None
        and score.source_key in signed_uplifts
    ]
    if len(pairs) < 5:
        return {
            "status": "not_evaluable",
            "n": len(pairs),
            "reason": "fewer than five signed revenue-window outcomes",
        }
    premium, uplift = zip(*pairs)
    return {
        "status": "descriptive_held_out_validation",
        "n": len(pairs),
        "spearman_rho": round(spearman_rho(premium, uplift), 4),
        "positive_uplift_count": sum(1 for value in uplift if value > 0),
        "median_signed_uplift": round(_median(uplift), 4),
        "note": "Revenue was not an input to the score and no weights were retuned.",
    }


def spearman_rho(left: Iterable[float], right: Iterable[float]) -> float:
    """Compute Spearman rank correlation with average ranks for ties."""
    xs = list(map(float, left))
    ys = list(map(float, right))
    if len(xs) != len(ys) or len(xs) < 2:
        return math.nan
    ranked_x = _average_ranks(xs)
    ranked_y = _average_ranks(ys)
    x_mean = mean(ranked_x)
    y_mean = mean(ranked_y)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(ranked_x, ranked_y))
    x_denom = math.sqrt(sum((x - x_mean) ** 2 for x in ranked_x))
    y_denom = math.sqrt(sum((y - y_mean) ** 2 for y in ranked_y))
    if x_denom == 0 or y_denom == 0:
        return math.nan
    return numerator / (x_denom * y_denom)


def _average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        rank = (start + 1 + end) / 2.0
        for pos in range(start, end):
            ranks[indexed[pos][0]] = rank
        start = end
    return ranks


def _median(values: Iterable[float]) -> float:
    ordered = sorted(map(float, values))
    halfway = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[halfway]
    return (ordered[halfway - 1] + ordered[halfway]) / 2.0


def _as_ten_scale(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 < numeric <= 10:
        return None
    return numeric


def _as_score(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= numeric <= 100:
        return None
    return numeric
