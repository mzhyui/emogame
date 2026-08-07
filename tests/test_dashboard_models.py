"""Unit tests for the dashboard read models and batched query layer.

Verifies the three value objects stay independent, cash priority is honoured,
CNY-convertible revenue is summed correctly, missing/empty DBs never crash, and
unvalidated emotional records never enter the leaderboard.
"""

import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from crawlers.wzry_skin_crawler import HeroRecord, SkinRecord, ensure_schema, save_hero, save_skin
from data.cash_value import CashValueRepository, CashValueService
from data.market_signal_repository import MarketSignalRepository
from feature_engineering.features import MarketValidationSignals

from dashboard.models import CashStatus, EmotionStatus
from dashboard.query import (
    default_period,
    get_coverage_gaps,
    get_portfolio_rows,
    get_portfolio_summary,
    get_release_revenue_timeline,
)


class DashboardQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "skins.sqlite3"
        conn = sqlite3.connect(self.db_path)
        ensure_schema(conn)
        save_hero(conn, HeroRecord("1", "测试英雄", "test", "测试", 1, "战士", [], {}))
        conn.commit()
        conn.close()
        self.service = CashValueService(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def add_skin(self, key: str, release: date, *, quality: str = "史诗",
                 acquire: str = "商城直售获取", price: str | None = "888点券") -> None:
        conn = sqlite3.connect(self.db_path)
        save_skin(conn, SkinRecord(
            source_key=key, source_index=int(key.split("-")[-1]), hero_id="1",
            hero_name="测试英雄", skin_index=int(key.split("-")[-1]), skin_id=key,
            skin_name=f"皮肤{key}", quality=quality, online_date=release.isoformat(),
            intro="", acquire_method=acquire, price_text=price, image_url="",
            detail_url="", mobile_url="", video_id="", catalog_source="test",
            detail_source="test", has_detail_record=True, raw_catalog_json={},
            raw_detail_json={},
        ), None)
        conn.commit()
        conn.close()

    def save_cash(self, source_key: str, method: str, *, currency: str = "CNY",
                  revenue: float = 1000.0, volume: int = 10, confidence: float = 0.5) -> None:
        repo = CashValueRepository(self.db_path)
        if method in ("manual_exact", "manual_estimated"):
            relation = "exact" if method == "manual_exact" else "estimated"
            repo.save_manual_record(source_key, {
                "period_start": "2026-01-01", "period_end": "2026-01-02",
                "sales_volume": volume, "volume_relation": relation,
                "avg_spend": 88.8, "currency": currency, "confidence": confidence,
            })
        else:
            repo.upsert_csv_record({
                "source_key": source_key, "period_start": "2026-01-01",
                "period_end": "2026-01-02", "sales_volume": volume,
                "volume_relation": "estimated", "avg_spend_original": 88.8,
                "spend_currency": "CNY", "avg_spend_cny": 88.8,
                "spend_basis": "csv_release_window", "attributed_revenue": revenue,
                "revenue_currency": currency, "attribution_method": method,
                "confidence": confidence, "provenance_json": "{}",
                "import_batch": "test-batch",
            })

    def test_portfolio_rows_merge_skin_cash_emotion(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.add_skin("1-2", date(2026, 2, 1))
        self.add_skin("1-3", date(2026, 3, 1))
        self.save_cash("1-1", "csv_release_window_uplift", revenue=5000.0)
        # Only value_score set -> aspect coverage < 0.5 -> NOT validated by the
        # real RuleEngine gate. Emotion stays missing with no score.
        MarketSignalRepository(self.db_path).upsert_signals(
            "1-1", MarketValidationSignals(value_score=80), signal_source="manual")
        # Full aspect set (>= 0.5 coverage) -> validated, scores populated.
        MarketSignalRepository(self.db_path).upsert_signals(
            "1-3",
            MarketValidationSignals(
                visual_score=0.8, feel_score=0.7, craftsmanship_score=0.75,
                collection_score=0.6, value_score=0.7, purchase_intent_score=0.65,
                sentiment_score=0.8),
            signal_source="manual")

        rows = get_portfolio_rows(self.db_path)
        self.assertEqual(len(rows), 3)
        by_key = {r.source_key: r for r in rows}
        self.assertIn("1-1", by_key)
        # 1-1: cash present, but sparse signals -> emotion missing.
        self.assertEqual(by_key["1-1"].cash_status, CashStatus.HAS_RECORD.value)
        self.assertEqual(by_key["1-1"].cash_attributed_revenue, 5000.0)
        self.assertEqual(by_key["1-1"].emotion_status, EmotionStatus.MISSING.value)
        self.assertIsNone(by_key["1-1"].emotion_score)
        self.assertIsNone(by_key["1-1"].perceived_value)
        # 1-3: full aspects -> validated with populated emotion + perceived value.
        self.assertEqual(by_key["1-3"].emotion_status, EmotionStatus.VALIDATED.value)
        self.assertIsNotNone(by_key["1-3"].emotion_score)
        self.assertIsNotNone(by_key["1-3"].perceived_value)
        # skin 1-2 has neither cash nor emotion
        self.assertEqual(by_key["1-2"].cash_status, CashStatus.MISSING.value)
        self.assertEqual(by_key["1-2"].emotion_status, EmotionStatus.MISSING.value)

    def test_cash_priority_manual_over_csv(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.save_cash("1-1", "csv_release_window_uplift", revenue=5000.0)
        self.save_cash("1-1", "manual_exact", revenue=9999.0)

        rows = get_portfolio_rows(self.db_path)
        row = rows[0]
        # Manual exact must win over CSV attribution (priority 0 < 2).
        # Manual records carry no attributed_revenue, so assert the method.
        self.assertEqual(row.cash_method, "manual_exact")
        self.assertEqual(row.cash_avg_spend_cny, 88.8)

    def test_fx_cny_only_counts(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.add_skin("1-2", date(2026, 1, 1))
        self.save_cash("1-1", "csv_release_window_uplift", currency="CNY", revenue=4000.0)
        self.save_cash("1-2", "csv_release_window_uplift", currency="USD", revenue=8000.0)

        rows = get_portfolio_rows(self.db_path)
        by_key = {r.source_key: r for r in rows}
        # CNY record contributes to portfolio total; USD record does not.
        self.assertEqual(by_key["1-1"].cash_attributed_revenue, 4000.0)
        self.assertIsNone(by_key["1-2"].cash_attributed_revenue)
        self.assertEqual(by_key["1-2"].cash_revenue_currency, "USD")

        summary = get_portfolio_summary(rows)
        self.assertEqual(summary.portfolio_attributed_revenue_cny, 4000.0)

    def test_date_range_and_missing_filters(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.add_skin("1-2", date(2026, 6, 1))
        self.add_skin("1-3", date(2026, 12, 1))

        # Only skins released in H1 2026.
        rows = get_portfolio_rows(
            self.db_path, online_from=date(2026, 1, 1), online_to=date(2026, 6, 30))
        self.assertEqual({r.source_key for r in rows}, {"1-1", "1-2"})

        # cash_coverage filter
        self.save_cash("1-1", "csv_release_window_uplift", revenue=100.0)
        rows = get_portfolio_rows(self.db_path, cash_coverage="has_record")
        self.assertEqual({r.source_key for r in rows}, {"1-1"})
        rows = get_portfolio_rows(self.db_path, cash_coverage="missing")
        self.assertEqual({r.source_key for r in rows}, {"1-2", "1-3"})

    def test_empty_and_missing_db_does_not_crash(self):
        empty_rows = get_portfolio_rows(self.db_path, search="nope")
        self.assertEqual(empty_rows, [])
        summary = get_portfolio_summary(empty_rows)
        self.assertEqual(summary.total_skins, 0)

        missing = get_portfolio_rows(Path(self.tmp.name) / "ghost.sqlite3")
        self.assertEqual(missing, [])

    def test_unvalidated_not_ranked(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.save_cash("1-1", "csv_release_window_uplift", revenue=3000.0)
        # No market_signal_records entry -> not validated, must not be counted.
        rows = get_portfolio_rows(self.db_path)
        self.assertEqual(rows[0].emotion_status, EmotionStatus.MISSING.value)
        summary = get_portfolio_summary(rows)
        self.assertEqual(summary.validated_emotion_count, 0)
        self.assertEqual(summary.cash_count, 1)
        # Cash still counts toward completeness/gaps independently.
        gaps = get_coverage_gaps(rows)
        self.assertEqual(gaps["missing_emotion"], 1)
        self.assertEqual(gaps["missing_cash"], 0)

    def test_missing_value_sorting(self):
        self.add_skin("1-1", date(2026, 1, 1))
        self.add_skin("1-2", date(2026, 1, 1))
        self.add_skin("1-3", date(2026, 1, 1))
        # Only 1-2 has cash; 1-1 and 1-3 are missing.
        self.save_cash("1-2", "csv_release_window_uplift", revenue=200.0)
        rows = get_portfolio_rows(self.db_path)
        # Sort by attributed revenue; missing (None) must come after real values.
        sorted_rows = sorted(
            rows, key=lambda r: (r.cash_attributed_revenue is None, r.cash_attributed_revenue))
        self.assertEqual(sorted_rows[0].cash_attributed_revenue, 200.0)
        self.assertIsNone(sorted_rows[-1].cash_attributed_revenue)

    def test_default_period_ends_at_latest_revenue(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        values = {release - timedelta(days=n): 100.0 for n in range(1, 29)}
        values.update({release + timedelta(days=n): 200.0 for n in range(7)})
        self.service.import_revenue_csv(
            ("日期,收入预估~iPhone\n"
             + "".join(f"{d.isoformat()},{v}\n" for d, v in sorted(values.items()))).encode())
        start, end = default_period(self.db_path)
        # Latest revenue date is release+6 days; start is 365 days before that.
        self.assertEqual(end, release + timedelta(days=6))
        self.assertEqual(start, end - timedelta(days=365))

    def test_cash_not_injected_into_emotion(self):
        """A cash-derived sales_volume must never validate an emotion score."""
        self.add_skin("1-1", date(2026, 1, 1))
        # Persist a cash record (sales_volume) but NO opinion/aspect signals.
        self.save_cash("1-1", "csv_release_window_uplift", revenue=3000.0, volume=500)
        rows = get_portfolio_rows(self.db_path)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        # Cash is present...
        self.assertEqual(row.cash_status, CashStatus.HAS_RECORD.value)
        self.assertEqual(row.cash_attributed_revenue, 3000.0)
        # ...but emotion stays missing; cash never validates it.
        self.assertEqual(row.emotion_status, EmotionStatus.MISSING.value)
        self.assertIsNone(row.emotion_score)
        self.assertIsNone(row.perceived_value)

    def test_period_scoped_cash(self):
        """Only the cash record overlapping the selected period backs the KPI."""
        self.add_skin("1-1", date(2026, 1, 1))
        repo = CashValueRepository(self.db_path)
        # Record inside the window 2026-02-01..2026-02-28.
        repo.save_manual_record("1-1", {
            "period_start": "2026-02-01", "period_end": "2026-02-28",
            "sales_volume": 10, "volume_relation": "exact",
            "avg_spend": 88.8, "currency": "CNY", "confidence": 0.5,
        })
        # Record OUTSIDE the window.
        repo.save_manual_record("1-1", {
            "period_start": "2026-05-01", "period_end": "2026-05-31",
            "sales_volume": 99, "volume_relation": "exact",
            "avg_spend": 88.8, "currency": "CNY", "confidence": 0.5,
        })
        rows = get_portfolio_rows(
            self.db_path,
            period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        )
        self.assertEqual(len(rows), 1)
        # Manual record has no attributed_revenue; the period-scoped resolve must
        # still pick the in-window record by sales_volume.
        self.assertIsNone(rows[0].cash_attributed_revenue)
        self.assertEqual(rows[0].cash_sales_volume, 10)
        # Outside the window -> no cash resolves.
        rows_out = get_portfolio_rows(
            self.db_path,
            period_start=date(2026, 6, 1), period_end=date(2026, 6, 30),
        )
        self.assertEqual(rows_out[0].cash_status, CashStatus.MISSING.value)

    def test_usd_excluded_from_cny_aggregate(self):
        """USD revenue counts as a record but is excluded from the CNY aggregate;
        an unconvertible USD record (no positive cny_per_usd) stays excluded."""
        self.add_skin("1-1", date(2026, 1, 1))
        self.add_skin("1-2", date(2026, 1, 1))
        self.save_cash("1-1", "csv_release_window_uplift", currency="CNY", revenue=4000.0)
        # USD row with a valid FX rate -> still excluded from CNY aggregate.
        CashValueRepository(self.db_path).upsert_csv_record({
            "source_key": "1-2", "period_start": "2026-01-01",
            "period_end": "2026-12-31", "sales_volume": 5,
            "volume_relation": "estimated", "avg_spend_original": 88.8,
            "spend_currency": "USD", "avg_spend_cny": 640.0,
            "spend_basis": "csv_release_window", "attributed_revenue": 8000.0,
            "revenue_currency": "USD", "attribution_method": "csv_release_window_uplift",
            "confidence": 0.5, "provenance_json": "{}", "import_batch": "b",
        })
        rows = get_portfolio_rows(self.db_path)
        by_key = {r.source_key: r for r in rows}
        self.assertEqual(by_key["1-1"].cash_attributed_revenue, 4000.0)
        self.assertIsNone(by_key["1-2"].cash_attributed_revenue)
        self.assertEqual(by_key["1-2"].cash_revenue_currency, "USD")
        summary = get_portfolio_summary(rows)
        self.assertEqual(summary.portfolio_attributed_revenue_cny, 4000.0)

    def test_missing_db_fails_closed(self):
        """Reads against a non-existent DB return empty and create NO file."""
        ghost = Path(self.tmp.name) / "ghost.sqlite3"
        self.assertFalse(ghost.exists())
        rows = get_portfolio_rows(ghost)
        self.assertEqual(rows, [])
        releases, revenue = get_release_revenue_timeline(ghost)
        self.assertEqual(releases, [])
        self.assertEqual(revenue, [])
        # Critically: no file was created by the read path.
        self.assertFalse(ghost.exists())

    def test_partial_db_no_skins_table(self):
        """A partial DB (value records but no skins table) fails closed, no create."""
        partial = Path(self.tmp.name) / "partial.sqlite3"
        conn = sqlite3.connect(partial)
        # Only the cash table exists; skins table is absent.
        conn.execute(
            "CREATE TABLE skin_value_records (value_id INTEGER PRIMARY KEY, "
            "source_key TEXT)"
        )
        conn.commit()
        conn.close()
        self.assertTrue(partial.exists())
        rows = get_portfolio_rows(partial)
        self.assertEqual(rows, [])
        releases, revenue = get_release_revenue_timeline(partial)
        self.assertEqual(releases, [])
        # The read path must not have created a skins table.
        conn = sqlite3.connect(partial)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        conn.close()
        self.assertNotIn("skins", tables)


if __name__ == "__main__":
    unittest.main()
