# Premium score feature provenance and frozen 50-skin run

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-08-28`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Implemented the confirmed first-stage 50-skin perceived/emotional-premium
scoring workflow with a frozen cohort, resumable local-plus-remote VLM
execution, producer-signed caches, bounded output recovery, row-level feature
and provenance traces, and an output-directory writer lock. The authoritative
local run is `data/premium_pilot/runs/20260828-seed42-partial-v3`: it contains
50 unique scored skins, 25 held-out revenue outcomes, and 50 complete
provenance traces whose reconstructed scores match their published scores
(V5, V6).

All 50 scores are intentionally partial because no verified real-media mapping
was supplied. L3 semantic text remains non-scoring rationale, and revenue was
used only for descriptive held-out validation.

## Task and Scope

The confirmed plan was to select 50 hero skins, score their perceived and
emotional premium using the existing visual and official evidence, combine
local and remote VLM processing, and make every feature and producer decision
traceable (E1). The plan also kept synthetic comments outside media evidence,
kept L3 rationale out of the numerical score, and reserved revenue for
validation rather than training or weight selection.

At the start of this implementation capture, the pilot runner and several VLM
modules existed as uncommitted work. They did not yet provide the complete
frozen-manifest, signed-cache, resumable-run, score-reconstruction, and
single-writer behavior delivered here. The work covered the scoring and VLM
runtime, trace artifacts, CLI controls, documentation, regression tests, and
one final local 50-skin run. It did not create real media links, collect human
reviewer labels, retune weights against revenue, deploy a service, or commit
repository changes.

## Interface and Behavior Changes

`scripts/run_premium_pilot.py` now accepts `--manifest` to replay a verified
cohort and `--resume-vlm-results` to reuse only successful rows with the
required tier lineage. Every non-dry run owns `<output-dir>/.run.lock`, writes
`feature_trace.jsonl` and `run_metadata.json`, and reports missing, rejected,
and fallback-recovered VLM rows separately.

VLM cache entries now carry a producer signature derived from model, prompt,
schema, source, and upstream inputs. Legacy or mismatched entries are cache
misses. Invalid local outputs are retried within a bounded chain and may fall
back to a distinct local family or the validated combined remote L2/L3 path;
only accepted structured outputs are cached or scored.

## Implementation

### Plan and Starting Status

The source locator is `2026-08-28 confirmed 50-skin feature/provenance implementation plan in current conversation`. That plan (E1) defined the 50-skin cohort, partial-first
evidence policy, local-plus-remote execution, explicit feature lineage, and
revenue-only validation boundary. Existing uncommitted pilot code supplied a
starting scorer and pipeline, while this work completed and hardened the
reproducible execution and trace contract.

### Core Functions and Result

| Path | Core symbols | Result |
| --- | --- | --- |
| `vlm/provenance.py` | `producer_signature`, `tier_provenance` | Defines stable hashes and public producer lineage for L1, L2, and L3. |
| `vlm/cache.py` | `VLMCache.get`, `VLMCache.set` | Migrates cache rows for producer signatures and rejects legacy or mismatched producers. |
| `vlm/l1_classifier.py`, `vlm/l2_analyzer.py` | `classify`, `analyze` | Validate each attempt, bound retries, trace attempted producers, and cache only accepted outputs. |
| `vlm/degradation.py`, `vlm/l3_semantic.py`, `vlm/pipeline.py` | combined remote recovery and pipeline assembly | Validates combined remote L2/L3 recovery and exposes complete tier provenance without allowing L3 into the score. |
| `models/premium_pilot.py` | `load_frozen_manifest`, `build_feature_trace`, `score_premium` | Verifies frozen image hashes, includes composition in visual scoring, records official/media derivation and normalized contributions, and checks score reconstruction. |
| `scripts/run_premium_pilot.py` | `reusable_vlm_result`, `output_run_lock`, `async_main` | Adds frozen replay, signed-row resume, per-directory exclusion, trace/report artifacts, and run-level source/model hashes. |
| `tests/test_premium_pilot.py`, `tests/test_vlm_output_validation.py` | regression cases | Covers manifest mutation, composition, trace reconstruction, L3 exclusion, resume filtering, locking, signed caches, fallback chains, and remote retries. |
| `docs/12-perceived-premium-pilot.md` | pilot protocol | Documents execution, artifacts, evidence boundaries, resume behavior, and provenance rules. |

The final run selected 50 skins with seed 42, including 25 skins with signed
revenue-window outcomes. Its manifest SHA-256 is
`0c5d8421c4e0aab150373809aa854f6fe064ab33982d7c2e439e3d23612b231f`.
Twenty VLM rows completed through their primary path and 30 recorded rejected
local output attempts followed by successful fallback recovery; no invalid or
missing row reached scoring (V5).

## Validation

### Test Result

All recorded checks passed. The focused suite passed 29 tests and the complete
root suite passed 221 tests. The authoritative frozen replay and independent
artifact audit both completed successfully (V1-V6).

### V1 - pass

```text
.venv/bin/python -m unittest tests.test_premium_pilot tests.test_vlm_output_validation
```

29 focused premium-pilot and VLM-hardening tests passed, including output
locking, signed cache behavior, fallback recovery, resume filtering, and trace
reconstruction.

### V2 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

All 221 root tests passed; only existing Streamlit and dependency deprecation
warnings were emitted.

### V3 - pass

```text
python3 -m py_compile scripts/run_premium_pilot.py vlm/provenance.py vlm/cache.py vlm/config.py vlm/prompts.py vlm/l1_classifier.py vlm/l2_analyzer.py vlm/l3_semantic.py vlm/degradation.py vlm/pipeline.py vlm/schemas.py models/premium_pilot.py tests/test_premium_pilot.py tests/test_vlm_output_validation.py
```

All changed premium pilot, VLM, and regression-test modules compiled
successfully.

### V4 - pass

```text
git diff --check
```

Git reported no whitespace errors in the working-tree diff.

### V5 - pass

```text
.venv/bin/python scripts/run_premium_pilot.py --manifest data/premium_pilot/runs/20260828-seed42-partial-v2/manifest.json --run-vlm --require-remote --execution-mode full --vlm-results data/premium_pilot/runs/20260828-seed42-partial-v2/vlm_outputs.jsonl --resume-vlm-results --output-dir data/premium_pilot/runs/20260828-seed42-partial-v3
```

The final frozen 50-skin replay completed with 50 partial scores, 50 provenance-complete traces, no invalid or missing VLM outputs, 30 recovered fallback rows, and manifest SHA-256 0c5d8421c4e0aab150373809aa854f6fe064ab33982d7c2e439e3d23612b231f.

### V6 - pass

```text
.venv/bin/python -c 'import json,pathlib; p=pathlib.Path("data/premium_pilot/runs/20260828-seed42-partial-v3"); m=json.loads((p/"manifest.json").read_text()); s=[json.loads(x) for x in (p/"scores.jsonl").read_text().splitlines()]; t=[json.loads(x) for x in (p/"feature_trace.jsonl").read_text().splitlines()]; v=[json.loads(x) for x in (p/"vlm_outputs.jsonl").read_text().splitlines()]; assert [len(m["records"]),len(s),len(t),len(v)]==[50]*4; assert all(len({x["source_key"] for x in rows})==50 for rows in (s,t,v)); assert all(abs(x["fusion"]["reconstructed_score"]-x["result"]["perceived_premium_score"])<=0.01 for x in t); assert all(x["provenance_status"]=="complete" for x in t); print("50 manifest, score, trace, and VLM rows; unique keys; complete provenance; exact score reconstruction")'
```

Artifact audit confirmed 50 rows and 50 unique keys in every core file,
complete provenance for every row, and exact reconstructed scores.

## Evidence Ledger

| ID | Class | Locator | Supported conclusion |
| --- | --- | --- | --- |
| E1 | user-stated | 2026-08-28 confirmed 50-skin feature/provenance implementation plan in the current conversation | Defines the requested cohort, traceability, hybrid execution, non-scoring L3, and revenue-validation scope. |
| V1 | verified | focused unittest command above | The premium-pilot and VLM-hardening regression cases pass. |
| V2 | verified | root unittest command above | All 221 root tests pass. |
| V3 | verified | compile command above | Changed Python modules are syntactically valid. |
| V4 | verified | Git whitespace check above | The working-tree diff has no whitespace errors. |
| V5 | verified | frozen replay command above | The final 50-skin run completed with the stated VLM and trace status. |
| V6 | verified | artifact-audit command above | Core artifact cardinality, key uniqueness, provenance completeness, and score reconstruction hold. |

## Git Custody

Branch: `main`. Baseline HEAD and final HEAD are both
`5a2e826267b8ba8def1f0cadf6d258d4453a2818`. History relation: `same`.
There are no commits since baseline. The scoped-diff token is
`files=9; insertions=686; deletions=149; binary_files=0; untracked_files=6`.

Explicit task paths and task-owned changed paths are
`docs/12-perceived-premium-pilot.md`, `models/premium_pilot.py`,
`scripts/run_premium_pilot.py`, `tests/test_premium_pilot.py`,
`tests/test_vlm_output_validation.py`, `vlm/cache.py`, `vlm/config.py`,
`vlm/degradation.py`, `vlm/l1_classifier.py`, `vlm/l2_analyzer.py`,
`vlm/l3_semantic.py`, `vlm/pipeline.py`, `vlm/prompts.py`,
`vlm/provenance.py`, and `vlm/schemas.py`. The record path is
`progress/2026-08-28-premium-score-feature-provenance.md`.

The repository was already dirty. Pre-existing overlaps are
`docs/12-perceived-premium-pilot.md`, `models/premium_pilot.py`,
`scripts/run_premium_pilot.py`, `tests/test_premium_pilot.py`,
`tests/test_vlm_output_validation.py`, `vlm/cache.py`,
`vlm/l1_classifier.py`, `vlm/l2_analyzer.py`, `vlm/pipeline.py`, and
`vlm/schemas.py`. Those paths were extended in place and were not reverted.

Outside-scope changed paths are `.dsh-tools/app_viewer_template.html`,
`.dsh-tools/db_dashboard_template.html`, `.dsh-tools/make_app_viewer.py`,
`.dsh-tools/make_db_dashboard.py`, `.dsh-tools/make_skins_dashboard.py`,
`.dsh-tools/skins_dashboard_template.html`, `.dsh-tools/test_viewer_logic.js`,
`.gitignore`, `.gitmodules`, `.npmrc`, `agent-lightning`,
`all.log.2026-07-16`, `data/weibo_comment_synthesizer.py`,
`docs/10-agent-lightning-local.md`, `docs/11-agentic-rl-method-survey-slides.md`,
`docs/11-agentic-rl-method-survey.md`,
`docs/11-agentic-rl-method-survey.pptx`, `docs/README.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`,
`progress/2026-08-11-emotional-value-framework-one-month-report.md`,
`progress/2026-08-11-emotional-value-framework-one-month-report.pdf`,
`progress/2026-08-27-perceived-premium-pilot.md`,
`progress/2026-08-27-vlm-output-hardening.md`,
`progress/2026-08-28-synthetic-comment-batch-integrity.md`,
`tests/test_weibo_comment_synthesizer.py`, and `vlm/output_validation.py`.
They were preserved. Local run artifacts under `data/premium_pilot/` are
ignored data rather than Git-owned implementation paths. No commit was
created.

## Evidence Boundary

This work establishes reproducible cohort selection, accepted-output scoring,
complete feature/producer traces, exact score reconstruction, and the stated
local run and test results. It does not establish that the current weights are
psychometrically calibrated, that the partial scores predict future revenue,
that the descriptive revenue correlation is causal, or that synthetic comments
are valid community evidence. Complete-evidence comparisons still require a
verified real-media mapping and reviewer labels.

## Next Steps

Collect and link verified real posts/comments for the frozen cohort, then have
annotators complete the blinded reviewer cards. Re-run the same manifest with
real media to produce complete-evidence scores and inter-rater validation;
keep the 25 revenue rows held out and do not retune against them during this
pilot.
