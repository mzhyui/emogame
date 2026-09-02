# P0 correctness and P1 asset reconciliation

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-09-02`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Implemented the approved correctness and asset-reconciliation plan. Insufficient
RuleEngine results are now audit-only; dashboard reads are fail-closed and
read-only; release and revenue events share the analysis period; the fifth page
accepts only the canonical 50-row social-v1 bundle; and 798 existing images were
render/hash verified and atomically bound after a consistent database backup.
The 154 missing-URL rows, six absent cache files, two ambiguous identities, and
zero validated emotion rows remain explicit limitations. [E1] [E2] [E3] [E4]

## Task and Scope

- Request: implement the user-approved “Fix P0 correctness and reconcile existing
  P1 assets” plan. [E1]
- Target: repair evidence gating, database read safety, period filtering, local
  image binding, canonical radar rendering, regression coverage, and README
  status without crawling, network access, commit, or push. [E1]
- Starting state: `app.py` already had an uncommitted fifth radar page, but it
  referenced the stale partial-v3 HTML. Dashboard detail output could expose a
  numeric insufficient score, repository reads could initialize schemas, release
  events ignored the analysis period, and all 806 primary assets were skipped.
- Constraints: preserve the existing `app.py` work, do not deduplicate the two
  ambiguous rows, keep perceived-premium results outside emotion/market tables,
  and back up the ignored live SQLite database before its first update. [E1]
- Out of scope: metadata collection, downloads, Weibo/VLM execution, emotion
  ranking enablement, duplicate-row deletion, and premium-result SQLite tables.

## Implementation

### Plan and Starting Status

The confirmed plan supplied exact live inputs, expected counts, safety gates,
and validation criteria. The fifth-page shell and frozen premium artifacts
already existed; the read-only adapter, reconciler, bundle validator, evidence
gates, and new regression tests did not. [E1] [E2] [E3]

### Core Functions and Result

| Path | Symbol or section | Behavioral change | Role |
|---|---|---|---|
| `data/sqlite_read.py` | `connect_readonly`, `table_exists` | Opens existing SQLite files with `mode=ro` | Common fail-closed read boundary |
| `data/skin_repository.py` | all query methods | Removed mutating connection behavior and returns empty states for missing/partial schemas | Safe catalog reads |
| `data/cash_value.py` | record/revenue reads | Schema initialization remains only on write/import paths | Safe cash resolution |
| `data/market_signal_repository.py` | signal/evidence reads | Optional tables are read-only and absent tables return empty signals/evidence | Safe emotion/evidence reads |
| `dashboard/query.py` | `get_skin_detail`, timeline and portfolio queries | Gates public aspect/cash inputs on `evidence_validated` and intersects release dates with both filters | P0 correctness |
| `pages/皮肤详情.py` | `main`, `_image_source` | Withholds insufficient headline/radar values and prefers validated local images | Honest detail UI |
| `data/image_reconciliation.py` | `reconcile_skin_images` | Verifies identity, containment, Pillow decode, SHA-256, snapshot counts, conflicts, backup, transaction, and idempotence | Safe P1 binding |
| `scripts/reconcile_skin_images.py` | CLI | Dry-run by default; explicit `--apply` with locked expectations | Operator interface |
| `dashboard/premium_radar.py` | `load_premium_radar_bundle` | Requires consistent HTML/report/metadata, canonical ID, 50 cards, 46/4 evidence split, and target-aware non-synthetic policy | Artifact provenance gate |
| `app.py` | `build_payload`, `score_text`, `_premium_radar_panel` | Gates workbench score/cash inputs and preserves the fifth page while switching it to the canonical bundle | Workbench and five-page integration |
| `dashboard/workbench_render.py` | header, cash, evaluation renderers | Withholds insufficient headline/radar values and emotional cash inputs | Complete dashboard evidence gate |
| `README` | P0/P1 backlog | Records current coverage, acceptance results, five-page contract, and unresolved counts | Project status contract |
| `tests/` | focused regressions | Covers sparse numeric emotion, no-create reads, periods, reconciliation safety, bundle rejection, and fifth-page rendering | Regression protection |

## Interface and Behavior Changes

| Surface | Before | After | Compatibility or failure behavior |
|---|---|---|---|
| Dashboard SQLite reads | Some reads called schema initializers | Every dashboard-facing read uses SQLite read-only mode | Missing/partial databases return structured empty states without file/schema creation |
| Detail evaluation | Numeric insufficient results could reach public fields | Raw result stays in audit JSON; headline, aspect radar, perceived value, and emotional efficiency remain missing | Validated results retain their prior public behavior |
| Timeline | Revenue used the analysis period; releases did not | Releases also use `period_start`/`period_end`, then intersect optional online filters | Existing filters are additive |
| Image CLI | No binding workflow | `--db`, `--image-dir`, `--report-dir`, explicit `--apply`; dry-run default | Snapshot, corruption, ambiguity, escape, or conflicts abort before mutation |
| Detail image | Remote URL first | Existing local binding first, remote URL fallback | Unbound rows continue to render remotely when possible |
| Premium radar | Fifth page pointed at partial-v3 HTML only | Canonical social-v1 three-file bundle is validated and rendered | Missing/inconsistent bundle shows unavailable; no stale fallback |

## Validation

### Test Result

All requested focused tests, the final 257-test root suite, 13 AppTests/radar tests,
compilation, live reconciliation checks, SQLite integrity checks, and diff
whitespace checks passed. [V4] [V5] [V6] [V9] [V10] [V11] [V12] [V13]

### V1 - pass

```text
.venv/bin/python -m unittest tests.test_dashboard_models tests.test_dashboard_pages tests.test_streamlit_app tests.test_cash_value tests.test_market_signals tests.test_skin_repository tests.test_image_reconciliation tests.test_premium_radar
```

Observed: 59 focused P0, image, radar, repository, and AppTest cases passed.

### V2 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: all 256 root tests passed.

### V3 - pass

```text
.venv/bin/python -m py_compile app.py dashboard/query.py dashboard/premium_radar.py data/sqlite_read.py data/skin_repository.py data/cash_value.py data/market_signal_repository.py data/image_reconciliation.py pages/皮肤详情.py scripts/reconcile_skin_images.py tests/test_dashboard_models.py tests/test_dashboard_pages.py tests/test_streamlit_app.py tests/test_image_reconciliation.py tests/test_premium_radar.py
```

Observed: all changed Python modules and focused tests compiled.

### V4 - pass

```text
.venv/bin/python scripts/reconcile_skin_images.py --apply
```

Observed: the locked 960/806/798 snapshot matched, a backup was created, and
exactly 798 pristine bindings were updated in one transaction.

### V5 - pass

```text
.venv/bin/python scripts/reconcile_skin_images.py
```

Observed: the post-apply dry run reported `already-reconciled`, 798 already
bound, 154 missing URLs, six missing cached images, two ambiguous identities,
zero updates, and no mismatches.

### V6 - pass

```text
sqlite3 live-and-backup count queries plus PRAGMA quick_check
```

Observed: live database 798 bound/existing, eight skipped, and 798 64-character
hashes; backup zero bound and 806 skipped; both databases returned `ok`.

### V7 - pass

```text
git diff --check
```

Observed: no whitespace errors.

### V8 - pass

```text
.venv/bin/python -m unittest tests.test_dashboard_pages tests.test_premium_radar
```

Observed: all 13 live-database dashboard AppTests and canonical bundle/page
tests passed.

### V9 - pass

```text
.venv/bin/python -m unittest tests.test_dashboard_models tests.test_dashboard_pages tests.test_streamlit_app tests.test_cash_value tests.test_market_signals tests.test_skin_repository tests.test_image_reconciliation tests.test_premium_radar
```

Observed: the final focused suite passed all 60 cases after closing the
workbench evidence path.

### V10 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed: the final root suite passed all 257 tests.

### V11 - pass

```text
.venv/bin/python -m py_compile app.py dashboard/query.py dashboard/premium_radar.py dashboard/workbench_render.py data/sqlite_read.py data/skin_repository.py data/cash_value.py data/market_signal_repository.py data/image_reconciliation.py pages/皮肤详情.py scripts/reconcile_skin_images.py tests/test_dashboard_models.py tests/test_dashboard_pages.py tests/test_streamlit_app.py tests/test_cash_value.py tests/test_market_signals.py tests/test_image_reconciliation.py tests/test_premium_radar.py
```

Observed: final compilation passed for every changed Python module and focused
test.

### V12 - pass

```text
git diff --check
```

Observed: the final diff check reported no whitespace errors.

### V13 - pass

```text
.venv/bin/python -m unittest tests.test_dashboard_pages tests.test_premium_radar
```

Observed: the final AppTest run passed all 13 live dashboard and canonical radar
page tests.

## Evidence Ledger

| ID | Class | Locator or check | Supported conclusion |
|---|---|---|---|
| E1 | user-stated | Current approved implementation plan | Scope, constraints, counts, and intended acceptance contract |
| E2 | verified | Canonical `report.json`, SHA-256 `f5382ea7...` | 50 selected, 46 complete, four partial, zero insufficient |
| E3 | verified | Canonical `run_metadata.json`, SHA-256 `1d7636b1...` | Correct run ID, target-aware comments, synthetic media disabled |
| E4 | verified | Apply report, SHA-256 `a2404e59...` | Exact live snapshot and 798-row applied update |
| V1 | verified | Focused unittest command | Targeted regressions pass |
| V2 | verified | Root unittest discovery | Repository root suite passes |
| V3 | verified | `py_compile` command | Changed Python syntax/import compilation passes |
| V4 | verified | Apply command and report | Backup-first atomic live update completed |
| V5 | verified | Post-apply dry run and report | Final state is idempotent with explicit unresolved counts |
| V6 | verified | SQLite counts and integrity checks | Live and backup database states are internally consistent |
| V7 | verified | `git diff --check` | Patch has no whitespace defects |
| V8 | verified | AppTest/radar unittest command | Live pages and canonical fifth page render without exceptions |
| V9 | verified | Final focused unittest command | All 60 final focused cases pass |
| V10 | verified | Final root unittest discovery | All 257 final root tests pass |
| V11 | verified | Final compilation command | Every changed Python module and focused test compiles |
| V12 | verified | Final `git diff --check` | Final patch has no whitespace errors |
| V13 | verified | Final AppTest/radar command | All 13 final live page tests pass |

Verbatim manifest evidence:

- E1 locator: `Current conversation: approved Fix P0 correctness and reconcile existing P1 assets implementation plan`
- E2 locator: `canonical premium report`; SHA-256: `f5382ea773653a37bfa6d77da245e9f1a38824a4e4861f818161baab8f0fafb1`
- E3 locator: `canonical premium run metadata`; SHA-256: `1d7636b171aee0b86196a4940c82c98653d3456720c823de8a72fdf40b1d8c02`
- E4 locator: `live image reconciliation apply report`; SHA-256: `a2404e599569fe3a8b70eac78e9731f225afe93220ae60f9cbb9beab97e051c7`
- V1 observed summary: `59 focused P0, image reconciliation, radar, repository, and AppTest cases passed.`
- V2 observed summary: `All 256 root unit tests passed.`
- V3 observed summary: `All changed Python modules and focused tests compiled successfully.`
- V4 observed summary: `Locked live snapshot matched; backup created and exactly 798 pristine bindings updated atomically.` Artifact SHA-256: `a2404e599569fe3a8b70eac78e9731f225afe93220ae60f9cbb9beab97e051c7`
- V5 observed summary: `Post-apply dry run was already-reconciled with 798 bound, 154 missing URL, 6 missing cached image, 2 ambiguous identity, and 0 updates.` Artifact SHA-256: `04e59efdf4f88b562d3de508904d0703c52c6145ac579d5fa56b5ff68e1a94c3`
- V6 observed summary: `Live DB has 798 bound/existing and 8 skipped primary assets; live and backup quick_check both returned ok; backup retained 0 bound and 806 skipped.` Artifact SHA-256: `fb9a759faa3cb25f28fdd2b91c3452a408d831c69285db2b147b5d9375014684`
- V7 observed summary: `No whitespace errors.`
- V8 observed summary: `All 13 live-database dashboard AppTests and canonical radar bundle/page tests passed.`
- V9 observed summary: `Final focused suite passed all 60 P0, image, radar, repository, and AppTest cases.`
- V10 observed summary: `Final root suite passed all 257 tests after the workbench evidence gate.`
- V11 observed summary: `Final compilation passed for every changed Python module and focused test.`
- V12 observed summary: `Final diff check reported no whitespace errors.`
- V13 observed summary: `Final AppTest run passed all 13 live dashboard and canonical radar page tests.`

## Git Custody

- Branch: `main`
- Baseline HEAD: `d26b703e2e67072e038e8a5f9a1ac0f0fe562750`
- Final HEAD at pre-commit evidence capture: `d26b703e2e67072e038e8a5f9a1ac0f0fe562750`
- History relation at pre-commit capture: `same`; commits since baseline: `None`
- Task commit: available from Git history after this record and its implementation
  are committed together.
- Explicit scopes: `README`, `app.py`, `dashboard/query.py`,
  `dashboard/premium_radar.py`, `dashboard/workbench_render.py`, `data/sqlite_read.py`,
  `data/skin_repository.py`, `data/cash_value.py`,
  `data/market_signal_repository.py`, `data/image_reconciliation.py`,
  `pages/皮肤详情.py`, `scripts/reconcile_skin_images.py`,
  `tests/test_cash_value.py`, `tests/test_dashboard_models.py`,
  `tests/test_dashboard_pages.py`, `tests/test_image_reconciliation.py`,
  `tests/test_market_signals.py`, `tests/test_premium_radar.py`,
  and `tests/test_streamlit_app.py`.
- Task-owned changes: all explicit scopes above plus this progress record.
- Record path: `progress/2026-09-02-p0-correctness-p1-asset-reconciliation.md`
- Pre-existing changes: `app.py`.
- Overlap: `app.py`; the pre-existing fifth-page work was preserved and extended
  to validate/load the canonical bundle.
- Outside-scope changed paths: `None`
- Ownership caveat: the baseline cannot attribute the pre-existing `app.py`
  additions to this task; this record claims only the integration edits made on
  top of that work.
- Scoped diff: `files=13; insertions=631; deletions=214; binary_files=0; untracked_files=6`
- Ignored live artifacts: updated `data/wzry_skins/skins.sqlite3`, reconciliation
  JSON reports, and the pre-update backup remain outside Git.

## Evidence Boundary

This work establishes code-level gating, read-only failure behavior, period
filtering, canonical bundle consistency, render/hash verification for 798 cached
images, transactional binding, live/backup SQLite integrity, and passing local
tests. It does not establish emotional validity, metadata completeness, remote
image availability, correctness of the two duplicate identities, causal or
predictive validity of premium scores, or production behavior beyond the tested
local artifacts. The premium pilot remains separate from RuleEngine emotion and
`market_signal_records`.

## Next Steps

- Backfill the 154 missing URLs and six absent cached files only through a
  separately authorized metadata/download workflow.
- Resolve or adjudicate the two duplicate identities before binding either row.
- Collect validated emotion evidence before enabling emotion ranking.
- Improve quality/date/acquisition/price coverage under separate provenance
  criteria.
