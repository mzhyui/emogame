import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from crawlers.wzry_skin_crawler import HeroRecord, SkinRecord, ensure_schema, save_hero, save_skin
from data.cash_value import CashValueService
from data.invoice_synthesizer import InvoiceSynthesizer, SyntheticInvoiceRepository, parse_price_cny


def revenue_csv(values: dict[date, float]) -> bytes:
    rows = sorted(values.items(), reverse=True)
    text = "日期,收入预估~iPhone\n" + "".join(f"{day.isoformat()},{amount}\n" for day, amount in rows)
    return ("" + text).encode()


BASE_DAY = date(2026, 1, 1)
WINDOW_START = date(2026, 1, 5)
WINDOW_END = date(2026, 1, 25)
DAILY_TOTAL = 10_000.0


def build_fixture(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    save_hero(conn, HeroRecord("1", "测试英雄", "test", "测试", 1, "战士", [], {}))
    skins = [
        # paid epic released before the window
        SkinRecord(
            source_key="1-1", source_index=1, hero_id="1", hero_name="测试英雄", skin_index=1,
            skin_id="1-1", skin_name="直售史诗", quality="史诗", online_date=(BASE_DAY - timedelta(days=12)).isoformat(),
            intro="", acquire_method="商城直售获取", price_text="888点券", image_url="", detail_url="",
            mobile_url="", video_id="", catalog_source="test", detail_source="test",
            has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
        ),
        # limited legend released mid-window, price falls back to tier default
        SkinRecord(
            source_key="1-2", source_index=2, hero_id="1", hero_name="测试英雄", skin_index=2,
            skin_id="1-2", skin_name="限定传说", quality="传说限定", online_date=date(2026, 1, 15).isoformat(),
            intro="", acquire_method="商城限时直售", price_text=None, image_url="", detail_url="",
            mobile_url="", video_id="", catalog_source="test", detail_source="test",
            has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
        ),
        # free acquisition skin: must never produce invoice volume
        SkinRecord(
            source_key="1-3", source_index=3, hero_id="1", hero_name="测试英雄", skin_index=3,
            skin_id="1-3", skin_name="活动皮肤", quality="勇者", online_date=(BASE_DAY - timedelta(days=60)).isoformat(),
            intro="", acquire_method="限时活动获取", price_text=None, image_url="", detail_url="",
            mobile_url="", video_id="", catalog_source="test", detail_source="test",
            has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
        ),
        # missing quality and release date: treated as active baseline skin
        SkinRecord(
            source_key="1-4", source_index=4, hero_id="1", hero_name="测试英雄", skin_index=4,
            skin_id="1-4", skin_name="默认皮肤", quality="", online_date=None,
            intro="", acquire_method="商城直售获取", price_text=None, image_url="", detail_url="",
            mobile_url="", video_id="", catalog_source="test", detail_source="test",
            has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
        ),
    ]
    for skin in skins:
        save_skin(conn, skin, None)
    conn.commit()
    conn.close()
    values = {BASE_DAY + timedelta(days=offset): DAILY_TOTAL + offset % 7 * 100 for offset in range(40)}
    CashValueService(db_path).import_revenue_csv(revenue_csv(values), source_name="test-revenue.csv")


class InvoiceSynthesizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "skins.sqlite3"
        build_fixture(self.db_path)
        self.synthesizer = InvoiceSynthesizer(self.db_path)
        self.repo = SyntheticInvoiceRepository(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def generate(self, seed: int = 7) -> dict:
        return self.synthesizer.generate(start=WINDOW_START, end=WINDOW_END, seed=seed)

    def row_tuples(self, synth_batch: str) -> list[tuple]:
        return [
            (row["source_key"], row["invoice_date"], row["units_sold"], row["unit_price"],
             row["gross_revenue"], row["discount_pct"], row["quality"], row["release_age_days"])
            for row in self.repo.daily_rows(synth_batch)
        ]

    def test_generation_row_counts_and_daily_reconciliation(self):
        report = self.generate()
        days = (WINDOW_END - WINDOW_START).days + 1
        self.assertEqual(report["row_count"], 4 * days)
        self.assertEqual(report["period_start"], WINDOW_START.isoformat())
        self.assertEqual(report["period_end"], WINDOW_END.isoformat())
        self.assertLessEqual(report["max_daily_deviation"], 0.01)
        reconciliation = self.repo.reconciliation(report["synth_batch"])
        self.assertEqual(len(reconciliation), days)
        for row in reconciliation:
            deviation = abs(row["synthesized_revenue"] - row["baseline_revenue"]) / row["baseline_revenue"]
            self.assertLessEqual(deviation, 0.01, row["invoice_date"])
            self.assertGreater(row["synthesized_revenue"], 0)

    def test_no_positive_units_before_release(self):
        report = self.generate()
        rows = self.repo.daily_rows(report["synth_batch"])
        release = date(2026, 1, 15)
        for row in rows:
            if row["source_key"] == "1-2":
                if date.fromisoformat(row["invoice_date"]) < release:
                    self.assertEqual(row["units_sold"], 0)
                    self.assertEqual(row["gross_revenue"], 0.0)
        launch_units = sum(
            row["units_sold"] for row in rows if row["source_key"] == "1-2" and row["units_sold"] > 0
        )
        self.assertGreater(launch_units, 0)

    def test_free_acquire_skins_never_sell(self):
        report = self.generate()
        rows = [row for row in self.repo.daily_rows(report["synth_batch"]) if row["source_key"] == "1-3"]
        self.assertEqual(len(rows), (WINDOW_END - WINDOW_START).days + 1)
        for row in rows:
            self.assertEqual(row["units_sold"], 0)
            self.assertEqual(row["gross_revenue"], 0.0)
            self.assertEqual(row["price_basis"], "free_or_inactive")

    def test_same_seed_is_deterministic_and_batches_idempotent(self):
        first = self.generate(seed=11)
        first_rows = self.row_tuples(first["synth_batch"])
        second = self.generate(seed=11)
        self.assertEqual(first["synth_batch"], second["synth_batch"])
        self.assertEqual(first_rows, self.row_tuples(second["synth_batch"]))
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM synthetic_invoice_daily WHERE synth_batch = ?",
                (first["synth_batch"],),
            ).fetchone()[0]
        self.assertEqual(count, len(first_rows))

    def test_different_seed_changes_output(self):
        first = self.generate(seed=11)
        first_rows = self.row_tuples(first["synth_batch"])
        second = self.generate(seed=12)
        self.assertNotEqual(first_rows, self.row_tuples(second["synth_batch"]))

    def test_window_outside_baseline_coverage_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside baseline coverage"):
            self.synthesizer.generate(start=date(2025, 1, 1), end=date(2025, 1, 8), seed=1)
        with self.assertRaisesRegex(ValueError, "after end"):
            self.synthesizer.generate(start=WINDOW_END, end=WINDOW_START, seed=1)

    def test_days_argument_combination_rejected(self):
        with self.assertRaisesRegex(ValueError, "days"):
            self.synthesizer.generate(start=WINDOW_START, days=7, seed=1)

    def test_batch_metadata_and_skin_summary(self):
        report = self.generate()
        batch = self.repo.get_batch(report["synth_batch"])
        self.assertIsNotNone(batch)
        self.assertEqual(batch["seed"], 7)
        self.assertEqual(batch["skin_count"], 4)
        self.assertIn("tier_default_price", batch["params_json"])
        summary = self.repo.skin_summary(report["synth_batch"])
        self.assertEqual(len(summary), 4)
        revenues = [float(row["total_revenue"]) for row in summary]
        self.assertEqual(revenues, sorted(revenues, reverse=True))
        self.assertIn("1-2", [row["source_key"] for row in summary[:2]])
        self.assertEqual(summary[-1]["total_units"], 0)  # free skin last
        total_units = sum(row["total_units"] for row in summary)
        self.assertGreater(total_units, 0)

    def test_dry_run_writes_nothing(self):
        report = self.synthesizer.generate(start=WINDOW_START, end=WINDOW_END, seed=3, dry_run=True)
        self.assertIsNone(self.repo.get_batch(report["synth_batch"]))
        self.assertEqual(self.repo.daily_rows(report["synth_batch"]), [])


class PriceParsingTests(unittest.TestCase):
    def test_price_text_wins_over_tier_default(self):
        price, basis = parse_price_cny("48皮肤碎片 / 488点券", "史诗")
        self.assertAlmostEqual(price, 48.8)
        self.assertEqual(basis, "price_text")

    def test_tier_default_fallback(self):
        price, basis = parse_price_cny(None, "传说限定")
        self.assertAlmostEqual(price, 168.8)
        self.assertEqual(basis, "tier_default")
        price, basis = parse_price_cny("限时秒杀", "")
        self.assertAlmostEqual(price, 28.8)
        self.assertEqual(basis, "tier_default")


if __name__ == "__main__":
    unittest.main()
