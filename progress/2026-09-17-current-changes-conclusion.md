# Dashboard sales-layout conclusion

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-09-17`
- Project: /home/mzhyui/git/emogame
- Status: `completed`
- Evidence state: `verified`

## Outcome

The Sven-Bo sales-dashboard adaptation is complete and the worktree is ready to
commit. The overview page now uses a three-KPI header, Plotly charts in a shared
blue presentation, a hero filter, a ranking control and a structured analysis
expander; the three multipage files were renamed to ASCII filenames; and the
scored metric now carries one name (`综合价值分`) across KPIs, chart axes,
ranking controls and export columns.

Deliverables: 18 task-owned paths covering `app.py`, seven `dashboard/` modules,
three renamed `pages/` files, three test modules, `docs/dashboard-sales-layout.md`,
the pinned `streamlit-sales-dashboard` submodule and its `.gitmodules` entry, plus
the earlier task record. Validation passed: 312/312 tests, 9/9 focused tests,
clean compilation and clean whitespace checks.

Limitations: no browser-level rendering check was performed, and this record does
not establish production readiness, external data freshness, or the scientific
validity of the scores.

## Task and Scope

Request: conclude the accumulated dashboard work and commit it (user source E6).

Target: capture the full uncommitted dashboard state in one reviewed record and
one local commit, excluding unrelated work.

Starting state: the dashboard adaptation began 2026-09-15 and was never committed;
its earlier record, `progress/2026-09-15-sven-bo-dashboard.md`, documented an
approved plan to adapt the Sven-Bo visual language onto EmoGame's SQLite data while
retaining the evidence rules and multipage navigation. The worktree had since
advanced beyond that record: page files were renamed to ASCII names, the overview
was recomposed, the charts were rewritten on Plotly, and a hero filter plus the
structured query interface were wired into the page.

Decisions and assumptions: the whole dashboards change set is treated as one task
because it shares the same plan source and the same uncommitted origin. Per the
user decision taken during this session, the reporter-GRPO scripts are treated as
unrelated work with their own commit (E7, E8); because both were still being
edited when this record was finalised, they are left uncommitted instead.

Exclusions: no push, no dependency installation, no browser-level visual review,
and no change to scoring semantics.

## Implementation

### Plan and Starting Status

The confirmed plan is the one recorded in `progress/2026-09-15-sven-bo-dashboard.md`
and summarized in `docs/dashboard-sales-layout.md` (E4, E6): adapt the reference
layout's composition onto the existing read-only portfolio data rather than porting
the upstream app or its supermarket workbook. Before this work the overview was a
five-KPI, Streamlit-native chart page with a two-column chart grid and no hero
filter, and the query interface existed only as a module.

### Core Functions and Result

| Path | Symbol or section | Change | Role in the result |
| --- | --- | --- | --- |
| `app.py` | `_portfolio_overview` | Recomposed into three KPI tiles with denominators, a ranking radio, a timeline section and two expanders | Delivers the adapted landing page |
| `dashboard/analysis.py` | `build_query_spec`, `validate_query_spec`, `summarize_rows`, `execute_query_spec` | Validated read-only query interface with grouping and explicit coverage denominators | Backs the analysis expander and future agent queries |
| `dashboard/charts.py` | `render_sales_chart`, `release_revenue_timeline`, `emotion_vs_cash_scatter`, `top_value_ranking`, `quality_distribution_chart` | Rewritten on Plotly; currencies separated, missing revenue days kept as gaps, scatter requires both values | Shared visual language and honest missing-data handling |
| `dashboard/filters.py` | `render_filter_sidebar`, `current_filters` | Adds an exact hero selector backed by `get_hero_names` | Sidebar filter parity with the query contract |
| `dashboard/query.py` | `get_hero_names`, `get_portfolio_rows` | New read-only hero listing and optional `hero_name` filter | Supplies hero options without scoring or writing |
| `dashboard/format.py` | `SCORE_LABEL`, `MEAN_SCORE_LABEL`, `SCORE_SOURCE_LABEL` | Single naming source for the scored metric | Keeps one concept across KPIs, axes and exports |
| `dashboard/models.py` | `DashboardFilters` | Adds optional `hero_name` | Carries the hero filter through session state |
| `pages/skin_explorer.py`, `pages/skin_detail.py`, `pages/data_workbench.py` | page entry points | Renamed from Chinese filenames; explorer forwards the hero filter and switches to `pages/skin_detail.py` | ASCII page paths and filter continuity |
| `tests/test_dashboard_sales_layout.py` | layout test module | New coverage for layout, query validation, empty states, filters and charts | Focused regression net for this change |
| `docs/dashboard-sales-layout.md` | integration contract | Documents the layout, metric denominators and query specification | Durable contract for the next task |

## Interface and Behavior Changes

- `DashboardFilters` gains an optional `hero_name` field; `get_portfolio_rows`
  gains the matching keyword argument. Both default to no hero restriction, so
  existing callers keep their behavior.
- The multipage file paths change from Chinese to ASCII names
  (`pages/skin_explorer.py`, `pages/skin_detail.py`, `pages/data_workbench.py`).
  Any bookmark, script or test that referenced the old paths must be updated.
- The structured query contract accepts only the `skin_portfolio` dataset, the
  `current_portfolio` evidence scope, `hero_name` or `quality` grouping, the four
  approved metrics and `DashboardFilters` fields. Unknown keys, invalid dates and
  reversed ranges are rejected before any database access.
- The scored metric is displayed as `综合价值分`; the `value_present` emotion
  source label becomes `综合价值评分`, and the detail page metric `完整维度`
  becomes `维度覆盖`. Chart, ranking and CSV column headers follow the same label.
- The timeline separates currencies into their own charts, plots release counts on
  a secondary axis, and leaves missing revenue days as gaps instead of zeros.
- Scatter points now require both a numeric score and numeric CNY revenue; the
  ranking accepts a metric key rather than the display wording.

## Validation

### Test Result

All recorded checks pass: 312/312 tests in the full suite, 9/9 focused dashboard
tests, clean compilation, and clean whitespace checks.

### V1 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: 312 tests ran in 23.994s; result OK, all tests pass.

### V2 - pass

```text
.venv/bin/python -m unittest discover -s tests -p "test_dashboard_sales_layout.py"
```

Observed: 9 focused dashboard layout, query-validation, empty-state, filter and
chart tests ran in 0.374s; result OK.

### V3 - pass

```text
.venv/bin/python -m py_compile app.py dashboard/*.py pages/*.py tests/*.py
```

Observed: Compilation succeeded for app, dashboard, pages and test modules;
py_compile exited 0.

### V4 - pass

```text
git diff --check && git diff --cached --check
```

Observed: No whitespace errors or conflict markers reported in the unstaged or
staged diff.

## Evidence Ledger

| ID | Kind | Classification | Locator / result | Supported conclusion |
| --- | --- | --- | --- | --- |
| E1 | repository | verified | app.py, dashboard/analysis.py, dashboard/charts.py, dashboard/filters.py, dashboard/format.py, dashboard/models.py, dashboard/query.py, dashboard/workbench_render.py | Implementation and query interface exist as described |
| E2 | repository | verified | pages/skin_explorer.py, pages/skin_detail.py, pages/data_workbench.py (renamed from Chinese page filenames) | Page renames and filter forwarding are in place |
| E3 | repository | verified | streamlit-sales-dashboard submodule pinned at d436bc8c4fbc6424828adccad792b4f46c2e8a84 | Reference layout source and its pinned revision |
| E4 | repository | verified | docs/dashboard-sales-layout.md | Layout, denominators and query contract are documented |
| E5 | repository | verified | tests/test_dashboard_sales_layout.py, tests/test_dashboard_pages.py, tests/test_premium_radar.py | Regression coverage for the change |
| E6 | user | user-stated | user request: conclude and commit all changes; prior task record progress/2026-09-15-sven-bo-dashboard.md records the approved plan to adapt the Sven-Bo sales-dashboard visual language | Request to conclude and commit, and the approved plan source |
| E7 | repository | verified | scripts/train_reporter_grpo.py (unrelated reporter-GRPO work, left uncommitted and still being edited) | Unrelated reporter-GRPO work exists and is out of scope |
| E8 | repository | verified | scripts/prepare_reporter_states.py (untracked companion exporter, left uncommitted and still being edited) | The out-of-scope workstream spans two untracked scripts |
| V1 | check | verified | 312 tests ran in 23.994s; all pass | Full suite is green |
| V2 | check | verified | 9 focused tests ran in 0.374s; all pass | Dashboard layout, query and chart behavior hold |
| V3 | check | verified | `py_compile` exited 0 | Modules compile |
| V4 | check | verified | no whitespace errors reported | Diff is clean of formatting defects |

## Git Custody

Repository `/home/mzhyui/git/emogame`; branch `main`; baseline HEAD
`5e3cb06991484073b58abba766fc2a727dd104d7`; final HEAD
`5e3cb06991484073b58abba766fc2a727dd104d7`; history relation `same`; no commit
existed when this record was finalised. This record is committed together with the
implementation as one local commit, so that commit's own hash cannot appear here
and is available from Git history.

Task-owned paths: `.gitmodules`, `app.py`, `dashboard/analysis.py`,
`dashboard/charts.py`, `dashboard/filters.py`, `dashboard/format.py`,
`dashboard/models.py`, `dashboard/query.py`, `dashboard/workbench_render.py`,
`docs/dashboard-sales-layout.md`, `pages/data_workbench.py`, `pages/skin_detail.py`,
`pages/skin_explorer.py`, `progress/2026-09-15-sven-bo-dashboard.md`, the
`streamlit-sales-dashboard` submodule, `tests/test_dashboard_pages.py`,
`tests/test_dashboard_sales_layout.py`, `tests/test_premium_radar.py`.

Outside scope, left uncommitted: `scripts/train_reporter_grpo.py`,
`scripts/prepare_reporter_states.py`, and the generated artifacts
`data/reporter_states.jsonl` and `data/reporter_grpo.jsonl`. This separate
reporter-GRPO workstream wrote and then revised its scripts during this session and
produced new data files, so it was excluded rather than committed as an
unvalidated moving target.

Overlap caveat: the task began on 2026-09-15 and none of its paths had ever been
committed, so every task-owned path was already dirty when this record started;
baseline and task scopes therefore overlap fully and neither is evidence that the
other's changes predate this task. The `.gitmodules` edit and the
`streamlit-sales-dashboard` submodule entry came from the earlier user-owned setup
step of the same plan and are included here deliberately.

Scoped diff: `files=18; insertions=1133; deletions=151; binary_files=0; untracked_files=0`.

Record path: `progress/2026-09-17-current-changes-conclusion.md`.

## Evidence Boundary

This record establishes the file state, the documented interface contract, and the
observed test, compilation and whitespace checks for this change set. It does not
establish production deployment, browser rendering or visual fidelity in a real
browser, freshness or accuracy of the underlying skin, revenue or comment data, or
experimental or scientific validity of the emotion and value scores. The full test
suite passed locally but is not a substitute for held-out or production validation.

## Next Steps

- A browser-level visual review of the adapted layout remains open.
- `scripts/train_reporter_grpo.py` and `scripts/prepare_reporter_states.py` still
  need their own task, validation and record; both were mid-edit at record time.
