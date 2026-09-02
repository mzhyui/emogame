"""Auditable period cash-value records and release-window attribution."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re
import sqlite3
import statistics
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from data.skin_repository import DEFAULT_DB_PATH, SkinRepository
from data.sqlite_read import connect_readonly, table_exists
from feature_engineering.pipeline import quality_to_tier


DEFAULT_COST_CONFIG = Path("config/cash_value_costs.json")
CSV_DATE_HEADER = "日期"
CSV_REVENUE_HEADER = "收入预估~iPhone"


@dataclass(frozen=True)
class SpendResolution:
    amount_cny: float | None
    basis: str
    original_amount: float | None = None
    original_currency: str | None = None
    warning: str | None = None


class CashValueRepository:
    """Additive SQLite persistence for revenue facts and skin value evidence."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _read_connect(self) -> sqlite3.Connection:
        return connect_readonly(self.db_path)

    def ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS revenue_import_batches (
                    import_batch TEXT PRIMARY KEY,
                    source_file_hash TEXT NOT NULL,
                    game TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    market TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    cny_per_usd REAL,
                    source TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(source_file_hash, game, platform, market, currency)
                );

                CREATE TABLE IF NOT EXISTS app_revenue_daily (
                    revenue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revenue_date TEXT NOT NULL,
                    game TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    market TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    estimated_revenue REAL NOT NULL CHECK(estimated_revenue >= 0),
                    source TEXT NOT NULL,
                    import_batch TEXT NOT NULL,
                    source_file_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(game, platform, market, revenue_date, source),
                    FOREIGN KEY(import_batch) REFERENCES revenue_import_batches(import_batch)
                );

                CREATE INDEX IF NOT EXISTS idx_app_revenue_period
                    ON app_revenue_daily(game, platform, revenue_date);

                CREATE TABLE IF NOT EXISTS skin_value_records (
                    value_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    sales_volume INTEGER,
                    volume_relation TEXT CHECK(volume_relation IN ('exact', 'estimated')),
                    avg_spend_original REAL,
                    spend_currency TEXT,
                    avg_spend_cny REAL,
                    spend_basis TEXT,
                    baseline_revenue REAL,
                    signed_uplift REAL,
                    attributed_revenue REAL,
                    revenue_currency TEXT,
                    cny_per_usd REAL,
                    attribution_method TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    notes TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    import_batch TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(source_key, period_start, period_end, attribution_method, import_batch)
                );

                CREATE INDEX IF NOT EXISTS idx_skin_value_period
                    ON skin_value_records(source_key, period_start, period_end);
                """
            )
            conn.commit()
        self._migrate_nullable_batch_fx()

    def _migrate_nullable_batch_fx(self) -> None:
        """Preserve existing facts while allowing CNY batches without FX."""
        with closing(self._connect()) as conn:
            columns = conn.execute("PRAGMA table_info(revenue_import_batches)").fetchall()
            fx_column = next((row for row in columns if row["name"] == "cny_per_usd"), None)
            if fx_column is None or not int(fx_column["notnull"]):
                return
            conn.executescript(
                """
                PRAGMA foreign_keys=OFF;
                BEGIN;
                CREATE TABLE revenue_import_batches_v2 (
                    import_batch TEXT PRIMARY KEY,
                    source_file_hash TEXT NOT NULL,
                    game TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    market TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    cny_per_usd REAL,
                    source TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(source_file_hash, game, platform, market, currency)
                );
                INSERT INTO revenue_import_batches_v2
                    SELECT * FROM revenue_import_batches;

                CREATE TABLE app_revenue_daily_v2 (
                    revenue_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    revenue_date TEXT NOT NULL,
                    game TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    market TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    estimated_revenue REAL NOT NULL CHECK(estimated_revenue >= 0),
                    source TEXT NOT NULL,
                    import_batch TEXT NOT NULL,
                    source_file_hash TEXT NOT NULL,
                    imported_at TEXT NOT NULL,
                    UNIQUE(game, platform, market, revenue_date, source),
                    FOREIGN KEY(import_batch) REFERENCES revenue_import_batches_v2(import_batch)
                );
                INSERT INTO app_revenue_daily_v2
                    SELECT * FROM app_revenue_daily;

                DROP TABLE app_revenue_daily;
                DROP TABLE revenue_import_batches;
                ALTER TABLE revenue_import_batches_v2 RENAME TO revenue_import_batches;
                ALTER TABLE app_revenue_daily_v2 RENAME TO app_revenue_daily;
                CREATE INDEX IF NOT EXISTS idx_app_revenue_period
                    ON app_revenue_daily(game, platform, revenue_date);
                COMMIT;
                PRAGMA foreign_keys=ON;
                """
            )

    def save_manual_record(self, source_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.ensure_schema()
        start = _iso_date(payload.get("period_start"), "period_start")
        end = _iso_date(payload.get("period_end"), "period_end")
        if end < start:
            raise ValueError("period_end must not be before period_start")
        relation = str(payload.get("volume_relation") or "").lower()
        if relation not in {"exact", "estimated"}:
            raise ValueError("volume_relation must be exact or estimated")
        volume = _optional_non_negative_int(payload.get("sales_volume"), "sales_volume")
        original = _optional_non_negative_float(payload.get("avg_spend"), "avg_spend")
        currency = str(payload.get("currency") or "CNY").upper()
        if currency not in {"CNY", "USD"}:
            raise ValueError("currency must be CNY or USD")
        fx = _optional_positive_float(payload.get("cny_per_usd"), "cny_per_usd")
        if original is not None and currency == "USD" and fx is None:
            raise ValueError("cny_per_usd is required for USD spend")
        spend_cny = original if currency == "CNY" else (original * fx if original is not None else None)
        confidence = float(payload.get("confidence", 1.0))
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if volume is None and original is None:
            raise ValueError("provide sales_volume or avg_spend")
        now = _now()
        method = f"manual_{relation}"
        provenance = {
            "kind": "operator_evidence",
            "entered_fields": [key for key in ("sales_volume", "avg_spend") if payload.get(key) is not None],
        }
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                """
                INSERT INTO skin_value_records(
                    source_key, period_start, period_end, sales_volume, volume_relation,
                    avg_spend_original, spend_currency, avg_spend_cny, spend_basis,
                    revenue_currency, cny_per_usd, attribution_method, confidence,
                    notes, provenance_json, import_batch, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (source_key, start, end, volume, relation, original, currency, spend_cny,
                 "manual_period", fx, method, confidence, str(payload.get("notes") or ""),
                 json.dumps(provenance, ensure_ascii=False), now, now),
            )
            value_id = int(cursor.lastrowid)
            conn.commit()
        return self.get_record(value_id)

    def upsert_csv_record(self, values: dict[str, Any]) -> dict[str, Any]:
        self.ensure_schema()
        now = _now()
        columns = (
            "source_key", "period_start", "period_end", "sales_volume", "volume_relation",
            "avg_spend_original", "spend_currency", "avg_spend_cny", "spend_basis",
            "baseline_revenue", "signed_uplift", "attributed_revenue", "revenue_currency",
            "cny_per_usd", "attribution_method", "confidence", "notes", "provenance_json",
            "import_batch",
        )
        params = [values.get(name) for name in columns]
        with closing(self._connect()) as conn:
            conn.execute(
                f"""
                INSERT INTO skin_value_records({', '.join(columns)}, created_at, updated_at)
                VALUES({', '.join('?' for _ in columns)}, ?, ?)
                ON CONFLICT(source_key, period_start, period_end, attribution_method, import_batch)
                DO UPDATE SET
                    sales_volume=excluded.sales_volume,
                    avg_spend_original=excluded.avg_spend_original,
                    spend_currency=excluded.spend_currency,
                    avg_spend_cny=excluded.avg_spend_cny,
                    spend_basis=excluded.spend_basis,
                    baseline_revenue=excluded.baseline_revenue,
                    signed_uplift=excluded.signed_uplift,
                    attributed_revenue=excluded.attributed_revenue,
                    cny_per_usd=excluded.cny_per_usd,
                    confidence=excluded.confidence,
                    notes=excluded.notes,
                    provenance_json=excluded.provenance_json,
                    updated_at=excluded.updated_at
                """,
                (*params, now, now),
            )
            conn.commit()
            row = conn.execute(
                """SELECT value_id FROM skin_value_records WHERE source_key=? AND period_start=?
                   AND period_end=? AND attribution_method=? AND import_batch=?""",
                (values["source_key"], values["period_start"], values["period_end"],
                 values["attribution_method"], values["import_batch"]),
            ).fetchone()
        return self.get_record(int(row["value_id"]))

    def get_record(self, value_id: int) -> dict[str, Any]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "skin_value_records"):
                    raise ValueError(f"value record not found: {value_id}")
                row = conn.execute(
                    "SELECT * FROM skin_value_records WHERE value_id=?", (value_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise ValueError(f"value record not found: {value_id}") from exc
        if not row:
            raise ValueError(f"value record not found: {value_id}")
        return _decode_record(dict(row))

    def list_records(self, source_key: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        where = ["source_key = ?"]
        params: list[Any] = [source_key]
        if start:
            start = _iso_date(start, "start")
            where.append("period_end >= ?")
            params.append(start)
        if end:
            end = _iso_date(end, "end")
            where.append("period_start <= ?")
            params.append(end)
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "skin_value_records"):
                    return []
                rows = conn.execute(
                    f"SELECT * FROM skin_value_records WHERE {' AND '.join(where)} "
                    "ORDER BY period_start DESC, value_id DESC", params,
                ).fetchall()
        except sqlite3.Error:
            return []
        return [_decode_record(dict(row)) for row in rows]

    def resolve(self, source_key: str, start: str | None = None, end: str | None = None) -> dict[str, Any] | None:
        records = self.list_records(source_key, start, end)
        priorities = {"manual_exact": 0, "manual_estimated": 1, "csv_release_window_uplift": 2}
        candidates = [row for row in records if row["attribution_method"] in priorities]
        if not candidates:
            return None
        return min(candidates, key=lambda row: (priorities[row["attribution_method"]], -row["value_id"]))

    def resolve_field(
        self,
        source_key: str,
        field: str,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve one field without a sparse record masking lower-priority evidence."""
        if field not in {"sales_volume", "avg_spend_cny"}:
            raise ValueError(f"unsupported cash-value field: {field}")
        priorities = {"manual_exact": 0, "manual_estimated": 1, "csv_release_window_uplift": 2}
        records = [
            row for row in self.list_records(source_key, start, end)
            if row["attribution_method"] in priorities and row.get(field) is not None
        ]
        if not records:
            return None
        return min(records, key=lambda row: (priorities[row["attribution_method"]], -row["value_id"]))

    def revenue_rows(self, start: str, end: str, *, game: str = "王者荣耀", platform: str = "iPhone") -> list[dict[str, Any]]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "app_revenue_daily"):
                    return []
                rows = conn.execute(
                    """SELECT revenue_date, estimated_revenue, currency, source, import_batch
                       FROM app_revenue_daily WHERE game=? AND platform=?
                       AND revenue_date BETWEEN ? AND ? ORDER BY revenue_date""",
                    (game, platform, start, end),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]


class CashValueService:
    """Shared importer, spend resolver, attribution, and reporting service."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, cost_config: str | Path = DEFAULT_COST_CONFIG):
        self.db_path = Path(db_path)
        self.repo = CashValueRepository(db_path)
        self.cost_config_path = Path(cost_config)

    def import_revenue_csv(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        cny_per_usd: float | None = None,
        currency: str = "CNY",
        game: str = "王者荣耀",
        platform: str = "iPhone",
        market: str = "CN",
        source_name: str = "WZRY_IPHONE_revenue.csv",
    ) -> dict[str, Any]:
        currency = str(currency or "").upper()
        if currency not in {"CNY", "USD"}:
            raise ValueError("currency must be CNY or USD")
        if currency == "USD":
            fx = _positive_float(cny_per_usd, "cny_per_usd")
        else:
            if cny_per_usd is not None:
                raise ValueError("cny_per_usd must be omitted for CNY revenue")
            fx = None
        raw = _read_bytes(source)
        source_hash = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must be UTF-8 or UTF-8 with BOM") from exc
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or CSV_DATE_HEADER not in reader.fieldnames or CSV_REVENUE_HEADER not in reader.fieldnames:
            raise ValueError(f"CSV headers must include {CSV_DATE_HEADER!r} and {CSV_REVENUE_HEADER!r}")
        parsed: list[tuple[str, float]] = []
        seen: set[str] = set()
        for line_no, row in enumerate(reader, 2):
            day = _iso_date(row.get(CSV_DATE_HEADER), f"row {line_no} date")
            amount = _non_negative_float(row.get(CSV_REVENUE_HEADER), f"row {line_no} revenue")
            if day in seen:
                raise ValueError(f"duplicate date in CSV: {day}")
            seen.add(day)
            parsed.append((day, amount))
        if not parsed:
            raise ValueError("CSV contains no revenue rows")
        self.repo.ensure_schema()
        batch = f"revenue-{source_hash[:16]}"
        now = _now()
        with closing(self.repo._connect()) as conn:
            existing = conn.execute(
                """SELECT import_batch, row_count FROM revenue_import_batches
                   WHERE source_file_hash=? AND game=? AND platform=? AND market=? AND currency=?""",
                (source_hash, game, platform, market, currency),
            ).fetchone()
            if existing:
                return {"import_batch": existing["import_batch"], "source_file_hash": source_hash,
                        "rows_read": len(parsed), "rows_inserted": 0, "idempotent": True,
                        "currency": currency, "market": market}
            conn.execute(
                """INSERT INTO revenue_import_batches(import_batch, source_file_hash, game, platform,
                   market, currency, cny_per_usd, source, row_count, imported_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (batch, source_hash, game, platform, market, currency, fx, source_name, len(parsed), now),
            )
            inserted = 0
            for day, amount in parsed:
                cursor = conn.execute(
                    """INSERT INTO app_revenue_daily(revenue_date, game, platform, market, currency,
                       estimated_revenue, source, import_batch, source_file_hash, imported_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(game, platform, market, revenue_date, source) DO UPDATE SET
                         currency=excluded.currency, estimated_revenue=excluded.estimated_revenue,
                         import_batch=excluded.import_batch, source_file_hash=excluded.source_file_hash,
                         imported_at=excluded.imported_at""",
                    (day, game, platform, market, currency, amount, source_name, batch, source_hash, now),
                )
                inserted += max(cursor.rowcount, 0)
            conn.commit()
        return {"import_batch": batch, "source_file_hash": source_hash, "rows_read": len(parsed),
                "rows_inserted": inserted, "idempotent": False, "currency": currency, "market": market}

    def resolve_spend(
        self,
        skin: dict[str, Any],
        *,
        period_start: str | None = None,
        period_end: str | None = None,
        gacha_pity_amount_cny: float | None = None,
    ) -> SpendResolution:
        if gacha_pity_amount_cny is not None:
            amount = _non_negative_float(gacha_pity_amount_cny, "gacha_pity_amount_cny")
            return SpendResolution(amount, "manual_gacha_override", amount, "CNY")
        manual = self.repo.resolve_field(str(skin["source_key"]), "avg_spend_cny", period_start, period_end)
        if manual and manual["attribution_method"].startswith("manual_"):
            return SpendResolution(float(manual["avg_spend_cny"]), "manual_period",
                                   manual.get("avg_spend_original"), manual.get("spend_currency"))

        acquire = str(skin.get("acquire_method") or "")
        price = str(skin.get("price_text") or "")
        combined = f"{price} {acquire}"
        point_matches = re.findall(r"(\d+(?:\.\d+)?)\s*点券", combined)
        if point_matches:
            points = float(point_matches[-1])
            return SpendResolution(points / 10, "explicit_point_price", points, "POINTS")
        cash_match = re.search(r"(?:¥|￥|人民币)\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元", combined)
        if cash_match:
            amount = float(cash_match.group(1) or cash_match.group(2))
            return SpendResolution(amount, "explicit_cash_price", amount, "CNY")
        if any(token in acquire for token in ("免费", "赠送", "活动", "任务获取", "福利获取")):
            return SpendResolution(0.0, "free_or_event", 0.0, "CNY", "zero spend does not yield a unit estimate")
        if "碎片" in acquire:
            return SpendResolution(None, "shard_only_unknown", warning="shard-only acquisition has unknown cash cost")

        config = json.loads(self.cost_config_path.read_text(encoding="utf-8"))
        quality = str(skin.get("quality") or "")
        overrides = config["quality_overrides_cny"]
        for key in sorted(overrides, key=len, reverse=True):
            if key in quality:
                amount = float(overrides[key])
                return SpendResolution(amount, "quality_override", amount, "CNY")
        tier = quality_to_tier(quality)
        amount = float(config["tier_defaults_cny"][str(tier)])
        return SpendResolution(amount, "tier_default", amount, "CNY")

    def attribute_releases(
        self,
        *,
        import_batch: str,
        cny_per_usd: float | None = None,
        allocation_weights: dict[str, float] | None = None,
        gacha_overrides: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        self.repo.ensure_schema()
        with closing(self.repo._connect()) as conn:
            batch = conn.execute("SELECT * FROM revenue_import_batches WHERE import_batch=?", (import_batch,)).fetchone()
            if not batch:
                raise ValueError(f"unknown import batch: {import_batch}")
            revenue_currency = str(batch["currency"]).upper()
            if revenue_currency == "CNY":
                if cny_per_usd is not None:
                    raise ValueError("cny_per_usd must be omitted for CNY attribution")
                conversion_rate = 1.0
                stored_fx = None
                conversion_basis = "native_cny"
            elif revenue_currency == "USD":
                stored_fx = _positive_float(
                    cny_per_usd if cny_per_usd is not None else batch["cny_per_usd"],
                    "cny_per_usd",
                )
                conversion_rate = stored_fx
                conversion_basis = "usd_to_cny"
            else:
                raise ValueError(f"unsupported revenue currency: {revenue_currency}")
            rows = conn.execute(
                """SELECT revenue_date, estimated_revenue FROM app_revenue_daily
                   WHERE import_batch=? ORDER BY revenue_date""", (import_batch,),
            ).fetchall()
        revenue = {date.fromisoformat(row["revenue_date"]): float(row["estimated_revenue"]) for row in rows}
        if not revenue:
            raise ValueError("import batch has no revenue facts")
        min_day, max_day = min(revenue), max(revenue)
        skins = [row for row in SkinRepository(self.db_path).list_skins(limit=None) if _try_date(row.get("online_date"))]
        cohorts: dict[date, list[dict[str, Any]]] = {}
        for skin in skins:
            release = _try_date(skin.get("online_date"))
            if release and min_day <= release <= max_day:
                cohorts.setdefault(release, []).append(skin)
        release_days = sorted(cohorts)
        created: list[dict[str, Any]] = []
        insufficient: list[dict[str, Any]] = []
        allocation_weights = allocation_weights or {}
        gacha_overrides = gacha_overrides or {}
        for index, release in enumerate(release_days):
            next_release = release_days[index + 1] if index + 1 < len(release_days) else None
            nominal_end = release + timedelta(days=6)
            end = min(nominal_end, next_release - timedelta(days=1)) if next_release else nominal_end
            baseline_days = [day for day in revenue if release - timedelta(days=28) <= day < release]
            attribution_days = [day for day in _date_range(release, end) if day in revenue]
            if len(baseline_days) < 21 or len(attribution_days) < 5:
                insufficient.extend({"source_key": skin["source_key"], "release_date": release.isoformat(),
                                     "baseline_days": len(baseline_days), "attribution_days": len(attribution_days),
                                     "status": "insufficient_coverage"} for skin in cohorts[release])
                continue
            daily: list[dict[str, Any]] = []
            for day in attribution_days:
                matches = [revenue[b] for b in baseline_days if b.weekday() == day.weekday()]
                if not matches:
                    continue
                expected = float(statistics.median(matches))
                observed = revenue[day]
                daily.append({"date": day.isoformat(), "baseline": expected, "observed": observed,
                              "signed_uplift": observed - expected, "positive_uplift": max(0.0, observed - expected)})
            if len(daily) < 5:
                insufficient.extend({"source_key": skin["source_key"], "release_date": release.isoformat(),
                                     "baseline_days": len(baseline_days), "attribution_days": len(daily),
                                     "status": "insufficient_coverage"} for skin in cohorts[release])
                continue
            cohort = cohorts[release]
            supplied = [allocation_weights.get(str(skin["source_key"])) for skin in cohort]
            weighted = all(value is not None and float(value) > 0 for value in supplied)
            if any(value is not None for value in supplied) and not weighted:
                raise ValueError(f"all allocation weights for cohort {release} must be positive")
            weights = [float(value) for value in supplied] if weighted else [1.0] * len(cohort)
            total_weight = sum(weights)
            baseline_total = sum(item["baseline"] for item in daily)
            signed_total = sum(item["signed_uplift"] for item in daily)
            positive_total = sum(item["positive_uplift"] for item in daily)
            for skin, weight in zip(cohort, weights):
                share = weight / total_weight
                attributed = positive_total * share
                spend = self.resolve_spend(skin, period_start=release.isoformat(), period_end=end.isoformat(),
                                           gacha_pity_amount_cny=gacha_overrides.get(str(skin["source_key"])))
                attributed_cny = attributed * conversion_rate
                units = None if spend.amount_cny in (None, 0) else round(attributed_cny / spend.amount_cny)
                confidence = 0.60
                penalties: list[str] = []
                if len(cohort) > 1 and not weighted:
                    confidence -= 0.10
                    penalties.append("equal_overlap_allocation")
                if spend.basis == "tier_default":
                    confidence -= 0.10
                    penalties.append("tier_default_spend")
                if end < nominal_end:
                    confidence -= 0.05
                    penalties.append("truncated_window")
                if len(baseline_days) < 28 or len(attribution_days) < 7:
                    confidence -= 0.05
                    penalties.append("incomplete_data")
                chart = [{**item, "allocated_positive_uplift": item["positive_uplift"] * share} for item in daily]
                provenance = {
                    "source_file_hash": batch["source_file_hash"], "source": batch["source"],
                    "game": batch["game"], "platform": batch["platform"], "market": batch["market"],
                    "allocation": "weighted" if weighted else ("equal" if len(cohort) > 1 else "single"),
                    "allocation_weight": weight, "cohort_size": len(cohort), "spend_basis": spend.basis,
                    "confidence_penalties": penalties, "daily_chart": chart,
                    "revenue_currency": revenue_currency,
                    "conversion_rate_to_cny": conversion_rate,
                    "conversion_basis": conversion_basis,
                }
                record = self.repo.upsert_csv_record({
                    "source_key": skin["source_key"], "period_start": release.isoformat(),
                    "period_end": end.isoformat(), "sales_volume": units, "volume_relation": "estimated",
                    "avg_spend_original": spend.original_amount, "spend_currency": spend.original_currency,
                    "avg_spend_cny": spend.amount_cny, "spend_basis": spend.basis,
                    "baseline_revenue": baseline_total * share, "signed_uplift": signed_total * share,
                    "attributed_revenue": attributed, "revenue_currency": revenue_currency,
                    "cny_per_usd": stored_fx,
                    "attribution_method": "csv_release_window_uplift", "confidence": max(0.0, min(0.75, confidence)),
                    "notes": spend.warning or (
                        "estimated China iPhone app revenue; not exact skin revenue"
                        if revenue_currency == "CNY"
                        else "estimated iPhone app revenue; not exact skin revenue"
                    ),
                    "provenance_json": json.dumps(provenance, ensure_ascii=False), "import_batch": import_batch,
                })
                created.append(record)
        return {"import_batch": import_batch, "eligible_records": len(created), "records": created,
                "insufficient_coverage": insufficient}

    def cash_value(self, source_key: str, start: str | None = None, end: str | None = None,
                   *, evaluation_score: float | None = None, legacy_signals: Any | None = None) -> dict[str, Any]:
        if start:
            start = _iso_date(start, "start")
        if end:
            end = _iso_date(end, "end")
        if start and end and end < start:
            raise ValueError("end must not be before start")
        records = self.repo.list_records(source_key, start, end)
        resolved = self.repo.resolve(source_key, start, end)
        if resolved is not None:
            # Compatibility fields resolve independently so a volume-only
            # exact record does not hide a lower-priority spend observation.
            resolved = dict(resolved)
            field_sources: dict[str, int] = {}
            for field in ("sales_volume", "avg_spend_cny"):
                field_record = self.repo.resolve_field(source_key, field, start, end)
                if field_record is not None:
                    resolved[field] = field_record[field]
                    field_sources[field] = int(field_record["value_id"])
                    if field == "avg_spend_cny":
                        for related in ("avg_spend_original", "spend_currency", "spend_basis"):
                            resolved[related] = field_record.get(related)
            resolved["resolved_field_sources"] = field_sources
        if resolved is None and legacy_signals is not None:
            volume = getattr(legacy_signals, "sales_volume", None)
            spend = getattr(legacy_signals, "avg_spend_to_obtain", None)
            if volume is not None or spend is not None:
                resolved = {"source_key": source_key, "sales_volume": volume, "volume_relation": "estimated",
                            "avg_spend_cny": spend, "spend_basis": "legacy_aggregate",
                            "attribution_method": "legacy_aggregate", "confidence": None,
                            "provenance": {"source": "market_signal_records"}}
        metrics: dict[str, Any] = {}
        chart: list[dict[str, Any]] = []
        warnings: list[str] = []
        if resolved:
            baseline = resolved.get("baseline_revenue")
            uplift = resolved.get("signed_uplift")
            spend = resolved.get("avg_spend_cny")
            units = resolved.get("sales_volume")
            revenue_currency = str(resolved.get("revenue_currency") or "").upper()
            conversion_rate = (
                1.0 if revenue_currency == "CNY"
                else float(resolved.get("cny_per_usd") or 0)
            )
            metrics = {
                "revenue_lift_percent": None if not baseline else round(float(uplift or 0) / float(baseline) * 100, 2),
                "effective_spend_per_estimated_unit_cny": None if not units else round(
                    float(resolved.get("attributed_revenue") or 0)
                    * conversion_rate / float(units), 2
                ),
                "emotional_value_efficiency_per_cny100": None if evaluation_score is None or not spend else
                    round(float(evaluation_score) / float(spend) * 100, 2),
                "emotional_value_efficiency_label": "descriptive score efficiency; not monetary valuation",
            }
            chart = list((resolved.get("provenance") or {}).get("daily_chart") or [])
            if resolved.get("attribution_method") == "csv_release_window_uplift":
                if revenue_currency == "CNY":
                    warnings.append(
                        "CSV is estimated China iPhone app revenue in CNY; attribution is not exact skin revenue."
                    )
                else:
                    warnings.append("CSV is estimated iPhone app revenue; attribution is not exact skin revenue.")
            if spend in (None, 0):
                warnings.append("Unknown or zero acquisition spend prevents a unit estimate.")
        return {"source_key": source_key, "query_period": {"start": start, "end": end}, "resolved": resolved,
                "records": records, "metrics": metrics, "daily_chart": chart, "warnings": warnings}


def _decode_record(row: dict[str, Any]) -> dict[str, Any]:
    row["provenance"] = json.loads(row.pop("provenance_json") or "{}")
    return row


def _read_bytes(source: str | Path | bytes | BinaryIO) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    value = source.read()
    return value.encode("utf-8") if isinstance(value, str) else bytes(value)


def _try_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _iso_date(value: Any, field: str) -> str:
    parsed = _try_date(value)
    if parsed is None:
        raise ValueError(f"{field} must be an ISO date (YYYY-MM-DD)")
    return parsed.isoformat()


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _non_negative_float(value: Any, field: str) -> float:
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be a non-negative finite number")
    return result


def _positive_float(value: Any, field: str) -> float:
    result = _non_negative_float(value, field)
    if result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _optional_non_negative_float(value: Any, field: str) -> float | None:
    return None if value is None or value == "" else _non_negative_float(value, field)


def _optional_positive_float(value: Any, field: str) -> float | None:
    return None if value is None or value == "" else _positive_float(value, field)


def _optional_non_negative_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    result = _non_negative_float(value, field)
    if not result.is_integer():
        raise ValueError(f"{field} must be an integer")
    return int(result)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
