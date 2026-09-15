"""Workbench render functions, extracted from app.py.

The data workbench is the management entry point: it keeps DB selection, the
evidence-mode toggles, manual simulation, ML calibration, CSV import, manual
cash-value entry, data audit, evidence detail, method notes, and JSON output.
These are intentionally separate from the read-only analysis pages.

This module re-uses the helper functions still defined in ``app`` (``build_payload``,
``dataset_summary``, ``classify_sales_basis``, ``_display_number``, etc.) so the
existing business logic is never duplicated.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import streamlit as st

from app import (  # reuse unchanged helpers/logic from app.py
    ASPECT_LABELS,
    apply_calibration,
    build_payload,
    classify_sales_basis,
    dataset_summary,
    empty_summary,
    load_calibration_model,
    load_calibration_report,
    load_signals,
    metric_summary,
    search_skins,
)
from data.cash_value import CashValueService
from data.market_signal_repository import MarketSignalRepository, OFFICIAL_EVIDENCE_PLATFORMS


def render_workbench_sidebar() -> tuple[Path, str | None, Any, Any, bool]:
    """The workbench's own sidebar (DB path + evidence controls)."""
    st.sidebar.title("EmoGame 数据工作台")
    st.sidebar.caption("证据审计、评分、销量偏差和校准工作台")

    st.sidebar.selectbox("游戏", ["王者荣耀（已接入）"], index=0)
    st.sidebar.caption("其他游戏还没有接入，不会伪装成可计算。")

    include_non_official = st.sidebar.toggle(
        "显示非官方旁证",
        value=False,
        help="默认关闭。B2B 模式只使用官方公开排名或客户授权销量数据。",
    )

    from data.skin_repository import DEFAULT_DB_PATH

    db_text = st.sidebar.text_input("SQLite 数据库", value=str(DEFAULT_DB_PATH))
    db_path = Path(db_text)
    if not db_path.exists():
        st.sidebar.error("数据库不存在。先运行采集脚本生成 skins.sqlite3。")
        return db_path, None, None, None, not include_non_official

    query = st.sidebar.text_input("搜索皮肤", value="龙胆")
    rows = search_skins(str(db_path), query, 60)
    if not rows:
        st.sidebar.warning("没有匹配的皮肤。")
        return db_path, None, None, None, not include_non_official

    labels = [f"{row['source_key']} | {row['hero_name']} / {row['skin_name']}" for row in rows]
    selected_label = st.sidebar.selectbox("选择皮肤", labels)
    source_key = str(rows[labels.index(selected_label)]["source_key"])

    mode = st.sidebar.segmented_control(
        "评分证据",
        ["数据库证据", "忽略证据", "手动模拟"],
        default="数据库证据",
    )
    if mode == "手动模拟":
        with st.sidebar.expander("模拟信号", expanded=True):
            st.slider("观感", 0, 100, 78, key="visual_score")
            st.slider("手感", 0, 100, 72, key="feel_score")
            st.slider("品质", 0, 100, 76, key="craftsmanship_score")
            st.slider("收藏", 0, 100, 70, key="collection_score")
            st.slider("性价比", 0, 100, 62, key="value_score")
            st.slider("购买意愿", 0, 100, 74, key="purchase_intent_score")
            st.slider("整体情绪", 0, 100, 78, key="sentiment_score")
            st.number_input("讨论量", min_value=0, value=8000, step=500, key="discussion_count")
            st.number_input("视频播放量", min_value=0, value=1000000, step=50000, key="video_views")
            st.number_input("传播互动量", min_value=0, value=30000, step=1000, key="marketing_volume")
            st.number_input("销量", min_value=0, value=0, step=1000, key="sales_volume")
            st.slider("拥有率 (%)", 0, 100, 0, key="ownership_rate")

    from app import CALIBRATION_MODEL_PATH

    use_calibration = st.sidebar.toggle("加载 ML 校准模型", value=CALIBRATION_MODEL_PATH.exists())
    calibration_model = load_calibration_model() if use_calibration else None
    if use_calibration and calibration_model is None:
        st.sidebar.warning("未找到 outputs/sales_calibration_model.json。")

    signals = load_signals(db_path, source_key, mode)
    return db_path, source_key, signals, calibration_model, not include_non_official


def render_header(payload: dict[str, Any], summary: dict[str, Any]) -> None:
    from app import score_text

    skin = payload["skin"]
    gap = payload["sales_gap"]
    evaluation = payload["evaluation"]
    report = payload["sales_report"]

    st.title(f"{skin['hero_name']} / {skin['skin_name']}")
    st.caption("当前只接入王者荣耀。销量结果来自公开证据代理，不是官方全量销量库。")

    cols = st.columns(6)
    cols[0].metric("皮肤库", f"{summary['skins']}")
    cols[1].metric("官方证据皮肤", f"{summary['sales_evidence_skins']}")
    cols[2].metric("原始评分", score_text(evaluation))
    cols[3].metric("当前评分", gap["score"] if gap["score"] is not None else "N/A")
    cols[4].metric("销量分", gap["sales_score"] if gap["sales_score"] is not None else "N/A")
    cols[5].metric("Gap", gap["gap"] if gap["gap"] is not None else "N/A")

    if "calibrated_score" in gap:
        st.markdown(
            "<div class='risk-note'>当前显示的是 ML 校准分。原始评分仍保留；校准模型只在 32 条公开样例上训练，"
            "不能当成已泛化的销量预测。</div>",
            unsafe_allow_html=True,
        )
    elif gap["sales_score"] is None:
        st.markdown(
            "<div class='risk-note'>当前皮肤没有可用销量证据，只能看评分和缺口，不应做销售结论。</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div class='audit-note'>{report['decision_reason']}</div>",
            unsafe_allow_html=True,
        )


def score_text(evaluation: dict[str, Any]) -> str:
    from app import score_text as _st

    return _st(evaluation)


def render_method_tab(calibration_report: dict[str, Any] | None) -> None:
    st.subheader("系统工作原理")
    st.markdown(
        """
| 步骤 | 输入 | 输出 | 审计点 |
|---|---|---|---|
| 1. 皮肤基础库 | 官方皮肤、英雄、品质、上架时间、获取方式 | `SkinFeatureVector` | 只说明皮肤是什么，不代表销量 |
| 2. 舆情/市场证据 | B站、微博、人工导入维度分、讨论量 | `evaluation_score` | 缺证据时只给官方先验 |
| 3. 销量证据 | 官方公开排名或客户授权销量；非官方旁证需手动开启 | `sales_score` | 必须标明证据类型和链接 |
| 4. 偏差检验 | `score - sales_score` | `gap` 和方向 | 判断模型低估/高估销量 |
| 5. ML 校准 | sales-blind 特征，不含销量字段 | `calibrated_score` | 只作校准层，不覆盖原始评分 |
        """
    )
    st.markdown(
        "<div class='risk-note'>关键限制：现在没有官方全量销量 API。公开榜单是代理证据，样本量仍很小，"
        "所以界面必须把来源、置信度和训练集/验证集结果展示出来。</div>",
        unsafe_allow_html=True,
    )

    if calibration_report:
        base = calibration_report["baseline"]
        calibrated = calibration_report["calibrated"]
        loo = calibration_report["leave_one_out"]
        cols = st.columns(4)
        cols[0].metric("校准样本", calibrated["n"])
        cols[1].metric("训练集超差", f"{calibrated['exceedances']}/{calibrated['n']}")
        cols[2].metric("二项检验 p", calibrated["binomial_p_value_at_target_probability"])
        cols[3].metric("留一超差", f"{loo['exceedances']}/{loo['n']}" if loo.get("available") else "N/A")
        st.write(
            f"Baseline：{base['exceedances']}/{base['n']} 个样本 |gap| > 10，"
            f"MAE={base['mae']}。校准后训练集通过，但 leave-one-out 未通过，说明还需要更多样本。"
        )
    else:
        st.info("未加载校准报告。运行 scripts/calibrate_sales_score.py 可生成 outputs/sales_calibration_report.json。")


def render_dataset_tab(db_path: Path, summary: dict[str, Any], *, official_only: bool = True) -> None:
    from app import all_evidence_rows

    st.subheader("数据审计")
    cols = st.columns(5)
    cols[0].metric("游戏数", summary["implemented_games"])
    cols[1].metric("英雄", summary["heroes"])
    cols[2].metric("皮肤", summary["skins"])
    cols[3].metric("有官方证据皮肤", summary["sales_evidence_skins"])
    cols[4].metric("官方证据条目", summary["sales_evidence_items"])

    st.markdown(
        "<div class='audit-note'>这不是生产级样本量。当前销量校准样本是公开证据样例；"
        "要服务销量，需要持续扩充到每个游戏数百条、每条有可追溯来源。</div>",
        unsafe_allow_html=True,
    )

    if official_only:
        st.info("B2B 官方证据模式：当前只统计 official_public_rank / official_exact_sales；非官方旁证默认隐藏。")
    else:
        st.warning("已显示非官方旁证。该模式只能用于探索，不应直接作为 B2B 销量验证口径。")

    left, right = st.columns(2)
    with left:
        st.write("证据类型")
        st.dataframe(
            [{"basis": key, "count": value} for key, value in summary["basis_counts"].items()],
            hide_index=True,
            use_container_width=True,
        )
    with right:
        st.write("平台来源")
        st.dataframe(
            [{"platform": key, "count": value} for key, value in summary["platform_counts"].items()],
            hide_index=True,
            use_container_width=True,
        )

    st.write("官方公开证据明细")
    st.dataframe(
        evidence_table(all_evidence_rows(db_path, official_only=official_only, limit=300)),
        hide_index=True,
        use_container_width=True,
    )


def render_gap_tab(payload: dict[str, Any]) -> None:
    from app import flatten_gap_evidence

    gap = payload["sales_gap"]
    cols = st.columns(5)
    cols[0].metric("Base score", gap.get("base_score", gap["score"]))
    cols[1].metric("Displayed score", gap["score"] if gap["score"] is not None else "N/A")
    cols[2].metric("Sales score", gap["sales_score"] if gap["sales_score"] is not None else "N/A")
    cols[3].metric("Gap", gap["gap"] if gap["gap"] is not None else "N/A")
    cols[4].metric("Evidence confidence", f"{gap['confidence']:.2f}")

    st.write(f"方向：`{gap['gap_direction']}`")
    st.write(gap["interpretation"])
    if gap.get("sales_evidence"):
        st.write("当前采用的销量证据")
        st.dataframe(evidence_table([flatten_gap_evidence(gap)]), hide_index=True, use_container_width=True)
    st.write("警告")
    for warning in gap["warnings"] or ["none"]:
        st.write(f"- `{warning}`")


def flatten_gap_evidence(gap: dict[str, Any]) -> dict[str, Any]:
    from app import metric_summary

    evidence = gap["sales_evidence"]
    return {
        "source_key": gap["source_key"],
        "platform": "sales_public",
        "title": evidence.get("source_title"),
        "url": evidence.get("source_url"),
        "basis": evidence.get("basis"),
        "confidence": evidence.get("confidence"),
        "metric_summary": metric_summary(
            {
                "estimated_sales_volume": evidence.get("volume"),
                "sales_rank": evidence.get("rank"),
                "rank_size": evidence.get("rank_size"),
                "source_confidence": evidence.get("confidence"),
            }
        ),
    }


def render_current_sources_tab(payload: dict[str, Any]) -> None:

    st.subheader("当前皮肤证据")
    items = payload["evidence_items"]
    if not items:
        st.warning("当前皮肤没有证据条目。")
        return
    rows = []
    for item in items:
        metrics = item.get("metrics") or {}
        rows.append(
            {
                "source_key": item["source_key"],
                "platform": item["platform"],
                "title": item.get("title"),
                "url": item.get("url"),
                "basis": classify_sales_basis(metrics),
                "confidence": metrics.get("source_confidence"),
                "metric_summary": metric_summary(metrics),
            }
        )
    st.dataframe(evidence_table(rows), hide_index=True, use_container_width=True)


def evidence_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source_key": row.get("source_key"),
            "platform": row.get("platform"),
            "basis": row.get("basis"),
            "confidence": row.get("confidence"),
            "title": row.get("title"),
            "url": row.get("url"),
            "metrics": row.get("metric_summary"),
        }
        for row in rows
    ]


def render_sales_tab(payload: dict[str, Any]) -> None:
    from app import _display_number, _display_percent

    report = payload["sales_report"]
    left, right = st.columns([1, 1])
    with left:
        st.subheader("购买驱动力")
        for item in report["purchase_drivers"]:
            st.info(item)
    with right:
        st.subheader("转化阻力")
        for item in report["conversion_blockers"] or ["暂无明确阻力。"]:
            st.warning(item)

    st.subheader("建议动作")
    for item in report["recommended_actions"]:
        st.write(f"**{item['action']}**")
        st.write(item["detail"])

    pricing = report["pricing_guidance"]
    st.subheader("价格动作")
    st.write(f"**{pricing['posture']}**")
    st.write(pricing["rationale"])
    if pricing.get("official_price_text"):
        st.caption(f"官方价格文本：{pricing['official_price_text']}")

    cash = report.get("cash_value") or {}
    resolved = cash.get("resolved")
    st.divider()
    st.subheader("现金价值（与情绪评分分开展示）")
    if not resolved:
        st.info("当前期间没有持久化现金价值证据。")
        return
    cols = st.columns(5)
    revenue_currency = resolved.get("revenue_currency") or "N/A"
    cols[0].metric(f"期间收入归因 ({revenue_currency})", _display_number(resolved.get("attributed_revenue")))
    cols[1].metric("估算件数", _display_number(resolved.get("sales_volume")))
    cols[2].metric("平均获取成本 (CNY)", _display_number(resolved.get("avg_spend_cny")))
    cols[3].metric("收入提升", _display_percent((cash.get("metrics") or {}).get("revenue_lift_percent")))
    cols[4].metric("置信度", _display_number(resolved.get("confidence")))
    st.caption(
        f"方法：{resolved.get('attribution_method')}；成本依据：{resolved.get('spend_basis')}；"
        f"期间：{resolved.get('period_start', 'legacy')} 至 {resolved.get('period_end', 'legacy')}"
    )
    efficiency = (cash.get("metrics") or {}).get("emotional_value_efficiency_per_cny100")
    if efficiency is not None:
        st.write(f"描述性情绪价值效率：每 ¥100 获取成本对应 `{efficiency}` 评分点；这不是货币估值。")
    for warning in cash.get("warnings") or []:
        st.warning(warning)
    if cash.get("daily_chart"):
        st.line_chart(cash["daily_chart"], x="date", y=["baseline", "observed"])


def render_cash_value_tab(db_path: Path, source_key: str, payload: dict[str, Any]) -> None:
    from app import _display_number, import_cash_value_upload, save_manual_cash_value

    st.subheader("期间现金价值证据")
    st.caption("这里保存的证据与侧栏“手动模拟”完全分离；模拟不会写入数据库。")
    today = __import__("datetime").date.today()
    view_period = st.date_input(
        "查看期间", value=(today - timedelta(days=365), today), key="cash_view_period",
    )
    with st.expander("导入 iPhone 收入 CSV", expanded=False):
        uploaded = st.file_uploader("收入 CSV", type=["csv"], key="cash_revenue_csv")
        revenue_currency = st.selectbox("流水币种", ["CNY", "USD"], key="cash_csv_currency")
        market = st.text_input("市场", value="CN", key="cash_csv_market")
        fx = None
        if revenue_currency == "USD":
            fx = st.number_input("CNY / USD", min_value=0.01, value=7.20, step=0.01, key="cash_csv_fx")
        if st.button("导入并运行归因", disabled=uploaded is None):
            try:
                result = import_cash_value_upload(
                    db_path, uploaded.getvalue(), currency=revenue_currency,
                    cny_per_usd=fx, market=market, source_name=uploaded.name,
                )
                st.success(
                    f"读取 {result['import']['rows_read']} 行；生成 "
                    f"{result['attribution']['eligible_records']} 条符合覆盖要求的皮肤估算。"
                )
                st.cache_data.clear()
            except ValueError as exc:
                st.error(str(exc))

    with st.form("manual_cash_value"):
        st.write("持久化人工证据")
        left, right = st.columns(2)
        start = left.date_input("期间开始", value=today - timedelta(days=6))
        end = right.date_input("期间结束", value=today)
        sales_volume = st.number_input("销量（留空请保持 0 并取消下方勾选）", min_value=0, value=0, step=1)
        include_volume = st.checkbox("保存销量字段", value=True)
        relation = st.selectbox("销量关系", ["exact", "estimated"])
        avg_spend = st.number_input("平均获取花费", min_value=0.0, value=0.0, step=0.1)
        include_spend = st.checkbox("保存平均花费字段", value=True)
        currency = st.selectbox("花费币种", ["CNY", "USD"])
        manual_fx = st.number_input("CNY / USD（USD 时必填）", min_value=0.01, value=7.20, step=0.01)
        confidence = st.slider("人工置信度", 0.0, 1.0, 1.0, 0.05)
        notes = st.text_area("备注")
        submitted = st.form_submit_button("保存人工证据")
        if submitted:
            try:
                save_manual_cash_value(db_path, source_key, {
                    "period_start": start.isoformat(), "period_end": end.isoformat(),
                    "sales_volume": int(sales_volume) if include_volume else None,
                    "volume_relation": relation,
                    "avg_spend": float(avg_spend) if include_spend else None,
                    "currency": currency, "cny_per_usd": manual_fx if currency == "USD" else None,
                    "confidence": confidence, "notes": notes,
                })
                st.success("人工现金价值证据已保存。")
                st.cache_data.clear()
            except ValueError as exc:
                st.error(str(exc))

    if isinstance(view_period, tuple) and len(view_period) == 2:
        view_start, view_end = view_period
        cash = CashValueService(db_path).cash_value(
            source_key, view_start.isoformat(), view_end.isoformat(),
            evaluation_score=payload["evaluation"].get("evaluation_score"),
            legacy_signals=MarketSignalRepository(db_path).get_signals(source_key),
        )
    else:
        cash = payload["sales_report"].get("cash_value") or {}
    resolved = cash.get("resolved")
    if resolved:
        cols = st.columns(4)
        cols[0].metric("期间归因收入", _display_number(resolved.get("attributed_revenue")))
        cols[1].metric("销量/估算件数", _display_number(resolved.get("sales_volume")))
        cols[2].metric("平均花费 CNY", _display_number(resolved.get("avg_spend_cny")))
        cols[3].metric("置信度", _display_number(resolved.get("confidence")))
        st.caption(
            f"{resolved.get('attribution_method')} | {resolved.get('spend_basis')} | "
            f"{resolved.get('period_start', 'legacy')} 至 {resolved.get('period_end', 'legacy')}"
        )
    if cash.get("daily_chart"):
        st.line_chart(cash["daily_chart"], x="date", y=["baseline", "observed"])
    for warning in cash.get("warnings") or []:
        st.warning(warning)
    if cash.get("records"):
        st.write("期间记录与来源")
        st.dataframe(cash["records"], hide_index=True, use_container_width=True)


def render_evaluation_tab(payload: dict[str, Any]) -> None:
    from app import _display_number  # noqa: F401  (kept for symmetry)

    evaluation = payload["evaluation"]
    report = payload["sales_report"]
    aspect_scores = evaluation["aspect_scores"]
    if any(score is not None for score in aspect_scores.values()):
        chart_data = {
            "维度": [ASPECT_LABELS.get(name, name) for name in aspect_scores],
            "分数": [score or 0 for score in aspect_scores.values()],
        }
        st.bar_chart(chart_data, x="维度", y="分数", height=260)
    else:
        st.info("当前皮肤没有可用维度评分。")

    cols = st.columns(2)
    with cols[0]:
        st.subheader("证据状态")
        st.write(f"验证状态：`{evaluation['validation_status']}`")
        st.write(f"证据覆盖：`{evaluation['evidence_coverage']}`")
        st.write("市场字段：")
        st.write(evaluation["evidence"]["market_signal_fields"] or "无")
    with cols[1]:
        st.subheader("缺失证据")
        for gap in report["evidence_gaps"] or ["none"]:
            st.write(f"- `{gap}`")


def render_game_scope_tab(summary: dict[str, Any]) -> None:
    st.subheader("游戏扩展状态")
    st.markdown(
        "<div class='risk-note'>当前没有做跨游戏通用评分。不同游戏的价格体系、稀缺机制、抽卡/直售、"
        "二级市场和舆情空间都不同，必须先接入各自的数据适配器。</div>",
        unsafe_allow_html=True,
    )
    rows = [
        {
            "game_scope": "王者荣耀",
            "status": "已接入本地皮肤库",
            "current_data": f"{summary['skins']} skins / {summary['sales_evidence_skins']} with official evidence",
            "next_requirement": "补真实销量、拥有率、舆情维度评分",
        },
        {
            "game_scope": "新 MOBA / 射击 / 抽卡游戏",
            "status": "未接入",
            "current_data": "0 production samples",
            "next_requirement": "实现游戏元数据 crawler、价格/获取方式解析、销量代理证据 importer",
        },
        {
            "game_scope": "跨游戏对比",
            "status": "未开放",
            "current_data": "不能直接比较",
            "next_requirement": "先做游戏内归一化，再做跨游戏层级校准",
        },
    ]
    st.dataframe(rows, hide_index=True, use_container_width=True)


def render_json_tab(payload: dict[str, Any], source_key: str) -> None:
    import json

    st.download_button(
        "下载 JSON",
        data=json.dumps(payload, ensure_ascii=False, indent=2),
        file_name=f"{source_key}-sales-audit.json",
        mime="application/json",
    )
    st.json(payload)


def render_workbench() -> None:
    """Full workbench page body (used by pages/数据工作台.py)."""
    from app import page_config

    page_config()
    db_path, source_key, signals, calibration_model, official_only = render_workbench_sidebar()
    if not source_key or signals is None:
        st.title("EmoGame 数据工作台")
        st.write("请选择一个皮肤开始审计。")
        return

    try:
        payload = build_payload(db_path, source_key, signals, calibration_model, official_only=official_only)
        summary = dataset_summary(str(db_path), official_only=official_only)
        calibration_report = load_calibration_report()
    except ValueError as exc:
        st.error(str(exc))
        return

    render_header(payload, summary)
    tabs = st.tabs([
        "工作原理", "数据审计", "评分/销量偏差", "当前证据", "销售动作",
        "现金价值录入", "评分结构", "游戏扩展", "JSON",
    ])
    with tabs[0]:
        render_method_tab(calibration_report)
    with tabs[1]:
        render_dataset_tab(db_path, summary, official_only=official_only)
    with tabs[2]:
        render_gap_tab(payload)
    with tabs[3]:
        render_current_sources_tab(payload)
    with tabs[4]:
        render_sales_tab(payload)
    with tabs[5]:
        render_cash_value_tab(db_path, source_key, payload)
    with tabs[6]:
        render_evaluation_tab(payload)
    with tabs[7]:
        render_game_scope_tab(summary)
    with tabs[8]:
        render_json_tab(payload, source_key)
