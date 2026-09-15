"""皮肤详情页：完整价值评分、感知价值与现金价值三块分析。

来源、计算口径、警告、证据明细和原始 JSON 默认折叠。观察值覆盖对应目录估计，
但发布、质量门和人工审核均不控制评分资格。
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from dashboard import charts, components, filters
from dashboard.format import (
    attribution_label,
    display_currency_amount,
    display_number,
    display_percent,
    emotion_source_label,
)
from dashboard.query import get_skin_detail
from data.skin_repository import DEFAULT_DB_PATH


def main() -> None:
    st.set_page_config(page_title="皮肤详情 · EmoGame", layout="wide")
    db_path = str(DEFAULT_DB_PATH)
    filters.render_filter_sidebar(db_path)
    f = filters.current_filters()

    if not f.is_valid_period():
        st.error("分析期间结束日期早于开始日期，请重新选择期间。")
        return

    source_key = filters.selected_source_key()
    if not source_key:
        st.title("皮肤详情")
        st.info("请从「皮肤探索」页选择一个皮肤下钻，或返回组合总览。")
        return

    with st.spinner("加载皮肤详情…"):
        detail = get_skin_detail(
            Path(db_path), source_key,
            period_start=f.period_start, period_end=f.period_end,
        )
    if detail is None:
        st.error(f"未找到皮肤：{source_key}")
        return

    skin = detail.skin
    st.title(f"{skin.get('hero_name', '')} / {skin.get('skin_name', '')}")

    # Header meta row.
    meta_cols = st.columns(4)
    meta_cols[0].metric("品质", skin.get("quality") or "—")
    meta_cols[1].metric("上线日期", skin.get("online_date") or "—")
    meta_cols[2].metric("获取方式", skin.get("acquire_method") or "—")
    meta_cols[3].metric("官方价格文本", skin.get("price_text") or "—")

    image_source = _image_source(skin)
    if image_source:
        st.image(image_source, width=220)

    evaluation = detail.evaluation or {}
    emotion_score = evaluation.get("evaluation_score")
    emotion_source = "value_present" if emotion_score is not None else None
    displayed_aspects = detail.aspect_scores or {}
    scoring_evidence = evaluation.get("evidence") or {}
    observed_aspects = scoring_evidence.get("observed_aspects") or []
    estimated_aspects = scoring_evidence.get("estimated_aspects") or []

    # ── Block 1: Emotional analysis ───────────────────────────────────────────
    st.divider()
    st.subheader("情绪分析")
    if emotion_score is None:
        st.warning("当前皮肤无法生成价值评分。")
    ecols = st.columns(4)
    ecols[0].metric(
        "情绪分",
        display_number(emotion_score) if emotion_score is not None else "",
    )
    ecols[1].metric("情绪来源", emotion_source_label(emotion_source))
    ecols[2].metric("完整维度", "6/6" if emotion_score is not None else "")
    ecols[3].metric("评分支持度", display_number(evaluation.get("confidence")))
    if emotion_score is not None:
        st.caption(
            f"价值存在即评分：{len(observed_aspects)} 个观察维度，"
            f"{len(estimated_aspects)} 个目录估计维度；不要求发布、质量门或人工审核。"
        )
    charts.aspect_radar(displayed_aspects)

    # ── Block 2: Perceived value ──────────────────────────────────────────────
    st.divider()
    st.subheader("感知价值")
    pcols = st.columns(3)
    aspect = displayed_aspects or {}
    pcols[0].metric("性价比 (value_for_money)", display_number(aspect.get("value_for_money")))
    pcols[1].metric("收藏价值", display_number(aspect.get("collection_value")))
    pcols[2].metric("购买意愿", display_number(aspect.get("purchase_intent")))

    # ── Block 3: Cash value ───────────────────────────────────────────────────
    st.divider()
    st.subheader("现金价值")
    _render_cash(detail)

    # ── Collapsible audit sections ────────────────────────────────────────────
    st.divider()
    _render_gap_and_actions(detail)
    components.render_audit_block("评分与销售偏差 / 业务建议（原始数据）", _audit_payload(detail), source_key)
    with st.expander("证据明细（可审计）"):
        st.json(detail.evidence_items)


def _image_source(skin: dict) -> str | None:
    """Prefer a valid local binding, falling back to the remote URL."""
    raw_path = skin.get("primary_asset_path") or skin.get("image_path")
    if raw_path:
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        if path.is_file():
            return str(path)
    return skin.get("primary_asset_url") or skin.get("image_url") or None


def _render_cash(detail) -> None:
    cash = detail.cash_value or {}
    resolved = cash.get("resolved")
    if not resolved:
        st.info("当前期间没有持久化现金价值证据（缺失值保持缺失，不按零处理）。")
        return
    ccols = st.columns(5)
    ccols[0].metric("估算归因收入", display_currency_amount(resolved.get("attributed_revenue"), resolved.get("revenue_currency")))
    ccols[1].metric("销量/估算件数", display_number(resolved.get("sales_volume")))
    ccols[2].metric("平均获取成本 (CNY)", display_number(resolved.get("avg_spend_cny")))
    ccols[3].metric("收入提升", display_percent((cash.get("metrics") or {}).get("revenue_lift_percent")))
    ccols[4].metric("置信度", display_number(resolved.get("confidence")))
    st.caption(
        f"归因方法：{attribution_label(resolved.get('attribution_method'))} | "
        f"成本依据：{resolved.get('spend_basis')} | "
        f"币种：{resolved.get('revenue_currency')} | "
        f"期间：{resolved.get('period_start', 'legacy')} 至 {resolved.get('period_end', 'legacy')}"
    )
    st.caption("CSV 归因只能称为估算归因收入，不能表述为真实皮肤收入。")
    if cash.get("daily_chart"):
        st.line_chart(cash["daily_chart"], x="date", y=["baseline", "observed"])
    for warning in cash.get("warnings") or []:
        st.warning(warning)


def _render_gap_and_actions(detail) -> None:
    gap = detail.sales_gap or {}
    report = detail.sales_report or {}
    with st.expander("评分与销售偏差 / 购买驱动力 / 建议动作"):
        if gap:
            st.write(f"方向：`{gap.get('gap_direction')}`")
            st.write(gap.get("interpretation"))
            st.write(f"Gap：{gap.get('gap')}；置信度：{display_number(gap.get('confidence'))}")
        drivers = report.get("purchase_drivers") or []
        blockers = report.get("conversion_blockers") or []
        if drivers:
            st.subheader("购买驱动力")
            for d in drivers:
                st.info(d)
        if blockers:
            st.subheader("转化阻力")
            for b in blockers:
                st.warning(b)
        for action in report.get("recommended_actions") or []:
            st.write(f"**{action.get('action')}** — {action.get('detail')}")


def _audit_payload(detail) -> dict:
    return {
        "evaluation": detail.evaluation,
        "final_truth_score": detail.final_truth_score,
        "model_comment_score": detail.model_comment_score,
        "aspect_scores": detail.aspect_scores,
        "sales_gap": detail.sales_gap,
        "sales_report": detail.sales_report,
        "cash_value": detail.cash_value,
    }


if __name__ == "__main__":
    main()
