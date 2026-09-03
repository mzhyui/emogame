"""Reusable chart components for the dashboard pages.

Uses Streamlit-native charts (consistent with the existing app.py) and stays
explicit about empty states instead of fabricating data. Every chart that
depends on emotional scores shows a clear placeholder when none are validated.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import render_empty_state
from dashboard.format import attribution_label, display_currency_amount, sort_missing_last
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
    st.bar_chart(df, x="品质", y="数量", height=300)


def release_revenue_timeline(releases: list[dict[str, Any]], revenue: list[dict[str, Any]]) -> None:
    """Show daily app revenue with release events marked on the same axis."""
    if not revenue and not releases:
        render_empty_state("当前分析期间内没有收入时间序列或上线事件。")
        return

    rev_df = pd.DataFrame()
    if revenue:
        rev_df = pd.DataFrame(revenue, columns=["revenue_date", "estimated_revenue"])
        rev_df["date"] = pd.to_datetime(rev_df["revenue_date"])
        rev_df = (
            rev_df[["date", "estimated_revenue"]]
            .set_index("date")
            .resample("D")
            .sum(numeric_only=True)
            .reset_index()
            .rename(columns={"estimated_revenue": "估算收入"})
        )
    else:
        st.info("当前期间没有收入数据；仅展示上线事件。")

    # Release events as a visibly-plotted event series on the same date axis.
    # Each release day carries a marker count so it appears on the timeline
    # instead of only in a caption. Aligned to the revenue resample grid when
    # revenue exists, otherwise plotted on its own daily grid.
    if releases:
        rel_df = pd.DataFrame(releases)
        rel_df["date"] = pd.to_datetime(rel_df["date"])
        rel_event = (
            rel_df.groupby(rel_df["date"].dt.normalize())
            .size()
            .reset_index(name="上线事件")
        )
        rel_event.columns = ["date", "上线事件"]
        rel_event["date"] = pd.to_datetime(rel_event["date"])
        if not rev_df.empty:
            merged = rev_df.merge(rel_event, on="date", how="outer").fillna(0)
            merged["上线事件"] = merged["上线事件"].astype(int)
            st.line_chart(merged, x="date", y=["估算收入", "上线事件"], height=300)
        else:
            st.line_chart(rel_event, x="date", y="上线事件", height=300)
        st.caption(f"筛选范围内共 {len(rel_df)} 个上线事件。")
    elif not rev_df.empty:
        st.line_chart(rev_df, x="date", y="估算收入", height=300)


def emotion_vs_cash_scatter(rows: list[PortfolioSkinRow]) -> None:
    """Scatter of emotional score vs attributed cash revenue.

    Currently no skin has a validated emotional score, so this shows an honest
    empty state rather than plotting prior scores as if they were validated.
    """
    validated = [r for r in rows if r.emotion_status == EmotionStatus.VALIDATED.value]
    if not validated:
        render_empty_state(
            "暂无可验证情绪分的皮肤，无法绘制情绪分 × 现金价值散点图。"
            "情绪分只来自持久化信号经 RuleEngine 计算的结果；当前信号表为空。",
        )
        return
    df = pd.DataFrame([
        {
            "情绪分": r.emotion_score or 0,
            "估算归因收入": r.cash_attributed_revenue or 0.0,
            "皮肤": f"{r.hero_name}/{r.skin_name}",
        }
        for r in validated
    ])
    st.scatter_chart(df, x="情绪分", y="估算归因收入", height=320)


def top_validated_ranking(rows: list[PortfolioSkinRow], by: str = "emotion") -> None:
    """Cohort-only leaderboard of provenance-qualified skins."""
    validated = [r for r in rows if r.emotion_status == EmotionStatus.VALIDATED.value]
    if not validated:
        render_empty_state(
            "当前感知情绪队列尚未发布，或未达到 80/100 发布门槛。"
            "只有持久化、来源合格且 validation_status == evidence_validated 的皮肤才能进入排行。",
        )
        return
    key = "emotion_score" if by == "emotion" else "cash_attributed_revenue"
    ranked = sort_missing_last(validated, key, reverse=True)
    df = pd.DataFrame([
        {"排名": i + 1, "皮肤": f"{r.hero_name}/{r.skin_name}",
         "情绪分": r.emotion_score,
         "95% CI": (
             f"{r.emotion_ci_low:.1f}–{r.emotion_ci_high:.1f}"
             if r.emotion_ci_low is not None and r.emotion_ci_high is not None
             else "—"
         ),
         "估算归因收入": r.cash_attributed_revenue}
        for i, r in enumerate(ranked[:20])
    ])
    st.dataframe(df, hide_index=True, use_container_width=True)


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
        render_empty_state("当前皮肤没有可审计的维度评分；系统只显示官方先验和缺失证据。")
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
