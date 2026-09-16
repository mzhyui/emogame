"""Read models for the EmoGame analysis dashboard.

These dataclasses are the shared contract between the batched query layer
(`dashboard.query`) and the Streamlit pages. They deliberately keep the three
value objects apart -- emotional score, perceived value, and cash value -- so no
page is tempted to fuse them into a single "total value" number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Literal


class EmotionStatus(str, Enum):
    SCORED = "scored"
    MISSING = "missing"


class EmotionScoreSource(str, Enum):
    VALUE_PRESENT = "value_present"
    HUMAN_FINAL_TRUTH = "human_final_truth"
    SELECTED_COMMENT_MODEL = "selected_comment_model"
    PUBLISHED_RULE_ENGINE = "published_rule_engine"


class CashStatus(str, Enum):
    HAS_RECORD = "has_record"
    MISSING = "missing"


class Completeness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    NONE = "none"


@dataclass
class DashboardFilters:
    """Global filters shared by every analysis page via ``st.session_state``."""

    search: str = ""
    quality: str | None = None
    online_from: date | None = None
    online_to: date | None = None
    emotion_coverage: Literal["all", "scored", "missing"] = "all"
    cash_coverage: Literal["all", "has_record", "missing"] = "all"
    period_start: date | None = None
    period_end: date | None = None
    hero_name: str | None = None

    def online_filter_active(self) -> bool:
        return self.online_from is not None or self.online_to is not None

    def is_valid_period(self) -> bool:
        """False when an end date precedes the start date.

        The analysis period is a hard contract: a reversed window cannot be
        meaningfully resolved, so callers must reject it with a clear UI message
        rather than silently swapping the bounds.
        """
        if self.period_start is None or self.period_end is None:
            return True
        return self.period_end >= self.period_start


@dataclass
class PortfolioSkinRow:
    """One skin as needed by the portfolio overview and explorer table/charts."""

    source_key: str
    hero_name: str
    skin_name: str
    quality: str | None
    online_date: str | None
    primary_asset_url: str | None = None

    # Operational full value score. Observed values are used when available
    # and catalog estimates complete every missing aspect.
    emotion_score: int | None = None
    emotion_scored: bool = False
    emotion_score_source: str | None = None
    emotion_score_status: str | None = None
    perceived_value: int | None = None  # aspect_scores["value_for_money"]
    emotion_status: str = EmotionStatus.MISSING.value
    emotion_run_id: str | None = None
    emotion_ci_low: float | None = None
    emotion_ci_high: float | None = None
    emotion_failure_reasons: list[str] = field(default_factory=list)
    emotion_qualified_aspect_count: int = 0

    # Cash value -- resolved via the existing priority chain.
    cash_attributed_revenue: float | None = None  # CNY-convertible only
    cash_sales_volume: int | None = None
    cash_avg_spend_cny: float | None = None
    cash_confidence: float | None = None
    cash_method: str | None = None  # attribution_method
    cash_revenue_currency: str | None = None
    cash_status: str = CashStatus.MISSING.value

    completeness: str = Completeness.NONE.value

    def has_cash(self) -> bool:
        return self.cash_status == CashStatus.HAS_RECORD.value

    def has_emotion(self) -> bool:
        return self.emotion_status == EmotionStatus.SCORED.value


@dataclass
class SkinDashboardDetail:
    """Full read model for the single-skin detail page."""

    source_key: str
    skin: dict[str, Any]
    evaluation: dict[str, Any] | None = None
    final_truth_score: dict[str, Any] | None = None
    model_comment_score: dict[str, Any] | None = None
    aspect_scores: dict[str, int | None] = field(default_factory=dict)
    cash_value: dict[str, Any] = field(default_factory=dict)
    sales_gap: dict[str, Any] = field(default_factory=dict)
    sales_report: dict[str, Any] = field(default_factory=dict)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PortfolioSummary:
    """Top-level KPIs for the portfolio overview page."""

    total_skins: int = 0
    catalog_size: int = 0
    catalog_scored_count: int = 0
    scored_count: int = 0
    scored_rate: float = 0.0
    cash_count: int = 0
    cash_rate: float = 0.0
    portfolio_attributed_revenue_cny: float = 0.0
    evidence_completeness_rate: float = 0.0
    coverage_gaps: dict[str, int] = field(default_factory=dict)
    emotion_cohort_run_id: str | None = None
    emotion_cohort_size: int = 100
    emotion_cohort_scored_count: int = 0
    emotion_cohort_release_status: str = "unavailable"
    emotion_observation_start: str | None = None
    emotion_observation_end: str | None = None

    def as_metrics(self) -> dict[str, Any]:
        return {
            "total_skins": self.total_skins,
            "catalog_size": self.catalog_size,
            "catalog_scored_count": self.catalog_scored_count,
            "scored_count": self.scored_count,
            "scored_rate": self.scored_rate,
            "cash_count": self.cash_count,
            "cash_rate": self.cash_rate,
            "portfolio_attributed_revenue_cny": self.portfolio_attributed_revenue_cny,
            "evidence_completeness_rate": self.evidence_completeness_rate,
            "coverage_gaps": self.coverage_gaps,
            "emotion_cohort_run_id": self.emotion_cohort_run_id,
            "emotion_cohort_size": self.emotion_cohort_size,
            "emotion_cohort_scored_count": self.emotion_cohort_scored_count,
            "emotion_cohort_release_status": self.emotion_cohort_release_status,
            "emotion_observation_start": self.emotion_observation_start,
            "emotion_observation_end": self.emotion_observation_end,
        }
