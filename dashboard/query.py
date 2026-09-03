"""Batched, read-only query layer for the dashboard.

This module is the single place that talks to SQLite for the analysis pages. It
reuses the existing repositories (``SkinRepository``, ``CashValueRepository``,
``MarketSignalRepository``) and never re-implements evaluation or cash-priority
logic. Every function is defensive about missing tables / files so pages never
crash on an empty or partial database.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dashboard.format import CASH_PRIORITY
from dashboard.models import (
    CashStatus,
    Completeness,
    EmotionStatus,
    PortfolioSkinRow,
    PortfolioSummary,
    SkinDashboardDetail,
)
from data.cash_value import CashValueRepository, CashValueService
from data.emotion_evidence_repository import EmotionEvidenceRepository
from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import SkinRepository
from data.sqlite_read import connect_readonly, table_exists
from feature_engineering.pipeline import FeatureBuilder
from feature_engineering.features import MarketValidationSignals
from models.emotion_evidence import signal_values_from_profile
from models.rule_engine import RuleEngine


# ── Default analysis period ────────────────────────────────────────────────
def default_period(db_path: str | Path) -> tuple[date, date]:
    """End at the latest revenue date, start 365 days before it.

    Falls back to today-365d when the revenue table is missing or empty so the
    result never depends on the day the app happens to run. Read-only: never
    creates a database or table.
    """
    end = date.today()
    if not Path(db_path).exists():
        return end - timedelta(days=365), end
    try:
        repo = CashValueRepository(db_path)
        with _connect_readonly(repo) as conn:
            if not _table_exists(conn, "app_revenue_daily"):
                return end - timedelta(days=365), end
            row = conn.execute(
                "SELECT MAX(revenue_date) FROM app_revenue_daily"
            ).fetchone()
        if row and row[0]:
            parsed = _safe_date(row[0])
            if parsed is not None:
                end = parsed
    except sqlite3.Error:
        pass
    except Exception:
        pass
    return end - timedelta(days=365), end


# ── Cash value resolution (batch) ──────────────────────────────────────────
def _resolve_cash_for_source(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Mirror CashValueRepository.resolve priority without per-skin queries."""
    candidates = [
        r for r in records if r.get("attribution_method") in CASH_PRIORITY
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda r: (CASH_PRIORITY[r["attribution_method"]], -r.get("value_id", 0)),
    )


def _batch_cash(
    db_path: str | Path,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, dict[str, Any]]:
    """One query for all cash records, grouped + resolved by source_key.

    Read-only. When a period is supplied, resolution is scoped to records that
    overlap the window (via ``CashValueRepository.resolve``), so the overview
    KPIs, explorer rows, and charts all honour the selected analysis period.
    """
    if not Path(db_path).exists():
        return {}
    repo = CashValueRepository(db_path)
    try:
        with _connect_readonly(repo) as conn:
            if not _table_exists(conn, "skin_value_records"):
                return {}
            rows = conn.execute(
                """
                SELECT value_id, source_key, sales_volume, volume_relation,
                       avg_spend_cny, attributed_revenue, revenue_currency,
                       attribution_method, confidence, period_start, period_end
                FROM skin_value_records
                ORDER BY source_key, value_id DESC
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["source_key"]].append(dict(row))

    start = period_start.isoformat() if period_start else None
    end = period_end.isoformat() if period_end else None
    resolved: dict[str, dict[str, Any]] = {}
    for key, recs in grouped.items():
        if start or end:
            rec = repo.resolve(key, start, end)
            if rec is not None:
                resolved[key] = rec
        else:
            rec = _resolve_cash_for_source(recs)
            if rec is not None:
                resolved[key] = rec
    return resolved


# ── Emotion coverage (batch, gated by RuleEngine) ──────────────────────────
def _batch_emotion_evaluations(
    db_path: str | Path,
) -> dict[str, Any]:
    """Return only profiles published by the 80/100 cohort release gate."""
    if not Path(db_path).exists():
        return {}
    profiles = EmotionEvidenceRepository(db_path).list_latest_published_profiles()
    if not profiles:
        return {}

    repo = SkinRepository(db_path)
    builder = FeatureBuilder(repo)
    engine = RuleEngine()
    validated: dict[str, Any] = {}
    for key, profile in profiles.items():
        try:
            signals = MarketValidationSignals.from_dict(
                signal_values_from_profile(profile)
            )
            features = builder.build(key, signals)
            result = engine.evaluate(features, profile)
        except Exception:
            continue
        if result.validation_status == "evidence_validated":
            validated[key] = result
    return validated


# ── Portfolio rows ───────────────────────────────────────────────────────────
def get_portfolio_rows(
    db_path: str | Path,
    *,
    search: str = "",
    quality: str | None = None,
    online_from: date | None = None,
    online_to: date | None = None,
    emotion_coverage: str = "all",
    cash_coverage: str = "all",
    period_start: date | None = None,
    period_end: date | None = None,
) -> list[PortfolioSkinRow]:
    if not Path(db_path).exists():
        return []

    repo = SkinRepository(db_path)
    try:
        with _connect_readonly(repo) as conn:
            if not _table_exists(conn, "skins"):
                return []
    except sqlite3.Error:
        return []
    skins = repo.list_skins(
        search=search or None,
        quality=quality,
        limit=None,
    )

    cash_by_key = _batch_cash(
        db_path, period_start=period_start, period_end=period_end
    )
    validated = _batch_emotion_evaluations(db_path)
    diagnostics = EmotionEvidenceRepository(db_path).list_active_run_profiles()

    rows: list[PortfolioSkinRow] = []
    for skin in skins:
        source_key = skin["source_key"]
        online_text = skin.get("online_date")
        online_d = _safe_date(online_text)
        # online-date range filter (applied before row construction)
        if online_from and (online_d is None or online_d < online_from):
            continue
        if online_to and (online_d is None or online_d > online_to):
            continue

        evaluation = validated.get(source_key)
        diagnostic = diagnostics.get(source_key)
        is_validated = evaluation is not None
        emotion_status = (
            EmotionStatus.VALIDATED.value if is_validated else EmotionStatus.MISSING.value
        )
        # Emotion score and perceived value are populated ONLY for validated
        # rows. Insufficient results stay missing in KPIs, charts, and rankings.
        emotion_score = evaluation.evaluation_score if is_validated else None
        perceived_value = (
            (evaluation.aspect_scores or {}).get("value_for_money")
            if is_validated
            else None
        )

        cash = cash_by_key.get(source_key) or {}
        cash_status = CashStatus.HAS_RECORD.value if cash else CashStatus.MISSING.value
        # Only CNY-convertible attributed revenue counts toward portfolio totals.
        # Manual CNY records store revenue_currency=NULL; USD records store
        # revenue_currency='USD' with an FX rate. Explicitly-USD records are
        # excluded from the portfolio sum (but still shown on the detail page).
        cur = str(cash.get("revenue_currency") or "").upper()
        cash_revenue_cny = cash.get("attributed_revenue") if cur != "USD" else None

        completeness = _completeness(cash_status, emotion_status)

        rows.append(
            PortfolioSkinRow(
                source_key=source_key,
                hero_name=skin.get("hero_name") or "",
                skin_name=skin.get("skin_name") or "",
                quality=skin.get("quality"),
                online_date=online_text,
                primary_asset_url=skin.get("primary_asset_url"),
                emotion_score=emotion_score,
                emotion_validated=is_validated,
                perceived_value=perceived_value,
                emotion_status=emotion_status,
                emotion_run_id=evaluation.evidence_run_id if is_validated else None,
                emotion_ci_low=(
                    (evaluation.score_ci or {}).get("low") if is_validated else None
                ),
                emotion_ci_high=(
                    (evaluation.score_ci or {}).get("high") if is_validated else None
                ),
                emotion_failure_reasons=(
                    list(diagnostic.validation_reasons) if diagnostic else []
                ),
                emotion_qualified_aspect_count=(
                    diagnostic.qualified_aspect_count if diagnostic else 0
                ),
                cash_attributed_revenue=cash_revenue_cny,
                cash_sales_volume=cash.get("sales_volume"),
                cash_avg_spend_cny=cash.get("avg_spend_cny"),
                cash_confidence=cash.get("confidence"),
                cash_method=cash.get("attribution_method"),
                cash_revenue_currency=cur if cash else None,
                cash_status=cash_status,
                completeness=completeness,
            )
        )

    # Apply coverage filters.
    if emotion_coverage == "validated":
        rows = [r for r in rows if r.emotion_status == EmotionStatus.VALIDATED.value]
    elif emotion_coverage == "missing":
        rows = [r for r in rows if r.emotion_status == EmotionStatus.MISSING.value]
    if cash_coverage == "has_record":
        rows = [r for r in rows if r.cash_status == CashStatus.HAS_RECORD.value]
    elif cash_coverage == "missing":
        rows = [r for r in rows if r.cash_status == CashStatus.MISSING.value]

    return rows


def _completeness(cash_status: str, emotion_status: str) -> str:
    has_cash = cash_status == CashStatus.HAS_RECORD.value
    has_emotion = emotion_status == EmotionStatus.VALIDATED.value
    if has_cash and has_emotion:
        return Completeness.COMPLETE.value
    if has_cash or has_emotion:
        return Completeness.PARTIAL.value
    return Completeness.NONE.value


def get_portfolio_summary(
    rows: list[PortfolioSkinRow],
    *,
    db_path: str | Path | None = None,
) -> PortfolioSummary:
    total = len(rows)
    validated = sum(1 for r in rows if r.emotion_status == EmotionStatus.VALIDATED.value)
    cash = sum(1 for r in rows if r.cash_status == CashStatus.HAS_RECORD.value)
    portfolio_revenue = sum(
        (r.cash_attributed_revenue or 0.0)
        for r in rows
        if r.cash_attributed_revenue is not None
    )
    complete = sum(1 for r in rows if r.completeness == Completeness.COMPLETE.value)
    cohort = (
        EmotionEvidenceRepository(db_path).latest_cohort_status()
        if db_path is not None
        else None
    ) or {}
    catalog_size = total
    catalog_validated = validated
    if db_path is not None:
        try:
            catalog_size = int(SkinRepository(db_path).stats().get("skins") or 0)
            catalog_validated = len(_batch_emotion_evaluations(db_path))
        except Exception:
            catalog_size = 0
            catalog_validated = 0
    return PortfolioSummary(
        total_skins=total,
        catalog_size=catalog_size,
        catalog_validated_emotion_count=catalog_validated,
        validated_emotion_count=validated,
        validated_emotion_rate=round(validated / total, 4) if total else 0.0,
        cash_count=cash,
        cash_rate=round(cash / total, 4) if total else 0.0,
        portfolio_attributed_revenue_cny=round(portfolio_revenue, 2),
        evidence_completeness_rate=round(complete / total, 4) if total else 0.0,
        coverage_gaps=get_coverage_gaps(rows),
        emotion_cohort_run_id=cohort.get("run_id"),
        emotion_cohort_size=int(cohort.get("cohort_size") or 100),
        emotion_cohort_validated_count=int(cohort.get("validated_count") or 0),
        emotion_cohort_release_status=str(cohort.get("release_status") or "unavailable"),
        emotion_observation_start=cohort.get("observation_start"),
        emotion_observation_end=cohort.get("observation_end"),
    )


def get_coverage_gaps(rows: list[PortfolioSkinRow]) -> dict[str, int]:
    missing_emotion = sum(
        1 for r in rows if r.emotion_status == EmotionStatus.MISSING.value
    )
    missing_cash = sum(1 for r in rows if r.cash_status == CashStatus.MISSING.value)
    missing_image = sum(1 for r in rows if not r.primary_asset_url)
    invalid_online = sum(1 for r in rows if r.online_date and _safe_date(r.online_date) is None)
    return {
        "missing_emotion": missing_emotion,
        "missing_cash": missing_cash,
        "missing_image": missing_image,
        "invalid_online_date": invalid_online,
    }


# ── Release + revenue timeline ───────────────────────────────────────────────
def get_release_revenue_timeline(
    db_path: str | Path,
    *,
    period_start: date | None = None,
    period_end: date | None = None,
    online_from: date | None = None,
    online_to: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (release events, daily revenue) for the timeline chart."""
    releases: list[dict[str, Any]] = []
    revenue: list[dict[str, Any]] = []
    if not Path(db_path).exists():
        return releases, revenue

    repo = SkinRepository(db_path)
    try:
        with _connect_readonly(repo) as conn:  # type: ignore[arg-type]
            if not _table_exists(conn, "skins"):
                return releases, revenue
            rows = conn.execute(
                "SELECT source_key, hero_name, skin_name, online_date FROM skins"
            ).fetchall()
    except sqlite3.Error:
        return releases, revenue
    for row in rows:
        d = _safe_date(row["online_date"])
        if d is None:
            continue
        if period_start and d < period_start:
            continue
        if period_end and d > period_end:
            continue
        if online_from and d < online_from:
            continue
        if online_to and d > online_to:
            continue
        releases.append(
            {
                "date": d.isoformat(),
                "hero_name": row["hero_name"],
                "skin_name": row["skin_name"],
                "source_key": row["source_key"],
            }
        )

    try:
        cash_repo = CashValueRepository(db_path)
        revenue = cash_repo.revenue_rows(
            (period_start or date.today() - timedelta(days=365)).isoformat(),
            (period_end or date.today()).isoformat(),
        )
    except Exception:
        revenue = []
    return releases, revenue


# ── Single-skin detail (gated emotion + separate cash) ───────────────────
def get_skin_detail(
    db_path: str | Path,
    source_key: str,
    *,
    official_only: bool = True,
    period_start: date | None = None,
    period_end: date | None = None,
) -> SkinDashboardDetail | None:
    from feature_engineering.pipeline import FeatureBuilder
    from models.rule_engine import RuleEngine

    if not Path(db_path).is_file():
        return None
    skin = SkinRepository(db_path).get_skin(source_key)
    if skin is None:
        return None

    # Emotion: only a published, provenance-qualified profile can validate.
    evaluation: dict[str, Any] | None = None
    aspect_scores: dict[str, Any] = {}
    signal_repo = MarketSignalRepository(db_path)
    try:
        evidence_repo = EmotionEvidenceRepository(db_path)
        profile = evidence_repo.latest_published_profile(source_key)
        diagnostic_profile = profile or evidence_repo.latest_run_profile(source_key)
        signals = MarketValidationSignals.from_dict(
            signal_values_from_profile(diagnostic_profile) if diagnostic_profile else {}
        )
        features = FeatureBuilder(SkinRepository(db_path)).build(source_key, signals)
        result = RuleEngine().evaluate(features, diagnostic_profile)
        evaluation = result.to_dict()
        if result.validation_status == "evidence_validated":
            aspect_scores = evaluation.get("aspect_scores", {})
    except Exception:
        evaluation = None

    # Cash value: resolved independently via the existing service, scoped to the
    # selected analysis period. Per the independent-value-object contract this
    # never feeds the emotional score.
    start = period_start.isoformat() if period_start else None
    end = period_end.isoformat() if period_end else None
    cash_value: dict[str, Any] = {}
    sales_report: dict[str, Any] = {}
    try:
        cv = CashValueService(db_path).cash_value(
            source_key, start, end,
            evaluation_score=(
                (evaluation or {}).get("evaluation_score")
                if (evaluation or {}).get("validation_status") == "evidence_validated"
                else None
            ),
            legacy_signals=signal_repo.get_signals(source_key),
        )
        cash_value = cv
        sales_report = {"cash_value": cv}
    except Exception:
        cash_value = {}

    return SkinDashboardDetail(
        source_key=source_key,
        skin=skin,
        evaluation=evaluation,
        aspect_scores=aspect_scores,
        cash_value=cash_value,
        sales_gap={},
        sales_report=sales_report,
        evidence_items=EmotionEvidenceRepository(db_path).list_public_evidence(
            source_key
        ),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────
def _safe_date(text: Any) -> date | None:
    if not text:
        return None
    import datetime

    try:
        return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _connect_readonly(repo: Any):
    """Yield a read connection from an existing repository instance."""
    return connect_readonly(repo.db_path)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """True when ``table`` is present in the database.

    Used by read-only callers to fail closed on a partial database (e.g. a file
    that exists but lacks the ``skins`` table) instead of letting SQLite create
    the missing table implicitly via a write. Never raises on a missing table.
    """
    try:
        return table_exists(conn, table)
    except sqlite3.Error:
        return False
