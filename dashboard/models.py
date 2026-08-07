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
    VALIDATED = "validated"
    MISSING = "missing"


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
    emotion_coverage: Literal["all", "validated", "missing"] = "all"
    cash_coverage: Literal["all", "has_record", "missing"] = "all"
    period_start: date | None = None
    period_end: date | None = None

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

    # Emotional evaluation -- only present when persisted & validated.
    emotion_score: int | None = None
    emotion_validated: bool = False
    perceived_value: int | None = None  # aspect_scores["value_for_money"]
    emotion_status: str = EmotionStatus.MISSING.value

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
        return self.emotion_status == EmotionStatus.VALIDATED.value


@dataclass
class SkinDashboardDetail:
    """Full read model for the single-skin detail page."""

    source_key: str
    skin: dict[str, Any]
    evaluation: dict[str, Any] | None = None
    aspect_scores: dict[str, int | None] = field(default_factory=dict)
    cash_value: dict[str, Any] = field(default_factory=dict)
    sales_gap: dict[str, Any] = field(default_factory=dict)
    sales_report: dict[str, Any] = field(default_factory=dict)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PortfolioSummary:
    """Top-level KPIs for the portfolio overview page."""

    total_skins: int = 0
    validated_emotion_count: int = 0
    validated_emotion_rate: float = 0.0
    cash_count: int = 0
    cash_rate: float = 0.0
    portfolio_attributed_revenue_cny: float = 0.0
    evidence_completeness_rate: float = 0.0
    coverage_gaps: dict[str, int] = field(default_factory=dict)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "total_skins": self.total_skins,
            "validated_emotion_count": self.validated_emotion_count,
            "validated_emotion_rate": self.validated_emotion_rate,
            "cash_count": self.cash_count,
            "cash_rate": self.cash_rate,
            "portfolio_attributed_revenue_cny": self.portfolio_attributed_revenue_cny,
            "evidence_completeness_rate": self.evidence_completeness_rate,
            "coverage_gaps": self.coverage_gaps,
        }
