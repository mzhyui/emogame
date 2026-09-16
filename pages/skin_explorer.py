"""皮肤探索页：全量皮肤的筛选、排序与横向比较。

只做只读分析展示；点击表格行可下钻到「皮肤详情」。情绪排名为空时显示明确空状态。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from dashboard import filters
from dashboard.format import (
    attribution_label,
    cash_status_label,
    display_currency_amount,
    display_number,
    emotion_reason_label,
    emotion_source_label,
    emotion_status_label,
    sort_missing_last,
)
from dashboard.models import CashStatus, EmotionStatus
from dashboard.query import get_portfolio_rows
from data.skin_repository import DEFAULT_DB_PATH

SORT_OPTIONS = {
    "情绪分": "emotion_score",
    "性价比": "perceived_value",
    "归因收入": "cash_attributed_revenue",
    "销量": "cash_sales_volume",
    "平均获取成本": "cash_avg_spend_cny",
}


def main() -> None:
    st.set_page_config(page_title="皮肤探索 · EmoGame", layout="wide")
    db_path = str(DEFAULT_DB_PATH)
    filters.render_filter_sidebar(db_path)
    f = filters.current_filters()

    if not f.is_valid_period():
        st.error("分析期间结束日期早于开始日期，请重新选择期间。")
        return

    with st.spinner("加载皮肤组合…"):
        rows = get_portfolio_rows(
            db_path,
            search=f.search,
            hero_name=f.hero_name,
            quality=f.quality,
            online_from=f.online_from,
            online_to=f.online_to,
            emotion_coverage=f.emotion_coverage,
            cash_coverage=f.cash_coverage,
            period_start=f.period_start,
            period_end=f.period_end,
        )

    st.title("皮肤探索")
    st.caption(f"筛选范围内 {len(rows)} 个皮肤 · 点击「查看详情」下钻到单皮肤分析")

    sort_by = st.selectbox("排序字段", list(SORT_OPTIONS.keys()), index=2)
    sort_key = SORT_OPTIONS[sort_by]
    # Missing values always land last regardless of direction.
    sorted_rows = sort_missing_last(rows, sort_key, reverse=True)

    # Comparison scatter (switchable axes).
    st.divider()
    st.subheader("横向比较散点图")
    ax = st.selectbox("横轴", list(SORT_OPTIONS.keys()), index=2, key="ax_x")
    ay = st.selectbox("纵轴", list(SORT_OPTIONS.keys()), index=0, key="ax_y")
    _render_scatter(sorted_rows, SORT_OPTIONS[ax], SORT_OPTIONS[ay])

    # Explorer table.
    st.divider()
    st.subheader("皮肤列表")
    df = _build_table(sorted_rows)
    event = st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
    )
    if event.selection.get("rows"):
        idx = event.selection["rows"][0]
        source_key = df.iloc[idx]["source_key"]
        st.session_state["dash_selected_source_key"] = source_key
        st.switch_page("pages/skin_detail.py")

    # Fallback detail selector (for environments without row selection).
    # Only render when there are rows; an empty result set must not show an
    # enabled selector/button that would crash on a zero-length list.
    if sorted_rows and not event.selection.get("rows"):
        sel = st.selectbox(
            "或选择皮肤查看详情",
            [f"{r.hero_name}/{r.skin_name} ({r.source_key})" for r in sorted_rows],
            index=0,
        )
        if st.button("查看详情"):
            source_key = sel.split("(")[-1].rstrip(")")
            st.session_state["dash_selected_source_key"] = source_key
            st.switch_page("pages/skin_detail.py")
    elif not sorted_rows:
        from dashboard.components import render_empty_state

        render_empty_state(
            "当前筛选条件下没有匹配的皮肤。调整搜索、品质或上线日期范围后再试。"
        )


def _render_scatter(rows, x_key: str, y_key: str) -> None:
    data = []
    for r in rows:
        xv = getattr(r, x_key)
        yv = getattr(r, y_key)
        if xv is None or yv is None:
            continue
        data.append({
            x_key: xv,
            y_key: yv,
            "皮肤": f"{r.hero_name}/{r.skin_name}",
        })
    if not data:
        st.info("所选两个轴都存在有效值的皮肤不足，无法绘制散点图（缺失值不参与比较）。")
        return
    st.scatter_chart(pd.DataFrame(data), x=x_key, y=y_key, height=340)


def _build_table(rows) -> pd.DataFrame:
    frame = pd.DataFrame([
        {
            "source_key": r.source_key,
            "英雄": r.hero_name,
            "皮肤": r.skin_name,
            "品质": r.quality or "—",
            "上线日期": r.online_date or "—",
            "情绪状态": emotion_status_label(r.emotion_status),
            "情绪来源": emotion_source_label(r.emotion_score_source),
            "情绪分": r.emotion_score,
            "有值维度": (
                f"{r.emotion_qualified_aspect_count}/6"
                if r.emotion_score_source
                else ""
            ),
            "缺失原因": (
                "；".join(emotion_reason_label(reason) for reason in r.emotion_failure_reasons)
                or "—"
            ),
            "性价比": r.perceived_value,
            "现金状态": cash_status_label(r.cash_status),
            "归因收入": display_currency_amount(r.cash_attributed_revenue, "CNY")
            if r.cash_attributed_revenue is not None else "—",
            "销量": display_number(r.cash_sales_volume),
            "获取成本(CNY)": display_number(r.cash_avg_spend_cny),
            "现金置信度": display_number(r.cash_confidence),
            "归因方法": attribution_label(r.cash_method),
            "完整度": _completeness_label(r),
        }
        for r in rows
    ])
    for column in ("情绪分", "性价比"):
        if column in frame:
            # Nullable integers keep missing emotional fields visually blank
            # without mixing strings and numbers (which breaks Arrow output).
            frame[column] = pd.array(frame[column], dtype="Int64")
    return frame


def _completeness_label(r) -> str:
    if r.completeness == "complete":
        return "完整"
    if r.completeness == "partial":
        return "部分"
    return "缺失"


if __name__ == "__main__":
    main()
