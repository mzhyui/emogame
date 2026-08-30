# Fail-closed local VLM output hardening

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `function-fix`
- Date: `2026-08-27`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Local L1 and L2 now reject empty, malformed, incomplete, and zero-default
structured replies rather than converting them into successful cached results.
The premium-pilot runner records a review gate for such failures, while keeping
the raw model response out of cache and output artifacts. The change is covered
by dedicated regression tests and the root suite passed (V1--V4).

## Task and Scope

The request was to harden the scripts before attempting another live premium
pilot run. The verified pre-existing contract was that local L1/L2 prompts
request complete JSON and that the pipeline supplies structured values to its
downstream consumers (E1--E3). The defect boundary was the use of schema
defaults after a missing `parsed_json`, which made a failed local response look
like an `_source: ollama` success and allowed it into the cache.

This fix covers local L1/L2 response validation, invalid-cache recovery,
pipeline diagnostics, pilot-report gating, tests, and the pilot runbook. It
does not change score fusion, media evidence, revenue validation, or make a new
local/API call.

## Implementation

### Preserved Contract

L1 and L2 continue to return their existing successful schema-shaped data to
downstream callers. Cache entries remain image-and-tier scoped. The runner
continues to emit reproducible artifacts and permits partial evidence, but a
local structured-output rejection must now be visible for human review.

### Corrective Change

| Path | Symbols | Result |
| --- | --- | --- |
| `vlm/output_validation.py` | `validate_l1_response`, `validate_l2_response` | Requires all prompt fields, validates L2 scores in the promised 1--10 range, and emits only reply length, SHA-256, parsed type, and structural validation details. |
| `vlm/l1_classifier.py`, `vlm/l2_analyzer.py` | local call and cache paths | Retries one invalid local reply, never caches it, evicts only a rejected legacy cache tier, and returns an explicit invalid-local diagnostic. L1 falls back to CV; L2 returns an error result. |
| `vlm/cache.py`, `vlm/pipeline.py`, `vlm/schemas.py` | `invalidate_level`, `tier_diagnostics` | Removes malformed/non-object cache records and carries bounded tier errors through the public VLM feature vector. Cache-hit flags now require the result actually to have been served from cache. |
| `scripts/run_premium_pilot.py` | `summarize_vlm_execution` | Adds `vlm_execution` to the JSON and Markdown reports; any invalid local output, missing output, or partial/error VLM status produces `review_required`. |
| `tests/test_vlm_output_validation.py`, `docs/12-perceived-premium-pilot.md` | regression coverage and runbook | Captures the zero-default failure and documents the smoke-test release gate. |

## Interface and Behavior Changes

`VLMFeatureVector` now includes `tier_diagnostics`. Pilot `report.json` and
`report.md` now include `vlm_execution`, with a `completed` or
`review_required` state and affected source keys. A legacy cached L1/L2 result
that cannot meet the current contract is automatically removed only for that
image and tier before the bounded retry; valid cache entries retain their
behavior.

## Validation

### Test Result

All recorded checks passed. These are code and dry-run validations; no new live
Ollama or AutoDL execution was performed.

### V1 - pass

```text
python3 -m py_compile vlm/output_validation.py vlm/cache.py vlm/l1_classifier.py vlm/l2_analyzer.py vlm/pipeline.py scripts/run_premium_pilot.py tests/test_vlm_output_validation.py
```

All modified Python modules compiled.

### V2 - pass

```text
.venv/bin/python -m unittest tests.test_vlm_output_validation tests.test_premium_pilot
```

19 focused tests passed, including invalid replies, cache eviction, report
gating, and pilot regressions.

### V3 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

211 root tests passed; existing Streamlit and dependency deprecation warnings
were emitted.

### V4 - pass

```text
python3 scripts/run_premium_pilot.py --limit 3 --dry-run
```

Deterministic smoke cohort selected three records without model calls or output
artifacts.

## Evidence Ledger

| ID | Class | Locator | Supported conclusion |
| --- | --- | --- | --- |
| E1 | verified | `vlm/l1_classifier.py`, SHA-256 `5be222cf1adc097bbdc4f6123624ed667315f62fbdf1e8071a61f5ed6c36cd65` | Missing parsed output had been replaced by schema defaults and cached. |
| E2 | verified | `vlm/l2_analyzer.py`, SHA-256 `745a612fda7bc8c25510fcefc6fb012698aeaca31eb347bd78065567e0280021` | L2 used the same default-accepting cache path. |
| E3 | verified | `vlm/prompts.py`, SHA-256 `a59dc45231d77e17140c431c0261c1fbe27b856b63b058feaccb66a1c80c7459` | The prompts require complete structured JSON; L2 asks for 1--10 scores. |
| E4 | verified | `vlm/output_validation.py`, SHA-256 `c3a82f438d7df9f844c026a57ab6b279319aeea702b32a5d323c8f29f3587812` | The new boundary validator is explicit and bounded. |
| E5 | verified | `scripts/run_premium_pilot.py`, SHA-256 `158c1c3f1c6ebe9bc271128f55efa77800b09804dc6a7428d01c8631b382aa2a` | The pilot report now exposes a VLM execution review gate. |
| E6 | verified | `docs/12-perceived-premium-pilot.md`, SHA-256 `781f7617494ed7ea50619a436b0c416b9e18e099e912868353a53e8f4e0d68ee` | The runbook now makes the smoke release criterion explicit. |
| V1 | verified | compile command above | Modified modules compiled. |
| V2 | verified | focused test command above | 19 focused tests passed. |
| V3 | verified | root test command above | 211 root tests passed. |
| V4 | verified | dry-run command above | The deterministic three-skin cohort remains usable without model calls. |

## Git Custody

Branch: `main`. Baseline HEAD and final HEAD are both
`5a2e826267b8ba8def1f0cadf6d258d4453a2818`. History relation: `same`.
There are no commits since baseline. The scoped-diff token is
`files=5; insertions=98; deletions=33; binary_files=0; untracked_files=4`.

Explicit task paths and task-owned changed paths are
`docs/12-perceived-premium-pilot.md`,
`progress/2026-08-27-vlm-output-hardening.md`,
`scripts/run_premium_pilot.py`, `tests/test_vlm_output_validation.py`,
`vlm/cache.py`, `vlm/l1_classifier.py`, `vlm/l2_analyzer.py`,
`vlm/output_validation.py`, `vlm/pipeline.py`, and `vlm/schemas.py`. The
record path is `progress/2026-08-27-vlm-output-hardening.md`.

The repository was dirty before this work. Pre-existing paths recorded at
baseline were `.dsh-tools/app_viewer_template.html`,
`.dsh-tools/db_dashboard_template.html`, `.dsh-tools/make_app_viewer.py`,
`.dsh-tools/make_db_dashboard.py`, `.dsh-tools/make_skins_dashboard.py`,
`.dsh-tools/skins_dashboard_template.html`, `.dsh-tools/test_viewer_logic.js`,
`.gitignore`, `.gitmodules`, `.npmrc`, `agent-lightning`,
`all.log.2026-07-16`, `docs/10-agent-lightning-local.md`,
`docs/11-agentic-rl-method-survey-slides.md`,
`docs/11-agentic-rl-method-survey.md`,
`docs/11-agentic-rl-method-survey.pptx`,
`docs/12-perceived-premium-pilot.md`, `docs/README.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`progress/2026-08-11-emotional-value-framework-one-month-report.md`,
`progress/2026-08-11-emotional-value-framework-one-month-report.pdf`,
`progress/2026-08-27-perceived-premium-pilot.md`,
`scripts/run_premium_pilot.py`, and `tests/test_premium_pilot.py`.

The pre-existing overlap paths are `docs/12-perceived-premium-pilot.md` and
`scripts/run_premium_pilot.py`. Outside-scope changed paths are
`.dsh-tools/app_viewer_template.html`, `.dsh-tools/db_dashboard_template.html`,
`.dsh-tools/make_app_viewer.py`, `.dsh-tools/make_db_dashboard.py`,
`.dsh-tools/make_skins_dashboard.py`, `.dsh-tools/skins_dashboard_template.html`,
`.dsh-tools/test_viewer_logic.js`, `.gitignore`, `.gitmodules`, `.npmrc`,
`agent-lightning`, `all.log.2026-07-16`, `docs/10-agent-lightning-local.md`,
`docs/11-agentic-rl-method-survey-slides.md`,
`docs/11-agentic-rl-method-survey.md`,
`docs/11-agentic-rl-method-survey.pptx`, `docs/README.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`progress/2026-08-11-emotional-value-framework-one-month-report.md`,
`progress/2026-08-11-emotional-value-framework-one-month-report.pdf`,
`progress/2026-08-27-perceived-premium-pilot.md`, and
`tests/test_premium_pilot.py`. No commit was created.

## Evidence Boundary

This establishes that local structured-output failure is fail-closed in the
tested code paths and that the pilot reports it. It does not establish that the
next live Ollama response will satisfy the contract, that a model is accurate,
or that any perceived-premium/revenue relationship is validated.

## Next Steps

Run the same three-skin live smoke command and inspect
`report.json.vlm_execution`. Expand only if it is `completed` with zero local
output rejections; otherwise use the retained bounded diagnostics to adjust the
local model or prompt.
