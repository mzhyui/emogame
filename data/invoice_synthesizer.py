"""Synthesized daily per-skin invoice aggregates reconciled to real app revenue.

The workflow distributes each day's real ``app_revenue_daily`` iPhone revenue
total across the WZRY skin catalog (top-down reconciliation), producing one
invoice row per skin per day: units sold, effective unit price, and gross
revenue. Data is fully synthetic but reproducible via a seed, and is stored in
dedicated tables (``synth_invoice_batches`` / ``synthetic_invoice_daily``) so
it never contaminates real evidence tables.

Paid-transaction convention: skins whose ``acquire_method`` marks a free
in-game acquisition keep units=0 / revenue=0 rows and are excluded from the
revenue distribution.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from data.skin_repository import DEFAULT_DB_PATH, SkinRepository

GENERATOR_VERSION = "invoice-synth-v1"
DIANQUAN_TO_CNY = 0.1

#: Fallback unit price (CNY) per quality tier when price_text is unparseable.
TIER_DEFAULT_PRICE: dict[str, float] = {
    "": 28.8,
    "勇者": 28.8,
    "勇者限定": 28.8,
    "勇者→史诗": 88.8,
    "史诗": 88.8,
    "史诗限定": 88.8,
    "传说": 168.8,
    "传说限定": 168.8,
    "珍品传说": 888.0,
    "无双": 588.0,
    "无双限定": 588.0,
    "荣耀典藏": 1288.0,
}

#: Demand weight per quality tier used by the top-down distribution.
TIER_WEIGHT: dict[str, float] = {
    "": 1.0,
    "勇者": 1.6,
    "勇者限定": 1.8,
    "勇者→史诗": 2.2,
    "史诗": 3.0,
    "史诗限定": 3.5,
    "传说": 6.0,
    "传说限定": 7.0,
    "珍品传说": 8.0,
    "无双": 10.0,
    "无双限定": 11.5,
    "荣耀典藏": 12.0,
}

#: acquire_method substrings that mark free (non-invoice) acquisitions.
FREE_ACQUIRE_KEYWORDS = ("限时活动获取", "赛季任务", "王者印记", "时空能量")

PRICE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*点券")
LAUNCH_SPIKE_TAU_DAYS = 12.0


def parse_price_cny(price_text: str | None, quality: str) -> tuple[float, str]:
    """Resolve a unit price in CNY from price_text, falling back to tier defaults."""
    if price_text:
        amounts = [float(match) for match in PRICE_PATTERN.findall(price_text)]
        if amounts:
            return max(amounts) * DIANQUAN_TO_CNY, "price_text"
    return TIER_DEFAULT_PRICE.get(quality or "", TIER_DEFAULT_PRICE[""]), "tier_default"


def is_free_acquire(acquire_method: str | None) -> bool:
    if not acquire_method:
        return False
    return any(keyword in acquire_method for keyword in FREE_ACQUIRE_KEYWORDS)


@dataclass(frozen=True)
class SkinProfile:
    source_key: str
    quality: str
    unit_price: float
    price_basis: str
    weight: float
    release_date: date | None
    free: bool


class SyntheticInvoiceRepository:
    """SQLite persistence for synthesized invoice batches and daily rows."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        return [dict(row) for row in cursor.fetchall()]

    def ensure_schema(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS synth_invoice_batches (
                    synth_batch TEXT PRIMARY KEY,
                    seed INTEGER NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    baseline_batch TEXT NOT NULL,
                    generator_version TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    skin_count INTEGER NOT NULL,
                    day_count INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    total_revenue REAL NOT NULL,
                    max_daily_deviation REAL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS synthetic_invoice_daily (
                    invoice_id INTEGER PRIMARY KEY,
                    synth_batch TEXT NOT NULL REFERENCES synth_invoice_batches(synth_batch),
                    source_key TEXT NOT NULL,
                    invoice_date TEXT NOT NULL,
                    units_sold INTEGER NOT NULL,
                    unit_price REAL NOT NULL,
                    gross_revenue REAL NOT NULL,
                    discount_pct REAL NOT NULL DEFAULT 0,
                    quality TEXT NOT NULL DEFAULT '',
                    release_age_days INTEGER,
                    price_basis TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (synth_batch, source_key, invoice_date)
                );
                CREATE INDEX IF NOT EXISTS idx_synthetic_invoice_daily_date
                    ON synthetic_invoice_daily (synth_batch, invoice_date);
                """
            )
            conn.commit()

    def save_batch(self, batch: dict[str, Any]) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO synth_invoice_batches
                   (synth_batch, seed, period_start, period_end, baseline_batch,
                    generator_version, params_json, skin_count, day_count, row_count,
                    total_revenue, max_daily_deviation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch["synth_batch"], batch["seed"], batch["period_start"],
                    batch["period_end"], batch["baseline_batch"], batch["generator_version"],
                    json.dumps(batch["params"], ensure_ascii=False, sort_keys=True),
                    batch["skin_count"], batch["day_count"], batch["row_count"],
                    batch["total_revenue"], batch["max_daily_deviation"], time.time(),
                ),
            )
            conn.commit()

    def save_daily_rows(self, synth_batch: str, rows: list[tuple[Any, ...]]) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM synthetic_invoice_daily WHERE synth_batch = ?", (synth_batch,))
            conn.executemany(
                """INSERT INTO synthetic_invoice_daily
                   (synth_batch, source_key, invoice_date, units_sold, unit_price,
                    gross_revenue, discount_pct, quality, release_age_days, price_basis, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            conn.commit()

    def get_batch(self, synth_batch: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM synth_invoice_batches WHERE synth_batch = ?", (synth_batch,)
            ).fetchone()
        return dict(row) if row else None

    def list_batches(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._rows(
                conn.execute("SELECT * FROM synth_invoice_batches ORDER BY period_start, synth_batch")
            )

    def daily_rows(self, synth_batch: str, start: str | None = None, end: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM synthetic_invoice_daily WHERE synth_batch = ?"
        params: list[Any] = [synth_batch]
        if start:
            query += " AND invoice_date >= ?"
            params.append(start)
        if end:
            query += " AND invoice_date <= ?"
            params.append(end)
        query += " ORDER BY invoice_date, source_key"
        with closing(self._connect()) as conn:
            return self._rows(conn.execute(query, params))

    def reconciliation(self, synth_batch: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return self._rows(
                conn.execute(
                    """
                    SELECT r.revenue_date AS invoice_date,
                           r.estimated_revenue AS baseline_revenue,
                           COALESCE(SUM(d.gross_revenue), 0) AS synthesized_revenue
                    FROM app_revenue_daily r
                    LEFT JOIN synthetic_invoice_daily d
                      ON d.synth_batch = ? AND d.invoice_date = r.revenue_date
                    WHERE r.revenue_date BETWEEN
                          (SELECT period_start FROM synth_invoice_batches WHERE synth_batch = ?)
                      AND (SELECT period_end FROM synth_invoice_batches WHERE synth_batch = ?)
                    GROUP BY r.revenue_date
                    ORDER BY r.revenue_date
                    """,
                    (synth_batch, synth_batch, synth_batch),
                )
            )

    def skin_summary(self, synth_batch: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT d.source_key,
                   s.skin_name,
                   s.hero_name,
                   d.quality,
                   SUM(d.units_sold) AS total_units,
                   SUM(d.gross_revenue) AS total_revenue,
                   SUM(CASE WHEN d.units_sold > 0 THEN 1 ELSE 0 END) AS active_days,
                   CASE WHEN SUM(d.units_sold) > 0
                        THEN SUM(d.gross_revenue) / SUM(d.units_sold) ELSE 0 END AS avg_unit_price
            FROM synthetic_invoice_daily d
            LEFT JOIN skins s ON s.source_key = d.source_key
            WHERE d.synth_batch = ?
            GROUP BY d.source_key
            ORDER BY total_revenue DESC, d.source_key
        """
        params: list[Any] = [synth_batch]
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        with closing(self._connect()) as conn:
            return self._rows(conn.execute(query, params))


class InvoiceSynthesizer:
    """Top-down seeded generator reconciling skin-level invoices to real totals."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.repo = SyntheticInvoiceRepository(self.db_path)
        self.skins = SkinRepository(self.db_path)

    # ------------------------------------------------------------------
    # Baseline revenue
    # ------------------------------------------------------------------
    def revenue_baseline(self, baseline_batch: str | None = None) -> tuple[str, dict[date, float], str]:
        """Return (batch_id, {day: revenue}, currency) from app_revenue_daily."""
        with closing(self.repo._connect()) as conn:
            if baseline_batch is None:
                rows = conn.execute(
                    "SELECT import_batch FROM revenue_import_batches ORDER BY imported_at"
                ).fetchall()
                if not rows:
                    raise ValueError("no revenue_import_batches found; import revenue CSV first")
                baseline_batch = dict(rows[-1])["import_batch"]
            batch = conn.execute(
                "SELECT * FROM revenue_import_batches WHERE import_batch = ?", (baseline_batch,)
            ).fetchone()
            if not batch:
                raise ValueError(f"unknown baseline batch: {baseline_batch}")
            currency = str(dict(batch)["currency"]).upper()
            facts = conn.execute(
                "SELECT revenue_date, estimated_revenue FROM app_revenue_daily WHERE import_batch = ?",
                (baseline_batch,),
            ).fetchall()
        revenue = {date.fromisoformat(dict(r)["revenue_date"]): float(dict(r)["estimated_revenue"]) for r in facts}
        if not revenue:
            raise ValueError(f"baseline batch {baseline_batch} has no revenue rows")
        return baseline_batch, revenue, currency

    def resolve_window(self, revenue: dict[date, float], *, start: date | None, end: date | None) -> tuple[date, date]:
        min_day, max_day = min(revenue), max(revenue)
        end = end or max_day
        start = start or end
        if start > end:
            raise ValueError(f"start {start} after end {end}")
        if start < min_day or end > max_day:
            raise ValueError(
                f"window {start}..{end} outside baseline coverage {min_day}..{max_day}"
            )
        return start, end

    # ------------------------------------------------------------------
    # Skin population
    # ------------------------------------------------------------------
    def build_profiles(self, skin_rows: list[dict[str, Any]]) -> list[SkinProfile]:
        profiles: list[SkinProfile] = []
        for skin in skin_rows:
            quality = (skin.get("quality") or "").strip()
            price, basis = parse_price_cny(skin.get("price_text"), quality)
            release = None
            raw_release = (skin.get("online_date") or "").strip()
            if raw_release:
                try:
                    release = date.fromisoformat(raw_release)
                except ValueError:
                    release = None
            profiles.append(
                SkinProfile(
                    source_key=str(skin["source_key"]),
                    quality=quality,
                    unit_price=price,
                    price_basis=basis,
                    weight=TIER_WEIGHT.get(quality, 1.0),
                    release_date=release,
                    free=is_free_acquire(skin.get("acquire_method")),
                )
            )
        profiles.sort(key=lambda profile: profile.source_key)
        return profiles

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------
    def generate(
        self,
        *,
        start: date | None = None,
        end: date | None = None,
        days: int | None = None,
        seed: int = 20260807,
        baseline_batch: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if days is not None and start is not None:
            raise ValueError("days cannot be combined with start; use days with end only")
        baseline_id, revenue, currency = self.revenue_baseline(baseline_batch)
        if days is not None:
            end_day = end or max(revenue)
            start_day = end_day - timedelta(days=days - 1)
        else:
            start_day, end_day = start, end
        start_day, end_day = self.resolve_window(revenue, start=start_day, end=end_day)
        day_list = [start_day + timedelta(offset) for offset in range((end_day - start_day).days + 1)]

        skin_rows = self.skins.list_skins(limit=None)
        profiles = self.build_profiles(skin_rows)
        n = len(profiles)
        order = np.arange(n)
        keys = [profile.source_key for profile in profiles]
        tier_weights = np.array([profile.weight for profile in profiles], dtype=float)
        base_prices = np.array([profile.unit_price for profile in profiles], dtype=float)
        release_dates = [profile.release_date for profile in profiles]
        paid_mask = np.array([not profile.free for profile in profiles], dtype=bool)
        limited_mask = np.array(
            ["限定" in profile.quality or profile.weight >= TIER_WEIGHT["传说"] for profile in profiles]
        )

        rng = np.random.default_rng(seed)
        # Deterministic draw order: hero priors, per-skin noise, rerun events.
        hero_of = {str(skin["source_key"]): str(skin["hero_id"]) for skin in skin_rows}
        hero_ids = sorted(set(hero_of.values()) or {"0"})
        hero_prior = {hero: float(rng.gamma(2.0, 0.5)) for hero in hero_ids}
        skin_hero = np.array([hero_prior[hero_of[key]] for key in keys])
        static_noise = rng.lognormal(mean=0.0, sigma=0.6, size=n)

        # Rerun ("返场") events for limited skins: short windows with a demand boost.
        rerun_boost = np.zeros((len(day_list), n), dtype=float)
        rerun_candidates = order[limited_mask & paid_mask]
        for index in rerun_candidates:
            if rng.random() < 0.2:
                latest = len(day_list) - 3
                if latest > 2:
                    offset = int(rng.integers(0, latest))
                    length = int(rng.integers(3, 7))
                    rerun_boost[offset : offset + length, index] = 3.5

        synth_batch = f"synth-{seed}-{start_day.isoformat()}-{end_day.isoformat()}"
        created_at = time.time()
        rows: list[tuple[Any, ...]] = []
        synth_by_day: dict[date, float] = {}

        for day_index, day in enumerate(day_list):
            total = revenue[day]
            ages = np.array(
                [(day - release).days if release is not None else 3650 for release in release_dates],
                dtype=float,
            )
            active = ages >= 0
            spike_amp = np.minimum(6.0, 0.8 * tier_weights)
            recency = 1.0 + spike_amp * np.exp(-np.clip(ages, 0.0, None) / LAUNCH_SPIKE_TAU_DAYS)
            weights = tier_weights * skin_hero * static_noise * recency * (1.0 + rerun_boost[day_index])
            eligible = active & paid_mask & (base_prices > 0)
            weights = np.where(eligible, weights, 0.0)
            weight_sum = weights.sum()
            shares = np.zeros(n)
            if weight_sum > 0:
                shares = weights / weight_sum * total

            # Occasional discount events change the unit price (revenue share unchanged).
            discount_pct = np.zeros(n)
            roll = rng.random(n)
            discount_roll = rng.random(n)
            hit = eligible & (roll < 0.02)
            discount_pct[hit] = np.where(discount_roll[hit] < 0.5, 10.0, np.where(discount_roll[hit] < 0.8, 20.0, 50.0))
            prices = np.round(base_prices * (1.0 - discount_pct / 100.0), 1)
            prices = np.where(eligible, prices, 0.0)

            units = np.zeros(n, dtype=np.int64)
            if eligible.any():
                potential = np.zeros(n)
                potential[eligible] = shares[eligible] / prices[eligible]
                units = np.floor(potential).astype(np.int64)
                # Largest-remainder: top up units until the residual drops below the
                # cheapest eligible price, keeping the daily sum close to baseline.
                fracs = potential - units
                residual = total - float((units * prices).sum())
                # Greedy largest-remainder top-up: add a unit whenever it shrinks
                # |residual|, so the final gap stays below half the cheapest price.
                for index in np.argsort(-fracs):
                    if not eligible[index]:
                        continue
                    price = float(prices[index])
                    if abs(residual - price) < abs(residual):
                        units[index] += 1
                        residual -= price

            day_revenue = np.round(units * prices, 2)
            synth_by_day[day] = float(day_revenue.sum())
            for i in range(n):
                rows.append(
                    (
                        synth_batch, keys[i], day.isoformat(), int(units[i]),
                        float(prices[i]), float(day_revenue[i]), float(discount_pct[i]),
                        profiles[i].quality,
                        None if release_dates[i] is None else int(ages[i]),
                        profiles[i].price_basis if eligible[i] else "free_or_inactive",
                        created_at,
                    )
                )

        deviations = [
            abs(synth_by_day[day] - revenue[day]) / max(revenue[day], 1.0) for day in day_list
        ]
        max_deviation = max(deviations) if deviations else 0.0
        report = {
            "synth_batch": synth_batch,
            "baseline_batch": baseline_id,
            "currency": currency,
            "period_start": start_day.isoformat(),
            "period_end": end_day.isoformat(),
            "day_count": len(day_list),
            "skin_count": n,
            "row_count": len(rows),
            "total_baseline_revenue": round(sum(revenue[day] for day in day_list), 2),
            "total_synthesized_revenue": round(sum(synth_by_day.values()), 2),
            "max_daily_deviation": round(max_deviation, 6),
            "mean_daily_deviation": round(sum(deviations) / len(deviations), 6) if deviations else 0.0,
            "seed": seed,
            "generator_version": GENERATOR_VERSION,
        }
        if not dry_run:
            self.repo.save_daily_rows(synth_batch, rows)
            self.repo.save_batch(
                {
                    "synth_batch": synth_batch,
                    "seed": seed,
                    "period_start": start_day.isoformat(),
                    "period_end": end_day.isoformat(),
                    "baseline_batch": baseline_id,
                    "generator_version": GENERATOR_VERSION,
                    "params": {
                        "tier_default_price": TIER_DEFAULT_PRICE,
                        "tier_weight": TIER_WEIGHT,
                        "free_acquire_keywords": list(FREE_ACQUIRE_KEYWORDS),
                        "launch_spike_tau_days": LAUNCH_SPIKE_TAU_DAYS,
                        "currency": currency,
                    },
                    "skin_count": n,
                    "day_count": len(day_list),
                    "row_count": len(rows),
                    "total_revenue": report["total_synthesized_revenue"],
                    "max_daily_deviation": max_deviation,
                }
            )
        return report
