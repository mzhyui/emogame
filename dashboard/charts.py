"""Reusable chart components for the dashboard pages.

Uses Streamlit-native charts (consistent with the existing app.py) and stays
explicit about empty states instead of fabricating data. Every chart that
depends on emotional scores shows a clear placeholder when no source exists.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.components import render_empty_state
from dashboard.format import (
    SCORE_LABEL,
    attribution_label,
    display_currency_amount,
    sort_missing_last,
)
from dashboard.models import EmotionStatus, PortfolioSkinRow

ASPECT_LABELS = {
    "visual_appeal": "观感",
    "in_game_feel": "手感",
    "craftsmanship_quality": "品质",
    "collection_value": "收藏",
    "value_for_money": "性价比",
    "purchase_intent": "购买意愿",
    "market_heat": "市场热度",
}

# Layout and blue accent adapted from Sven-Bo/streamlit-sales-dashboard.
SALES_BLUE = "#0083B8"


def render_sales_chart(figure: go.Figure) -> None:
    """Shared, responsive Plotly presentation for the sales-style overview."""
    figure.update_layout(
        template="plotly_white", height=340,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=12, r=12, t=24, b=12),
        colorway=[SALES_BLUE, "#F2A541", "#7D6CCA"],
        legend=dict(orientation="h", y=1.15),
    )
    st.plotly_chart(figure, width="stretch", config={"displaylogo": False})


def quality_distribution_chart(rows: list[PortfolioSkinRow]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        q = r.quality or "未标注"
        counts[q] = counts.get(q, 0) + 1
    if not counts:
        render_empty_state("没有符合筛选条件的皮肤。")
        return
    df = pd.DataFrame(
        [{"品质": k, "数量": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])]
    )
    figure = px.bar(
        df.sort_values("数量"), x="数量", y="品质", orientation="h",
        color_discrete_sequence=[SALES_BLUE], text_auto=True,
    )
    figure.update_xaxes(showgrid=False)
    render_sales_chart(figure)


def release_revenue_timeline(releases: list[dict[str, Any]], revenue: list[dict[str, Any]]) -> None:
    """Show daily app revenue with release events marked on the same axis."""
    if not revenue and not releases:
        render_empty_state("当前分析期间内没有收入时间序列或上线事件。")
        return

    # Keep currencies separate and retain missing days as gaps, never zeros.
    currencies = sorted({str(row.get("currency") or "未标注币种") for row in revenue})
    for currency in currencies or [None]:
        figure = make_subplots(specs=[[{"secondary_y": True}]])
        selected = [row for row in revenue if str(row.get("currency") or "未标注币种") == currency]
        if selected:
            frame = pd.DataFrame(selected)
            frame["date"] = pd.to_datetime(frame["revenue_date"])
            daily = frame.set_index("date")["estimated_revenue"].resample("D").sum(min_count=1)
            figure.add_trace(go.Scatter(
                x=daily.index, y=daily.values, name=f"游戏收入 ({currency})",
                mode="lines", line=dict(color=SALES_BLUE), connectgaps=False,
            ), secondary_y=False)
        if releases:
            counts = pd.Series(pd.to_datetime([row["date"] for row in releases])).value_counts().sort_index()
            figure.add_trace(go.Bar(
                x=counts.index, y=counts.values, name="筛选内上线皮肤",
                marker_color="#F2A541", opacity=0.65,
            ), secondary_y=True)
        figure.update_yaxes(title_text=f"游戏估算收入 ({currency or '无收入数据'})", secondary_y=False)
        figure.update_yaxes(title_text="上线皮肤数", dtick=1, showgrid=False, secondary_y=True)
        render_sales_chart(figure)
    st.caption(f"筛选范围内 {len(releases)} 个上线事件；游戏收入为全游戏背景数据。")


def emotion_vs_cash_scatter(rows: list[PortfolioSkinRow]) -> None:
    """Scatter of full operational value score vs attributed cash revenue."""
    scored = [r for r in rows if r.emotion_score is not None and r.cash_attributed_revenue is not None]
    if not scored:
        render_empty_state(
            "当前没有同时具有价值分和可换算 CNY 收入的皮肤。",
        )
        return
    df = pd.DataFrame([
        {
            SCORE_LABEL: r.emotion_score,
            "估算归因收入 (CNY)": r.cash_attributed_revenue,
            "皮肤": f"{r.hero_name}/{r.skin_name}",
            "现金置信度": r.cash_confidence,
            "归因方法": attribution_label(r.cash_method),
            "评分状态": r.emotion_score_status or "未标注",
        }
        for r in scored
    ])
    figure = px.scatter(
        df, x=SCORE_LABEL, y="估算归因收入 (CNY)", hover_name="皮肤",
        hover_data=["现金置信度", "归因方法", "评分状态"],
        color_discrete_sequence=[SALES_BLUE],
    )
    figure.update_traces(marker_size=10)
    render_sales_chart(figure)
    st.caption(f"双值覆盖 {len(scored)}/{len(rows)}；缺少任一数值的皮肤不绘点。")


def top_value_ranking(rows: list[PortfolioSkinRow], by: str = "emotion") -> None:
    """Leaderboard containing every skin with a value-present score."""
    key = "emotion_score" if by == "emotion" else "cash_attributed_revenue"
    scored = [r for r in rows if getattr(r, key) is not None]
    if not scored:
        render_empty_state(
            "当前筛选指标没有可用数值，排行留空。",
        )
        return
    ranked = sort_missing_last(scored, key, reverse=True)
    df = pd.DataFrame([
        {"排名": i + 1, "皮肤": f"{r.hero_name}/{r.skin_name}",
         SCORE_LABEL: r.emotion_score,
         "评分状态": r.emotion_score_status,
         "95% CI": (
             f"{r.emotion_ci_low:.1f}–{r.emotion_ci_high:.1f}"
             if r.emotion_ci_low is not None and r.emotion_ci_high is not None
             else "—"
         ),
         "估算归因收入 (CNY)": r.cash_attributed_revenue,
         "现金置信度": r.cash_confidence,
         "归因方法": attribution_label(r.cash_method),
         "Source key": r.source_key}
        for i, r in enumerate(ranked[:20])
    ])
    st.dataframe(df, hide_index=True, width="stretch")


def coverage_gap_chart(gaps: dict[str, int]) -> None:
    if not gaps:
        return
    df = pd.DataFrame([
        {"缺口类型": k, "数量": v} for k, v in gaps.items() if v > 0
    ])
    if df.empty:
        st.success("筛选范围内的皮肤在情绪、现金、图片维度均已有覆盖。")
        return
    st.dataframe(df, hide_index=True, use_container_width=True)
    for label, key in [
        ("缺情绪证据", "missing_emotion"),
        ("缺现金记录", "missing_cash"),
        ("缺图片", "missing_image"),
        ("无效上线日期", "invalid_online_date"),
    ]:
        if gaps.get(key):
            st.caption(f"{label}：{gaps[key]} 个皮肤")


def aspect_radar(aspect_scores: dict[str, int | None]) -> None:
    """Seven-dimension aspect scores as a horizontal bar (no fabrication)."""
    present = {k: v for k, v in aspect_scores.items() if v is not None}
    if not present:
        render_empty_state("当前皮肤没有可用情绪来源，维度分留空。")
        return
    df = pd.DataFrame([
        {"维度": ASPECT_LABELS.get(k, k), "分数": v} for k, v in present.items()
    ])
    st.bar_chart(df, x="维度", y="分数", height=280, horizontal=True)


def cash_method_breakdown(rows: list[PortfolioSkinRow]) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        if r.cash_status == "has_record" and r.cash_method:
            label = attribution_label(r.cash_method)
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        st.info("当前筛选范围内没有现金价值记录。")
        return
    df = pd.DataFrame([{"归因方法": k, "皮肤数": v} for k, v in counts.items()])
    st.dataframe(df, hide_index=True, use_container_width=True)
