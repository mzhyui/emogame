"""Deterministic full scoring whenever a catalog skin exists.

The value-present contract is deliberately operational rather than
claim-bearing: every catalog skin receives six aspect scores and one composite.
Observed community values replace the corresponding catalog estimates when
available, but publication, calibration, and human-audit state never suppress
the result.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from feature_engineering.pipeline import quality_to_tier
from models.emotion_evidence import (
    SUBJECTIVE_ASPECTS,
    weighted_subjective_score,
)


VALUE_PRESENT_SCORER_VERSION = "value-present-scorer-v1"


@dataclass(slots=True)
class ValuePresentScore:
    """Complete six-aspect result with descriptive source information."""

    source_key: str
    hero_name: str
    skin_name: str
    score: int
    aspect_scores: dict[str, int]
    score_status: str
    valid: bool
    observed_aspects: list[str]
    estimated_aspects: list[str]
    aspect_sources: dict[str, str]
    source_fields: list[str]
    support: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp_score(value: float) -> int:
    return min(max(round(value), 0), 100)


def _observed_scores(values: Mapping[str, Any] | None) -> dict[str, int]:
    observed: dict[str, int] = {}
    for aspect in SUBJECTIVE_ASPECTS:
        value = (values or {}).get(aspect)
        if value is None or isinstance(value, bool):
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= numeric <= 1:
            numeric *= 100
        if 0 <= numeric <= 100:
            observed[aspect] = _clamp_score(numeric)
    return observed


def _catalog_inputs(skin: Mapping[str, Any]) -> tuple[dict[str, int], list[str]]:
    quality = str(skin.get("quality") or "").strip()
    acquire = str(skin.get("acquire_method") or "").strip()
    price = str(skin.get("price_text") or "").strip()
    has_detail = bool(skin.get("has_detail_record"))
    has_asset = bool(
        skin.get("has_primary_asset")
        or skin.get("primary_asset_url")
        or skin.get("primary_asset_path")
        or skin.get("image_path")
        or skin.get("image_url")
    )

    fields = [
        name
        for name, present in (
            ("quality", bool(quality)),
            ("acquire_method", bool(acquire)),
            ("price_text", bool(price)),
            ("official_detail", has_detail),
            ("primary_asset", has_asset),
        )
        if present
    ]

    # Unknown metadata is a neutral catalog estimate, not a zero-value claim.
    tier = quality_to_tier(quality)
    tier_anchor = (45, 52, 62, 72, 84, 94)[tier] if quality else 55
    asset_anchor = 74 if has_asset else 50
    detail_anchor = 70 if has_detail else 50
    text = f"{quality} {acquire}"
    limited = any(token in text for token in ("限定", "限时", "返场"))
    gacha = any(token in text for token in ("抽奖", "祈愿", "夺宝", "荣耀水晶", "无双"))
    event = any(token in acquire for token in ("活动", "福利", "任务", "免费"))
    shard = "碎片" in acquire
    battle_pass = any(token in acquire for token in ("战令", "礼册"))
    direct = any(token in acquire for token in ("商城直售", "直售", "点券"))

    if event:
        acquisition_anchor = 84
    elif shard:
        acquisition_anchor = 78
    elif battle_pass:
        acquisition_anchor = 72
    elif gacha:
        acquisition_anchor = 42
    elif direct:
        acquisition_anchor = 62
    else:
        acquisition_anchor = 58

    visual = _clamp_score(0.62 * tier_anchor + 0.28 * asset_anchor + 0.10 * detail_anchor)
    craftsmanship = _clamp_score(
        0.72 * tier_anchor + 0.18 * detail_anchor + 0.10 * asset_anchor
    )
    collection = _clamp_score(
        0.65 * tier_anchor + (18 if limited else 6) + (10 if gacha else 3)
    )
    value_for_money = _clamp_score(0.55 * tier_anchor + 0.45 * acquisition_anchor)

    return {
        "visual_appeal": visual,
        "craftsmanship_quality": craftsmanship,
        "collection_value": collection,
        "value_for_money": value_for_money,
    }, fields


def score_value_present_skin(
    skin: Mapping[str, Any],
    observed_aspect_scores: Mapping[str, Any] | None = None,
    *,
    observed_source: str = "community_observation",
) -> ValuePresentScore:
    """Return a full score without publication or audit prerequisites."""
    source_key = str(skin.get("source_key") or skin.get("skin_key") or "").strip()
    if not source_key:
        raise ValueError("value-present scoring requires source_key")

    catalog, source_fields = _catalog_inputs(skin)
    observed = _observed_scores(observed_aspect_scores)
    aspects = dict(catalog)
    for aspect, value in observed.items():
        aspects[aspect] = value

    if "in_game_feel" not in observed:
        aspects["in_game_feel"] = _clamp_score(
            0.45 * aspects["visual_appeal"]
            + 0.45 * aspects["craftsmanship_quality"]
            + 0.10 * catalog["craftsmanship_quality"]
        )
    if "purchase_intent" not in observed:
        aspects["purchase_intent"] = _clamp_score(
            0.30 * aspects["visual_appeal"]
            + 0.20 * aspects["in_game_feel"]
            + 0.20 * aspects["collection_value"]
            + 0.30 * aspects["value_for_money"]
        )

    # Restore the locked order and assert the new full-score contract.
    full_aspects = {aspect: int(aspects[aspect]) for aspect in SUBJECTIVE_ASPECTS}
    score = weighted_subjective_score(full_aspects)
    if score is None:
        raise AssertionError("value-present scorer produced an incomplete result")

    observed_names = [aspect for aspect in SUBJECTIVE_ASPECTS if aspect in observed]
    estimated_names = [aspect for aspect in SUBJECTIVE_ASPECTS if aspect not in observed]
    if len(observed_names) == len(SUBJECTIVE_ASPECTS):
        status = "full_observed"
    elif observed_names:
        status = "full_hybrid"
    else:
        status = "full_catalog_estimate"

    aspect_sources = {
        aspect: observed_source if aspect in observed else "catalog_estimate"
        for aspect in SUBJECTIVE_ASPECTS
    }
    catalog_support = min(len(source_fields) / 5, 1.0)
    observed_support = len(observed_names) / len(SUBJECTIVE_ASPECTS)
    support = round(0.25 + 0.35 * catalog_support + 0.40 * observed_support, 2)

    return ValuePresentScore(
        source_key=source_key,
        hero_name=str(skin.get("hero_name") or ""),
        skin_name=str(skin.get("skin_name") or ""),
        score=int(score),
        aspect_scores=full_aspects,
        score_status=status,
        valid=True,
        observed_aspects=observed_names,
        estimated_aspects=estimated_names,
        aspect_sources=aspect_sources,
        source_fields=source_fields or ["catalog_identity"],
        support=support,
    )
