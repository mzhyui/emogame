"""皮肤详情页：单皮肤的情绪分析、感知价值与现金价值三块分析。

来源、计算口径、警告、证据明细和原始 JSON 默认折叠但可审计。人工最终真值分优先，
已发布 RuleEngine 分作为回退；无任一可用来源时情绪分留空。
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
    validation_status = evaluation.get("validation_status", "insufficient_market_evidence")
    published = validation_status == "evidence_validated"
    truth = detail.final_truth_score or {}
    has_declared_truth = bool(
        truth and truth.get("score_status") != "no_final_truth_rows"
    )
    model_score_payload = detail.model_comment_score or {}
    observed_payload = truth if has_declared_truth else model_score_payload
    observed_score = observed_payload.get("observed_emotion_score")
    has_truth_score = has_declared_truth and observed_score is not None
    has_model_score = (
        not has_declared_truth
        and bool(model_score_payload)
        and observed_score is not None
    )
    emotion_score = observed_score if observed_score is not None else (
        evaluation.get("evaluation_score")
        if published and not observed_payload
        else None
    )
    emotion_source = (
        "human_final_truth" if has_truth_score
        else "selected_comment_model" if has_model_score
        else "published_rule_engine" if published and not observed_payload
        else None
    )
    displayed_aspects = (
        observed_payload.get("aspect_scores") or {}
        if observed_payload
        else detail.aspect_scores
    )

    # ── Block 1: Emotional analysis ───────────────────────────────────────────
    st.divider()
    st.subheader("情绪分析")
    if emotion_score is None:
        if observed_payload:
            st.warning("当前皮肤有评论，但评分来源未发现可用情绪维度，情绪分留空。")
        else:
            st.warning(
                "当前皮肤没有可用评论来源或已发布模型分，"
                "情绪分留空。"
            )
        reasons = evaluation.get("validation_reasons") or []
        if reasons:
            st.caption("模型评估未发布原因：" + "；".join(str(reason) for reason in reasons))
    ecols = st.columns(4)
    ecols[0].metric(
        "情绪分",
        display_number(emotion_score) if emotion_score is not None else "",
    )
    ecols[1].metric("情绪来源", emotion_source_label(emotion_source))
    if has_truth_score:
        ecols[2].metric("维度覆盖", display_percent(float(truth.get("aspect_coverage") or 0) * 100))
        ecols[3].metric(
            "相关/审核文本",
            f"{truth.get('relevant_row_count', 0)}/{truth.get('review_row_count', 0)}",
        )
        st.caption(
            "来源：development-v2.csv 人工最终真值。分数仅聚合已观察情绪维度；"
            "缺失维度不补中性值。"
        )
    elif has_model_score:
        ecols[2].metric(
            "维度覆盖",
            display_percent(float(model_score_payload.get("aspect_coverage") or 0) * 100),
        )
        ecols[3].metric(
            "相关/评论文本",
            f"{model_score_payload.get('relevant_row_count', 0)}/"
            f"{model_score_payload.get('comment_row_count', 0)}",
        )
        gate_note = (
            "已通过质量门"
            if model_score_payload.get("quality_gate_passed")
            else "未通过发布质量门，按用户要求作探索显示"
        )
        st.caption(
            f"来源：{model_score_payload.get('model_name', '未知模型')} 对可用评论的评分；"
            f"{gate_note}。分数仅聚合已观察维度，缺失维度不补中性值。"
        )
    elif published and not observed_payload:
        ecols[2].metric("证据覆盖", display_number(evaluation.get("evidence_coverage")))
        ecols[3].metric("置信度", display_number(evaluation.get("confidence")))
        st.caption("来源：已发布、来源合格的 RuleEngine 社区证据分。")
    else:
        ecols[2].metric("维度覆盖", "")
        ecols[3].metric("相关/审核文本", "")
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
