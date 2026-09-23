# Reporter GRPO paired evaluation

- Record format: `3`
- Record ID: `RCP-20260918T153034Z-2fc0f88f`
- Mode: `coding-progress`
- Task type: `evaluation`
- Task slug: `reporter-grpo-paired-evaluation`
- Implementation class: `fresh-implementation`
- Date: `2026-09-18`
- Project: /home/mzhyui/git/emogame
- Priority: `unspecified`
- Owner: Unassigned
- Components: None
- Labels: None
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-18T15:30:34Z`
- Started at: `2026-09-18T15:30:24Z`
- Updated at: `2026-09-19T01:49:19Z`
- Completed at: `2026-09-19T01:49:19Z`
- Due date: Not applicable
- Evidence state: `mixed`
- Validation state: `pass`

## Outcome

The confirmed CUDA evaluation completed for all 960 paired rows with zero
evaluator errors. GRPO beats the SFT initializer on the mechanical verifier:
mean normalized score changes from `-0.9209` to `-0.7093`, a candidate-minus-
baseline delta of `+0.2116` (hero-clustered 95% bootstrap interval
`[+0.1889, +0.2356]`). This is an in-sample diagnostic only: the same 960
rows were used during GRPO training, so it is not evidence of held-out
generalization or production report quality. [E6, E10, E11]

The trained checkpoint can produce Chinese, section-oriented draft text and
has a measurable tendency to include required headings, subject identifiers,
numbers, and business anchors. It remains unsafe as an analyst or production
reporter: both models hit the 1,024-token generation cap on about 97% of rows,
decoded responses were nonempty on only `301/960` rows for each model, and the
required evidence caveat was present on `0/154` eligible rows for both models.
The mechanical verifier does not assess semantic correctness or factuality.
[E10, E11]

## Task and Scope

This evaluation compares `minimind/out/full_sft_768.pth` (SFT baseline) with
`minimind/out/reporter_grpo_20260917T174040Z_768.pth` (GRPO candidate) under the
same 960 records from `data/reporter_grpo.jsonl`. Prompts reconstruct the exact
reporter chat schema; decoding is deterministic greedy generation with a
768-token input limit, 1,024 maximum new tokens, and batch size 4. The scope is
read-only inference and paired analysis. It excludes retraining, dashboard
integration, deployment, a human semantic review, and a fresh sealed cohort.
[E1, E2, E3, E6-E8]

## Lifecycle

- Current blocker: None

| ID | At | Action | From | To | Actor | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | 2026-09-18T15:30:34Z | created | none | in-progress | record-tool | record created |
| L2 | 2026-09-19T01:49:19Z | transition | in-progress | done | codex | Full paired evaluation and hero-clustered analysis completed; result is mechanically verified but scientifically limited to an in-sample diagnostic. |

Local relationships: None.

## Implementation

### Plan and Starting Status

The confirmed plan was to run a deterministic paired evaluator over the
existing SFT and GRPO checkpoints, retain every row identity, apply the
existing `verify_report` checks, and then quantify paired deltas with
hero-clustered bootstrap intervals. The starting status was an implemented
evaluator with smoke tests passed and a full external CUDA run still in
progress. [E1-E8]

### Core Functions and Result

- `scripts/evaluate_reporter_grpo.py` loads both checkpoints, reconstructs
  prompts, generates greedily, records per-row verifier components and lengths,
  and writes `summary.json`.
- `scripts/analyze_reporter_grpo_eval.py` checks pair identity, derives cap and
  nonempty indicators, and runs 10,000 paired percentile bootstrap replicates
  by resampling the 133 hero clusters with replacement while preserving all
  skins within a sampled hero.
- Outputs are retained at
  `minimind/out/reporter_grpo_eval_20260918T154225Z/`:
  `sft.jsonl`, `grpo.jsonl`, `summary.json`, and `paired_analysis.json`. [E9-E11]

## Validation

### Test Result

Aggregate validation is `pass`: V1-V4 and V6-V9 passed; V5 was the earlier
launch-time check recorded as `not-run` because terminal completion had not yet
been observed at the initial record capture. The later V6 check is the decisive
terminal-completion evidence.

### V1 - pass

```text
.venv/bin/python -m py_compile scripts/evaluate_reporter_grpo.py
```

Evaluator script compiles successfully.

### V2 - pass

```text
.venv/bin/python -m unittest tests.test_reporter_grpo.PromptBudgetTests -v
```

5/5 prompt-budget and launcher regression tests pass.

### V3 - pass

```text
.venv/bin/python scripts/evaluate_reporter_grpo.py --device cpu --limit 2 --batch-size 2 --max-new-tokens 8 --output-dir /tmp/emogame-grpo-eval-smoke3
```

Two-row paired SFT/GRPO smoke completes with zero errors and aligned indices.

### V4 - pass

```text
.venv/bin/python -c 'import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))' (host execution)
```

Project environment sees one NVIDIA GeForce RTX 3080 CUDA device.

### V5 - not-run

```text
screen -dmS reporter_grpo_eval_20260918 ... evaluate_reporter_grpo.py --device cuda --batch-size 4 --max-new-tokens 1024
```

The full evaluation had been launched, but terminal completion was not yet
observed when this check was first recorded. It is superseded as a completion
claim by V6; it is retained as historical launch evidence.

### V6 - pass

```text
screen reporter_grpo_eval_20260918 full CUDA evaluation
```

960/960 SFT and 960/960 GRPO rows completed with zero evaluator errors;
`summary.json` was written and pair indices aligned.

### V7 - pass

```text
.venv/bin/python -m py_compile scripts/analyze_reporter_grpo_eval.py
```

Clustered-analysis script compiles successfully.

### V8 - pass

```text
.venv/bin/python scripts/analyze_reporter_grpo_eval.py minimind/out/reporter_grpo_eval_20260918T154225Z --bootstrap-samples 10000 --seed 42
```

10,000 paired hero-cluster bootstrap replicates completed over 133 heroes; 95%
intervals were written with aligned 960-row pairs.

### V9 - pass

```text
python3 -c 'inspect summary.json and paired_analysis.json'
```

Diagnostic metrics and limits were inspected: GRPO verifier-normalized delta
`+0.2116`, cap-hit rates about `0.97`, and evidence-caveat recall `0/154` for
both models.

### Validation evidence classification

The observed checks V1, V2, V3, V4, V5, V6, V7, V8, and V9 are verified
records of the commands and outcomes above. V5 is verified as a historical
launch observation; its result remains `not-run` for terminal completion.

Recorded check summaries (verbatim):

- V5: Full 960-row paired evaluation launched; terminal completion not yet observed at record capture.
- V6: 960/960 SFT and 960/960 GRPO rows completed with zero evaluator errors; summary.json written and pair indices aligned.
- V8: 10,000 paired hero-cluster bootstrap replicates completed over 133 heroes; 95% intervals written with aligned 960-row pairs.
- V9: Diagnostic metrics and limits inspected: GRPO verifier-normalized delta +0.2116, cap-hit rates about 0.97, evidence-caveat recall 0/154 for both models.

### Paired results

All deltas are GRPO minus SFT. The intervals are 95% percentile intervals from
the hero-clustered paired bootstrap; the estimand is row-weighted.

| Metric | SFT | GRPO | Delta | 95% interval |
| --- | ---: | ---: | ---: | ---: |
| Exact section coverage | 0.0085 | 0.1477 | +0.1392 | [+0.1202, +0.1591] |
| Heading progress | 0.0128 | 0.1498 | +0.1370 | [+0.1177, +0.1572] |
| Subject grounding | 0.1583 | 0.2316 | +0.0733 | [+0.0593, +0.0876] |
| Numeric coverage | 0.0052 | 0.1063 | +0.1010 | [+0.0856, +0.1172] |
| Business-anchor coverage | 0.0049 | 0.0481 | +0.0432 | [+0.0332, +0.0539] |
| Task verifier total | 0.1880 | 0.6912 | +0.5032 | [+0.4491, +0.5604] |
| Verifier normalized | -0.9209 | -0.7093 | +0.2116 | [+0.1889, +0.2356] |
| Mean response tokens | 1013.47 | 1014.58 | +1.11 | [-5.30, +7.53] |
| Generation cap hit | 0.9698 | 0.9667 | -0.0031 | [-0.0216, +0.0150] |
| Nonempty decoded response | 0.3010 | 0.3010 | 0.0000 | [0.0000, 0.0000] |
| Evidence-caveat recall (154 eligible) | 0.0000 | 0.0000 | 0.0000 | [0.0000, 0.0000] |

## Evidence Ledger

| ID | Class | Kind | Locator | SHA-256 | Supported conclusion |
| --- | --- | --- | --- | --- | --- |
| E1 | user-stated | user | Current user request: run the confirmed read-only paired reporter evaluation | N/A | Authorizes this evaluation. |
| E2 | verified | repository | `scripts/evaluate_reporter_grpo.py` | `4bf4045bc993fa1108681c448106616968b6500392ed92972f236f3b32409924` | Defines deterministic prompt reconstruction, decoding, per-row metrics, and summary output. |
| E3 | verified | repository | `scripts/train_reporter_grpo.py; existing verifier and prompt contract` | `7a6dacf3dd62bfa74c8798217abbcb3de103debfbca689f84cd579c061f79a31` | Defines the reporter prompt and mechanical verifier. |
| E4 | verified | repository | `minimind/trainer/train_grpo.py; trainer defaults and checkpoint model shape` | `5023557dc0d7f0d49a5917dfadae22568da21d189a517689f8ddc783b9da7f0f` | Binds the MiniMind checkpoint shape used by inference. |
| E5 | verified | repository | `tests/test_reporter_grpo.py; prompt budget and launcher regression tests` | `36515eff18d59c9617c5467a80c853c8e04fb92451a654ed66fe635a898c4fed` | Binds the smoke-test contract. |
| E6 | verified | repository | `data/reporter_grpo.jsonl; evaluation input` | `9dcb50666c99fdb8b7e35158d03c659f320464bd6d3ccf8104a5d14cf8f832e8` | Binds the 960 input records; these rows were also used in training. |
| E7 | verified | artifact | `minimind/out/full_sft_768.pth; SFT baseline` | `5b8c49f6c9d965092e651cafeeaeb8705558632b3fd4ac8ab319cbc5a3cbc4a0` | Binds the evaluated SFT weights. |
| E8 | verified | artifact | `minimind/out/reporter_grpo_20260917T174040Z_768.pth; GRPO candidate` | `f7124ee4f2f70ebf0705e6aa8efeefb6f252c6376990b89e5cdcd8f9e30e9256` | Binds the evaluated GRPO weights. |
| E9 | verified | repository | `scripts/analyze_reporter_grpo_eval.py` | `22a821baf10638cb631dccda1941ed28c3dc9521fc9d75e724fb980e024052a6` | Defines the paired hero-cluster bootstrap analysis. |
| E10 | verified | artifact | `minimind/out/reporter_grpo_eval_20260918T154225Z/summary.json` | `f0542d9b2c2a52230d3480a0053da7ca3c4d8d5c1edb1c52bf96cb96cd8adfc6` | Binds full-run row counts, errors, means, cap rates, and raw paired deltas. |
| E11 | verified | artifact | `minimind/out/reporter_grpo_eval_20260918T154225Z/paired_analysis.json` | `72985efc57a31da175f1fbe5d5ff24f8824e747aba4350e74134e4c0c1dca33d` | Binds 133-hero bootstrap intervals, eligible counts, and paired outcomes. |
| V1 | verified | check | `.venv/bin/python -m py_compile scripts/evaluate_reporter_grpo.py` | N/A | Evaluator script compiles successfully. |
| V2 | verified | check | `.venv/bin/python -m unittest tests.test_reporter_grpo.PromptBudgetTests -v` | N/A | 5/5 prompt-budget and launcher regression tests pass. |
| V3 | verified | check | `.venv/bin/python scripts/evaluate_reporter_grpo.py --device cpu --limit 2 --batch-size 2 --max-new-tokens 8 --output-dir /tmp/emogame-grpo-eval-smoke3` | N/A | Two-row paired smoke completes with zero errors and aligned indices. |
| V4 | verified | check | `.venv/bin/python -c 'import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))' (host execution)` | N/A | Project environment sees one NVIDIA GeForce RTX 3080 CUDA device. |
| V5 | verified | check | `screen -dmS reporter_grpo_eval_20260918 ... evaluate_reporter_grpo.py --device cuda --batch-size 4 --max-new-tokens 1024` | N/A | Full evaluation launch was observed; terminal completion was not yet observed at initial capture. |
| V6 | verified | check | `screen reporter_grpo_eval_20260918 full CUDA evaluation` | `f0542d9b2c2a52230d3480a0053da7ca3c4d8d5c1edb1c52bf96cb96cd8adfc6` | 960/960 SFT and 960/960 GRPO rows completed with zero evaluator errors; summary written and pairs aligned. |
| V7 | verified | check | `.venv/bin/python -m py_compile scripts/analyze_reporter_grpo_eval.py` | N/A | Clustered-analysis script compiles successfully. |
| V8 | verified | check | `.venv/bin/python scripts/analyze_reporter_grpo_eval.py minimind/out/reporter_grpo_eval_20260918T154225Z --bootstrap-samples 10000 --seed 42` | `72985efc57a31da175f1fbe5d5ff24f8824e747aba4350e74134e4c0c1dca33d` | 10,000 paired hero-cluster bootstrap replicates completed over 133 heroes. |
| V9 | verified | check | `python3 -c 'inspect summary.json and paired_analysis.json'` | N/A | GRPO verifier-normalized delta +0.2116; cap-hit rates about 0.97; caveat recall 0/154 for both. |

## Git Custody

- Baseline HEAD: `f7c12beff465cc858b3598134817878e348edb3a` on `main`.
- Final branch: `main`; final HEAD: `f7c12beff465cc858b3598134817878e348edb3a`; history relation: `same`.
- No commit or push was requested or performed.
- Task-owned changed paths: `scripts/evaluate_reporter_grpo.py` and
  `scripts/analyze_reporter_grpo_eval.py`, and
  `progress/2026-09-18-reporter-grpo-paired-evaluation.md`.
- Task paths: `scripts/evaluate_reporter_grpo.py`,
  `scripts/analyze_reporter_grpo_eval.py`.
- Git record path: `progress/2026-09-18-reporter-grpo-paired-evaluation.md`.
- The worktree had unrelated pre-existing changes, including the nested
  `minimind` checkout, data/model assets, training scripts, tests, and earlier
  progress notes. They were preserved.
- Pre-existing paths preserved: `data/reporter_grpo.jsonl`,
  `data/reporter_states.jsonl`, `docs/06-agent-architecture.md`,
  `internlm2-1_8b-reward/.gitattributes`,
  `internlm2-1_8b-reward/README.md`, `internlm2-1_8b-reward/config.json`,
  `internlm2-1_8b-reward/configuration_internlm2.py`,
  `internlm2-1_8b-reward/model.safetensors.index.json`,
  `internlm2-1_8b-reward/modeling_internlm2.py`,
  `internlm2-1_8b-reward/reward_bench_results/eval-set/internlm2-1_8b-reward.json`,
  `internlm2-1_8b-reward/reward_bench_results/pref-sets/internlm2-1_8b-reward.json`,
  `internlm2-1_8b-reward/special_tokens_map.json`,
  `internlm2-1_8b-reward/tokenization_internlm2.py`,
  `internlm2-1_8b-reward/tokenization_internlm2_fast.py`,
  `internlm2-1_8b-reward/tokenizer.model`,
  `internlm2-1_8b-reward/tokenizer_config.json`,
  `progress/2026-09-18-cloud-skin-catalog-maintenance-workflow.md`,
  `progress/2026-09-18-reporter-grpo-training-result-and-capabilities.md`,
  `scripts/prepare_reporter_states.py`.
- Outside-scope changed paths are the same preserved list above plus
  `minimind`; no pre-existing overlap was detected.
- Final scoped diff token: `files=0; insertions=0; deletions=0; binary_files=0; untracked_files=2`.
  The two task-owned scripts remain untracked and are not committed by this
  record.

## Evidence Boundary

The evaluation proves successful deterministic inference over 960 paired rows,
mechanical verifier movement, output-length behavior, and the stated clustered
intervals. It does not prove semantic report quality, factuality, calibrated
recommendations, evidence-aware behavior, held-out generalization, or
production readiness. Because every row was in the GRPO training input, all
quality comparisons must be labeled `in_sample_diagnostic_only`. The verifier
checks strings and coverage; it is not a human or semantic judge.

## Next Steps

1. Build a fresh sealed, hero-disjoint cohort not used in training and rerun the
   exact paired evaluator.
2. Fix or investigate the 1,024-token cap and the 69.9% empty-decoded response
   rate before any product integration.
3. Add semantic/human report-quality checks and explicit evidence-caveat tests;
   do not infer factuality from the mechanical verifier.
4. Keep the checkpoint offline and externally validated until those gates pass.
