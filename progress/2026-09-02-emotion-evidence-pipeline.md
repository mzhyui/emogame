# P1 provenance-qualified emotion-evidence pipeline

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-09-02`
- Project: /home/mzhyui/git/emogame
- Status: `partial`
- Evidence state: `mixed`

## Outcome

Implemented the P1 emotion-evidence contracts, additive SQLite persistence,
bounded resumable collection, blinded review workflow, deterministic and local
model extraction paths, calibration/audit gates, per-skin aggregation,
transactional publication gate, API/dashboard binding, and regression tests.
The fixed run `20260902-current100-v1` is persisted with 100 cohort members,
1,089 deduplicated warm evidence items, and 100 diagnostic validation rows.

Publication correctly remains disabled: the run has 0/100 qualifying published
profiles, ethics/model-selection/calibration/audit are pending, and the current
development review dry run reaches only 7/20 required skins. No new network
collection, Ollama evaluation, human review, or ranking publication was
performed. The catalog-wide 960-skin ranking remains disabled. [E1, E3, E4,
V2, V5, V6, V8]

## Task and Scope

The user supplied the full P1 Emotion-Evidence Solution and asked for its
implementation. The plan requires a purposive warm-50 plus extension-50 cohort,
the 2024-09-02 through 2026-09-01 perception estimand, exact target mapping,
privacy-minimized public comments, six subjective aspects, human/model
validation, an 80/100 release gate, and a permanently separate catalog-wide
ranking. [E1]

The starting implementation had a permissive RuleEngine coverage calculation
that could treat official detail/image availability and sparse aggregate market
signals as validated evidence. No provenance-qualified emotion run schema,
cohort collector, immutable annotation contract, publication gate, or published
profile read path existed. The existing 50-target Weibo artifact was retained
only as a real-comment warm source; premium scores and other pilot outputs were
not imported. [E2]

Constraints preserved throughout:

- no synthetic, cash, revenue, official-prior, VLM, or premium-pilot score can
  qualify emotion evidence;
- no thresholds were weakened and no missing evidence was imputed;
- new collection is network-free by default and ethics-gated when applied;
- reviewer originals are immutable and consensus needs two human labels;
- locked review cannot open before development-only model selection freezes;
- local databases/artifacts stay ignored, and `README` remains unchanged until
  a qualifying release is actually published;
- the unrelated pre-existing
  `progress/2026-09-02-app-interface-robustness-review.md` was preserved.

## Implementation

### Plan and Starting Status

The supplied plan was an unverified execution plan. This work implemented and
code-validated its engineering path, then executed only safe pre-publication
stages. The evidence collection and human-validation outcomes remain incomplete
and are not promoted to scientific or product claims. [E1, V1, V5, V6]

### Core Functions and Result

| Core paths | Main symbols or behavior | Result |
| --- | --- | --- |
| `models/emotion_evidence.py` | `EvidenceQualificationProfile`, qualification reasons, one-author/aspect aggregation, 60% parent cap, post-release feel gate, author bootstrap CI | Defines a deterministic six-aspect evidence contract and rejects forbidden lineage. |
| `data/emotion_evidence_repository.py` | additive run/cohort/checkpoint/annotation/result tables, privacy guard, immutable hashes, model/review gates, active-run published reads | Writers are explicit; all read paths fail closed without creating schema. |
| `models/emotion_workflow.py` | deterministic cohort selection, privacy sanitizer, blinded CSV packs, extraction schema, calibration metrics | Freezes the requested warm/era cohort and supports reproducible review/model evaluation. |
| `scripts/collect_emotion_evidence.py` | six aspect queries per target, direct-then-proxy transport, 60/3 Weibo and 40/2 Bilibili bounds, resumable checkpoints | Dry-run by default; live collection requires a hash-bound ethics determination. |
| `scripts/run_emotion_evidence.py` | manifest, ethics, staging, annotation, review import, model selection, audit, validation, publication, status commands | Provides the complete fail-closed operator workflow and hash-bound artifacts. |
| `models/rule_engine.py` | published qualification profile input, fixed six-aspect composite, protocol/run/CI/reason outputs | Manual aggregates and official/image flags can no longer confer `evidence_validated`; `market_heat` is descriptive only. |
| `api/routes/*.py`, `scripts/evaluate_skin.py` | published-profile lookup and validation-aware cash handoff | API/CLI validate only the active published cohort profile; explicit/manual signals stay audit-only. |
| `dashboard/*.py`, `app.py`, `pages/*.py` | active cohort state, fixed catalog/cohort denominators, CI display, missing reasons, ranking empty state | UI shows 0/960 and 0/100 while staged and ranks only active published qualifiers. |
| `crawlers/*.py` | Weibo comment IDs/proxy support and Bilibili reply parsing/fetching | Supports privacy-safe evidence identity and bounded reply collection. |
| `tests/test_emotion_*.py` and updated regressions | privacy, cohort, date, lineage, scoring, release, dry-run, API and legacy behavior | The root suite now encodes the new fail-closed contract. |
| `docs/13-emotion-evidence-protocol.md`, `.gitignore` | frozen protocol and ignored local run directory | Records the claim boundary and operator sequence without publishing unfinished evidence. |

The live local run has cohort hash
`ee88c6e06374668e185bad26e0d26ecb8acda9a284d5dd9ef985336aa9dc3143`
and protocol hash
`c1c56a404bab4bcc59bc6a7068b70196a45435fcf20d48566c5c34d4d4e16769`.
The warm source was reduced from 1,112 associations to 1,089 unique evidence
items: 1,065 accepted for annotation and 24 quarantined for fallback mapping.
[E2, E3, E4, E5, V3, V4, V8]

## Interface and Behavior Changes

- `RuleEngine.evaluate` accepts an optional `EvidenceQualificationProfile`.
  Without a matching active published profile, its result is
  `insufficient_market_evidence`; a complete manual numeric composite remains
  audit-only.
- `EvaluationResult` now exposes run/protocol identifiers, evidence cutoff,
  per-aspect author counts, validation reasons, and score confidence interval.
- SQLite gains additive emotion-run, cohort, checkpoint, annotation, and
  validation-result contracts plus provenance/privacy columns on legacy
  opinion evidence.
- `run_emotion_evidence.py` and `collect_emotion_evidence.py` are dry-run by
  default. Mutations require `--apply`; publication additionally verifies the
  validation artifact and a non-overwritten SQLite backup.
- Dashboard ranking reads only the active published run after its 80/100 gate.
  It shows fixed catalog coverage and cohort coverage separately, and exposes
  per-skin missing-evidence reasons without displaying an unvalidated headline
  emotion score.
- Weibo transport accepts an optional proxy and retains comment IDs; Bilibili
  now has typed top-level reply retrieval and a transport-independent parser.

## Validation

### Test Result

The implementation test suite and Streamlit acceptance passed. The bounded
local run is structurally valid and deliberately staged. The separate legacy
dataset validator still fails on pre-existing catalog/image discrepancies; no
attempt was made to alter that unrelated dataset. [V1-V10]

### V1 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

Observed 273 tests passing in 20.551 seconds.

### V2 - pass

```text
.venv/bin/python -c "from streamlit.testing.v1 import AppTest; ..."
```

Observed zero Streamlit exceptions. The rendered scope states cohort 0/100,
catalog 0/960, release `staged`, and an empty ranking.

### V3 - pass

```text
.venv/bin/python scripts/run_emotion_evidence.py manifest
```

The deterministic rerun reproduced 100 targets and cohort SHA-256
`ee88c6e06374668e185bad26e0d26ecb8acda9a284d5dd9ef985336aa9dc3143`.

### V4 - pass

```text
.venv/bin/python scripts/run_emotion_evidence.py stage-warm
```

The dry run accounted for all 1,112 source associations as 1,089 unique items,
with 1,065 accepted and 24 quarantined.

### V5 - pass

```text
.venv/bin/python scripts/collect_emotion_evidence.py --target-limit 1 --apply
```

The expected safety failure occurred before network access because the run's
ethics status is `pending`.

### V6 - partial

```text
.venv/bin/python scripts/run_emotion_evidence.py review-pack --phase development --output /tmp/emogame-development-review.csv
```

Only 7/20 development skins currently have accepted rows. Dry-run mode wrote no
pack, and model selection remains unavailable.

### V7 - pass

```text
.venv/bin/python scripts/run_emotion_evidence.py review-pack --phase locked --output /tmp/emogame-locked-review.csv
```

The expected safety failure kept the locked set closed because development-only
model selection is not frozen.

### V8 - pass

```text
SQLite run audit query
```

Observed 960 catalog skins, 100 cohort members, 1,089 run evidence items, 100
validation rows, zero published rows, zero non-null raw author fields, and zero
private identity keys in persisted raw JSON.

### V9 - pass

```text
sqlite3 data/wzry_skins/skins.sqlite3 'PRAGMA integrity_check;' && git diff --check
```

SQLite returned `ok`; Git reported no whitespace errors.

### V10 - fail

```text
.venv/bin/python test_wzry_skins.py
```

The pre-existing standalone dataset validator reports 14 hero skin-count
mismatches and one missing image. Its query API checks pass. This failure is
outside the emotion-evidence implementation and was not modified.

## Evidence Ledger

| ID | Class | Locator | Supported conclusion |
| --- | --- | --- | --- |
| E1 | user-stated | Current P1 implementation request | Defines the cohort, estimand, thresholds, exclusions, and desired release workflow. |
| E2 | verified | Warm Weibo artifact SHA-256 `541e2373...f0606f` | Supplies the only reused real comment source; no pilot score fields were consumed. |
| E3 | verified | Frozen manifest SHA-256 `7a7531bb...797bb` | Persists the ordered 100-target cohort and canonical record hash. |
| E4 | verified | Validation report SHA-256 `24b35fe2...349` | Records 0 eligible profiles and prioritized per-skin recollection gaps. |
| E5 | verified | Protocol SHA-256 `c1c56a40...16769` | Binds the scoring, privacy, review, and publication rules used by the run. |
| V1 | verified | Root unittest command | Code regression suite passes. |
| V2 | verified | Streamlit AppTest | UI is exception-free and reports fixed fail-closed coverage. |
| V3 | verified | Manifest dry run | Cohort selection is deterministic. |
| V4 | verified | Warm-stage dry run | Every source association is accepted, deduplicated, or quarantined. |
| V5 | verified | Applied collector preflight | Ethics gate prevents live collection before network access. |
| V6 | verified | Development-pack dry run | Current evidence covers only 7/20 review targets. |
| V7 | verified | Locked-pack preflight | Locked labels cannot be opened before model selection. |
| V8 | verified | SQLite audit | Database counts and privacy checks match the staged state. |
| V9 | verified | SQLite/Git checks | Database integrity and patch whitespace pass. |
| V10 | verified | Standalone dataset validator | Captures an unrelated pre-existing failure rather than treating it as a pass. |

### Manifest-verbatim evidence

- E1 locator: `Current task: implement the supplied P1 Emotion-Evidence Solution plan`
- E2 locator: `warm Weibo comments`; SHA-256:
  `541e237306abb68db60d6648b5a1d12131ff1b8c7bea9003def01f4501f0606f`
- E3 locator: `frozen 100-skin manifest`; SHA-256:
  `7a7531bb93392bd6597fe87446616c648f936b7a02f2563c1ffc3ebd9de797bb`
- E4 locator: `pre-publication validation report`; SHA-256:
  `24b35fe211c1da115fe3b892abe40eac56051335d2281bd24305cdc80cd17349`
- E5 locator: `docs/13-emotion-evidence-protocol.md`; SHA-256:
  `c1c56a404bab4bcc59bc6a7068b70196a45435fcf20d48566c5c34d4d4e16769`

Observed summaries captured by the evidence manifest:

- V1: 273 tests passed in 20.551 seconds
- V2: Streamlit AppTest completed with zero exceptions and showed cohort 0/100, catalog 0/960, release staged
- V3: Deterministic cohort rerun returned 100 records and SHA-256 ee88c6e06374668e185bad26e0d26ecb8acda9a284d5dd9ef985336aa9dc3143
- V4: Dry run accounted for 1,112 source associations as 1,089 deduplicated items: 1,065 accepted and 24 quarantined
- V5: Expected fail-closed result: live collection stopped before network because ethics status is pending
- V6: Only 7 of 20 development review skins currently have accepted rows; no pack was written in dry-run mode
- V7: Expected fail-closed result: locked review stayed closed because model selection is not frozen
- V8: Live DB has 960 catalog skins, 100 cohort members, 1,089 evidence items, 100 validation rows, zero published rows, zero raw author fields, and zero private raw JSON rows
- V9: SQLite integrity returned ok and the patch had no whitespace errors
- V10: Pre-existing dataset validator reports 14 hero skin-count mismatches and one missing image; query API checks still pass

## Git Custody

- Branch: `main`
- Baseline HEAD: `9228f2e969723972809abbe8dbadc017b1017220`
- Final HEAD: `9228f2e969723972809abbe8dbadc017b1017220`
- History relation: `same`
- Commits since baseline: none
- Commit/push: not requested and not performed
- Explicit task scopes: `.gitignore`, `api`, `app.py`, the two modified crawler
  files, `dashboard`, `data/emotion_evidence_repository.py`, the protocol,
  emotion/rule models, the two dashboard pages, the three task scripts, and
  `tests`
- Task-owned changed paths: 27
- Pre-existing paths: `progress/2026-09-02-app-interface-robustness-review.md`
- Pre-existing overlaps: none
- Outside-scope changed paths: the same unrelated pre-existing progress note
- Ownership caveat: ignored SQLite/run artifacts were intentionally updated by
  the bounded local staging run and are not represented in Git diff counts.
- Scoped diff: `files=19; insertions=454; deletions=106; binary_files=0; untracked_files=8`

Manifest task-path scopes, verbatim:

- `.gitignore`
- `api`
- `app.py`
- `crawlers/bilibili_evidence.py`
- `crawlers/weibo_skin_comment_crawler.py`
- `dashboard`
- `data/emotion_evidence_repository.py`
- `docs/13-emotion-evidence-protocol.md`
- `models/emotion_evidence.py`
- `models/emotion_workflow.py`
- `models/rule_engine.py`
- `pages/皮肤探索.py`
- `pages/皮肤详情.py`
- `scripts/collect_emotion_evidence.py`
- `scripts/evaluate_skin.py`
- `scripts/run_emotion_evidence.py`
- `tests`

Task-owned changed paths, verbatim:

- `.gitignore`
- `api/routes/cash_value.py`
- `api/routes/evaluation.py`
- `app.py`
- `crawlers/bilibili_evidence.py`
- `crawlers/weibo_skin_comment_crawler.py`
- `dashboard/charts.py`
- `dashboard/models.py`
- `dashboard/query.py`
- `data/emotion_evidence_repository.py`
- `docs/13-emotion-evidence-protocol.md`
- `models/emotion_evidence.py`
- `models/emotion_workflow.py`
- `models/rule_engine.py`
- `pages/皮肤探索.py`
- `pages/皮肤详情.py`
- `progress/2026-09-02-emotion-evidence-pipeline.md`
- `scripts/collect_emotion_evidence.py`
- `scripts/evaluate_skin.py`
- `scripts/run_emotion_evidence.py`
- `tests/test_api.py`
- `tests/test_cash_value.py`
- `tests/test_dashboard_models.py`
- `tests/test_emotion_collector.py`
- `tests/test_emotion_evidence.py`
- `tests/test_evaluation.py`
- `tests/test_market_signals.py`
- `tests/test_sales_advisor.py`

Record path: `progress/2026-09-02-emotion-evidence-pipeline.md`

## Evidence Boundary

This work establishes implementation correctness for the new contracts,
regressions, fail-closed UI/API behavior, deterministic cohort creation,
privacy-minimized warm staging, and release-gate mechanics. It does not establish
that the deterministic or Ollama extractors meet human validation thresholds,
that the current cohort has sufficient evidence, that community perception is
representative of all skins, or that any emotion score predicts revenue,
willingness to pay, pricing response, or causal value.

Validated emotion coverage remains 0/960 catalog-wide and 0/100 cohort-wide.
The 50-row perceived-premium pilot remains separate and does not close P1.
`README` was intentionally not updated because publication did not pass. [E4,
V2, V6, V8]

## Next Steps

1. Supply and hash-bind the public-data/privacy or institutional ethics
   determination with `record-ethics`.
2. Run the resumable Weibo/Bilibili collector to fill all 20 development skins,
   then import two independent development reviews and consensus labels.
3. Run all three local extractors, freeze the development-only selection, open
   the locked set once, and execute the locked acceptance gate.
4. Collect/annotate the remaining production evidence, complete the blinded 10%
   production audit, rerun validation, and recollect exact missing aspects.
5. Publish only if at least 80/100 profiles pass with zero forbidden lineage;
   otherwise retain the staged state and use the prioritized gap report.
6. Address the standalone catalog/image validator discrepancies separately if
   that legacy dataset check is required for release.
