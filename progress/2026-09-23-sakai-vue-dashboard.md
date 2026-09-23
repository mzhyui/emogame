# Sakai Vue portfolio dashboard implementation

- Record format: `3`
- Record ID: `RCP-20260923T075756Z-6cb9fca3`
- Mode: `coding-progress`
- Task type: `feature`
- Task slug: `sakai-vue-dashboard`
- Implementation class: `fresh-implementation`
- Date: `2026-09-23`
- Project: /home/mzhyui/git/emogame
- Priority: `unspecified`
- Owner: Unassigned
- Components: None
- Labels: None
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-23T07:57:56Z`
- Started at: `unavailable`
- Updated at: `2026-09-23T08:34:09Z`
- Completed at: `2026-09-23T08:34:09Z`
- Due date: Not applicable
- Evidence state: `verified`
- Validation state: `pass`

## Outcome

The Sakai Vue submodule now contains a production-buildable Vue 3 and PrimeVue portfolio dashboard backed by a new read-only FastAPI overview endpoint. It preserves the Streamlit portfolio filters, three KPIs, quality and score/cash charts, independent score-versus-cash ranking keys, release timeline, coverage gaps, and staged-evidence boundaries. The ranking badges now follow the visible PrimeVue row order after score or revenue sorting. [E1-E6, V1-V9]

## Task and Scope

The requested scope was to follow Sakai Vue and implement the current app dashboard as a Vue frontend. The implementation covers the portfolio-overview route and its data adapter; the Streamlit explorer, detail, premium-radar, and workbench pages remain unchanged. Existing Reporter GRPO, MiniMind, documentation, model, and dataset changes were preserved. [E1-E6]

## Lifecycle

Current blocker: None


| ID | At                   | Action     | From        | To          | Actor       | Reason                                                                                                         |
| ---- | ---------------------- | ------------ | ------------- | ------------- | ------------- | ---------------------------------------------------------------------------------------------------------------- |
| L1 | 2026-09-23T07:57:56Z | created    | none        | in-progress | record-tool | record created                                                                                                 |
| L2 | 2026-09-23T07:58:52Z | transition | in-progress | done        | codex       | Implemented and validated the live Sakai Vue portfolio dashboard and read-only API adapter.                    |
| L3 | 2026-09-23T07:59:56Z | resume     | done        | in-progress | codex       | Add the pre-implementation custody observation to distinguish the existing Sakai gitlink from this task edits. |
| L4 | 2026-09-23T07:59:56Z | transition | in-progress | done        | codex       | Completed the durable handoff with explicit pre-implementation custody evidence.                               |
| L5 | 2026-09-23T08:34:09Z | resume     | done        | in-progress | codex       | Addressed the ranking badge sorting review finding before commit.                                             |
| L6 | 2026-09-23T08:34:09Z | transition | in-progress | done        | codex       | Vue dashboard implementation and sorting review fix are validated and ready for scoped commit.               |


| ID | Type       | Target                                                     |
| ---- | ------------ | ------------------------------------------------------------ |
| R1 | relates-to | progress/2026-09-19-current-changes-and-sakai-submodule.md |

## Implementation

### Plan and Starting Status

The existing `sakai-vue` gitlink was already staged at the Sakai 5.0.0 revision, but its own progress record explicitly stated that the Streamlit frontend had not been migrated. `app.py` and `dashboard/query.py` defined the current overview behavior and the separation of comprehensive value scores, cash attribution, missing values, and evidence release status. [E2-E5]

### Core Functions and Result

- Added `GET /api/dashboard/overview`, which reuses `dashboard.query` and returns filters, KPIs, quality counts, score/cash scatter rows, separate emotion and cash rankings, release/revenue data, coverage metrics, and cohort provenance.
- Replaced the Sakai demo dashboard with an EmoGame portfolio page using responsive PrimeVue controls, Chart.js visualizations, a top-20 data table, dark-mode-aware styling, loading/error/empty states, and mobile layout.
- Rebranded the Sakai top bar, menu, footer, title, package metadata, and route table; demo routes no longer appear in the active application.
- Added a small frontend service and Vite `/api` proxy. Production documentation requires a same-origin reverse proxy unless cross-origin API access is explicitly configured.
- Initialized Sakai's pinned nested `src/assets` submodule so the existing template styles could build.
- Changed ranking badges to use PrimeVue's rendered row index, keeping displayed ranks and top-three styling synchronized after sortable-column reordering.

## Interface and Behavior Changes

- New read-only API: `GET /api/dashboard/overview` with search, hero, quality, online-date, score-coverage, cash-coverage, and analysis-period query parameters.
- Reversed analysis or online-date periods return HTTP 422 instead of being silently swapped.
- Ranking labels remain localized presentation text, while the Vue control submits stable `emotion` and `cash` keys.
- Missing cash and score values remain `null`; zero and negative numeric records remain eligible for aggregation and ranking.
- The UI labels comprehensive value scores as operational values composed from observations and catalog estimates, and shows the evidence cohort's actual `staged` state separately.

## Validation

### Test Result

All nine recorded checks passed. The browser checks used the live local SQLite-backed API, but they are local implementation verification rather than production deployment evidence.

### V1 - pass

```text
npm run build
```

Observed: Vite production build passed; 453 modules transformed and dist emitted. Rollup reported only a non-failing large-chunk advisory.

### V2 - pass

```text
npx eslint src/views/Dashboard.vue src/layout/AppMenu.vue src/layout/AppTopbar.vue src/layout/AppFooter.vue src/router/index.js src/service/DashboardService.js
```

Observed: Targeted ESLint check passed with no errors or warnings after formatting.

### V3 - pass

```text
.venv/bin/python -m unittest tests.test_api
```

Observed: 9 focused FastAPI tests passed, including the new overview payload and reversed-period rejection cases.

### V4 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: All 357 root tests passed in 24.682 seconds.

### V5 - pass

```text
curl -fsS 'http://127.0.0.1:4173/api/dashboard/overview?hero_name=赵云&period_start=2025-07-15&period_end=2026-07-15'
```

Observed: End-to-end Vite proxy request returned a read-only staged-evidence payload for 14 Zhao Yun skins.

### V6 - pass

```text
chromium --headless --window-size=1600,1200 and --window-size=390,844 http://127.0.0.1:4173/
```

Observed: Desktop and mobile renders loaded live API data; navigation, filters, KPI hierarchy, charts, and responsive stacking were visually inspected.

### V7 - pass

```text
git diff --check && git -C sakai-vue diff --check
```

Observed: Root and Sakai submodule whitespace checks passed.

### V8 - pass

```text
npm run build
```

Observed: Vite production build passed after the rank badge fix; 453 modules transformed and dist emitted, with only the existing large-chunk advisory.

### V9 - pass

```text
npx eslint src/views/Dashboard.vue src/layout/AppMenu.vue src/layout/AppTopbar.vue src/layout/AppFooter.vue src/router/index.js src/service/DashboardService.js
```

Observed: Targeted ESLint passed after the rank badge fix with no errors or warnings.

## Evidence Ledger


| ID | Classification | Evidence                                                                                                                                                                            |
| ---- | ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E1 | user-stated    | `Current request: follow Sakai Vue and implement the current app dashboard in Vue`                                                                                                  |
| E2 | verified       | `app.py`; SHA-256 `f3482b38403b5bbb67d2175d5274106766d9348329f95459c8582aea188bf007`.                                                                                               |
| E3 | verified       | `dashboard/query.py`; SHA-256 `692fdf5dd0129bcdbe0e588ec37f8630b0b73abe6bc342c0ba4458da221a9756`.                                                                                   |
| E4 | verified       | `progress/2026-09-19-current-changes-and-sakai-submodule.md`; SHA-256 `7988e51b7ddec6aff7ddd889f8fa3ee57bd1f2418f7e62dea75c79b7d5af02d9`.                                           |
| E5 | verified       | `Current-turn pre-implementation git status: the Sakai parent gitlink was already staged; api/main.py and tests/test_api.py were clean, and api/routes/dashboard.py did not exist.` |
| E6 | user-stated    | `Current review finding: sortable ranking columns must keep displayed ranks synchronized with the reordered rows.` |
| V1 | verified       | Vue production build passed.                                                                                                                                                        |
| V2 | verified       | Targeted frontend lint passed.                                                                                                                                                      |
| V3 | verified       | Nine focused API tests passed.                                                                                                                                                      |
| V4 | verified       | All 357 root tests passed.                                                                                                                                                          |
| V5 | verified       | Live filtered API proxy smoke passed.                                                                                                                                               |
| V6 | verified       | Desktop and mobile live-data browser renders passed visual review.                                                                                                                  |
| V7 | verified       | Root and nested whitespace checks passed.                                                                                                                                           |
| V8 | verified       | Vue production build passed after the ranking badge sorting fix.                                                                                                                     |
| V9 | verified       | Targeted frontend lint passed after the ranking badge sorting fix.                                                                                                                  |

## Git Custody

- Baseline HEAD: `f7c12beff465cc858b3598134817878e348edb3a`
- Parent HEAD before the parent commit: `f7c12beff465cc858b3598134817878e348edb3a`
- History relation: `same`
- Nested Sakai commit: `a30e459` (`feat: add EmoGame portfolio dashboard`)
- Parent commit hash is intentionally omitted because this record is included in that commit; report it from Git history.
- Final branch: `main`
- Root scoped diff token: `files=3; insertions=35; deletions=0; binary_files=0; untracked_files=1`
- Nested Sakai implementation is committed at `a30e459`; the parent gitlink will point to that commit.
- Task-owned changed paths: `api/main.py`, `api/routes/dashboard.py`, `tests/test_api.py`, and the working tree inside `sakai-vue`.
- Pre-existing overlap: the parent `sakai-vue` gitlink was staged before this task. Because the record manifest was created after implementation, it mechanically lists all four task paths as overlap; E5 preserves the actual starting distinction.
- Pre-existing/out-of-scope changes: `.gitmodules`, `data/reporter_grpo.jsonl`, `data/reporter_states.jsonl`, `docs/03-data-crawling.md`, `docs/06-agent-architecture.md`, `internlm2-1_8b-reward/`, `minimind`, the September 18-19 progress records, Reporter GRPO scripts, and `tests/test_reporter_grpo.py`.
- Manifest pre-existing/out-of-scope inventory (the record was captured late, so this list also includes the task-path overlap already qualified above):
  - `internlm2-1_8b-reward/.gitattributes`
  - `internlm2-1_8b-reward/README.md`
  - `internlm2-1_8b-reward/config.json`
  - `internlm2-1_8b-reward/configuration_internlm2.py`
  - `internlm2-1_8b-reward/model.safetensors.index.json`
  - `internlm2-1_8b-reward/modeling_internlm2.py`
  - `internlm2-1_8b-reward/reward_bench_results/eval-set/internlm2-1_8b-reward.json`
  - `internlm2-1_8b-reward/reward_bench_results/pref-sets/internlm2-1_8b-reward.json`
  - `internlm2-1_8b-reward/special_tokens_map.json`
  - `internlm2-1_8b-reward/tokenization_internlm2.py`
  - `internlm2-1_8b-reward/tokenization_internlm2_fast.py`
  - `internlm2-1_8b-reward/tokenizer.model`
  - `internlm2-1_8b-reward/tokenizer_config.json`
  - `progress/2026-09-18-cloud-skin-catalog-maintenance-workflow.md`
  - `progress/2026-09-18-reporter-grpo-paired-evaluation.md`
  - `progress/2026-09-18-reporter-grpo-training-result-and-capabilities.md`
  - `scripts/analyze_reporter_grpo_eval.py`
  - `scripts/evaluate_reporter_grpo.py`
  - `scripts/prepare_reporter_states.py`
  - `scripts/train_reporter_grpo.py`
- No push was performed.
- Record path: `progress/2026-09-23-sakai-vue-dashboard.md`

## Evidence Boundary

The checks establish a working local implementation, consistent read-only aggregation, passing repository regressions, and responsive browser rendering. They do not establish production deployment, external data freshness, independent scientific validation of the operational score, or release of the currently staged emotion-evidence cohort. The build's large-chunk advisory is not a functional failure but remains an optimization opportunity.

## Next Steps

Configure a production same-origin reverse proxy for `/api`. Route-level code splitting can be considered if initial bundle size becomes a deployment constraint.
