# Sales Volume, Acquisition Spend, and Cash-Value Implementation Plan

## Goal

Add an auditable cash-value layer supporting:

- Persistent manual entry of `sales_volume` and `avg_spend_to_obtain`.
- Import of `data/WZRY_IPHONE_revenue.csv`.
- Skin-level incremental-revenue approximation using release-window uplift.
- Estimated sales volume derived from attributed revenue and acquisition spend.
- Period charts, provenance, confidence, and value-for-money reporting.
- Backward compatibility with the existing evaluation, sales-gap, and calibration interfaces.

The CSV is treated as estimated China-region iPhone app revenue in CNY. It must never be presented as exact skin revenue.

## Data and Public Interfaces

- Add append-only `app_revenue_daily` records containing date, platform, market, currency, estimated revenue, source, import batch, and source-file hash.
- Add period-scoped `skin_value_records` containing:
  - `source_key`, period boundaries, `sales_volume`, and volume relation: `exact` or `estimated`.
  - Average spend in original currency plus canonical CNY.
  - Baseline revenue, signed uplift, attributed non-negative revenue, FX rate, attribution method, confidence, notes, and provenance.
- Keep `MarketValidationSignals.sales_volume` and `avg_spend_to_obtain` compatible:
  - `avg_spend_to_obtain` is formally defined as canonical CNY.
  - Resolution priority is manual exact, manual estimated, CSV approximation, then legacy aggregate.
  - Existing evidence is preserved and never silently overwritten.
- Add:
  - `POST /api/skins/{source_key}/value-records` for persistent manual evidence.
  - `GET /api/skins/{source_key}/cash-value?start=&end=` for period results.
  - A CLI and Streamlit CSV uploader using the same import and attribution service.
- Manual UI fields: period, sales volume, relation, average spend, currency, CNY-per-USD rate when needed, confidence, and notes. Keep the existing non-persistent simulation mode separate.

## Attribution and Value Calculations

- Import the BOM-prefixed Chinese CSV headers, validate ISO dates and non-negative amounts, and upsert idempotently by game, platform, date, and source.
- Use each skin's official `online_date`:
  - Baseline: median revenue for matching weekdays across the preceding 28 days.
  - Attribution window: release day through day 6.
  - Truncate a window the day before the next release cohort to prevent double counting.
  - Require at least 21 baseline days and 5 attribution days; otherwise return insufficient coverage.
  - Preserve signed uplift for diagnostics but attribute `max(0, actual - baseline)`.
- For multiple skins released together, accept optional positive allocation weights. Otherwise divide equally and mark reduced confidence.
- Resolve average acquisition spend in this order:
  1. Period-matched manual spend.
  2. Explicit cash or point price, converting `10点券 = ¥1`; for mixed fragments and points, prioritize the point price.
  3. Quality override.
  4. Official-tier default.
- Ship an editable JSON CNY cost configuration:
  - Tier defaults: `0=28.8`, `1=48.8`, `2=88.8`, `3=128.8`, `4=168.8`, `5=500`.
  - Quality overrides: `无双/无双限定=500`, `荣耀典藏=2000`.
  - Free or event acquisition resolves to zero and does not produce a unit estimate.
  - Shard-only acquisition remains unknown.
  - Manual gacha spend or pity amount overrides every default.
- Preserve the CSV revenue currency. Native CNY imports require no FX; USD imports require and store `cny_per_usd`. Calculate:
  - CNY: `estimated_sales_volume = round(attributed_revenue_cny / avg_spend_cny)`.
  - USD: `estimated_sales_volume = round(attributed_revenue_usd * cny_per_usd / avg_spend_cny)`.
  - No sales-volume estimate when spend is zero or unknown.
  - Revenue lift percentage against baseline.
  - Effective spend per estimated unit.
  - Emotional-value efficiency: evaluation score per ¥100 acquisition spend, clearly labeled descriptive rather than monetary valuation.
- Confidence starts at `0.60` for CSV estimates, with penalties for equal overlap allocation, tier-default spend, truncated windows, or incomplete data; cap CSV-derived confidence at `0.75`. Manual confidence is operator-supplied.
- Extend the sales report with period revenue, estimated units, spend basis, lift, confidence, warnings, and a daily baseline-versus-observed chart. Keep emotional score and cash metrics visually separate.

## Test and Acceptance Plan

- CSV tests: BOM headers, the current 365-row file, descending dates, malformed numbers, duplicate imports, native CNY, missing USD FX, and source-hash idempotency.
- Attribution tests: weekday baseline, negative uplift, incomplete windows, next-release truncation, same-day equal or weighted allocation, and conservation. Allocated daily revenue must never exceed positive app uplift.
- Spend tests: manual precedence, point conversion, mixed shard and point text, quality or tier fallback, gacha override, free or event behavior, USD/CNY conversion, and unknown spend.
- Interface tests: persistent manual save and read, period filtering, provenance display, API validation, compatibility aggregation, and simulation remaining non-persistent.
- End-to-end acceptance:
  - Import all 365 CSV rows.
  - Produce estimates only for eligible releases within the CSV period.
  - Every estimate exposes method, period, currency, conversion basis, spend basis, and confidence; FX is present only when conversion is required.
  - Reimporting the same file creates no duplicate facts.
  - All existing 146 tests continue to pass.

## Assumptions and Boundaries

- The supplied CSV is an estimated China-region iPhone revenue series in CNY.
- CSV attribution measures incremental release-window revenue, not total lifetime skin revenue.
- Tier costs are configurable assumptions, not observed facts, and always lower confidence.
- Taxes, refunds, platform fees, marketing costs, net revenue, ROI, pricing recommendations, and bundle simulations remain outside this Cash MVP.
- Schema creation is additive through the repository's existing `ensure_schema` pattern; the current SQLite database is not dropped.

## 2026-07-16 Currency Correction and Import Result

The source owner confirmed that `data/WZRY_IPHONE_revenue.csv` is a China-region
iPhone revenue series in CNY. The implementation now treats CNY as the native
default and keeps USD plus `cny_per_usd` only as an optional compatibility path.
The old non-null FX batch schema is migrated without losing existing facts.

The production import command was:

```bash
.venv/bin/python scripts/import_cash_value.py \
  data/WZRY_IPHONE_revenue.csv \
  --db data/wzry_skins/skins.sqlite3 \
  --currency CNY \
  --market CN
```

Verified production state:

- Import batch: `revenue-99873cc59b5b2d27`.
- Source SHA-256: `99873cc59b5b2d27cbeceedc68ad34806c04af6ebfea2aabf9984d0b1f0fb4a6`.
- 365 daily facts from `2025-07-16` through `2026-07-15`.
- Total recorded estimated revenue: `¥683,849,794`.
- 49 eligible skin-window records; 42 releases rejected for insufficient coverage.
- All 49 records use `revenue_currency=CNY`, `conversion_basis=native_cny`, and no FX.
- 38 records have mechanical unit estimates; zero/free or unknown shard costs do not.
- Reimport reads 365 rows, inserts zero new facts, and leaves one batch and 49 value records.

The release-window result remains an attribution approximation. In particular,
positive-day uplift can coexist with a non-positive signed window, so the signed
uplift and warnings must remain visible and estimates must not be described as
exact skin sales.
