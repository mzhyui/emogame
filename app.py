"""EmoGame 分析看板 — 组合总览入口 + Streamlit 多页导航。

本文件是门户的默认入口（组合总览），并通过 ``st.navigation`` 挂载皮肤探索、
皮肤详情、溢价雷达和数据工作台页面。

兼容性红线：``build_payload``、``import_cash_value_upload``、``save_manual_cash_value``、
``load_signals``、``apply_calibration`` 等被现有测试直接 import，签名保持不变。
工作台的全部渲染逻辑已迁至 ``dashboard/workbench_render.py``。
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

from business.sales_advisor import SalesAdvisor
from dashboard.premium_radar import (
    PremiumRadarBundle,
    PremiumRadarBundleError,
    load_premium_radar_bundle,
)
from data.cash_value import CashValueService
from data.market_signal_repository import OFFICIAL_EVIDENCE_PLATFORMS, MarketSignalRepository
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from data.sqlite_read import connect_readonly, table_exists
from feature_engineering.features import MarketValidationSignals
from feature_engineering.pipeline import FeatureBuilder
from models.rule_engine import RuleEngine
from models.sales_calibration import RbfSalesCalibrator, calibration_features
from models.sales_deviation import compare_score_to_sales, sales_blind_signals


CALIBRATION_MODEL_PATH = Path("outputs/sales_calibration_model.json")
CALIBRATION_REPORT_PATH = Path("outputs/sales_calibration_report.json")
PREMIUM_RADAR_RUN_DIR = (
    Path(__file__).resolve().parent
    / "data/premium_pilot/runs/20260831-seed42-social-v1"
)

ASPECT_LABELS = {
    "visual_appeal": "观感",
    "in_game_feel": "手感",
    "craftsmanship_quality": "品质",
    "collection_value": "收藏",
    "value_for_money": "性价比",
    "purchase_intent": "购买意愿",
    "market_heat": "市场热度",
}


def page_config() -> None:
    st.set_page_config(
        page_title="EmoGame 分析看板",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; padding-bottom: 2rem; max-width: 1500px; }
        [data-testid="stMetric"] {
            border: 1px solid #e6e8ef;
            padding: 10px 12px;
            border-radius: 10px;
            border-top: 3px solid #0083B8;
            background: var(--secondary-background-color);
        }
        h1 { letter-spacing: -0.035em; }
        [data-testid="stSidebar"] { border-right: 1px solid #e6e8ef; }
        .small-muted { color: #667085; font-size: 0.86rem; }
        .audit-note {
            border-left: 4px solid #4b5563;
            background: #f8fafc;
            padding: 0.75rem 0.9rem;
            margin: 0.5rem 0 1rem;
        }
        .risk-note {
            border-left: 4px solid #b45309;
            background: #fffbeb;
            padding: 0.75rem 0.9rem;
            margin: 0.5rem 0 1rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_data(show_spinner=False)
def search_skins(db_path: str, query: str, limit: int) -> list[dict[str, Any]]:
    path = Path(db_path)
    if not path.exists():
        return []
    repo = SkinRepository(path)
    return repo.search_skins(query, limit=limit) if query else repo.list_skins(limit=limit)


@st.cache_data(show_spinner=False)
def dataset_summary(db_path: str, *, official_only: bool = True) -> dict[str, Any]:
    path = Path(db_path)
    if not path.exists():
        return empty_summary()

    skin_repo = SkinRepository(path)
    market_repo = MarketSignalRepository(path)
    stats = skin_repo.stats()
    evidence_rows = all_evidence_rows(path, official_only=official_only)
    basis_counts = Counter(classify_sales_basis(row["metrics"]) for row in evidence_rows)
    platform_counts = Counter(row["platform"] for row in evidence_rows)
    sales_source_keys = market_repo.list_source_keys_with_sales_evidence(official_only=official_only)

    return {
        "game": "王者荣耀",
        "implemented_games": 1,
        "candidate_games": 0,
        "skins": stats["skins"],
        "heroes": stats["heroes"],
        "assets": stats["assets"],
        "skins_with_detail": stats["with_detail"],
        "skins_missing_detail": stats["missing_detail"],
        "sales_evidence_skins": len(sales_source_keys),
        "sales_evidence_items": len(evidence_rows),
        "basis_counts": dict(sorted(basis_counts.items())),
        "platform_counts": dict(sorted(platform_counts.items())),
        "source_keys": sales_source_keys,
        "evidence_scope": "official_only" if official_only else "all_public_evidence",
    }


def empty_summary() -> dict[str, Any]:
    return {
        "game": "王者荣耀",
        "implemented_games": 1,
        "candidate_games": 0,
        "skins": 0,
        "heroes": 0,
        "assets": 0,
        "skins_with_detail": 0,
        "skins_missing_detail": 0,
        "sales_evidence_skins": 0,
        "sales_evidence_items": 0,
        "basis_counts": {},
        "platform_counts": {},
        "source_keys": [],
        "evidence_scope": "official_only",
    }


def all_evidence_rows(
    db_path: Path,
    *,
    source_key: str | None = None,
    official_only: bool = True,
    limit: int = 500,
) -> list[dict[str, Any]]:
    query = """
        SELECT source_key, platform, external_id, url, title, author,
               published_at, metrics_json, collected_at
        FROM opinion_evidence_items
    """
    params: list[Any] = []
    where: list[str] = []
    if source_key:
        where.append("source_key = ?")
        params.append(source_key)
    if official_only:
        placeholders = ", ".join("?" for _ in OFFICIAL_EVIDENCE_PLATFORMS)
        where.append(f"platform IN ({placeholders})")
        params.extend(OFFICIAL_EVIDENCE_PLATFORMS)
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY collected_at DESC, evidence_id DESC LIMIT ?"
    params.append(limit)

    try:
        with connect_readonly(db_path) as conn:
            if not table_exists(conn, "opinion_evidence_items"):
                return []
            rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    except sqlite3.Error:
        return []

    for row in rows:
        row["metrics"] = json.loads(row.pop("metrics_json") or "{}")
        row["basis"] = classify_sales_basis(row["metrics"])
        row["confidence"] = row["metrics"].get("source_confidence")
        row["metric_summary"] = metric_summary(row["metrics"])
    return rows


def classify_sales_basis(metrics: dict[str, Any]) -> str:
    if any(key in metrics for key in ("sales_volume", "units_sold", "sales")):
        return "exact_volume"
    if any(key in metrics for key in ("estimated_sales_volume", "sales_volume_estimate")):
        return "estimated_volume"
    if any(key in metrics for key in ("sales_rank", "hot_sales_rank", "rank")):
        return "rank_proxy"
    if any(key in metrics for key in ("sales_volume_upper_bound", "sales_upper_bound")):
        return "upper_bound"
    if any(key in metrics for key in ("sales_volume_lower_bound", "sales_lower_bound")):
        return "lower_bound"
    return "non_sales_signal"


def metric_summary(metrics: dict[str, Any]) -> str:
    keys = [
        "sales_volume",
        "estimated_sales_volume",
        "sales_volume_upper_bound",
        "sales_rank",
        "rank_size",
        "source_confidence",
    ]
    parts = [f"{key}={metrics[key]}" for key in keys if key in metrics]
    return ", ".join(parts) if parts else json.dumps(metrics, ensure_ascii=False)


def build_payload(
    db_path: Path,
    source_key: str,
    signals: MarketValidationSignals,
    calibration_model: RbfSalesCalibrator | None = None,
    official_only: bool = True,
) -> dict[str, Any]:
    repo = SkinRepository(db_path)
    market_repo = MarketSignalRepository(db_path)
    features = FeatureBuilder(repo).build(source_key, signals)
    evaluation = RuleEngine().evaluate(features)
    report = SalesAdvisor().advise(features, evaluation)
    evidence = market_repo.list_evidence(source_key, official_only=official_only)

    score_features = FeatureBuilder(repo).build(source_key, sales_blind_signals(signals))
    gap_evaluation = RuleEngine().evaluate(score_features)
    sales_comparison_features = score_features if official_only else features
    sales_gap = compare_score_to_sales(sales_comparison_features, gap_evaluation, evidence)
    if calibration_model and sales_gap["sales_score"] is not None:
        sales_gap = apply_calibration(score_features, gap_evaluation, sales_gap, calibration_model)

    cash_value = CashValueService(db_path).cash_value(
        source_key,
        evaluation_score=evaluation.evaluation_score,
        legacy_signals=market_repo.get_signals(source_key),
    )
    # Surface cash-value as a low-priority sales-evidence candidate for the
    # gap comparison. It never feeds the emotional score (kept sales-blind).
    resolved = cash_value.get("resolved")
    if resolved and resolved.get("attribution_method") != "legacy_aggregate":
        evidence.append(
            {
                "title": "现金价值归因",
                "url": None,
                "cash_value_resolved": resolved,
            }
        )
    sales_report = report.to_dict()
    sales_report["cash_value"] = cash_value
    return {
        "skin": features.to_dict(),
        "evaluation": evaluation.to_dict(),
        "score_evaluation": gap_evaluation.to_dict(),
        "sales_report": sales_report,
        "sales_gap": sales_gap,
        "evidence_items": evidence,
        "evidence_scope": "official_only" if official_only else "all_public_evidence",
    }


def apply_calibration(
    score_features: Any,
    gap_evaluation: Any,
    sales_gap: dict[str, Any],
    calibration_model: RbfSalesCalibrator,
) -> dict[str, Any]:
    calibrated_score = calibration_model.predict(calibration_features(score_features, gap_evaluation))
    calibrated_gap = calibrated_score - int(sales_gap["sales_score"])
    updated = dict(sales_gap)
    updated["base_score"] = sales_gap["score"]
    updated["base_gap"] = sales_gap["gap"]
    updated["calibrated_score"] = calibrated_score
    updated["calibrated_gap"] = calibrated_gap
    updated["score"] = calibrated_score
    updated["score_basis"] = "calibrated_sales_score"
    updated["gap"] = calibrated_gap
    updated["absolute_gap"] = abs(calibrated_gap)
    updated["gap_direction"] = (
        "aligned"
        if abs(calibrated_gap) <= 8
        else "score_above_sales"
        if calibrated_gap > 0
        else "sales_above_score"
    )
    updated["warnings"] = list(updated["warnings"]) + ["calibrated_on_small_public_sample"]
    return updated


def load_calibration_model(path: Path = CALIBRATION_MODEL_PATH) -> RbfSalesCalibrator | None:
    if not path.exists():
        return None
    return RbfSalesCalibrator.from_dict(json.loads(path.read_text(encoding="utf-8-sig")))


def load_calibration_report(path: Path = CALIBRATION_REPORT_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_signals(db_path: Path, source_key: str, mode: str) -> MarketValidationSignals:
    if mode == "忽略证据":
        return MarketValidationSignals()
    if mode == "手动模拟":
        return MarketValidationSignals(
            visual_score=st.session_state.get("visual_score", 70) / 100,
            feel_score=st.session_state.get("feel_score", 70) / 100,
            craftsmanship_score=st.session_state.get("craftsmanship_score", 70) / 100,
            collection_score=st.session_state.get("collection_score", 70) / 100,
            value_score=st.session_state.get("value_score", 70) / 100,
            purchase_intent_score=st.session_state.get("purchase_intent_score", 70) / 100,
            sentiment_score=st.session_state.get("sentiment_score", 70) / 100,
            discussion_count=st.session_state.get("discussion_count", 1000),
            video_views=st.session_state.get("video_views", 100000),
            marketing_volume=st.session_state.get("marketing_volume", 5000),
            sales_volume=st.session_state.get("sales_volume", None) or None,
            ownership_rate=st.session_state.get("ownership_rate", 0) / 100 or None,
        )
    return MarketSignalRepository(db_path).get_signals(source_key)


def import_cash_value_upload(
    db_path: Path,
    content: bytes,
    *,
    currency: str = "CNY",
    cny_per_usd: float | None = None,
    market: str = "CN",
    source_name: str,
    run_attribution: bool = True,
) -> dict[str, Any]:
    """Testable Streamlit adapter around the shared import service."""
    service = CashValueService(db_path)
    imported = service.import_revenue_csv(
        content, currency=currency, cny_per_usd=cny_per_usd,
        market=market, source_name=source_name,
    )
    result: dict[str, Any] = {"import": imported}
    if run_attribution:
        result["attribution"] = service.attribute_releases(
            import_batch=imported["import_batch"], cny_per_usd=cny_per_usd,
        )
    return result


def save_manual_cash_value(db_path: Path, source_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist manual UI evidence; simulation widgets never call this helper."""
    return CashValueService(db_path).repo.save_manual_record(source_key, payload)


# ── Display helpers reused by dashboard/workbench_render.py ──────────────────
def _display_number(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return f"{value:,}"


def _display_percent(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.2f}%"


def score_text(evaluation: dict[str, Any]) -> str:
    score = evaluation["evaluation_score"]
    return str(score) if score is not None else f"{evaluation['official_prior_score']} 先验"


def flatten_gap_evidence(gap: dict[str, Any]) -> dict[str, Any]:
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


# ── Streamlit multipage navigation ───────────────────────────────────────────
@st.cache_data(show_spinner=False)
def load_premium_radar_report(
    run_dir: str, report_mtime_ns: int, metadata_mtime_ns: int, html_mtime_ns: int
) -> PremiumRadarBundle:
    """Load and validate the canonical bundle with all files in the cache key."""
    del report_mtime_ns, metadata_mtime_ns, html_mtime_ns
    return load_premium_radar_bundle(run_dir)


def _premium_radar_panel() -> None:
    """Render the frozen premium-pilot radar report in a scrollable panel."""
    st.title("皮肤溢价雷达")
    st.caption(
        "感知溢价试点的评分与 RuleEngine 的情绪分各自独立计算；收入只作验证参考，"
        "不参与溢价打分。可按英雄、皮肤或 source key 搜索。"
    )

    run_dir = PREMIUM_RADAR_RUN_DIR
    required = [run_dir / name for name in ("report.json", "run_metadata.json", "radar_plots.html")]
    if not all(path.is_file() for path in required):
        st.error(f"规范溢价雷达包不可用：{run_dir}")
        return
    try:
        bundle = load_premium_radar_report(
            str(run_dir), *(path.stat().st_mtime_ns for path in required)
        )
    except (OSError, PremiumRadarBundleError) as exc:
        st.error(f"规范溢价雷达包校验失败：{exc}")
        return

    st.caption(f"运行 ID：{bundle.run_id}")
    metrics = st.columns(3)
    metrics[0].metric("试点皮肤", bundle.selected)
    metrics[1].metric("证据齐全", bundle.complete)
    metrics[2].metric("证据不全", bundle.partial)
    components.html(
        bundle.html,
        height=1_300,
        scrolling=True,
    )


def _portfolio_overview(db_path: str) -> None:
    """Sven-Bo sales-dashboard composition adapted to the skin portfolio."""
    from dataclasses import asdict
    from dashboard import charts, filters
    from dashboard.analysis import GROUPS, METRICS, build_query_spec, summarize_rows
    from dashboard.format import (
        MEAN_SCORE_LABEL,
        SCORE_LABEL,
        display_currency_amount,
        display_number,
        display_percent,
    )
    from dashboard.query import (
        get_coverage_gaps,
        get_portfolio_rows,
        get_portfolio_summary,
        get_release_revenue_timeline,
    )

    filters.render_filter_sidebar(db_path)
    f = filters.current_filters()

    if not f.is_valid_period() or (
        f.online_from and f.online_to and f.online_to < f.online_from
    ):
        st.error("分析期间结束日期早于开始日期，请重新选择期间。")
        return

    with st.spinner("加载组合数据…"):
        rows = get_portfolio_rows(
            db_path,
            **asdict(f),
        )
        summary = get_portfolio_summary(rows, db_path=db_path)
        releases, revenue = get_release_revenue_timeline(
            db_path,
            period_start=f.period_start,
            period_end=f.period_end,
            online_from=f.online_from,
            online_to=f.online_to,
        )

    selected_keys = {row.source_key for row in rows}
    releases = [release for release in releases if release["source_key"] in selected_keys]

    st.title("📊 组合总览")
    st.caption(
        f"王者荣耀 · {summary.total_skins:,} 个皮肤 · "
        f"{len({row.hero_name for row in rows}):,} 位英雄 · "
        f"{f.period_start} 至 {f.period_end}"
    )
    if not rows:
        st.warning("当前筛选条件下没有皮肤数据，请调整侧栏筛选或检查数据是否已导入。")
        return

    cash_values = [row.cash_attributed_revenue for row in rows if row.cash_attributed_revenue is not None]
    scores = [row.emotion_score for row in rows if row.emotion_score is not None]
    total_cash = sum(cash_values, 0.0) if cash_values else None
    average_cash = total_cash / len(cash_values) if cash_values else None
    average_score = sum(scores) / len(scores) if scores else None
    k1, k2, k3 = st.columns(3)
    k1.metric("归因收入合计", display_currency_amount(total_cash, "CNY"))
    k1.caption(f"有 CNY 收入 {len(cash_values)}/{len(rows)} 个皮肤 · 包含估算归因")
    k2.metric(MEAN_SCORE_LABEL, display_number(average_score))
    k2.caption(f"有分值 {len(scores)}/{len(rows)} · 满分 100 · 观察值 + 目录估计")
    k3.metric("有收入皮肤平均收入", display_currency_amount(average_cash, "CNY"))
    k3.caption(f"分母：{len(cash_values)} 个有数值的 CNY 收入皮肤")

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("皮肤品质分布")
        charts.quality_distribution_chart(rows)
    with c2:
        st.subheader(f"{SCORE_LABEL} × 估算现金价值")
        charts.emotion_vs_cash_scatter(rows)

    st.divider()
    st.subheader("皮肤排行")
    # Keep sorting keys independent of user-facing wording.
    ranking_labels = {"emotion": SCORE_LABEL, "cash": "估算归因收入"}
    ranking = st.radio(
        "排行指标", list(ranking_labels),
        format_func=ranking_labels.__getitem__, horizontal=True,
    )
    charts.top_value_ranking(rows, by=ranking)
    st.caption("前 20 名 · 表中保留评分状态、置信区间与现金归因方法。")

    st.divider()
    st.subheader("上线事件与游戏收入时间线")
    charts.release_revenue_timeline(releases, revenue)

    with st.expander("分析问题 · 按英雄或品质比较"):
        group = st.selectbox("比较维度", list(GROUPS), format_func=GROUPS.get)
        metrics = st.multiselect("分析指标", list(METRICS),
                                 default=["skin_count", "mean_score", "cash_total"],
                                 format_func=METRICS.get)
        if metrics:
            spec = build_query_spec(f, group, metrics)
            frame = summarize_rows(rows, spec)
            st.dataframe(frame, hide_index=True, width="stretch")
            st.caption("沿用侧栏范围；均值仅使用有数值皮肤，缺失值保留为空。")
            st.download_button("下载分析表 CSV", frame.to_csv(index=False).encode("utf-8-sig"),
                               "portfolio-analysis.csv", "text/csv")
            with st.expander("查询与来源"):
                st.json(spec)
                st.download_button(
                    "下载查询与来源 JSON",
                    json.dumps({"query": spec, "source_keys": sorted(selected_keys)}, ensure_ascii=False, indent=2),
                    "portfolio-query.json", "application/json",
                )
        else:
            st.info("请选择至少一个分析指标。")

    with st.expander("数据覆盖与评分说明"):
        st.caption(
            f"目录里每张皮肤都会算出{SCORE_LABEL}：优先采用实际观察到的维度，"
            "缺失维度用目录估计补齐；不要求皮肤已上线、通过质检或经人工审核。"
        )
        st.caption(
            f"已评分 {summary.scored_count}/{summary.total_skins} · "
            f"现金记录 {summary.cash_count}/{summary.total_skins} · "
            f"证据覆盖率 {display_percent(summary.evidence_completeness_rate * 100)}"
        )
        if summary.emotion_cohort_run_id:
            st.caption(
                f"观察队列 {summary.emotion_cohort_run_id} · "
                f"{summary.emotion_observation_start} 至 {summary.emotion_observation_end} · "
                f"已评分 {summary.emotion_cohort_scored_count}/{summary.emotion_cohort_size}"
            )
        charts.coverage_gap_chart(summary.coverage_gaps or get_coverage_gaps(rows))


def main() -> None:
    page_config()
    db_path = str(DEFAULT_DB_PATH)

    overview = st.Page(lambda: _portfolio_overview(db_path), title="组合总览", icon="📊")
    explore = st.Page("pages/skin_explorer.py", title="皮肤探索", icon="🔍")
    detail = st.Page("pages/skin_detail.py", title="皮肤详情", icon="🧬")
    radar = st.Page(_premium_radar_panel, title="溢价雷达", icon="🕸️")
    workbench = st.Page("pages/data_workbench.py", title="数据工作台", icon="🛠️")

    pg = st.navigation([overview, explore, detail, radar, workbench])
    pg.run()


if __name__ == "__main__":
    main()
