"""Read-only portfolio dashboard endpoint for the Vue frontend.

The endpoint deliberately reuses the same dashboard query layer as Streamlit so
the two frontends cannot drift on score provenance, cash attribution, or
missing-value behavior.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from dashboard.format import attribution_label
from dashboard.query import (
    default_period,
    get_hero_names,
    get_portfolio_rows,
    get_portfolio_summary,
    get_release_revenue_timeline,
)
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository


router = APIRouter(tags=["dashboard"])


def _ranking_row(row: object) -> dict[str, object]:
    payload = asdict(row)  # type: ignore[arg-type]
    payload["cash_method_label"] = attribution_label(payload.get("cash_method"))
    return payload


@router.get("/dashboard/overview")
def dashboard_overview(
    search: str = "",
    hero_name: str | None = None,
    quality: str | None = None,
    online_from: date | None = None,
    online_to: date | None = None,
    emotion_coverage: Literal["all", "scored", "missing"] = "all",
    cash_coverage: Literal["all", "has_record", "missing"] = "all",
    period_start: date | None = None,
    period_end: date | None = None,
    db: Path = Query(DEFAULT_DB_PATH),
) -> dict[str, object]:
    """Return the complete portfolio-overview payload without mutating SQLite."""

    if period_start is None or period_end is None:
        default_start, default_end = default_period(db)
        period_start = period_start or default_start
        period_end = period_end or default_end
    if period_end < period_start:
        raise HTTPException(status_code=422, detail="period_end must be on or after period_start")
    if online_from and online_to and online_to < online_from:
        raise HTTPException(status_code=422, detail="online_to must be on or after online_from")

    rows = get_portfolio_rows(
        db,
        search=search,
        hero_name=hero_name,
        quality=quality,
        online_from=online_from,
        online_to=online_to,
        emotion_coverage=emotion_coverage,
        cash_coverage=cash_coverage,
        period_start=period_start,
        period_end=period_end,
    )
    summary = get_portfolio_summary(rows, db_path=db)

    releases, revenue = get_release_revenue_timeline(
        db,
        period_start=period_start,
        period_end=period_end,
        online_from=online_from,
        online_to=online_to,
    )
    selected_keys = {row.source_key for row in rows}
    releases = [row for row in releases if row["source_key"] in selected_keys]

    quality_counts = Counter(row.quality or "未标注" for row in rows)
    cash_values = [
        row.cash_attributed_revenue
        for row in rows
        if row.cash_attributed_revenue is not None
    ]
    scores = [row.emotion_score for row in rows if row.emotion_score is not None]
    cash_total = sum(cash_values, 0.0) if cash_values else None

    emotion_ranking = sorted(
        (row for row in rows if row.emotion_score is not None),
        key=lambda row: row.emotion_score,
        reverse=True,
    )[:20]
    cash_ranking = sorted(
        (row for row in rows if row.cash_attributed_revenue is not None),
        key=lambda row: row.cash_attributed_revenue,
        reverse=True,
    )[:20]
    scatter = [
        _ranking_row(row)
        for row in rows
        if row.emotion_score is not None and row.cash_attributed_revenue is not None
    ]

    all_skins = SkinRepository(db).list_skins(limit=None) if db.is_file() else []
    qualities = sorted({str(row.get("quality") or "") for row in all_skins if row.get("quality")})

    return {
        "scope": {
            "game": "王者荣耀",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "read_only": True,
            "score_label": "综合价值分",
            "score_note": "观察值与目录估计组成的运营分值，不等同于独立验证的情绪结论。",
        },
        "filters": {
            "heroes": get_hero_names(db),
            "qualities": qualities,
        },
        "kpis": {
            "cash_total_cny": round(cash_total, 2) if cash_total is not None else None,
            "cash_average_cny": (
                round(cash_total / len(cash_values), 2) if cash_total is not None else None
            ),
            "cash_count": len(cash_values),
            "score_average": round(sum(scores) / len(scores), 2) if scores else None,
            "score_count": len(scores),
            "skin_count": len(rows),
            "hero_count": len({row.hero_name for row in rows}),
        },
        "summary": summary.as_metrics(),
        "quality_distribution": [
            {"quality": name, "count": count}
            for name, count in sorted(quality_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "scatter": scatter,
        "rankings": {
            "emotion": [_ranking_row(row) for row in emotion_ranking],
            "cash": [_ranking_row(row) for row in cash_ranking],
        },
        "timeline": {"releases": releases, "revenue": revenue},
    }
