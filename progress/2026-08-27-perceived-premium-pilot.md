# Perceived-premium pilot implementation

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-08-27`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Implemented the approved Week-1 perceived/emotional-premium pilot as a
reproducible, evidence-weighted batch workflow. It builds a deterministic
50-skin manifest, reconciles the legacy image cache by stable skin identity,
keeps real linked media separate from missing media, produces blind-review
cards, and compares frozen scores with reviewer and signed revenue-window
outcomes only after scoring. The result is implementation-ready; no live VLM
or remote API batch has been run, and no claim of revenue prediction or causal
value is made. E1, E2, E3, V1, V2, V3, and V4 support this outcome.

## Task and Scope

The source plan was `Current conversation: approved Week-1 perceived-premium
pilot plan` (E1). Before this change the repository had VLM, community-label,
cash-value, and rule-engine components, but no isolated first-stage pilot that
could freeze a multimodal score before validation. The confirmed constraints
were a 50-skin pilot, one blind reviewer, a 15–20-skin real-media subset,
local L1/L2 plus AutoDL-compatible remote analysis, report-and-dataset
delivery, and no dashboard change.

In scope: deterministic selection, image identity reconciliation, evidence
formats, frozen fusion, validation reports, runbook, and tests. Out of scope:
training a supervised model, altering `RuleEngine` or Streamlit, generating or
using synthetic media, writing score outputs into production SQLite tables,
and a live external-model run.

## Interface and Behavior Changes

- `models/premium_pilot.py` exposes manifest, media-validation, frozen-score,
  reviewer-audit, and held-out revenue-validation functions.
- `scripts/run_premium_pilot.py` adds a CLI for `--dry-run`, `--run-vlm`, a
  cached/precomputed VLM JSONL, verified media maps, reviewer CSVs, and
  report/dataset artifacts under `data/premium_pilot/`.
- `docs/premium-pilot-media-mapping.example.json` defines the required real
  post-to-skin mapping fields. Synthetic mappings fail closed.
- `docs/premium-pilot-reviewer-template.csv` defines the blind scalar-rating
  input. `docs/12-perceived-premium-pilot.md` records the execution boundary.

## Implementation

### Plan and Starting Status

The implementation follows E1. Existing VLM and cash-value modules were kept
as dependencies; no compatible pre-existing pilot interface was found. E2 and
E3 are the hash-bound source artifacts for the fresh implementation.

### Core Functions and Result

| Path | Core behavior | Result |
| --- | --- | --- |
| `models/premium_pilot.py` | Reconciles old filenames through hero name and skin ID, rejects ambiguous matches, selects a seed-42 manifest, validates real media, fuses visual 55%, official context 20%, and media 25%, and keeps reviewer/revenue values out of fusion. | Implemented |
| `scripts/run_premium_pilot.py` | Runs local/remote VLM only when requested, strips user identity before optional remote media enrichment, writes manifest/score/provenance/reviewer/report artifacts, and supports no-client dry runs. | Implemented |
| `tests/test_premium_pilot.py` | Covers image matching, deterministic selection, missing-modality confidence caps, synthetic-media rejection, validation boundaries, and a no-network runner path. | Implemented |
| `docs/12-perceived-premium-pilot.md` | Supplies the cohort, mapping, review, smoke-test, and interpretation runbook. | Implemented |

Partial scores are re-normalized only across available inputs, capped at 0.65
confidence, and placed in `partial_evidence`; complete scores use
`complete_evidence`. Revenue is loaded only by the post-freeze validation
helper. The runner does not begin a model batch unless `--run-vlm` is supplied.

## Validation

### Test Result

All recorded checks passed. The full suite verified 205 tests; the live
50-skin command was dry-run only and did not call a local model server or a
remote API.

### V1 - pass

```text
python3 -m py_compile models/premium_pilot.py scripts/run_premium_pilot.py tests/test_premium_pilot.py
```

All three premium-pilot files compiled successfully.

### V2 - pass

```text
python3 -m unittest tests.test_premium_pilot
```

13 premium-pilot regression tests passed.

### V3 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

205 project tests passed.

### V4 - pass

```text
python3 scripts/run_premium_pilot.py --limit 50 --dry-run > /tmp/emogame-premium-pilot-manifest.json
```

The live 50-skin dry run completed and wrote only a temporary manifest.

## Evidence Ledger

- E1 — user-stated — `Current conversation: approved Week-1 perceived-premium
  pilot plan`; supplies the approved first-stage objective, scope, and
  constraints.
- E2 — verified — `models/premium_pilot.py`, SHA-256
  `8738d3a0f28e49a48f83a6da243ea47fb65e68f5e0653c513a8ee3d35e1241fd`;
  implements the score, evidence, and validation boundary.
- E3 — verified — `scripts/run_premium_pilot.py`, SHA-256
  `f6d7de9160c8f89252c5f902bc8320a353c36f9553d95b7a3d7d6078a7d6cad6`;
  implements the reproducible command-line workflow and artifact output.
- V1 — verified — `python3 -m py_compile models/premium_pilot.py
  scripts/run_premium_pilot.py tests/test_premium_pilot.py`; all three
  premium-pilot files compiled successfully.
- V2 — verified — `python3 -m unittest tests.test_premium_pilot`; 13
  premium-pilot regression tests passed.
- V3 — verified — `.venv/bin/python -m unittest discover -s tests`; 205
  project tests passed.
- V4 — verified — `python3 scripts/run_premium_pilot.py --limit 50 --dry-run
  > /tmp/emogame-premium-pilot-manifest.json`; the live 50-skin dry run
  completed and wrote only a temporary manifest.

## Git Custody

Branch: `main`. Baseline HEAD:
`5a2e826267b8ba8def1f0cadf6d258d4453a2818`. Final HEAD:
`5a2e826267b8ba8def1f0cadf6d258d4453a2818`. History relation: `same`.
There are no commits since baseline.

Explicit task paths are `.gitignore`, `docs/12-perceived-premium-pilot.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`scripts/run_premium_pilot.py`, and `tests/test_premium_pilot.py`. The record
path is `progress/2026-08-27-perceived-premium-pilot.md`.

The initial evidence capture happened after implementation, so its dirty-tree
snapshot already contained all seven task paths. The task-owned changed paths
observed in that capture are `.gitignore`, `docs/12-perceived-premium-pilot.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`scripts/run_premium_pilot.py`, and `tests/test_premium_pilot.py`; each is also
listed as a pre-existing overlap. The task-owned implementation is established
by E1–E3 and this session's direct edits, while the late custody snapshot cannot
separate those paths from its own baseline status.

The pre-existing paths recorded at capture were
`.dsh-tools/app_viewer_template.html`, `.dsh-tools/db_dashboard_template.html`,
`.dsh-tools/make_app_viewer.py`, `.dsh-tools/make_db_dashboard.py`,
`.dsh-tools/make_skins_dashboard.py`, `.dsh-tools/skins_dashboard_template.html`,
`.dsh-tools/test_viewer_logic.js`, `.gitignore`, `.gitmodules`, `.npmrc`,
`agent-lightning`, `all.log.2026-07-16`, `docs/10-agent-lightning-local.md`,
`docs/11-agentic-rl-method-survey-slides.md`, `docs/11-agentic-rl-method-survey.md`,
`docs/11-agentic-rl-method-survey.pptx`, `docs/12-perceived-premium-pilot.md`,
`docs/README.md`, `docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`progress/2026-08-11-emotional-value-framework-one-month-report.md`,
`progress/2026-08-11-emotional-value-framework-one-month-report.pdf`,
`scripts/run_premium_pilot.py`, and `tests/test_premium_pilot.py`.

Outside-scope changed paths were `.dsh-tools/app_viewer_template.html`,
`.dsh-tools/db_dashboard_template.html`, `.dsh-tools/make_app_viewer.py`,
`.dsh-tools/make_db_dashboard.py`, `.dsh-tools/make_skins_dashboard.py`,
`.dsh-tools/skins_dashboard_template.html`, `.dsh-tools/test_viewer_logic.js`,
`.gitmodules`, `.npmrc`, `agent-lightning`, `all.log.2026-07-16`,
`docs/10-agent-lightning-local.md`, `docs/11-agentic-rl-method-survey-slides.md`,
`docs/11-agentic-rl-method-survey.md`, `docs/11-agentic-rl-method-survey.pptx`,
`docs/README.md`, `progress/2026-08-11-emotional-value-framework-one-month-report.md`,
and `progress/2026-08-11-emotional-value-framework-one-month-report.pdf`.

The initial scoped-diff token is
`files=1; insertions=11; deletions=1; binary_files=0; untracked_files=6`.
No commit, push, or cleanup was performed.

## Evidence Boundary

This record establishes code, deterministic dry-run behavior, and the listed
test outcomes. It does not establish that the configured local model endpoint
or AutoDL credential works, that a 15–20-skin media map is real and correctly
linked, that the one reviewer agrees with the model, or that revenue validation
is sufficiently powered. The score remains a pilot evidence-weighted ranking,
not a deployed price, causal attribution, or revenue-prediction model.

## Next Steps

1. Create and review a real 15–20-skin media map using the supplied JSON
   schema.
2. Run the three-skin local/API smoke test, then the 50-skin batch with
   `--run-vlm --require-remote`.
3. Obtain the blind reviewer CSV, rerun against cached VLM outputs, and inspect
   the report before describing any validation result.
