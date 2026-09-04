# Final-truth scoring and dashboard conclusion

- Record format: `3`
- Record ID: `RCP-20260904T113734Z-ca494c03`
- Mode: `coding-progress`
- Task type: `feature`
- Task slug: `final-truth-dashboard-conclusion`
- Implementation class: `fresh-implementation`
- Date: `2026-09-04`
- Project: `/home/mzhyui/git/emogame`
- Priority: `high`
- Owner: Unassigned
- Components: emotion-evidence, dashboard
- Labels: final-truth, publication-gate
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-04T11:37:34Z`
- Started at: `unavailable`
- Updated at: `2026-09-04T11:39:57Z`
- Completed at: `2026-09-04T11:39:57Z`
- Due date: Not applicable
- Evidence state: `mixed`
- Validation state: `pass`

## Outcome

Concluded the complete 15-path visible working-tree snapshot as one locally
committable feature batch. The batch imports a project-owner-declared human
truth source, binds its artifact and semantic annotation digests, adds a direct
observed-aspect scorer, and exposes source-labelled emotion scores in the
Streamlit portfolio and detail views. Human final truth takes precedence over
the frozen selected comment model, which takes precedence over an already
published RuleEngine result. Missing and no-relevant results remain null.
[E2-E7]

All 300 root tests pass, every changed Python file compiles, the score report
recomputes from 209 truth rows across 20 skins, and the live SQLite database
passes its integrity check. The current local dashboard projection has 56
numeric scores across 960 catalog skins: 17 human-final-truth scores and 39
selected-comment-model scores. Six other truth/model-covered skins remain null
because their annotations contain no relevant emotional dimension. [V1-V6]

This display availability is not publication qualification. The active
100-skin run remains staged with zero published qualifying profiles; the
selected `qwen3.5:4b` extractor is frozen but has not passed the configured
quality gate, calibration is pending, and production audit is pending. [V3-V5]

## Task and Scope

The user requested that all current changes be concluded and committed. The
scope therefore includes every staged, unstaged, and untracked path visible at
record start, plus this conclusion record. All 15 implementation paths were
already dirty when this late capture began; none is silently treated as newly
authored during the conclusion turn. [E1]

Included scope:

- declared-final-truth import, selection, status, validation, and immutable
  hash bindings;
- direct scoring for observed aspects and the catalog-wide report CLI;
- fail-closed loading of the local score artifact and completely annotated
  frozen-model comment scores;
- dashboard source precedence, source/status labels, filtering, ranking,
  portfolio counts, detail metrics, and audit payloads;
- operational documentation and regression tests.

Ignored local artifacts remain excluded under repository policy, including the
truth CSV, generated score JSON, SQLite databases, collected evidence, model
outputs, media, caches, logs, and `.env`. E6 and E7 are evidence inputs bound by
hash, not Git payloads. No push or deployment is authorized.

## Lifecycle

- Current blocker: None

| ID | At | Action | From | To | Actor | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | 2026-09-04T11:37:34Z | created | none | in-progress | record-tool | record created |
| L2 | 2026-09-04T11:39:57Z | transition | in-progress | validating | codex | Full repository tests and live read-only artifact checks passed; preparing durable custody record |
| L3 | 2026-09-04T11:39:57Z | transition | validating | done | codex | All visible current changes were reviewed and proportionately validated for one local commit |

Local relationships: None.

## Implementation

### Plan and Starting Status

This is a late-captured fresh implementation because it introduces new CLI,
scoring, dashboard-model, and display-source behavior. No separate plan file
exists for this final batch; the operational contract and evidence boundaries
are recorded in E2. The verified Git baseline is
`9807445b11f1b297ba041029d8e3975539d1d9df`, and the actual implementation
start time is unavailable.

At record start, the full 15-path snapshot already existed. The prior tracked
pipeline admitted only published provenance-qualified profiles to dashboard
rankings; this batch adds a distinct exploratory display layer while retaining
the existing publication gate.

### Core Functions and Result

| Area | Core functions or behavior | Result |
| --- | --- | --- |
| Truth custody | `_truth_annotations`, `command_import_review`, `final_truth_annotation_digest` | `final_truth` is preferred, bound to one reviewer/artifact per phase, and rejected on artifact or stored-annotation drift. |
| Scoring | `score_observed_annotation_rows`, `score_final_truth_rows`, `build_report` | Scores only observed aspects, keeps missing dimensions null, and separates partial observed scores from the complete six-aspect composite. |
| Model comments | `_batch_model_comment_scores` | Accepts only exact-mapped, non-synthetic, non-quarantined comments with complete annotations matching the frozen model digest and prompt hash. |
| Dashboard merge | `_batch_final_truth_scores`, `get_portfolio_rows`, `get_skin_detail` | Uses human truth, then selected comment model, then published RuleEngine precedence; identity or digest drift fails closed. |
| Presentation | portfolio, explorer, detail, filters, labels, and charts | Shows source, coverage, relevant/total counts, null reasons, and model quality-gate status without imputing missing scores. |
| Tests | dashboard model/page and emotion-evidence regressions | Covers precedence, complete annotation requirements, artifact tampering, null behavior, immutability, and scorer semantics. [V1] |

## Interface and Behavior Changes

- `run_emotion_evidence.py import-review --kind` now accepts `final_truth` and
  binds both the source-file hash and canonical annotation digest.
- `annotate-local` gains `--phase` and `--truth-only`; model selection,
  calibration, validation, and status prefer declared final truth when present.
- `score_final_truth_emotion.py` generates a catalog-wide report and writes it
  only with `--apply`.
- `PortfolioSkinRow` and `SkinDashboardDetail` expose emotion source/status,
  final-truth score, and selected-comment-model score fields.
- Dashboard `validated`/`emotion_validated` compatibility fields now indicate a
  numeric source-backed display score, not publication qualification. User-facing
  labels were changed from “validated” to “has source”; cohort publication
  counts and release state remain separately displayed.
- Rankings and filters may include exploratory selected-model scores. The
  detail view explicitly reports a failed model quality gate when applicable.

## Validation

### Test Result

The aggregate manifest result is `pass`: all seven observed checks passed. This
establishes implementation behavior, artifact readability, current local data
projection, SQLite integrity, and patch hygiene. It does not establish
scientific or publication validity.

### V1 - pass

```text
.venv/bin/python -m unittest discover -s tests -v
```

300/300 tests passed in 16.541 seconds; deprecation and expected bare-Streamlit warnings only.

### V2 - pass

```text
.venv/bin/python -m py_compile app.py dashboard/charts.py dashboard/filters.py dashboard/format.py dashboard/models.py dashboard/query.py pages/皮肤探索.py pages/皮肤详情.py models/final_truth_emotion.py scripts/run_emotion_evidence.py scripts/score_final_truth_emotion.py tests/test_dashboard_models.py tests/test_dashboard_pages.py tests/test_emotion_evidence.py
```

All changed Python implementation and test files compiled without errors.

### V3 - pass

```text
.venv/bin/python scripts/score_final_truth_emotion.py
```

Dry run read 209 truth rows across 20 skins and 960 catalog skins; 17 partial
observed scores, 3 no-relevant-truth skins, 940 no-truth skins, and 0 complete
six-aspect scores.

### V4 - pass

```text
.venv/bin/python scripts/run_emotion_evidence.py status
```

Run 20260902-current100-v1 is staged with frozen qwen3.5:4b, 100 cohort skins, 1346 evidence items, 3029 annotations, 209 final-truth annotations across 20 skins, and 0 published qualifying profiles.

### V5 - pass

```text
.venv/bin/python -c "from collections import Counter; from dashboard.query import get_portfolio_rows; from data.skin_repository import DEFAULT_DB_PATH; rows=get_portfolio_rows(DEFAULT_DB_PATH); print('rows',len(rows)); print('numeric',sum(r.emotion_score is not None for r in rows)); print('sources',dict(Counter(r.emotion_score_source or 'none' for r in rows))); print('statuses',dict(Counter(r.emotion_score_status or 'none' for r in rows)))"
```

Read-only dashboard projection returned 960 rows and 56 numeric scores: 17
human-final-truth and 39 selected-comment-model scores; explicit no-relevant
results remained null.

### V6 - pass

```text
sqlite3 data/wzry_skins/skins.db 'PRAGMA integrity_check;'
```

SQLite integrity_check returned ok.

### V7 - pass

```text
git diff --check
```

No whitespace errors were reported for the unstaged tracked patch.

## Evidence Ledger

| ID | Class | Kind | Locator | SHA-256 | Supported conclusion |
| --- | --- | --- | --- | --- | --- |
| E1 | user-stated | user | Current user request: conclude all current changes and commit | N/A | Authorizes the complete visible local snapshot and this conclusion record; push remains separate. |
| E2 | verified | repository | `docs/14-emotion-evidence-operations.md` | `7a0b8bd0418b46c0a7578ad0f1f345c3b163b1b445eb9e3e35f5b840cc4943b1` | Records final-truth operation, scoring semantics, dashboard precedence, and publication boundary. |
| E3 | verified | repository | `models/final_truth_emotion.py` | `68fb1140fe97bae28f0545343c069cef3024c5812350377ca3a21963f61a715c` | Implements semantic annotation hashing and observed-aspect aggregation. |
| E4 | verified | repository | `dashboard/query.py` | `24b7b9cf8cc4d8befe5921f3852d352b200a76f25e0561c48eb5f9341d6ac9ef` | Implements fail-closed artifact/model loading and dashboard precedence. |
| E5 | verified | repository | `scripts/run_emotion_evidence.py` | `d00d88f4e38a1d12d2b2c4eb89fcbc90acab34897777dc8be4aa6a418ac47aa7` | Implements final-truth import, selection, gating, validation, and status handling. |
| E6 | verified | artifact | `data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv` | `8941bbd4bcc97bd4225477e441b38584ea7f8a8b0bd22ea1c47b65f2cd82206e` | Supplies the 209 declared truth rows used by the local scorer. |
| E7 | verified | artifact | `data/emotion_evidence/runs/20260902-current100-v1/final-truth-scores.json` | `657c46237ef5c8446d2f42d134d6310d5aab02b43b2dec23633e0b2d1e9b9047` | Supplies the current all-catalog score projection accepted by the dashboard loader. |
| V1 | verified | check | Root unittest suite | N/A | Establishes current regression status. |
| V2 | verified | check | Changed-Python compilation | N/A | Establishes syntax/import compilation for the changed Python paths. |
| V3 | verified | check | Final-truth scorer dry run | N/A | Establishes current truth/report counts and score semantics. |
| V4 | verified | check | Emotion-run status | N/A | Establishes current run, truth, extractor, and publication state. |
| V5 | verified | check | Read-only dashboard projection | N/A | Establishes current source counts and null preservation. |
| V6 | verified | check | SQLite integrity | N/A | Establishes structural integrity of the local skin database. |
| V7 | verified | check | Unstaged whitespace validation | N/A | Establishes tracked patch whitespace status before staging. |

## Git Custody

- Final branch: `main`
- Baseline HEAD: `9807445b11f1b297ba041029d8e3975539d1d9df`
- Final HEAD: `9807445b11f1b297ba041029d8e3975539d1d9df`
- History relation: `same`
- Commits since baseline: none at record finalization
- Commit/push: one local commit requested and follows this validated record; push not requested
- Record path: `progress/2026-09-04-final-truth-dashboard-conclusion.md`
- Scoped diff: `files=13; insertions=1185; deletions=105; binary_files=0; untracked_files=2`
- Task-owned changed paths: 16 paths: the authorized 15-path snapshot plus this record
- Pre-existing paths at record start: the same 15 implementation paths
- Pre-existing overlap: all 15 implementation paths because this was a verified late capture
- Outside-scope changed paths: None

Manifest task-path scopes, verbatim:

- `app.py`
- `dashboard/charts.py`
- `dashboard/filters.py`
- `dashboard/format.py`
- `dashboard/models.py`
- `dashboard/query.py`
- `docs/14-emotion-evidence-operations.md`
- `models/final_truth_emotion.py`
- `pages/皮肤探索.py`
- `pages/皮肤详情.py`
- `scripts/run_emotion_evidence.py`
- `scripts/score_final_truth_emotion.py`
- `tests/test_dashboard_models.py`
- `tests/test_dashboard_pages.py`
- `tests/test_emotion_evidence.py`

Task-owned and pre-existing-overlap paths, verbatim:

- `app.py`
- `dashboard/charts.py`
- `dashboard/filters.py`
- `dashboard/format.py`
- `dashboard/models.py`
- `dashboard/query.py`
- `docs/14-emotion-evidence-operations.md`
- `models/final_truth_emotion.py`
- `pages/皮肤探索.py`
- `pages/皮肤详情.py`
- `scripts/run_emotion_evidence.py`
- `scripts/score_final_truth_emotion.py`
- `tests/test_dashboard_models.py`
- `tests/test_dashboard_pages.py`
- `tests/test_emotion_evidence.py`

## Evidence Boundary

This record verifies the implemented contracts, regression behavior, local
artifact hashes, current read-only score projection, and Git custody. It does
not independently validate the human labels, make a single human source into
external or adjudicated truth, establish model generalization, or qualify any
profile for publication.

The 17 human scores are partial observed-aspect summaries; no reviewed skin has
all six aspects, so every complete six-aspect score remains null. The 39
selected-model scores are exploratory because the frozen extractor did not pass
the configured quality gate. Neither source changes the run's `staged` release
state, its 0/100 published coverage, or its pending calibration and audit gates.

The 56-score dashboard count depends on ignored local artifacts E6-E7 and the
current local SQLite state. The Git commit alone does not reproduce those data.
No claim is made about catalog-wide representativeness, true user emotion,
willingness to pay, revenue prediction, pricing impact, or causal value.

## Next Steps

1. Complete the locked calibration and production audit before considering any
   publication transition; do not promote exploratory model scores through the
   dashboard availability path.
2. Obtain complete six-aspect coverage and independent validation if a complete
   or externally validated human score is required.
3. Define a separately approved distribution or regeneration policy if E6-E7
   must be reproducible outside this local workspace.
4. Push or deploy only under a separate explicit request.
