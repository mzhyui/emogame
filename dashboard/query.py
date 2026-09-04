"""Batched, read-only query layer for the dashboard.

This module is the single place that talks to SQLite for the analysis pages. It
reuses the existing repositories (``SkinRepository``, ``CashValueRepository``,
``MarketSignalRepository``) and never re-implements evaluation or cash-priority
logic. Every function is defensive about missing tables / files so pages never
crash on an empty or partial database.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dashboard.format import CASH_PRIORITY
from dashboard.models import (
    CashStatus,
    Completeness,
    EmotionScoreSource,
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
from models.emotion_workflow import parse_review_pack
from models.final_truth_emotion import (
    FINAL_TRUTH_POLICY,
    FINAL_TRUTH_SCORER_VERSION,
    final_truth_annotation_digest,
    score_observed_annotation_rows,
    score_final_truth_rows,
)
from models.rule_engine import RuleEngine


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FINAL_TRUTH_SCORES_PATH = (
    REPOSITORY_ROOT
    / "data/emotion_evidence/runs/20260902-current100-v1/final-truth-scores.json"
)
DEFAULT_EMOTION_RUN_ID = FINAL_TRUTH_SCORES_PATH.parent.name


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _batch_final_truth_scores(
    score_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load the human-final-truth score artifact and fail closed on drift.

    The score report is accepted only when its scorer contract matches and its
    declared source CSV still has the bound SHA-256. Invalid rows, duplicate
    identities, missing files, and malformed JSON make the entire source
    unavailable so the UI shows blanks instead of questionable numbers.
    """
    path = Path(score_path) if score_path is not None else FINAL_TRUTH_SCORES_PATH
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        if payload.get("schema_version") != 1:
            return {}
        if payload.get("scorer_version") != FINAL_TRUTH_SCORER_VERSION:
            return {}
        if payload.get("truth_policy") != FINAL_TRUTH_POLICY:
            return {}

        truth_artifact = payload.get("truth_artifact")
        truth_sha256 = payload.get("truth_sha256")
        if not isinstance(truth_artifact, str) or not isinstance(truth_sha256, str):
            return {}
        truth_path = Path(truth_artifact)
        if not truth_path.is_absolute():
            truth_path = REPOSITORY_ROOT / truth_path
        if not truth_path.is_file() or _sha256_file(truth_path) != truth_sha256:
            return {}
        truth_rows = parse_review_pack(truth_path)
        if payload.get("truth_rows") != len(truth_rows):
            return {}
        if payload.get("truth_annotations_sha256") != final_truth_annotation_digest(
            truth_rows
        ):
            return {}
        recomputed = score_final_truth_rows(truth_rows)
        if payload.get("truth_skins") != len(recomputed):
            return {}

        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            return {}
        expected_catalog_size = payload.get("catalog_skins")
        if expected_catalog_size is not None and expected_catalog_size != len(raw_rows):
            return {}

        scores: dict[str, dict[str, Any]] = {}
        for raw in raw_rows:
            if not isinstance(raw, dict):
                return {}
            source_key = raw.get("source_key")
            if not isinstance(source_key, str) or not source_key or source_key in scores:
                return {}
            observed = raw.get("observed_emotion_score")
            if observed is not None:
                if (
                    isinstance(observed, bool)
                    or not isinstance(observed, (int, float))
                    or not math.isfinite(float(observed))
                    or not 0 <= float(observed) <= 100
                ):
                    return {}
                if not float(observed).is_integer():
                    return {}
            aspect_scores = raw.get("aspect_scores") or {}
            if not isinstance(aspect_scores, dict):
                return {}
            for value in aspect_scores.values():
                if value is None:
                    continue
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or not 0 <= float(value) <= 100
                ):
                    return {}
            expected = recomputed.get(source_key)
            if expected is not None:
                for field, expected_value in expected.to_dict().items():
                    if raw.get(field) != expected_value:
                        return {}
            elif any((
                raw.get("score_status") != "no_final_truth_rows",
                raw.get("review_row_count") != 0,
                raw.get("relevant_row_count") != 0,
                raw.get("observed_emotion_score") is not None,
                raw.get("complete_six_aspect_score") is not None,
            )):
                return {}
            scores[source_key] = dict(raw)
        return scores
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}


def _final_truth_for_skin(
    skin: dict[str, Any], scores: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """Return a score only when both the key and catalog identity agree."""
    row = scores.get(str(skin.get("source_key") or ""))
    if row is None:
        return None
    for field in ("hero_name", "skin_name"):
        expected = str(skin.get(field) or "")
        observed = str(row.get(field) or "")
        if observed and observed != expected:
            return None
    return row


def _selected_model_annotator_id(model_name: str, prompt_hash: str) -> str:
    if model_name == "deterministic-v1":
        return model_name
    return f"{model_name}@{prompt_hash[:12]}"


def _batch_model_comment_scores(
    db_path: str | Path,
    *,
    run_id: str = DEFAULT_EMOTION_RUN_ID,
) -> dict[str, dict[str, Any]]:
    """Score every fully annotated skin with exact, usable run comments.

    A source is omitted unless every usable comment for that skin has an
    annotation from the run's frozen selected model and all stored model
    identity fields match. The score still remains null when the model finds no
    relevant emotional aspect in those comments.
    """
    if not Path(db_path).is_file():
        return {}
    repo = EmotionEvidenceRepository(db_path)
    run = repo.get_run(run_id)
    if not run or run.get("model_selection_status") != "frozen":
        return {}
    model_name = str(run.get("model_name") or "")
    model_digest = str(run.get("model_digest") or "")
    prompt_hash = str(run.get("prompt_hash") or "")
    if not model_name or not model_digest or not prompt_hash:
        return {}

    usable_candidates = {
        int(row["evidence_id"]): row
        for row in repo.list_annotation_candidates(run_id)
        if row.get("mapping_scope") == "exact_skin"
        and not row.get("is_synthetic")
        and not row.get("quarantine_reason")
        and str(row.get("input_class") or "public_comment") == "public_comment"
    }
    if not usable_candidates:
        return {}
    annotator_id = _selected_model_annotator_id(model_name, prompt_hash)
    annotations = repo.list_annotations(run_id, annotator_id=annotator_id)

    candidate_ids: dict[str, set[int]] = defaultdict(set)
    annotated_ids: dict[str, set[int]] = defaultdict(set)
    valid_annotations: list[dict[str, Any]] = []
    invalid_sources: set[str] = set()
    for evidence_id, candidate in usable_candidates.items():
        candidate_ids[str(candidate["source_key"])].add(evidence_id)
    for annotation in annotations:
        evidence_id = int(annotation["evidence_id"])
        candidate = usable_candidates.get(evidence_id)
        if candidate is None:
            continue
        source_key = str(candidate["source_key"])
        if (
            str(annotation.get("source_key") or "") != source_key
            or annotation.get("annotator_kind") != "model"
            or annotation.get("extractor_digest") != model_digest
            or annotation.get("prompt_hash") != prompt_hash
        ):
            invalid_sources.add(source_key)
            continue
        annotated_ids[source_key].add(evidence_id)
        valid_annotations.append(annotation)

    scored = score_observed_annotation_rows(
        valid_annotations, source_kind="model_comments"
    )
    quality_gate_passed = bool(
        (run.get("model_selection_metrics") or {}).get(
            "selected_quality_gate_passed", False
        )
    )
    results: dict[str, dict[str, Any]] = {}
    for source_key, expected_ids in candidate_ids.items():
        if source_key in invalid_sources or annotated_ids[source_key] != expected_ids:
            continue
        score = scored.get(source_key)
        if score is None:
            continue
        sample_candidate = usable_candidates[min(expected_ids)]
        results[source_key] = {
            **score.to_dict(),
            "hero_name": str(sample_candidate.get("hero_name") or ""),
            "skin_name": str(sample_candidate.get("skin_name") or ""),
            "comment_row_count": len(expected_ids),
            "annotation_source": "selected_comment_model",
            "run_id": run_id,
            "model_name": model_name,
            "model_digest": model_digest,
            "prompt_hash": prompt_hash,
            "quality_gate_passed": quality_gate_passed,
        }
    return results


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
    final_truth_scores = _batch_final_truth_scores()
    model_comment_scores = _batch_model_comment_scores(db_path)
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
        final_truth = _final_truth_for_skin(skin, final_truth_scores)
        has_declared_truth = bool(
            final_truth
            and final_truth.get("score_status") != "no_final_truth_rows"
        )
        model_comment = (
            None
            if has_declared_truth
            else _final_truth_for_skin(skin, model_comment_scores)
        )
        diagnostic = diagnostics.get(source_key)
        # Declared human truth governs every reviewed skin, including an
        # explicit no-relevant result. The selected comment scorer covers other
        # fully annotated sources; a published profile is the final fallback.
        if has_declared_truth:
            final_truth_score = final_truth.get("observed_emotion_score")
            truth_aspects = final_truth.get("aspect_scores") or {}
            emotion_score = (
                int(final_truth_score) if final_truth_score is not None else None
            )
            emotion_source = (
                EmotionScoreSource.HUMAN_FINAL_TRUTH.value
                if emotion_score is not None
                else None
            )
            emotion_score_status = str(final_truth.get("score_status") or "") or None
            perceived_value = truth_aspects.get("value_for_money")
            qualified_aspect_count = sum(
                value is not None for value in truth_aspects.values()
            )
            emotion_run_id = DEFAULT_EMOTION_RUN_ID
        elif model_comment is not None:
            model_score = model_comment.get("observed_emotion_score")
            model_aspects = model_comment.get("aspect_scores") or {}
            emotion_score = int(model_score) if model_score is not None else None
            emotion_source = (
                EmotionScoreSource.SELECTED_COMMENT_MODEL.value
                if emotion_score is not None
                else None
            )
            emotion_score_status = (
                str(model_comment.get("score_status") or "") or None
            )
            perceived_value = model_aspects.get("value_for_money")
            qualified_aspect_count = sum(
                value is not None for value in model_aspects.values()
            )
            emotion_run_id = str(model_comment.get("run_id") or "") or None
        elif evaluation is not None:
            emotion_score = evaluation.evaluation_score
            emotion_source = EmotionScoreSource.PUBLISHED_RULE_ENGINE.value
            emotion_score_status = "published_qualified"
            perceived_value = (evaluation.aspect_scores or {}).get("value_for_money")
            qualified_aspect_count = diagnostic.qualified_aspect_count if diagnostic else 0
            emotion_run_id = evaluation.evidence_run_id
        else:
            emotion_score = None
            emotion_source = None
            emotion_score_status = None
            perceived_value = None
            qualified_aspect_count = diagnostic.qualified_aspect_count if diagnostic else 0
            emotion_run_id = None
        is_validated = emotion_score is not None
        emotion_status = (
            EmotionStatus.VALIDATED.value if is_validated else EmotionStatus.MISSING.value
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
                emotion_score_source=emotion_source,
                emotion_score_status=emotion_score_status,
                perceived_value=perceived_value,
                emotion_status=emotion_status,
                emotion_run_id=emotion_run_id,
                emotion_ci_low=(
                    (evaluation.score_ci or {}).get("low")
                    if evaluation is not None
                    and emotion_source == EmotionScoreSource.PUBLISHED_RULE_ENGINE.value
                    else None
                ),
                emotion_ci_high=(
                    (evaluation.score_ci or {}).get("high")
                    if evaluation is not None
                    and emotion_source == EmotionScoreSource.PUBLISHED_RULE_ENGINE.value
                    else None
                ),
                emotion_failure_reasons=(
                    [emotion_score_status]
                    if emotion_score is None and emotion_score_status
                    else list(diagnostic.validation_reasons)
                    if diagnostic and emotion_score is None
                    else []
                ),
                emotion_qualified_aspect_count=qualified_aspect_count,
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
            catalog = SkinRepository(db_path).list_skins(limit=None)
            catalog_size = len(catalog)
            published = _batch_emotion_evaluations(db_path)
            truth_scores = _batch_final_truth_scores()
            model_scores = _batch_model_comment_scores(db_path)
            available_keys = set()
            for skin in catalog:
                truth_score = _final_truth_for_skin(skin, truth_scores)
                has_declared_truth = bool(
                    truth_score
                    and truth_score.get("score_status") != "no_final_truth_rows"
                )
                model_score = (
                    None
                    if has_declared_truth
                    else _final_truth_for_skin(skin, model_scores)
                )
                source_key = str(skin["source_key"])
                if has_declared_truth:
                    score = truth_score.get("observed_emotion_score")
                elif model_score is not None:
                    score = model_score.get("observed_emotion_score")
                else:
                    score = getattr(published.get(source_key), "evaluation_score", None)
                if score is not None:
                    available_keys.add(source_key)
            catalog_validated = len(available_keys)
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

    # Emotion: human final truth takes precedence over the selected comment
    # scorer, then a published qualified profile. Diagnostics never fill blank.
    evaluation: dict[str, Any] | None = None
    final_truth_score = _final_truth_for_skin(skin, _batch_final_truth_scores())
    has_declared_truth = bool(
        final_truth_score
        and final_truth_score.get("score_status") != "no_final_truth_rows"
    )
    model_comment_score = (
        None
        if has_declared_truth
        else _final_truth_for_skin(skin, _batch_model_comment_scores(db_path))
    )
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
        final_truth_score=final_truth_score,
        model_comment_score=model_comment_score,
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
