"""Validated read-only query interface for dashboard controls and future agents."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from dashboard.format import MEAN_SCORE_LABEL
from dashboard.models import DashboardFilters, PortfolioSkinRow

METRICS = {
    "skin_count": "皮肤数",
    "mean_score": MEAN_SCORE_LABEL,
    "cash_total": "归因收入合计 (CNY)",
    "cash_mean": "有收入皮肤平均收入 (CNY)",
}
GROUPS = {"hero_name": "英雄", "quality": "品质"}
EVIDENCE_SCOPE = "current_portfolio"


def build_query_spec(filters: DashboardFilters, group_by: str, metrics: list[str]) -> dict[str, Any]:
    """Produce a JSON-serializable specification using the shared filters."""
    values = {key: value.isoformat() if isinstance(value, date) else value
              for key, value in asdict(filters).items()}
    spec = dict(dataset="skin_portfolio", filters=values, metrics=metrics,
                group_by=group_by, evidence_scope=EVIDENCE_SCOPE)
    validate_query_spec(spec)
    return spec


def validate_query_spec(spec: dict[str, Any]) -> DashboardFilters:
    """Reject unsupported scopes, fields and operations before any I/O."""
    if not isinstance(spec, dict) or set(spec) != {
        "dataset", "filters", "metrics", "group_by", "evidence_scope"
    }:
        raise ValueError("查询必须包含 dataset、filters、metrics、group_by、evidence_scope。")
    if spec["dataset"] != "skin_portfolio" or spec["evidence_scope"] != EVIDENCE_SCOPE:
        raise ValueError("仅支持当前组合评分与现金归因范围 current_portfolio。")
    if not isinstance(spec["group_by"], str) or spec["group_by"] not in GROUPS:
        raise ValueError("仅支持按英雄或品质分组。")
    metrics = spec["metrics"]
    if (not isinstance(metrics, list) or not metrics
            or any(not isinstance(item, str) or item not in METRICS for item in metrics)
            or len(set(metrics)) != len(metrics)):
        raise ValueError("请选择非重复的受支持指标。")
    values = spec["filters"]
    if not isinstance(values, dict) or set(values) - set(asdict(DashboardFilters())):
        raise ValueError("查询包含未知筛选字段。")
    values = dict(values)
    for key in ("online_from", "online_to", "period_start", "period_end"):
        value = values.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f"{key} 必须是 ISO 日期字符串。")
            try:
                values[key] = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{key} 日期无效。") from exc
    for key in ("search", "quality", "hero_name"):
        if key in values and not isinstance(values[key], str) and not (
            values[key] is None and key != "search"
        ):
            raise ValueError(f"{key} 必须是文本。")
    if values.get("emotion_coverage", "all") not in ("all", "scored", "missing"):
        raise ValueError("情绪覆盖筛选无效。")
    if values.get("cash_coverage", "all") not in ("all", "has_record", "missing"):
        raise ValueError("现金覆盖筛选无效。")
    filters = DashboardFilters(**values)
    if not filters.is_valid_period() or (
        filters.online_from and filters.online_to and filters.online_to < filters.online_from
    ):
        raise ValueError("结束日期不得早于起始日期。")
    return filters


def summarize_rows(rows: list[PortfolioSkinRow], spec: dict[str, Any]) -> pd.DataFrame:
    """Aggregate already-filtered rows; retain missing values and denominators."""
    validate_query_spec(spec)
    grouped: dict[str, list[PortfolioSkinRow]] = {}
    for row in rows:
        grouped.setdefault(getattr(row, spec["group_by"]) or "未标注", []).append(row)
    result = []
    for label, members in sorted(grouped.items()):
        scores = [row.emotion_score for row in members if row.emotion_score is not None]
        cash = [row.cash_attributed_revenue for row in members if row.cash_attributed_revenue is not None]
        values = dict(skin_count=len(members), mean_score=sum(scores) / len(scores) if scores else None,
                      cash_total=sum(cash) if cash else None,
                      cash_mean=sum(cash) / len(cash) if cash else None)
        result.append({
            GROUPS[spec["group_by"]]: label,
            **{METRICS[key]: values[key] for key in spec["metrics"]},
            "有分值皮肤": len(scores), "有 CNY 收入皮肤": len(cash),
        })
    columns = [GROUPS[spec["group_by"]], *[METRICS[key] for key in spec["metrics"]],
               "有分值皮肤", "有 CNY 收入皮肤"]
    return pd.DataFrame(result, columns=columns)


def execute_query_spec(db_path: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    """Validate then query through the existing read-only data layer.

    No SQL, code, scoring overrides, or database paths are accepted in the spec.
    The trusted application supplies db_path. Missing aggregates serialize as null.
    """
    from dashboard.query import get_portfolio_rows

    filters = validate_query_spec(spec)
    rows = get_portfolio_rows(db_path, **asdict(filters))
    frame = summarize_rows(rows, spec)
    return {
        "query": spec,
        "rows": frame.astype(object).where(pd.notna(frame), None).to_dict("records"),
        "source_keys": [row.source_key for row in rows],
    }
