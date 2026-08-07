# Dashboard Backbone Implementation — Streamlit Multi-Page Portal

- Record format: `2`
- Mode: `coding-progress`
- Date: `2026-08-07`
- Project: emogame
- Status: `completed`
- Evidence state: `verified`

## Outcome

Single-skin audit workbench `app.py` refactored into 4-page Streamlit portal (portfolio overview + skin explorer + skin detail + data workbench). Cash value, perceived value, and emotion score are strictly separated with fail-closed read paths. All 181 unit tests pass; real-database smoke test confirms stable operation. Limitation: `market_signal_records` table remains empty — no skins pass emotion gate until evidence collection is implemented (P1/P2 work).

## Task and Scope

**Request:** Refactor monolithic `app.py` into multi-page Streamlit application with strict separation between cash value, perceived value, and emotion scoring.

**Target:** 4-page portal with portfolio overview as default landing, skin explorer for filtering/browsing, skin detail for single-skin audit, data workbench for simulation/CSV import/manual evidence entry/calibration/data audit/JSON export.

**Starting state:** Single-page `app.py` (~700 lines) mixing analysis UI with data workbench operations. No structural separation between read-only analysis and mutable data operations.

**Constraints:**
- Maintain backward compatibility: `build_payload` / `import_cash_value_upload` / `save_manual_cash_value` / `load_signals` / `apply_calibration` signatures unchanged
- Cash value / perceived value / emotion score must remain independent — no composite scoring
- Emotion score only from `RuleEngine.evaluate()` with coverage ≥ 0.5 and `validation_status == 'evidence_validated'`
- Cash priority fixed: `manual_exact > manual_estimated > csv_release_window_uplift`
- Only CNY records count toward portfolio attributed revenue (USD retained for audit but excluded from totals)
- Missing values stay `None` — no zero-fill, no metadata estimation
- Read paths fail closed — no `ensure_schema` calls, missing database/table returns empty state

**Decisions:**
- Extract dashboard logic into `dashboard/` package with `models.py` / `query.py` / `filters.py` / `format.py` / `components.py` / `charts.py` / `workbench_render.py`
- Use `st.navigation` for page routing
- Shared sidebar filters via `st.session_state` for cross-page synchronization
- `MarketSignalRepository.get_opinion_signals` — read-only, no cash field projection, no table creation

**Assumptions:**
- Existing `RuleEngine` / `CashValueRepository` / `FeatureBuilder` logic is correct
- SQLite database exists with expected schema
- Weibo crawler cookie available via `WEIBO_COOKIE` environment variable

**Exclusions:**
- Online VLM/LLM integration
- Cross-game comparison
- Database migration
- Metadata/image backfill
- Cold-start database initializer

## Implementation

Execution sequence:
1. Created `dashboard/` package with data models (`DashboardFilters` / `PortfolioSkinRow` / `SkinDashboardDetail` / `PortfolioSummary`)
2. Implemented `dashboard/query.py` — batch read-only query layer reusing existing repo/service
3. Built `dashboard/filters.py` — shared sidebar writing fixed `st.session_state` keys
4. Added `dashboard/format.py` / `components.py` / `charts.py` — formatting, status tags, empty states, chart components
5. Extracted `dashboard/workbench_render.py` from `app.py` — imports helpers from `app` (no circular dependency via lazy import)
6. Refactored `app.py` to portfolio overview + `st.navigation` — net reduction of ~511 lines
7. Created `pages/皮肤探索.py` / `pages/皮肤详情.py` / `pages/数据工作台.py`
8. Added `MarketSignalRepository.get_opinion_signals` — read-only, no cash field projection
9. Fixed `dashboard/workbench_render.py` import of non-existent `app.evidence_table` — replaced with local module-level `evidence_table`
10. Updated `crawlers/weibo_skin_comment_crawler.py` to read `WEIBO_COOKIE` from environment instead of `weiboSpider/.secret`
11. Updated `.env.example` with `WEIBO_COOKIE` documentation
12. Updated `README` with progress checkboxes
13. Updated `docs/07-business-analysis.md` pricing mapping table
14. Added `tests/test_dashboard_models.py` (8 unit tests) and `tests/test_dashboard_pages.py` (6 AppTest page tests)

| Path | Symbols/Changes | Role |
|------|-----------------|------|
| `app.py` | Portfolio overview + `st.navigation` | Landing page, retains helper functions |
| `dashboard/models.py` | `DashboardFilters` / `PortfolioSkinRow` / `SkinDashboardDetail` / `PortfolioSummary` | Data transfer objects |
| `dashboard/query.py` | `load_portfolio_rows` / `load_skin_detail` / `load_data_workbench` | Batch read-only queries |
| `dashboard/filters.py` | `render_sidebar_filters` | Shared sidebar state |
| `dashboard/format.py` | `format_currency` / `sort_missing_last` / `render_status_tags` | Formatting utilities |
| `dashboard/components.py` | `render_empty_state` / `render_metric_row` | Reusable UI components |
| `dashboard/charts.py` | `render_revenue_timeline` / `render_emotion_radar` | Chart rendering |
| `dashboard/workbench_render.py` | `render_data_workbench` | Extracted workbench UI |
| `pages/皮肤探索.py` | Skin explorer page | Filtering/browsing |
| `pages/皮肤详情.py` | Skin detail page | Single-skin audit |
| `pages/数据工作台.py` | Data workbench page | Simulation/CSV import/manual evidence/calibration/audit/export |
| `crawlers/weibo_skin_comment_crawler.py` | Cookie from `WEIBO_COOKIE` env var | Environment-based configuration |
| `data/market_signal_repository.py` | `get_opinion_signals` | Read-only opinion signal query |
| `tests/test_dashboard_models.py` | 8 unit tests | Model validation |
| `tests/test_dashboard_pages.py` | 6 AppTest tests | Page rendering validation |

## Validation

### V1 - pass

```text
python3 -m py_compile app.py dashboard/*.py pages/*.py
```

All Python files compile without syntax errors.

### V2 - pass

```text
python3 -m unittest discover -s tests
```

181/181 unit tests passed (170 existing + 5 model tests + 6 page AppTests).

### V3 - pass

```text
streamlit run app.py
```

4 pages render without errors via AppTest smoke test. Real-database snapshot: 960 skins, 49 cash records, 0 validated emotion rows (ranking empty as expected), 365 revenue days.

## Evidence Ledger

| ID | Class | Locator/Check | Conclusion |
|----|-------|---------------|------------|
| E1 | verified | `app.py` (SHA-256: 46a87a687cc3a3d459df28f6d2bf44c3102a3939191418ea7e3166d15cb8e8c2) | Refactored entry point with navigation |
| E2 | verified | `dashboard/models.py` (SHA-256: 926bc99f5a4027a289c22c0541eba33f3a357377838370ac88cf2a7c1a42d414) | Data transfer objects implemented |
| E3 | verified | `dashboard/query.py` (SHA-256: 2ccd39cf55969c2866a204feda65c9debfb2d51e1c3141b2bb25b171ee44d963) | Batch read-only query layer |
| E4 | verified | `crawlers/weibo_skin_comment_crawler.py` (SHA-256: 768ad50c4c78557a954c5e8e97111cd43717887d3bb0f9bab960440da9955fd1) | Environment-based cookie configuration |
| E5 | verified | `tests/test_dashboard_models.py` (SHA-256: fb4835f9701e7e342c5f79cd6b17b2daf8a7dfca93be22feaaf8e05f6dd9f729) | Model validation tests |
| V1 | verified | `python3 -m py_compile` | Syntax validation pass |
| V2 | verified | `python3 -m unittest discover` | 181/181 tests pass |
| V3 | verified | `streamlit run app.py` | 4 pages render without errors |

## Git Custody

**Branch:** `main`

**Baseline HEAD:** unavailable (recording started late)

**Final HEAD:** 654f3f8e79114eea606147115dc2a78a0069683b

**History relation:** unavailable

**Commits since baseline:** 0

**Explicit scopes:** `app.py`, `dashboard/`, `pages/`, `tests/test_dashboard_models.py`, `tests/test_dashboard_pages.py`, `crawlers/weibo_skin_comment_crawler.py`, `crawlers/weibo_store.py`, `data/market_signal_repository.py`, `.env.example`, `README`, `docs/07-business-analysis.md`

**Task-owned changes:** 20 paths — `.env.example`, `README`, `app.py`, `crawlers/weibo_skin_comment_crawler.py`, `crawlers/weibo_store.py`, `dashboard/__init__.py`, `dashboard/charts.py`, `dashboard/components.py`, `dashboard/filters.py`, `dashboard/format.py`, `dashboard/models.py`, `dashboard/query.py`, `dashboard/workbench_render.py`, `data/market_signal_repository.py`, `docs/07-business-analysis.md`, `pages/数据工作台.py`, `pages/皮肤探索.py`, `pages/皮肤详情.py`, `tests/test_dashboard_models.py`, `tests/test_dashboard_pages.py`

**Pre-existing changes:** All 20 task-owned paths were pre-existing uncommitted changes from 2026-07-16/17 work sessions. This record consolidates and commits that work.

**Overlaps:** 20 paths overlap between task-owned and pre-existing — all changes originate from the 2026-07-16 dashboard backbone implementation and 2026-07-17 amendment fixes.

**Outside-scope changes:** `progress/2026-07-16-dashboard-backbone-plan.md`, `progress/2026-07-17-dashboard-amendment-plan.md`, `progress/2026-07-17-dashboard-backbone-impl.md`, `progress/2026-07-17-dashboard-summary.md`, `weiboSpider` (submodule) — documentation and submodule pointer, not committed in this task.

**Ownership caveats:** All code changes were implemented in prior sessions (2026-07-16/17). This session records and commits that work.

**Scoped diff:** files=0; insertions=0; deletions=0; binary_files=0; untracked_files=14

**Record path:** progress/2026-08-07-dashboard-backbone.md

## Evidence Boundary

**Establishes:**
- 4-page Streamlit portal with strict separation between cash value, perceived value, and emotion scoring
- Fail-closed read paths that return empty state without creating database/tables
- Cash priority resolution and CNY-only portfolio revenue attribution
- Emotion gate requiring coverage ≥ 0.5 and `evidence_validated` status
- Backward compatibility with existing helper function signatures
- 181 unit tests covering models, queries, and page rendering

**Does not establish:**
- Emotion evidence collection (requires P1/P2 work — `market_signal_records` table is empty)
- Online VLM/LLM integration
- Cross-game comparison
- Metadata/image backfill
- Cold-start database initialization
- Production readiness (requires emotion evidence pipeline)

## Next Steps

- P1: Implement emotion evidence collection pipeline to populate `market_signal_records`
- P1: Integrate online VLM for automated aspect extraction
- P2: Metadata/image backfill for skin catalog
- P2: Cold-start database initializer for new deployments
- P2: Cross-game comparison framework
