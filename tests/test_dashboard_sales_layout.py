"""Regression coverage for the sales layout and its read-only query interface."""

import copy
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from dashboard.analysis import build_query_spec, execute_query_spec, summarize_rows
from dashboard.charts import emotion_vs_cash_scatter, release_revenue_timeline
from dashboard.models import DashboardFilters, PortfolioSkinRow
from dashboard.query import get_hero_names, get_portfolio_summary


def overview(db_path):
    from app import _portfolio_overview
    _portfolio_overview(db_path)


def skin(key, hero="甲", score=None, cash=None):
    return PortfolioSkinRow(
        source_key=key, hero_name=hero, skin_name=key, quality="史诗", online_date="2026-01-01",
        emotion_score=score, cash_attributed_revenue=cash,
        emotion_status="scored" if score is not None else "missing",
        cash_status="has_record" if cash is not None else "missing",
    )


class SalesAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.spec = build_query_spec(DashboardFilters(), "hero_name",
                                     ["skin_count", "mean_score", "cash_total", "cash_mean"])

    def test_aggregates_use_numeric_coverage_including_zero_and_negative(self):
        rows = [skin("a", score=0, cash=0), skin("b", score=80, cash=-20),
                skin("c"), skin("d", hero="乙")]
        frame = summarize_rows(rows, self.spec).set_index("英雄")
        self.assertEqual(frame.loc["甲", "平均综合价值分"], 40)
        self.assertEqual(frame.loc["甲", "有收入皮肤平均收入 (CNY)"], -10)
        self.assertEqual(frame.loc["甲", "有 CNY 收入皮肤"], 2)
        self.assertTrue(frame.loc[["乙"], "归因收入合计 (CNY)"].isna().all())

    def test_execution_passes_filters_and_returns_source_keys(self):
        spec = build_query_spec(DashboardFilters(hero_name="甲", period_start=date(2026, 1, 1)),
                                "quality", ["cash_mean"])
        with patch("dashboard.query.get_portfolio_rows", return_value=[skin("a")]) as query:
            result = execute_query_spec("unused.sqlite3", spec)
        self.assertEqual(query.call_args.kwargs["hero_name"], "甲")
        self.assertEqual(query.call_args.kwargs["period_start"], date(2026, 1, 1))
        self.assertEqual(result["source_keys"], ["a"])
        self.assertIsNone(result["rows"][0]["有收入皮肤平均收入 (CNY)"])

    def test_invalid_queries_fail_before_database_access(self):
        variants = [
            {"dataset": "arbitrary_table"}, {"evidence_scope": "official_only"},
            {"metrics": ["DROP TABLE skins"]}, {"metrics": ["skin_count", "skin_count"]},
            {"group_by": "unknown"}, {"filters": {"db_path": "/tmp/other.db"}},
            {"filters": {"period_start": "2026-02-30"}},
            {"filters": {"period_start": "2026-02-01", "period_end": "2026-01-01"}},
            {"filters": {"online_from": "2026-02-01", "online_to": "2026-01-01"}},
            {"filters": {"hero_name": ["甲"]}},
        ]
        for updates in variants:
            with self.subTest(updates=updates):
                spec = copy.deepcopy(self.spec)
                spec.update(updates)
                with patch("dashboard.query.get_portfolio_rows") as query:
                    with self.assertRaises(ValueError):
                        execute_query_spec("unused.sqlite3", spec)
                    query.assert_not_called()

    def test_missing_and_partial_database_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.sqlite3"
            self.assertEqual(execute_query_spec(path, self.spec)["rows"], [])
            self.assertEqual(get_hero_names(path), [])
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE unrelated (id INTEGER)")
            before = path.read_bytes()
            self.assertEqual(execute_query_spec(path, self.spec)["rows"], [])
            self.assertEqual(get_hero_names(path), [])
            self.assertEqual(path.read_bytes(), before)

    def test_scatter_omits_missing_cash_but_keeps_zero(self):
        with patch("dashboard.charts.render_sales_chart") as render:
            emotion_vs_cash_scatter([skin("zero", score=0, cash=0), skin("gap", score=80)])
        trace = render.call_args.args[0].data[0]
        self.assertEqual(list(trace.x), [0])
        self.assertEqual(list(trace.y), [0])

    def test_timeline_separates_currency_and_preserves_missing_days(self):
        revenue = [
            {"revenue_date": "2026-01-01", "estimated_revenue": 100, "currency": "CNY"},
            {"revenue_date": "2026-01-03", "estimated_revenue": 200, "currency": "CNY"},
            {"revenue_date": "2026-01-01", "estimated_revenue": 9, "currency": "USD"},
        ]
        with patch("dashboard.charts.render_sales_chart") as render:
            release_revenue_timeline([{"date": "2026-01-01"}], revenue)
        self.assertEqual(render.call_count, 2)
        figure = render.call_args_list[0].args[0]
        self.assertNotEqual(figure.data[0].yaxis, figure.data[1].yaxis)
        self.assertNotEqual(figure.data[0].y[1], figure.data[0].y[1])  # NaN gap


class SalesOverviewTests(unittest.TestCase):
    def test_empty_and_partial_database_render_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skins.sqlite3"
            for partial in (False, True):
                with self.subTest(partial=partial):
                    if partial:
                        with sqlite3.connect(path) as conn:
                            conn.execute("CREATE TABLE unrelated (id INTEGER)")
                    before = path.read_bytes() if partial else None
                    at = AppTest.from_function(overview, args=(str(path),)).run(timeout=30)
                    self.assertFalse(at.exception)
                    self.assertTrue(at.warning)
                    self.assertEqual(path.read_bytes() if path.exists() else None, before)

    def test_overview_kpis_ranking_filters_and_empty_metrics(self):
        rows = [skin("a", score=80, cash=0), skin("b", score=60), skin("c", hero="乙", cash=100)]
        with (
            patch("dashboard.query.get_portfolio_rows", return_value=rows) as query,
            patch("dashboard.query.get_portfolio_summary", return_value=get_portfolio_summary(rows)),
            patch("dashboard.query.get_release_revenue_timeline", return_value=([], [])),
            patch("dashboard.filters.get_hero_names", return_value=["甲", "乙"]),
        ):
            at = AppTest.from_function(overview, args=("missing.sqlite3",)).run(timeout=30)
            self.assertFalse(at.exception)
            metrics = {item.label: item.value for item in at.metric}
            self.assertEqual(metrics["归因收入合计"], "CNY 100.00")
            self.assertEqual(metrics["平均综合价值分"], "70.00")
            self.assertEqual(metrics["有收入皮肤平均收入"], "CNY 50.00")
            self.assertTrue(at.dataframe)
            self.assertEqual(query.call_args.kwargs["hero_name"], None)

            def ranked_keys():
                return next(
                    table.value["Source key"].tolist()
                    for table in at.dataframe if "排名" in table.value.columns
                )

            ranking = next(radio for radio in at.radio if radio.label == "排行指标")
            self.assertEqual(ranking.options, ["综合价值分", "估算归因收入"])
            self.assertEqual(ranked_keys(), ["a", "b"])
            ranking.set_value("cash").run()
            self.assertFalse(at.exception)
            self.assertEqual(ranked_keys(), ["c", "a"])
            ranking.set_value("emotion").run()
            self.assertFalse(at.exception)
            self.assertEqual(ranked_keys(), ["a", "b"])

    def test_overview_reversed_period_does_not_query(self):
        with patch("dashboard.query.get_portfolio_rows") as query:
            at = AppTest.from_function(overview, args=("missing.sqlite3",))
            at.session_state["dash_period_start"] = date(2026, 2, 1)
            at.session_state["dash_period_end"] = date(2026, 1, 1)
            at.run(timeout=30)
        self.assertFalse(at.exception)
        self.assertTrue(at.error)
        query.assert_not_called()
