# Sven-Bo dashboard adaptation

- Record format: `3`
- Record ID: `RCP-20260915T075051Z-b2f7c108`
- Mode: `coding-progress`
- Task type: `feature`
- Task slug: `sven-bo-dashboard`
- Implementation class: `fresh-implementation`
- Date: `2026-09-15`
- Project: /home/mzhyui/git/emogame
- Priority: `unspecified`
- Owner: Unassigned
- Components: None
- Labels: None
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-15T07:50:51Z`
- Started at: `2026-09-15T07:50:51Z`
- Updated at: `2026-09-15T10:47:11Z`
- Completed at: `2026-09-15T10:47:11Z`
- Due date: Not applicable
- Evidence state: `verified`
- Validation state: `partial`

## Outcome

The portfolio landing page now uses the Sven-Bo sales-dashboard visual language while retaining EmoGame's SQLite data, evidence rules, multipage navigation and public helper imports. A validated structured query interface is available for future agent-assisted analysis. Focused validation passed, and follow-up verification completed the full suite and live server HTTP smoke successfully.

## Task and Scope

The approved plan selected visual adaptation and a portfolio-overview landing page. The upstream submodule is retained at commit `d436bc8c4fbc6424828adccad792b4f46c2e8a84`; its supermarket workbook remains a reference asset and is not the production data source.

## Lifecycle

Current blocker: None

| ID | At | Action | From | To | Actor | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | 2026-09-15T07:50:51Z | created | none | in-progress | record-tool | record created |
| L2 | 2026-09-15T08:11:05Z | transition | in-progress | done | codex | Implemented and focused-validated the adapted sales-dashboard layout; full suite and live server smoke remain environment-limited. |
| L3 | 2026-09-15T10:46:57Z | resume | done | in-progress | codex | Follow-up verification completed the previously partial full-suite and Streamlit smoke checks. |
| L4 | 2026-09-15T10:47:11Z | transition | in-progress | done | codex | Follow-up verification resolved the previous environment-limited checks: full suite and live HTTP smoke now pass. |

## Implementation

### Plan and Starting Status

The starting worktree already contained the user-owned `.gitmodules` change and `streamlit-sales-dashboard` submodule. The implementation added the adapted layout, hero filtering, Plotly charts, grouped analysis controls, and tests.

### Core Functions and Result

- `app.py` presents three sales-style KPIs, filters, charts, ranking, timeline, coverage notes, and CSV/query downloads.
- `dashboard/analysis.py` validates `skin_portfolio` specifications and computes grouped results with explicit scored and CNY-revenue denominators.
- `dashboard/query.py`, `dashboard/filters.py`, and the explorer page support the exact hero filter through the read-only query path.
- `dashboard/charts.py` separates currencies and release counts, preserves missing revenue gaps, omits incomplete scatter points, and keeps zero values.
- `docs/dashboard-sales-layout.md` documents the integration and query contract.

## Interface and Behavior Changes

`DashboardFilters` gains optional `hero_name`. `get_portfolio_rows` gains the matching optional filter. The structured query contract accepts only the `skin_portfolio` dataset, `current_portfolio` evidence scope, `hero_name` or `quality` grouping, approved metrics, and `DashboardFilters` fields. It returns grouped rows plus source keys and rejects unknown fields, invalid dates, and reversed periods before database access.

## Validation

### Test Result

Aggregate validation is `partial` because the initial V4 and V5 attempts remain recorded as partial historical checks; the follow-up V6 and V7 checks pass.

### V1 - pass

```text
.venv/bin/python -m unittest discover -s tests -p test_dashboard_sales_layout.py
```

Observed: 9 focused dashboard layout, query validation, empty-state, filter, and chart tests pass.

### V2 - pass

```text
.venv/bin/python -m py_compile app.py dashboard/*.py
```

Observed: Python compilation passes for app and dashboard modules.

### V3 - pass

```text
git diff --check
```

Observed: no whitespace errors.

### V4 - partial

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: the run started but did not complete within the available run; no final aggregate result was captured. A verbose reproduction stopped at `test_evaluate_reads_latest_profile_without_publication` while Starlette `TestClient` waited on its AnyIO portal. A stale interrupted runner was also found and stopped during follow-up cleanup.

### V5 - partial

```text
streamlit run app.py --server.headless true --server.port 8511
```

Observed: sandbox socket binding was denied with `PermissionError: [Errno 1] Operation not permitted`; no application exception was observed from attempted startup. This is a sandbox policy restriction on local server sockets.

### V6 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: 312 tests completed in 28.665 seconds; all tests pass.

### V7 - pass

```text
temporary Streamlit server plus HTTP probes on 127.0.0.1:8511
```

Observed: Streamlit started, `/_stcore/health` returned HTTP 200 and the root returned HTTP 200 HTML, then the temporary process stopped.

## Evidence Ledger

| ID | Kind | Classification | Locator / result |
| --- | --- | --- | --- |
| E1 | repository | verified | app.py and dashboard implementation files |
| E2 | repository | verified | streamlit-sales-dashboard submodule at d436bc8c4fbc6424828adccad792b4f46c2e8a84 |
| E3 | user | user-stated | approved implementation plan: adapt visual design, portfolio overview landing page |
| V1 | check | verified | 9 focused tests pass |
| V2 | check | verified | compilation passes |
| V3 | check | verified | No whitespace errors |
| V4 | check | verified | Started but did not complete within the available run; no final aggregate result |
| V5 | check | verified | Sandbox denied socket binding; no application exception was observed |
| V6 | check | verified | 312 tests completed in 28.665 seconds; all tests pass |
| V7 | check | verified | Streamlit started, /_stcore/health returned HTTP 200 and root returned HTTP 200 HTML, then process stopped |

## Git Custody

Repository `/home/mzhyui/git/emogame`; branch `main`; baseline and final HEAD `5e3cb06991484073b58abba766fc2a727dd104d7`; history relation `same`; no commit was created. Task-owned paths are app.py, dashboard/analysis.py, dashboard/charts.py, dashboard/filters.py, dashboard/models.py, dashboard/query.py, docs/dashboard-sales-layout.md, pages/皮肤探索.py, progress/2026-09-15-sven-bo-dashboard.md, and tests/test_dashboard_sales_layout.py. Pre-existing paths outside scope are .gitmodules and streamlit-sales-dashboard. Record path: progress/2026-09-15-sven-bo-dashboard.md. Scoped diff: `files=6; insertions=187; deletions=122; binary_files=0; untracked_files=3`.

## Evidence Boundary

This validates application interfaces, focused rendering behavior and deterministic aggregation. It does not establish production deployment, browser rendering in this sandbox, external data freshness, or scientific validity of emotion scores.

## Next Steps

The complete suite and local HTTP smoke now pass when executed with local thread and
socket permissions. A browser-level inspection can be added separately if visual
review is needed. Review the intentionally pre-existing submodule changes before
staging or committing.
