"""Cash-value evidence and period reporting routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from data.cash_value import CashValueService
from data.emotion_evidence_repository import EmotionEvidenceRepository
from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from feature_engineering.pipeline import FeatureBuilder
from feature_engineering.features import MarketValidationSignals
from models.emotion_evidence import signal_values_from_profile
from models.rule_engine import RuleEngine


router = APIRouter()


class ManualValueRequest(BaseModel):
    period_start: str
    period_end: str
    sales_volume: int | None = Field(default=None, ge=0)
    volume_relation: str
    avg_spend: float | None = Field(default=None, ge=0)
    currency: str = "CNY"
    cny_per_usd: float | None = Field(default=None, gt=0)
    confidence: float = Field(default=1.0, ge=0, le=1)
    notes: str = ""


def _context(source_key: str, db: str) -> tuple[Path, SkinRepository]:
    path = Path(db)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"database not found: {path}")
    repo = SkinRepository(path)
    if not repo.get_skin(source_key):
        raise HTTPException(status_code=404, detail=f"skin not found: {source_key}")
    return path, repo


@router.post("/skins/{source_key}/value-records", status_code=201)
def save_value_record(
    source_key: str,
    request: ManualValueRequest,
    db: str = Query(default=str(DEFAULT_DB_PATH)),
) -> dict:
    path, _ = _context(source_key, db)
    try:
        record = CashValueService(path).repo.save_manual_record(source_key, request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"value_record": record}


@router.get("/skins/{source_key}/cash-value")
def get_cash_value(
    source_key: str,
    start: str | None = None,
    end: str | None = None,
    db: str = Query(default=str(DEFAULT_DB_PATH)),
) -> dict:
    path, skin_repo = _context(source_key, db)
    market_repo = MarketSignalRepository(path)
    signals = market_repo.get_signals(source_key)
    profile = EmotionEvidenceRepository(path).latest_published_profile(source_key)
    emotion_signals = MarketValidationSignals.from_dict(
        signal_values_from_profile(profile) if profile else {}
    )
    evaluation = RuleEngine().evaluate(
        FeatureBuilder(skin_repo).build(source_key, emotion_signals), profile
    )
    try:
        return CashValueService(path).cash_value(
            source_key, start, end, evaluation_score=evaluation.evaluation_score,
            legacy_signals=signals,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
