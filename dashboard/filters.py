"""Shared filter sidebar for the analysis pages.

Renders the global filters into fixed ``st.session_state`` keys so they persist
across the Streamlit multipage navigation. The main analysis flow deliberately
hides the database path and any write controls -- those live only on the
data workbench page.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import streamlit as st

from dashboard.models import DashboardFilters
from dashboard.query import default_period

# Fixed session-state keys shared by every analysis page.
KEY_SEARCH = "dash_search"
KEY_QUALITY = "dash_quality"
KEY_ONLINE_FROM = "dash_online_from"
KEY_ONLINE_TO = "dash_online_to"
KEY_EMOTION = "dash_emotion"
KEY_CASH = "dash_cash"
KEY_PERIOD_START = "dash_period_start"
KEY_PERIOD_END = "dash_period_end"

DEFAULT_QUALITIES = [
    "", "传说", "传说限定", "勇者", "勇者→史诗", "勇者限定", "史诗",
    "史诗限定", "无双", "无双限定", "珍品传说", "荣耀典藏",
]


def render_filter_sidebar(db_path: str | Path) -> DashboardFilters:
    """Render the shared filter widgets and persist them in session_state."""
    st.sidebar.title("EmoGame 分析看板")
    st.sidebar.caption("王者荣耀 · 本地 SQLite · 只读分析")

    default_start, default_end = default_period(db_path)

    st.sidebar.text_input("搜索（英雄 / 皮肤 / 皮肤 ID）", value="", key=KEY_SEARCH)
    quality = st.sidebar.selectbox(
        "皮肤品质", DEFAULT_QUALITIES, index=0, key=KEY_QUALITY,
    )
    st.sidebar.caption("留空表示不过滤品质。")

    col1, col2 = st.sidebar.columns(2)
    col1.date_input("上线起始", value=None, key=KEY_ONLINE_FROM)
    col2.date_input("上线结束", value=None, key=KEY_ONLINE_TO)

    st.sidebar.divider()
    emotion = st.sidebar.segmented_control(
        "情绪证据状态", ["全部", "有验证结果", "缺少结果"], default="全部", key=KEY_EMOTION,
    )
    cash = st.sidebar.segmented_control(
        "现金证据状态", ["全部", "有记录", "缺少记录"], default="全部", key=KEY_CASH,
    )

    st.sidebar.divider()
    pcol1, pcol2 = st.sidebar.columns(2)
    pcol1.date_input("分析期间起", value=default_start, key=KEY_PERIOD_START)
    pcol2.date_input("分析期间止", value=default_end, key=KEY_PERIOD_END)

    return current_filters()


def current_filters() -> DashboardFilters:
    """Read the shared filters back out of session_state."""

    def _date(key: str) -> date | None:
        v = st.session_state.get(key)
        return v if isinstance(v, date) else None

    emotion_map = {"全部": "all", "有验证结果": "validated", "缺少结果": "missing"}
    cash_map = {"全部": "all", "有记录": "has_record", "缺少记录": "missing"}

    return DashboardFilters(
        search=str(st.session_state.get(KEY_SEARCH, "") or ""),
        quality=(st.session_state.get(KEY_QUALITY) or None) or None,
        online_from=_date(KEY_ONLINE_FROM),
        online_to=_date(KEY_ONLINE_TO),
        emotion_coverage=emotion_map.get(st.session_state.get(KEY_EMOTION, "全部"), "all"),
        cash_coverage=cash_map.get(st.session_state.get(KEY_CASH, "全部"), "all"),
        period_start=_date(KEY_PERIOD_START),
        period_end=_date(KEY_PERIOD_END),
    )


def selected_source_key() -> str | None:
    """Read the skin the user drilled into from session_state."""
    return st.session_state.get("dash_selected_source_key")
