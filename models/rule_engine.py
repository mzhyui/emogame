"""Value-present skin evaluator.

Every catalog skin receives a full operational score. Community observations
replace the matching catalog estimates when available; publication,
calibration, and human-audit state are retained only as provenance and never
suppress the score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from feature_engineering.features import SkinFeatureVector
from models.emotion_evidence import (
    EvidenceQualificationProfile,
    SUBJECTIVE_ASPECT_WEIGHTS,
    SUBJECTIVE_ASPECTS,
)
from models.value_present_scoring import score_value_present_skin


ASPECT_WEIGHTS = SUBJECTIVE_ASPECT_WEIGHTS


@dataclass(slots=True)
class EvaluationResult:
    source_key: str
    hero_name: str
    skin_name: str
    evaluation_score: int | None
    official_prior_score: int
    aspect_scores: dict[str, int | None]
    confidence: float
    validation_status: str
    evidence_coverage: float
    warnings: list[str]
    evidence: dict[str, Any]
    evidence_run_id: str | None = None
    protocol_version: str | None = None
    evidence_cutoff: str | None = None
    qualified_aspect_counts: dict[str, int] | None = None
    validation_reasons: list[str] | None = None
    score_ci: dict[str, float | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "hero_name": self.hero_name,
            "skin_name": self.skin_name,
            "evaluation_score": self.evaluation_score,
            "official_prior_score": self.official_prior_score,
            "aspect_scores": self.aspect_scores,
            "confidence": self.confidence,
            "validation_status": self.validation_status,
            "evidence_coverage": self.evidence_coverage,
            "warnings": self.warnings,
            "evidence": self.evidence,
            "evidence_run_id": self.evidence_run_id,
            "protocol_version": self.protocol_version,
            "evidence_cutoff": self.evidence_cutoff,
            "qualified_aspect_counts": self.qualified_aspect_counts,
            "validation_reasons": self.validation_reasons or [],
            "score_ci": self.score_ci,
        }


class RuleEngine:
    """Complete value scorer with provenance and support metadata."""

    def evaluate(
        self,
        features: SkinFeatureVector,
        qualification: EvidenceQualificationProfile | None = None,
    ) -> EvaluationResult:
        """Evaluate a skin whenever it exists in the catalog."""
        raw_aspects = self._aspect_scores(features)
        observed_aspects = {
            aspect: raw_aspects.get(aspect) for aspect in SUBJECTIVE_ASPECTS
        }
        warnings: list[str] = []
        evidence_run_id: str | None = None
        protocol_version: str | None = None
        evidence_cutoff: str | None = None
        qualified_counts: dict[str, int] | None = None
        if qualification is not None:
            if qualification.source_key != features.source_key:
                warnings.append("evidence_profile_source_mismatch")
            else:
                for aspect in SUBJECTIVE_ASPECTS:
                    profile_value = qualification.aspect_scores.get(aspect)
                    if profile_value is not None:
                        observed_aspects[aspect] = profile_value
                evidence_run_id = qualification.run_id
                protocol_version = qualification.protocol_version
                evidence_cutoff = qualification.evidence_cutoff
                qualified_counts = dict(qualification.aspect_author_counts)

        full_score = score_value_present_skin(
            features.to_dict(),
            observed_aspects,
            observed_source="community_observation",
        )
        aspect_scores: dict[str, int | None] = dict(full_score.aspect_scores)
        aspect_scores["market_heat"] = raw_aspects.get("market_heat")
        evidence_coverage = len(full_score.observed_aspects) / len(SUBJECTIVE_ASPECTS)
        official_prior_score = self._official_prior(features)
        evaluation_score = full_score.score
        confidence = full_score.support
        validation_status = "value_scored"
        warnings.extend(self._warnings(features, full_score.estimated_aspects))

        return EvaluationResult(
            source_key=features.source_key,
            hero_name=features.hero_name,
            skin_name=features.skin_name,
            evaluation_score=evaluation_score,
            official_prior_score=official_prior_score,
            aspect_scores=aspect_scores,
            confidence=confidence,
            validation_status=validation_status,
            evidence_coverage=round(evidence_coverage, 2),
            warnings=warnings,
            evidence=self._evidence(features, qualification, full_score.to_dict()),
            evidence_run_id=evidence_run_id,
            protocol_version=protocol_version,
            evidence_cutoff=evidence_cutoff,
            qualified_aspect_counts=qualified_counts,
            validation_reasons=[],
            score_ci=None,
        )

    def _aspect_scores(self, f: SkinFeatureVector) -> dict[str, int | None]:
        s = f.market_signals
        return {
            "visual_appeal": optional_score(s.visual_score),
            "in_game_feel": optional_score(s.feel_score),
            "craftsmanship_quality": optional_score(s.craftsmanship_score),
            "collection_value": optional_score(s.collection_score),
            "value_for_money": optional_score(s.value_score),
            "purchase_intent": optional_score(s.purchase_intent_score),
            "market_heat": self._market_heat_score(f),
        }

    def _market_heat_score(self, f: SkinFeatureVector) -> int | None:
        s = f.market_signals
        heat_parts = [
            log_score(s.discussion_count, 10000),
            log_score(s.video_views, 5_000_000),
            log_score(s.marketing_volume, 10000),
            log_score(s.sales_volume, 500_000),
        ]
        available = [part for part in heat_parts if part is not None]
        if not available:
            return None
        return round_score(sum(available) / len(available) * 100)

    def _official_prior(self, f: SkinFeatureVector) -> int:
        scarcity = 30 * bool_score(f.is_limited or f.is_gacha)
        quality = 45 * f.quality_score
        data_quality = 15 * bool_score(f.has_detail_record) + 10 * bool_score(f.has_primary_asset)
        return round_score(scarcity + quality + data_quality)

    def _warnings(self, f: SkinFeatureVector, estimated_aspects: list[str]) -> list[str]:
        warnings: list[str] = []
        if not f.has_detail_record:
            warnings.append("missing_official_detail")
        if not f.has_primary_asset:
            warnings.append("missing_primary_asset")
        if estimated_aspects:
            warnings.append("catalog_estimated_aspects:" + ",".join(estimated_aspects))
        if f.market_signals.sales_volume is None and f.market_signals.marketing_volume is None:
            warnings.append("missing_sales_or_marketing_volume")
        return warnings

    def _evidence(
        self,
        f: SkinFeatureVector,
        qualification: EvidenceQualificationProfile | None,
        value_score: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "quality": f.quality,
            "official_tier": f.official_tier,
            "acquire_method": f.acquire_method,
            "is_limited": f.is_limited,
            "is_gacha": f.is_gacha,
            "has_detail_record": f.has_detail_record,
            "has_primary_asset": f.has_primary_asset,
            "skin_age_days": f.skin_age_days,
            "hero_skin_count": f.hero_skin_count,
            "market_signal_fields": f.market_signals.present_fields(),
            "scoring_contract": "value_present",
            "score_status": value_score["score_status"],
            "observed_aspects": value_score["observed_aspects"],
            "estimated_aspects": value_score["estimated_aspects"],
            "aspect_sources": value_score["aspect_sources"],
            "source_fields": value_score["source_fields"],
        }
        if qualification is not None:
            payload["qualification"] = qualification.to_dict()
        return payload


def optional_score(value: float | None) -> int | None:
    if value is None:
        return None
    if 0 <= value <= 1:
        return round_score(value * 100)
    return round_score(value)


def bool_score(value: bool) -> float:
    return 1.0 if value else 0.0


def clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def log_score(value: int | float | None, max_value: int | float) -> float | None:
    if value is None:
        return None
    if value <= 0:
        return 0.0
    return clamp01(math.log(value + 1) / math.log(max_value + 1))


def round_score(value: float) -> int:
    return min(max(round(value), 0), 100)
