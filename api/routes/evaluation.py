"""Evaluation and sales-report routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from business.sales_advisor import SalesAdvisor
from data.cash_value import CashValueService
from data.emotion_evidence_repository import EmotionEvidenceRepository
from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from feature_engineering.features import MarketValidationSignals
from feature_engineering.pipeline import FeatureBuilder
from models.emotion_evidence import signal_values_from_profile
from models.rule_engine import RuleEngine
from models.sales_deviation import compare_score_to_sales, sales_blind_signals


router = APIRouter()


class EvaluationRequest(BaseModel):
    source_key: str | None = None
    search: str | None = None
    signals: dict[str, Any] | None = None
    ignore_db_signals: bool = False


class SkinListResponse(BaseModel):
    skins: list[dict[str, Any]]


class EvaluationResponse(BaseModel):
    evaluation: dict[str, Any]


class SalesReportResponse(BaseModel):
    evaluation: dict[str, Any]
    sales_report: dict[str, Any]


class SalesGapResponse(BaseModel):
    evaluation: dict[str, Any]
    sales_gap: dict[str, Any]


def repo_or_404(db_path: Path) -> SkinRepository:
    if not db_path.exists():
        raise HTTPException(status_code=404, detail=f"database not found: {db_path}")
    return SkinRepository(db_path)


def resolve_source_key(repo: SkinRepository, request: EvaluationRequest) -> str:
    if request.source_key:
        if not repo.get_skin(request.source_key):
            raise HTTPException(status_code=404, detail=f"skin not found: {request.source_key}")
        return request.source_key
    if not request.search:
        raise HTTPException(status_code=400, detail="provide source_key or search")
    rows = repo.search_skins(request.search, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no skin matched search: {request.search}")
    return str(rows[0]["source_key"])


def build_features_and_evaluation(
    db_path: Path,
    request: EvaluationRequest,
) -> tuple[Any, Any]:
    repo = repo_or_404(db_path)
    source_key = resolve_source_key(repo, request)
    qualification = None
    if request.signals is not None:
        signals = MarketValidationSignals.from_dict(request.signals)
    elif request.ignore_db_signals:
        signals = MarketValidationSignals()
    else:
        qualification = EmotionEvidenceRepository(db_path).latest_published_profile(
            source_key
        )
        signals = (
            MarketValidationSignals.from_dict(signal_values_from_profile(qualification))
            if qualification
            else MarketSignalRepository(db_path).get_opinion_signals(source_key)
        )
    features = FeatureBuilder(repo).build(source_key, signals)
    evaluation = RuleEngine().evaluate(features, qualification)
    return features, evaluation


@router.get("/skins", response_model=SkinListResponse)
def list_skins(
    search: str | None = None,
    hero_name: str | None = None,
    quality: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    db: str = Query(default=str(DEFAULT_DB_PATH)),
) -> dict[str, Any]:
    repo = repo_or_404(Path(db))
    skins = repo.list_skins(search=search, hero_name=hero_name, quality=quality, limit=limit)
    return {"skins": skins}


@router.get("/skins/{source_key}")
def get_skin(source_key: str, db: str = Query(default=str(DEFAULT_DB_PATH))) -> dict[str, Any]:
    repo = repo_or_404(Path(db))
    skin = repo.get_skin(source_key)
    if not skin:
        raise HTTPException(status_code=404, detail=f"skin not found: {source_key}")
    return skin


@router.post("/evaluate", response_model=EvaluationResponse)
def evaluate_skin(
    request: EvaluationRequest,
    db: str = Query(default=str(DEFAULT_DB_PATH)),
) -> dict[str, Any]:
    _, evaluation = build_features_and_evaluation(Path(db), request)
    return {"evaluation": evaluation.to_dict()}


@router.post("/sales-report", response_model=SalesReportResponse)
def sales_report(
    request: EvaluationRequest,
    db: str = Query(default=str(DEFAULT_DB_PATH)),
) -> dict[str, Any]:
    features, evaluation = build_features_and_evaluation(Path(db), request)
    report = SalesAdvisor().advise(features, evaluation)
    report_payload = report.to_dict()
    db_signals = MarketSignalRepository(Path(db)).get_signals(features.source_key)
    report_payload["cash_value"] = CashValueService(Path(db)).cash_value(
        features.source_key,
        evaluation_score=(
            evaluation.evaluation_score
            if evaluation.validation_status == "evidence_validated"
            else None
        ),
        legacy_signals=db_signals,
    )
    return {
        "evaluation": evaluation.to_dict(),
        "sales_report": report_payload,
    }


@router.post("/sales-gap", response_model=SalesGapResponse)
def sales_gap(
    request: EvaluationRequest,
    db: str = Query(default=str(DEFAULT_DB_PATH)),
    official_only: bool = Query(default=True),
) -> dict[str, Any]:
    db_path = Path(db)
    sales_features, _ = build_features_and_evaluation(db_path, request)
    comparison_features = sales_features
    if official_only:
        comparison_features = FeatureBuilder(repo_or_404(db_path)).build(
            sales_features.source_key,
            sales_blind_signals(sales_features.market_signals),
        )
    update = {"signals": sales_blind_signals(sales_features.market_signals).model_dump()}
    score_request = (
        request.model_copy(update=update)
        if hasattr(request, "model_copy")
        else request.copy(update=update)
    )
    score_features, evaluation = build_features_and_evaluation(db_path, score_request)
    evidence = MarketSignalRepository(db_path).list_evidence(
        sales_features.source_key,
        official_only=official_only,
    )
    cash_value = CashValueService(db_path).cash_value(
        sales_features.source_key,
        evaluation_score=(
            evaluation.evaluation_score
            if evaluation.validation_status == "evidence_validated"
            else None
        ),
        legacy_signals=MarketSignalRepository(db_path).get_signals(sales_features.source_key),
    )
    if cash_value.get("resolved") and cash_value["resolved"].get("attribution_method") != "legacy_aggregate":
        evidence.append(
            {
                "title": "现金价值归因",
                "url": None,
                "cash_value_resolved": cash_value["resolved"],
            }
        )
    gap = compare_score_to_sales(comparison_features, evaluation, evidence)
    return {
        "evaluation": evaluation.to_dict(),
        "sales_gap": gap,
    }
