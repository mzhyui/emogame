import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from app import build_payload, import_cash_value_upload, score_text
from crawlers.wzry_skin_crawler import HeroRecord, SkinRecord, ensure_schema, save_hero, save_skin
from data.cash_value import CashValueRepository, CashValueService
from data.market_signal_repository import MarketSignalRepository
from feature_engineering.features import MarketValidationSignals


def revenue_csv(values: dict[date, float], *, bom: bool = True, descending: bool = True) -> bytes:
    rows = sorted(values.items(), reverse=descending)
    text = "日期,收入预估~iPhone\n" + "".join(f"{day.isoformat()},{amount}\n" for day, amount in rows)
    return (("\ufeff" if bom else "") + text).encode()


class CashValueTests(unittest.TestCase):
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

    def test_read_methods_do_not_initialize_missing_database(self):
        ghost = Path(self.tmp.name) / "ghost.sqlite3"
        repo = CashValueRepository(ghost)
        self.assertEqual(repo.list_records("missing"), [])
        self.assertEqual(repo.revenue_rows("2026-01-01", "2026-01-31"), [])
        with self.assertRaisesRegex(ValueError, "not found"):
            repo.get_record(1)
        self.assertFalse(ghost.exists())

    def test_build_payload_uses_completed_value_score_in_cash_metrics(self):
        self.add_skin("1-1", date(2026, 1, 1))
        CashValueRepository(self.db_path).save_manual_record(
            "1-1",
            {
                "period_start": "2026-01-01",
                "period_end": "2026-01-31",
                "sales_volume": 10,
                "volume_relation": "exact",
                "avg_spend": 88.8,
                "currency": "CNY",
            },
        )
        payload = build_payload(
            self.db_path,
            "1-1",
            MarketValidationSignals(visual_score=80),
        )
        evaluation = payload["evaluation"]
        self.assertIsNotNone(evaluation["evaluation_score"])
        self.assertEqual(evaluation["validation_status"], "value_scored")
        self.assertEqual(score_text(evaluation), str(evaluation["evaluation_score"]))
        self.assertIsNotNone(
            payload["sales_report"]["cash_value"]["metrics"][
                "emotional_value_efficiency_per_cny100"
            ]
        )

    def add_skin(self, key: str, release: date, *, quality: str = "史诗", acquire: str = "商城直售获取",
                 price: str | None = "888点券") -> None:
        conn = sqlite3.connect(self.db_path)
        save_skin(conn, SkinRecord(
            source_key=key, source_index=int(key.split("-")[-1]), hero_id="1", hero_name="测试英雄",
            skin_index=int(key.split("-")[-1]), skin_id=key, skin_name=f"皮肤{key}", quality=quality,
            online_date=release.isoformat(), intro="", acquire_method=acquire, price_text=price,
            image_url="", detail_url="", mobile_url="", video_id="", catalog_source="test",
            detail_source="test", has_detail_record=True, raw_catalog_json={}, raw_detail_json={},
        ), None)
        conn.commit()
        conn.close()

    def make_series(self, release: date, observed: float = 200, days_after: int = 7) -> dict[date, float]:
        values = {release - timedelta(days=n): float(100 + (release - timedelta(days=n)).weekday())
                  for n in range(1, 29)}
        values.update({release + timedelta(days=n): float(observed) for n in range(days_after)})
        return values

    def import_series(self, values: dict[date, float]) -> dict:
        return self.service.import_revenue_csv(revenue_csv(values))

    def test_csv_bom_descending_duplicate_hash_and_validation(self):
        real = Path("data/WZRY_IPHONE_revenue.csv")
        result = self.service.import_revenue_csv(real)
        self.assertEqual(result["rows_read"], 365)
        self.assertFalse(result["idempotent"])
        again = self.service.import_revenue_csv(real.read_bytes(), source_name="renamed.csv")
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["rows_inserted"], 0)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM app_revenue_daily").fetchone()[0], 365)
        with self.assertRaisesRegex(ValueError, "number"):
            self.service.import_revenue_csv("日期,收入预估~iPhone\n2026-01-01,nope".encode())
        with self.assertRaisesRegex(ValueError, "duplicate date"):
            self.service.import_revenue_csv(
                "日期,收入预估~iPhone\n2026-01-01,1\n2026-01-01,2".encode())
        with self.assertRaisesRegex(ValueError, "ISO date"):
            self.service.import_revenue_csv(
                "日期,收入预估~iPhone\n2026-99-01,1".encode())
        with self.assertRaisesRegex(ValueError, "cny_per_usd"):
            self.service.import_revenue_csv(real, currency="USD")
        with self.assertRaisesRegex(ValueError, "must be omitted"):
            self.service.import_revenue_csv(real, currency="CNY", cny_per_usd=7.2)
        with self.assertRaisesRegex(ValueError, "currency"):
            self.service.import_revenue_csv(real, currency="")

    def test_streamlit_upload_adapter_uses_shared_import_and_attribution(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        result = import_cash_value_upload(
            self.db_path, revenue_csv(self.make_series(release)), source_name="ui-upload.csv",
        )
        self.assertEqual(result["import"]["rows_read"], 35)
        self.assertEqual(result["attribution"]["eligible_records"], 1)

    def test_usd_import_retains_optional_fx_compatibility(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        imported = self.service.import_revenue_csv(
            revenue_csv(self.make_series(release)), currency="USD", cny_per_usd=7.2,
            market="US", source_name="usd-revenue.csv",
        )
        result = self.service.attribute_releases(import_batch=imported["import_batch"])
        record = result["records"][0]
        self.assertEqual(record["revenue_currency"], "USD")
        self.assertEqual(record["cny_per_usd"], 7.2)
        self.assertEqual(record["provenance"]["conversion_basis"], "usd_to_cny")
        self.assertEqual(
            record["sales_volume"],
            round(record["attributed_revenue"] * 7.2 / record["avg_spend_cny"]),
        )

    def test_legacy_batch_fx_schema_migrates_without_losing_facts(self):
        legacy_path = Path(self.tmp.name) / "legacy.sqlite3"
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript("""
                CREATE TABLE revenue_import_batches (
                    import_batch TEXT PRIMARY KEY, source_file_hash TEXT NOT NULL,
                    game TEXT NOT NULL, platform TEXT NOT NULL, market TEXT NOT NULL,
                    currency TEXT NOT NULL, cny_per_usd REAL NOT NULL, source TEXT NOT NULL,
                    row_count INTEGER NOT NULL, imported_at TEXT NOT NULL,
                    UNIQUE(source_file_hash, game, platform, market, currency)
                );
                CREATE TABLE app_revenue_daily (
                    revenue_id INTEGER PRIMARY KEY AUTOINCREMENT, revenue_date TEXT NOT NULL,
                    game TEXT NOT NULL, platform TEXT NOT NULL, market TEXT NOT NULL,
                    currency TEXT NOT NULL, estimated_revenue REAL NOT NULL,
                    source TEXT NOT NULL, import_batch TEXT NOT NULL,
                    source_file_hash TEXT NOT NULL, imported_at TEXT NOT NULL,
                    UNIQUE(game, platform, market, revenue_date, source)
                );
                INSERT INTO revenue_import_batches VALUES(
                    'old', 'hash', '王者荣耀', 'iPhone', 'unknown', 'USD', 7.2,
                    'old.csv', 1, '2026-01-01'
                );
                INSERT INTO app_revenue_daily(
                    revenue_date, game, platform, market, currency, estimated_revenue,
                    source, import_batch, source_file_hash, imported_at
                ) VALUES('2026-01-01', '王者荣耀', 'iPhone', 'unknown', 'USD', 10,
                         'old.csv', 'old', 'hash', '2026-01-01');
            """)
        CashValueRepository(legacy_path).ensure_schema()
        with sqlite3.connect(legacy_path) as conn:
            fx_column = next(row for row in conn.execute("PRAGMA table_info(revenue_import_batches)")
                             if row[1] == "cny_per_usd")
            self.assertEqual(fx_column[3], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM app_revenue_daily").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT cny_per_usd FROM revenue_import_batches").fetchone()[0], 7.2)

    def test_spend_resolution_precedence_and_fallbacks(self):
        release = date(2026, 1, 10)
        base = {"source_key": "1-1", "quality": "史诗", "acquire_method": "", "price_text": None}
        self.assertEqual(self.service.resolve_spend({**base, "price_text": "888点券"}).amount_cny, 88.8)
        mixed = self.service.resolve_spend({**base, "acquire_method": "58碎片或888点券", "price_text": None})
        self.assertEqual((mixed.amount_cny, mixed.basis), (88.8, "explicit_point_price"))
        self.assertEqual(self.service.resolve_spend({**base, "price_text": "￥68"}).amount_cny, 68)
        self.assertEqual(self.service.resolve_spend({**base, "quality": "荣耀典藏"}).amount_cny, 2000)
        self.assertEqual(self.service.resolve_spend(base).amount_cny, 88.8)
        self.assertEqual(self.service.resolve_spend({**base, "acquire_method": "活动获取"}).amount_cny, 0)
        self.assertIsNone(self.service.resolve_spend({**base, "acquire_method": "皮肤碎片兑换"}).amount_cny)
        self.assertEqual(self.service.resolve_spend(base, gacha_pity_amount_cny=450).basis, "manual_gacha_override")
        self.service.repo.save_manual_record("1-1", {
            "period_start": release.isoformat(), "period_end": release.isoformat(),
            "sales_volume": 3, "volume_relation": "exact", "avg_spend": 10, "currency": "USD",
            "cny_per_usd": 7.2, "confidence": .9,
        })
        manual = self.service.resolve_spend(base, period_start=release.isoformat(), period_end=release.isoformat())
        self.assertEqual((manual.amount_cny, manual.basis), (72, "manual_period"))

    def test_weekday_baseline_negative_uplift_and_no_unit_for_zero_spend(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release, acquire="活动获取", price=None)
        imported = self.import_series(self.make_series(release, observed=50))
        result = self.service.attribute_releases(import_batch=imported["import_batch"])
        record = result["records"][0]
        self.assertLess(record["signed_uplift"], 0)
        self.assertEqual(record["attributed_revenue"], 0)
        self.assertIsNone(record["sales_volume"])
        self.assertEqual(len(record["provenance"]["daily_chart"]), 7)
        first_day = record["provenance"]["daily_chart"][0]
        self.assertEqual(first_day["baseline"], 100 + release.weekday())

    def test_incomplete_window_is_not_estimated(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        imported = self.import_series(self.make_series(release, days_after=4))
        result = self.service.attribute_releases(import_batch=imported["import_batch"])
        self.assertEqual(result["eligible_records"], 0)
        self.assertEqual(result["insufficient_coverage"][0]["status"], "insufficient_coverage")

    def test_next_release_truncation_equal_weight_and_conservation(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        self.add_skin("1-2", release)
        self.add_skin("1-3", release + timedelta(days=5))
        values = self.make_series(release, observed=210, days_after=12)
        imported = self.import_series(values)
        result = self.service.attribute_releases(import_batch=imported["import_batch"])
        first = [r for r in result["records"] if r["period_start"] == release.isoformat()]
        self.assertEqual(len(first), 2)
        self.assertEqual(first[0]["period_end"], (release + timedelta(days=4)).isoformat())
        self.assertAlmostEqual(first[0]["attributed_revenue"], first[1]["attributed_revenue"])
        self.assertIn("equal_overlap_allocation", first[0]["provenance"]["confidence_penalties"])
        daily_allocated = sum(sum(day["allocated_positive_uplift"] for day in r["provenance"]["daily_chart"])
                              for r in first)
        positive_app_uplift = sum(day["positive_uplift"] for day in first[0]["provenance"]["daily_chart"])
        self.assertAlmostEqual(daily_allocated, positive_app_uplift)

    def test_weighted_same_day_allocation(self):
        release = date(2026, 3, 2)
        self.add_skin("1-1", release)
        self.add_skin("1-2", release)
        imported = self.import_series(self.make_series(release))
        result = self.service.attribute_releases(
            import_batch=imported["import_batch"],
            allocation_weights={"1-1": 3, "1-2": 1},
        )
        records = {r["source_key"]: r for r in result["records"]}
        self.assertAlmostEqual(records["1-1"]["attributed_revenue"], records["1-2"]["attributed_revenue"] * 3)
        self.assertEqual(records["1-1"]["provenance"]["allocation"], "weighted")
        for record in records.values():
            self.assertEqual(
                record["sales_volume"],
                round(record["attributed_revenue"] / record["avg_spend_cny"]),
            )
            for field in ("period_start", "period_end", "revenue_currency",
                          "spend_basis", "attribution_method", "confidence", "provenance"):
                self.assertIsNotNone(record[field])
            self.assertEqual(record["revenue_currency"], "CNY")
            self.assertIsNone(record["cny_per_usd"])
            self.assertEqual(record["provenance"]["conversion_basis"], "native_cny")
        cash = self.service.cash_value("1-1", evaluation_score=80)
        self.assertIsNotNone(cash["metrics"]["revenue_lift_percent"])
        self.assertIsNotNone(cash["metrics"]["effective_spend_per_estimated_unit_cny"])
        self.assertIsNotNone(cash["metrics"]["emotional_value_efficiency_per_cny100"])
        self.assertTrue(cash["daily_chart"])
        self.assertTrue(cash["warnings"])

    def test_manual_period_filter_priority_compatibility_and_provenance(self):
        self.add_skin("1-1", date(2026, 1, 1))
        market = MarketSignalRepository(self.db_path)
        market.upsert_signals("1-1", {"sales_volume": 999, "avg_spend_to_obtain": 50})
        estimated = self.service.repo.save_manual_record("1-1", {
            "period_start": "2026-01-01", "period_end": "2026-01-07", "sales_volume": 20,
            "volume_relation": "estimated", "avg_spend": 10, "currency": "CNY", "confidence": .7,
            "notes": "estimate",
        })
        exact = self.service.repo.save_manual_record("1-1", {
            "period_start": "2026-02-01", "period_end": "2026-02-07", "sales_volume": 30,
            "volume_relation": "exact", "avg_spend": 12, "currency": "CNY", "confidence": .95,
            "notes": "operator exact",
        })
        january = self.service.cash_value("1-1", "2026-01-01", "2026-01-31")
        self.assertEqual(january["resolved"]["value_id"], estimated["value_id"])
        self.assertEqual(january["resolved"]["provenance"]["kind"], "operator_evidence")
        self.assertEqual(self.service.repo.resolve("1-1")["value_id"], exact["value_id"])
        signals = market.get_signals("1-1")
        self.assertEqual((signals.sales_volume, signals.avg_spend_to_obtain), (30, 12))
        with sqlite3.connect(self.db_path) as conn:
            legacy = conn.execute("SELECT sales_volume FROM market_signal_records WHERE source_key='1-1'").fetchone()[0]
        self.assertEqual(legacy, 999, "legacy evidence must not be overwritten")

        volume_only = self.service.repo.save_manual_record("1-1", {
            "period_start": "2026-03-01", "period_end": "2026-03-07", "sales_volume": 40,
            "volume_relation": "exact", "currency": "CNY", "confidence": .9,
        })
        spend_only = self.service.repo.save_manual_record("1-1", {
            "period_start": "2026-03-01", "period_end": "2026-03-07", "volume_relation": "estimated",
            "avg_spend": 15, "currency": "CNY", "confidence": .8,
        })
        march = self.service.cash_value("1-1", "2026-03-01", "2026-03-31")
        self.assertEqual((march["resolved"]["sales_volume"], march["resolved"]["avg_spend_cny"]), (40, 15))
        self.assertEqual(march["resolved"]["resolved_field_sources"], {
            "sales_volume": volume_only["value_id"], "avg_spend_cny": spend_only["value_id"],
        })

    def test_api_save_read_validation_and_simulation_nonpersistent(self):
        self.add_skin("1-1", date(2026, 1, 1))
        client = TestClient(app)
        response = client.post("/api/skins/1-1/value-records", params={"db": str(self.db_path)}, json={
            "period_start": "2026-01-01", "period_end": "2026-01-07", "sales_volume": 50,
            "volume_relation": "exact", "avg_spend": 88.8, "currency": "CNY",
            "confidence": .9, "notes": "audited",
        })
        self.assertEqual(response.status_code, 201)
        read = client.get("/api/skins/1-1/cash-value", params={
            "db": str(self.db_path), "start": "2026-01-01", "end": "2026-01-31",
        })
        self.assertEqual(read.status_code, 200)
        self.assertEqual(read.json()["resolved"]["sales_volume"], 50)
        report = client.post("/api/sales-report", params={"db": str(self.db_path)}, json={"source_key": "1-1"})
        self.assertIn("cash_value", report.json()["sales_report"])
        invalid = client.post("/api/skins/1-1/value-records", params={"db": str(self.db_path)}, json={
            "period_start": "bad", "period_end": "2026-01-07", "volume_relation": "unknown",
        })
        self.assertEqual(invalid.status_code, 422)
        before = len(CashValueRepository(self.db_path).list_records("1-1"))
        build_payload(self.db_path, "1-1", MarketValidationSignals(sales_volume=123456))
        after = len(CashValueRepository(self.db_path).list_records("1-1"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
