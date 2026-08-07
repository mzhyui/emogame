# Invoice Data Synthesis Workflow (Simulated Invoice↔Heroskin Analysis)

- Date: `2026-08-07`
- Repository: `emogame` (/home/mzhyui/git/emogame)
- Status: `completed`

## Task and Target

- Task: Draft and implement a synthetic invoice data workflow that simulates invoice↔heroskin analysis, covering 7-day to 6-month data records (anchor-plans interview completed first; plan at `progress/2026-08-07-invoice-synthesis-plan.md`).
- Target: A seeded, reproducible generator producing one daily invoice aggregate per skin (units, unit price, gross revenue) whose per-day sums reconcile to the real `app_revenue_daily` iPhone revenue baseline, stored with batch/seed provenance and consumable by score-vs-sales gap analysis.

## Task Context

- Starting state: Repo had aggregate sales evidence only — `app_revenue_daily` (365 days, 2025-07-16..2026-07-15, CNY) and 49 period-level `skin_value_records`; no transaction/daily invoice concept anywhere. Skin catalog: 960 skins in `data/wzry_skins/skins.sqlite3`.
- Constraints: SQLite + Python 3.12 repo conventions (repository classes with `ensure_schema`, `scripts/*.py` CLIs, provenance columns); synthetic data must never be written into real evidence tables (`market_signal_records`, `opinion_evidence_items`, `skin_value_records`); windows must stay inside baseline coverage.
- Decisions and assumptions (locked via anchor-plans interview): invoice unit = daily per-skin aggregate; purpose = E2E pipeline simulation; configurable window with 7d/30d/90d/180d presets; top-down reconciliation to real totals; all 960 skins × every day (zero rows for unreleased/free); seeded determinism. Declared assumptions: paid-transaction convention (free acquisitions = 0 rows); iPhone-only baseline; currency follows `revenue_import_batches.currency`; missing `online_date` treated as available; unparseable `price_text` falls back to quality-tier default prices.
- Out of scope: per-transaction order data, player dimension, Android/other channels, dashboard page, wiring synthetic volumes into `calibrate_sales_score.py`.

## Implementation Guidelines

- Followed `data/cash_value.py` patterns: repository class owning `ensure_schema`, additive tables in `skins.sqlite3`, batch-level provenance (params JSON, seed, deviation stats).
- Top-down allocation keeps daily totals anchored to real revenue: distribute shares by weight, `units = floor(share/price)`, then greedy largest-remainder top-up that only adds a unit when it shrinks `|residual|` (final gap ≤ half the cheapest eligible price).
- Determinism: single `numpy.random.default_rng(seed)` with fixed draw order (hero priors → per-skin noise → rerun events → per-day discount rolls); batch id = `synth-{seed}-{start}-{end}`; regeneration replaces batch rows (idempotent).
- CLI mirrors existing `scripts/` argparse style with ROOT path bootstrap.

## Execution Process

1. Anchored scope via 3 interview rounds (objective / constraints / execution); scanned repo schemas (`skins`, `app_revenue_daily`, `revenue_import_batches`) to lock facts before asking.
2. Implemented `data/invoice_synthesizer.py` (profiles, weight model, price parsing, generator, repository).
3. Implemented `scripts/synthesize_invoices.py` CLI; smoke-tested 7d dry-run, then real write; verified row counts and zero pre-release violations.
4. Wrote `tests/test_invoice_synthesizer.py`; fixed an `UnboundLocalError` in window resolution and a largest-remainder early-break bug found by tests; fixed missing schema on dry-run DB reads by ensuring schema in repository `__init__`.
5. Verified determinism by content-hash comparison across regenerations (excluding `created_at`); ran 180d acceptance run with `--report`.
6. Delivered analysis-ready outputs (`--summary`, `--export-csv`), documented SQL join pattern and a worked `sales_above_score` example; updated `CLAUDE.md` status and memory notes.

## Core Code and Functions

| Path | Symbol or section | Change | Role in the result |
|---|---|---|---|
| `data/invoice_synthesizer.py` | `InvoiceSynthesizer.generate` | New: seeded top-down distribution of each day's real revenue across skins (tier weight × hero prior × lognormal noise × launch spike × rerun events), unit derivation with largest-remainder reconciliation | The generator; produces all invoice rows and the deviation report |
| `data/invoice_synthesizer.py` | `SyntheticInvoiceRepository` | New: `synth_invoice_batches` / `synthetic_invoice_daily` schema, batch + row persistence, `reconciliation`, `skin_summary` | Storage, provenance, and analysis-ready aggregation |
| `data/invoice_synthesizer.py` | `parse_price_cny`, `is_free_acquire` | New: 点券→CNY parsing with tier-default fallback; free-acquisition classification | Price and eligibility contract for invoice rows |
| `scripts/synthesize_invoices.py` | `main` | New CLI: presets, explicit windows, seed, `--dry-run/--report/--json/--summary/--export-csv/--list-batches` | User-facing workflow entry |
| `tests/test_invoice_synthesizer.py` | `InvoiceSynthesizerTests` | New: 11 cases (row counts, reconciliation ≤1%, pre-release zeros, free-skin zeros, determinism, idempotency, window rejection, metadata, summary) | Regression guard for the whole workflow |
| `CLAUDE.md` | 当前状态 | +1 line announcing the workflow | Project status stays truthful |

## Input and Output Constraint Shifts

| Surface | Before | After | Compatibility or failure behavior |
|---|---|---|---|
| `skins.sqlite3` schema | No invoice tables | `synth_invoice_batches`, `synthetic_invoice_daily` (UNIQUE batch×skin×date) | `CREATE TABLE IF NOT EXISTS`; existing tables untouched |
| CLI surface | No invoice entrypoint | `scripts/synthesize_invoices.py` | Windows outside baseline coverage raise a clear error with the available range; `--days` + `--start` rejected |
| Generated data contract | None | Daily rows for all 960 skins; free/unreleased rows carry units=0, `price_basis='free_or_inactive'` | Consumers can rely on full skin×day coverage |

## Outcome

- Result: Working, validated invoice synthesis workflow; two batches generated in the local DB (180d and 7d, seed 20260807).
- Deliverables: `data/invoice_synthesizer.py`, `scripts/synthesize_invoices.py`, `tests/test_invoice_synthesizer.py`, plan + record notes, `CLAUDE.md` status line, `outputs/invoice_summary_180d.csv` (gitignored).
- Limitations or unresolved items: Only ~2 of 960 skins have parseable `price_text`, so nearly all prices are tier defaults; synthetic volumes are not yet fed into calibration or a dashboard page (explicitly out of scope); DB/CSV artifacts are gitignored, so downstream users must regenerate batches locally.
- Evidence boundary: Validation proves the generator is deterministic, reconciles to the real iPhone baseline within 0.0285% daily, and respects release-date/free-acquisition rules on this catalog. It does not prove market realism of the per-skin distribution (weights are modeled assumptions), and no downstream model training used this data.

## Validation

| Command or check | Result | Interpretation |
|---|---|---|
| `python -m pytest tests/test_invoice_synthesizer.py -q` | passed | 11 passed |
| `python -m pytest tests/ -q` | passed | 192 passed, 0 failed (full suite) |
| `python scripts/synthesize_invoices.py --preset 180d --report` | passed | 172,800 rows (960×180); max daily deviation 0.0285% vs ≤1% criterion |
| `python scripts/synthesize_invoices.py --preset 7d` + SQL checks | passed | 6,720 rows; 0 pre-release positive-unit violations; 960 skins/day |
| Content-hash regeneration check (same seed) | passed | Identical row content across regenerations (`created_at` excluded by design) |
| `python -m pytest tests/test_cash_value.py tests/test_market_signals.py tests/test_skin_repository.py -q` | passed | 20 passed — neighboring data-layer suites unaffected |

## Git Information

- Branch: `main`
- Baseline HEAD: `a3ba68f`
- Final HEAD: available from Git history (record committed together with the implementation)
- Task commits: available from Git history (single commit for this task)
- Task-owned paths: `data/invoice_synthesizer.py`, `scripts/synthesize_invoices.py`, `tests/test_invoice_synthesizer.py`, `CLAUDE.md`, `progress/2026-08-07-invoice-synthesis-plan.md`, `progress/2026-08-07-invoice-synthesis-impl.md`
- Scoped diff summary: 3 new Python files (~1,050 lines incl. tests), +1 line in `CLAUDE.md`, 2 new progress notes
- Pre-existing worktree changes: `weiboSpider` submodule dirty; untracked July dashboard notes (`progress/2026-07-16-dashboard-backbone-plan.md`, `progress/2026-07-17-dashboard-*.md`) — excluded from the commit
- Remaining worktree status: pre-existing items above remain; task-owned paths clean after commit
- Ownership caveats: `data/wzry_skins/skins.sqlite3` and `outputs/` are gitignored — generated batches live only in the local DB
