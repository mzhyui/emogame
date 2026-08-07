"""Formatting helpers and site-wide status labels for the dashboard.

Centralising the labels here guarantees every analysis page uses the same
Chinese wording for value status, cash attribution method, and risk notes.
"""

from __future__ import annotations

from typing import Any

# ── Cash attribution method labels ──────────────────────────────────────────
ATTRIBUTION_METHOD_LABELS: dict[str, str] = {
    "manual_exact": "人工精确值",
    "manual_estimated": "人工估算值",
    "csv_release_window_uplift": "CSV 上线窗口归因",
    "legacy_aggregate": "历史聚合（不计入情绪排名）",
}

# ── Value-status labels (emotional / cash) ─────────────────────────────────
EMOTION_STATUS_LABELS: dict[str, str] = {
    "validated": "已验证",
    "missing": "缺结果",
}
CASH_STATUS_LABELS: dict[str, str] = {
    "has_record": "有记录",
    "missing": "缺记录",
}
COMPLETENESS_LABELS: dict[str, str] = {
    "complete": "完整",
    "partial": "部分",
    "none": "缺失",
}

# Priority chain reused from CashValueRepository (kept here for display ordering).
CASH_PRIORITY: dict[str, int] = {
    "manual_exact": 0,
    "manual_estimated": 1,
    "csv_release_window_uplift": 2,
    "legacy_aggregate": 3,
}


def attribution_label(method: str | None) -> str:
    if not method:
        return "无"
    return ATTRIBUTION_METHOD_LABELS.get(method, str(method))


def emotion_status_label(status: str) -> str:
    return EMOTION_STATUS_LABELS.get(status, status)


def cash_status_label(status: str) -> str:
    return CASH_STATUS_LABELS.get(status, status)


def completeness_label(completeness: str) -> str:
    return COMPLETENESS_LABELS.get(completeness, completeness)


def display_number(value: Any) -> str:
    """Render a number with thousands separators, or ``N/A`` for missing."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def display_percent(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value):.2f}%"


def display_currency_amount(value: Any, currency: str | None = None) -> str:
    if value is None:
        return "N/A"
    cur = (currency or "CNY").upper()
    if isinstance(value, float):
        return f"{cur} {value:,.2f}"
    return f"{cur} {value:,}"


def sort_missing_last(rows: list[Any], key: str, *, reverse: bool = True) -> list[Any]:
    """Sort rows by an attribute, always pushing ``None`` to the end.

    A plain ``sorted(..., key=lambda r: r.x or 0, reverse=True)`` puts a
    ``None`` key *first* because ``None`` sorts below any number in ascending
    order and ``reverse`` still preserves that relative ordering. We make the
    presence of a value the primary key so missing values land last regardless
    of ``reverse``.

    ``rows`` may be any list of objects exposing ``getattr(row, key)``
    (e.g. ``PortfolioSkinRow``). The sort key falls back to ``0`` for the
    numeric comparison so heterogeneous types never crash the sort.
    """

    def _value(row: Any) -> Any:
        v = getattr(row, key, None)
        return v if v is not None else 0

    return sorted(
        rows,
        key=lambda r: (getattr(r, key, None) is not None, _value(r)),
        reverse=reverse,
    )


def safe_date(text: str | None) -> Any | None:
    """Return an ISO date string only if parseable, else ``None``.

    Returns a ``datetime.date`` when the input is a valid ``YYYY-MM-DD`` string,
    otherwise ``None``. Invalid dates must never crash the page.
    """
    if not text:
        return None
    import datetime

    try:
        return datetime.datetime.strptime(str(text)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
