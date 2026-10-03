# Local Ollama agent pipeline

- Record format: `3`
- Record ID: `RCP-20260925T153600Z-6a7d279f`
- Mode: `coding-progress`
- Task type: `feature`
- Task slug: `local-agent-pipeline`
- Date: `2026-09-25`
- Project: `/home/mzhyui/git/emogame`
- Priority: `unspecified`
- Owner: `Unassigned`
- Components: `None`
- Labels: `None`
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-25T15:36:00Z`
- Started at: `unavailable`
- Updated at: `2026-09-25T15:54:34Z`
- Completed at: `2026-09-25T15:54:34Z`
- Due date: `Not applicable`
- Evidence state: `verified`
- Validation state: `fail`
- Implementation class: `fresh-implementation`

## Outcome

Implemented and exercised the six-stage local evaluation workflow. The final live run completed with Qwen 3.5 4B (V4), the final repository suite passed 374 tests (V5), and the offline dry run passed (V6). The aggregate validation field is `fail` because historical failed smoke attempts are deliberately retained as V1–V3; it does not describe the final implementation checks.

## Task and Scope

Implement the architecture in docs/06-agent-architecture.md with a runnable Python script, user input, fixed prompt templates, local evidence, and local Ollama (E1, E2). Scope includes graph orchestration, CLI, prompts, bounded retries, local artifacts, regression tests, and usage documentation. Existing dashboards, GRPO training, downloaded weights, and source databases remain outside the edit scope.

## Lifecycle

Current blocker: None

| ID | At | Action | From | To | Actor | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | 2026-09-25T15:36:00Z | created | none | in-progress | record-tool | record created |
| L2 | 2026-09-25T15:54:34Z | transition | in-progress | done | codex | Implemented local six-stage workflow; final dry run, 374 root tests, and live Ollama execution passed. Historical failures retained. |

Local relationships: None

## Implementation

### Plan and Starting Status

E1 described six nodes but agents/ contained only __init__.py. FeatureBuilder, the 33-field SkinFeatureVector, RuleEngine, SalesAdvisor, SQLite repositories, and VLM prompts/validators already existed. The current rule engine supplies six operational aspect scores and does not implement the proposed learned five-dimension premium predictor.

### Core Functions and Result

| Path | Core function and result |
| --- | --- |
| agents/local_pipeline.py | LocalAgentPipeline creates the six-node StateGraph with one conditional route per node; collects local evidence, runs L1/L2, builds 33 fields, evaluates rules, produces business advice, and writes state/report/chart artifacts. |
| agents/local_ollama.py | LocalOllamaClient uses native /api/chat with proxy bypass, three attempts at most, fixed corrective prompts, complete-response checks, and per-call audit logs. |
| agents/prompts.py | Fixed Chinese system/user prompts and six-section JSON schema; required field and limited false-attribution checks. |
| scripts/run_local_agent.py | Interactive or command-line question and unique source_key/search selection; explicit model, host, timeout, strict-VLM, dry-run, and output controls. |
| tests/test_local_agent_pipeline.py | 17 offline cases cover graph order, read-only custody, missing data, profile provenance, retries, empty/truncated replies, and report attribution. |
| docs/18-local-agent-pipeline.md | Setup, commands, stages, evidence boundaries, fixed templates, statuses, and artifact guide. |
| docs/06-agent-architecture.md | Link to implementation; remove duplicate unconditional VLM transition and terminate the illustrative error handler. |

Official point prices are not projected as CNY spend. Failed VLM results remain null instead of becoming zero scores. Unpublished evidence-profile status survives serialization. Report prompts include the actual user question, raw signal values, feature coverage, rule score provenance, business output, and the selected comparison subset. LangSmith tracing is disabled for this local graph.

## Interface and Behavior Changes

New CLI: `.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question "分析视觉卖点和证据缺口"`. Both vision and text default to `qwen3.5:4b`. Existing source_key is required or resolved from exactly one search match. New run directories prevent overwriting artifacts. `completed`, `degraded`, and `dry_run` exit zero; `failed` exits one. Missing images/model failures degrade by default or stop with `--strict-vlm`. Output metadata and report remain explicit about rule scoring and missing learned predictions.

## Validation

### Test Result

Historical aggregate: `fail`. Final required checks V4–V7 passed; V5 includes all 374 root tests. V8 is an interrupted sandbox invocation, not a passing test run.

### V1 - fail

```text
.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question '请分析小乔天鹅之梦的视觉卖点、定价证据缺口和可执行的运营建议，并与本地同英雄皮肤比较。' --strict-vlm --attempts 1 --output outputs/local_agent/smoke-20260925
```

Historical first smoke: qwen2.5vl:3b image call returned HTTP 500; stopped before features.

Artifact: `/home/mzhyui/git/emogame/outputs/local_agent/smoke-20260925/state.json`; SHA-256: `f55b1f8d87c4040407fe23780fd7f137bd79c31c11865a596f9d65d272fae7b5`.

### V2 - fail

```text
.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question '请分析小乔天鹅之梦的视觉卖点、定价证据缺口和可执行的运营建议，并与本地同英雄皮肤比较。' --vision-model qwen3.5:4b --strict-vlm --attempts 1 --output outputs/local_agent/smoke-qwen35-20260925
```

Historical smoke: valid L1, fenced L2 JSON rejected. Complete-fence handling was subsequently added.

Artifact: `/home/mzhyui/git/emogame/outputs/local_agent/smoke-qwen35-20260925/state.json`; SHA-256: `bb9f76c47afaf2f37a747f6ec352424f190dd2b2fb5ea91230b431e6a1dde9b3`.

### V3 - fail

```text
.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question '请分析小乔天鹅之梦的视觉卖点、定价证据缺口和可执行的运营建议，并与本地同英雄皮肤比较。' --strict-vlm --attempts 1 --output outputs/local_agent/verified-20260925
```

Historical prompt revision: report returned prose instead of JSON. Added final JSON example and corrective retry feedback.

Artifact: `/home/mzhyui/git/emogame/outputs/local_agent/verified-20260925/state.json`; SHA-256: `e36c796a5938d9aea5f9ab5ab7e1983f4d81d64d3a653645cd4ada62ddba3d04`.

### V4 - pass

```text
.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question '请分析小乔天鹅之梦的视觉卖点、定价证据缺口和可执行的运营建议，并与本地同英雄皮肤比较。' --strict-vlm --output outputs/local_agent/reviewed-20260925
```

Final implementation completed all six stages: L1 2.473s, L2 1.890s; report rejected once for false attribution and accepted on attempt two. Stage total about 14.03s; 33 feature fields retained. Report remains a model draft.

Artifact: `/home/mzhyui/git/emogame/outputs/local_agent/reviewed-20260925/state.json`; SHA-256: `15fcc5c074eba22d02e893e2ff4dda0e67738fed767c7f3b9c547385bc5c6369`.

### V5 - pass

```text
timeout 180 .venv/bin/python -m unittest discover -s tests > /tmp/emogame-local-agent-final-tests.log 2>&1
```

Final root suite: 374 tests passed in 26.969 seconds, including the 17 new pipeline/client tests. Ran outside sandbox after the sandboxed suite stalled.

Artifact: `/tmp/emogame-local-agent-final-tests.log`; SHA-256: `a5754a9e9ab4f54dc99dda1e0e68b8e98faa4616a0077ad5512c11dd4d6fe529`.

### V6 - pass

```text
.venv/bin/python scripts/run_local_agent.py --source-key 106-03 --question '分析证据缺口' --dry-run --output outputs/local_agent/final-dry-run-20260925
```

All deterministic stages completed, VLM/report calls skipped, prompt and 33-field state saved without an Ollama call log.

Artifact: `/home/mzhyui/git/emogame/outputs/local_agent/final-dry-run-20260925/state.json`; SHA-256: `155cc96f5fc0413c01184146513e141a22ccf696ead3ad6b0652c063477b87cf`.

### V7 - pass

```text
.venv/bin/python -m compileall -q agents scripts/run_local_agent.py tests/test_local_agent_pipeline.py && git diff --check
```

Python compilation and whitespace check passed.

### V8 - partial

```text
.venv/bin/python -m unittest discover -s tests > /tmp/emogame-local-agent-root-tests.log 2>&1
```

Sandboxed root test invocation stalled before reporting results and was interrupted. Host execution later passed; this invocation is not counted as a pass.

Artifact: `/tmp/emogame-local-agent-root-tests.log`; SHA-256: `c6352e89d984dda65fd0ec387462201304fa5de5eceaf7f306a715b77992102b`.

## Evidence Ledger

| ID | Class | Locator and support |
| --- | --- | --- |
| E1 | verified | docs/06-agent-architecture.md; SHA-256 `d9ae3349d6f6db889731ed214d6909b0e44f8486718afe84b0e627fbcaae9a18` |
| E2 | user-stated | User request: implement docs/06-agent-architecture.md as a local Python pipeline using user input, fixed prompts, local data, and Ollama |
| E3 | verified | docs/18-local-agent-pipeline.md; SHA-256 `72e6c942219805233b0986285dfda8c407fc4aeb267ed8d934b3cb980dd9a2d6` |
| E4 | verified | outputs/local_agent/reviewed-20260925/state.json; SHA-256 `15fcc5c074eba22d02e893e2ff4dda0e67738fed767c7f3b9c547385bc5c6369` |
| E5 | verified | outputs/local_agent/reviewed-20260925/ollama_calls.jsonl; SHA-256 `1085afcc547c9ac08945217be55907669e0f6cd3288ae1f90784076a254a7882` |
| E6 | verified | https://docs.ollama.com/api/chat |
| E7 | verified | https://docs.langchain.com/oss/python/langgraph/graph-api |
| V1 | verified | Observed `fail` check in Validation; historical failures remain visible. |
| V2 | verified | Observed `fail` check in Validation; historical failures remain visible. |
| V3 | verified | Observed `fail` check in Validation; historical failures remain visible. |
| V4 | verified | Observed `pass` check in Validation; historical failures remain visible. |
| V5 | verified | Observed `pass` check in Validation; historical failures remain visible. |
| V6 | verified | Observed `pass` check in Validation; historical failures remain visible. |
| V7 | verified | Observed `pass` check in Validation; historical failures remain visible. |
| V8 | verified | Observed `partial` check in Validation; historical failures remain visible. |

E1 is the pre-edit architecture snapshot; the current file also contains the implementation link.

## Git Custody

Branch: `main`. Baseline HEAD: `db67eda8f75cb8ac62288b67c470e8e153db8af6`. Final HEAD: `db67eda8f75cb8ac62288b67c470e8e153db8af6`.
History relation: `same`. Commits since baseline: None. No commit or push was requested or performed.
Record path: `progress/2026-09-25-local-agent-pipeline.md`.

Scoped diff: `files=1; insertions=6; deletions=1; binary_files=0; untracked_files=6`.

The scoped-diff insertion/deletion counts cover tracked files; the six new implementation files are separately counted as untracked.

Task paths:

- `agents/local_ollama.py`
- `agents/local_pipeline.py`
- `agents/prompts.py`
- `docs/06-agent-architecture.md`
- `docs/18-local-agent-pipeline.md`
- `scripts/run_local_agent.py`
- `tests/test_local_agent_pipeline.py`

Task-owned changed paths:

- `agents/local_ollama.py`
- `agents/local_pipeline.py`
- `agents/prompts.py`
- `docs/06-agent-architecture.md`
- `docs/18-local-agent-pipeline.md`
- `progress/2026-09-25-local-agent-pipeline.md`
- `scripts/run_local_agent.py`
- `tests/test_local_agent_pipeline.py`

Pre-existing and outside-scope changed paths (same set, preserved):

- `README`
- `docs/17-vue-frontend-design-ppt-outline.md`
- `docs/README.md`
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

Pre-existing overlap: None. Ownership caveat: ignored runtime artifacts under outputs/local_agent are local evidence, not committed deliverables. The source database is read only; a regression test checks identical before/after bytes.

## Evidence Boundary

This establishes implementation behavior and one fresh local execution, not model quality, calibrated price prediction, or scientific validation. V4 preserved 33 feature fields and all six completed stages; L1/L2 succeeded on their first requests and the reporter succeeded after one corrective retry. Native Ollama syntax and graph routing were checked against E6/E7.

RuleEngine does not consume the VLM feature vector; that vector feeds artifacts and report interpretation. Five-dimension scores and a recommended CNY range stay null. The LLM report is a draft: structural checks and limited terminology exclusions do not establish the factual correctness of every sentence. Earlier generated drafts contain overstatements and are retained only as diagnostic artifacts. A human should compare narrative claims against state.json before business use. The qwen2.5vl image failure was avoided by using the installed Qwen 3.5 model; that older model was not repaired.

## Next Steps

No remaining implementation work in this scope. Review generated narrative against its local evidence before operational use; a learned premium predictor and validated pricing data remain separate future work.
