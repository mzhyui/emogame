# Reporter GRPO training result and capability boundary

- Record format: `3`
- Record ID: `RCP-20260918T145413Z-b833938d`
- Mode: `session-documentation`
- Task type: `research`
- Task slug: `reporter-grpo-training-result-and-capabilities`
- Date: `2026-09-18`
- Project: /home/mzhyui/git/emogame
- Priority: `unspecified`
- Owner: Unassigned
- Components: None
- Labels: None
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-18T14:54:13Z`
- Started at: `2026-09-18T14:54:13Z`
- Updated at: `2026-09-18T14:55:14Z`
- Completed at: `2026-09-18T14:55:14Z`
- Due date: Not applicable
- Evidence state: `mixed`
- Validation state: `not-applicable`

## Outcome

The latest reporter GRPO run is mechanically complete and produced retained
policy and resume artifacts. On the training rollouts, the policy learned to
increase mechanical report-structure, identity-grounding, numeric, and business
anchor scores. The result does not establish semantic report quality,
held-out generalization, reliable evidence caveats, or application readiness.
The model should currently be described as a trained reporter-policy artifact,
not as a validated production reporter. [E2, E3, E7-E10]

The strongest demonstrated capability is: given the prepared compact catalog
state, generate a Chinese, sectioned commercialization report that often
contains the required headings and some state-derived identifiers, numbers, and
business anchors. The audit did not observe a successful evidence-caveat
response in any of the 616 generations for which a caveat was required. [E7,
E8]

## Context and Scope

This record preserves the completed run `reporter_grpo_20260917T174040Z`, its
actual trainer settings, its verifier-based optimization behavior, and the
capability claims supported by the retained log and reward audit. The run used
960 prepared catalog states and generated four responses per state. The scope
does not include a new inference benchmark, human quality study, held-out split,
dashboard integration, or model deployment. [E1, E5-E8]

Source E1 is the Current user request on 2026-09-18: record the GRPO training result and explain what the trained model can do. Source E6 is `app.py; inspected for reporter-checkpoint integration`.

The current application entry point was inspected for a direct reference to the
reporter checkpoint; no such integration was found. The checkpoint is therefore
an available model artifact rather than an already-wired dashboard feature. [E6]

## Lifecycle

- Current blocker: None

| ID | At | Action | From | To | Actor | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | 2026-09-18T14:54:13Z | created | none | in-progress | record-tool | record created |
| L2 | 2026-09-18T14:55:14Z | transition | in-progress | done | codex | Recorded the completed GRPO run, its actual settings and optimization path, the verifier-based capability evidence, and the held-out/generalization limitations. |

Local relationships: None.

## Findings and Decisions

### Run completion and retained artifacts

- **Verified:** The log reaches step 960/960 and records 3,840 reward-audit
  rows without a traceback or OOM. The final model weights and resume state are
  retained as separate artifacts. [E7, E9, E10]
- **Verified:** The run used verifier-only reward, zero generic reward weight,
  a 32-group/30% task-signal gate, four generations per prompt, a 1,024-token
  generation limit, batch size 1, no thinking mode, GRPO loss, and checkpoint
  interval 50. [E7]

### Observed training behavior

- **Verified:** Mean normalized verifier reward increased from approximately
  `-0.622` in the first 400 audit rows to `+0.324` in the last 400. In the last
  400 rows, mean exact-section coverage was `0.8275`, grounding was `0.7808`,
  numeric coverage was `0.5075`, and business-anchor coverage was `0.1854`.
  [E8]
- **Verified:** The task-signal gate remained active and reached approximately
  `99.58%` varying groups near the end of training. This proves reward
  variation was available to GRPO; it is not a semantic-quality gate. [E7, E8]
- **Verified:** The final generations frequently reached the 1,024-token limit.
  Length growth is therefore an observed training behavior, not evidence that
  reports are complete or well-written. [E7]
- **Verified:** Evidence-caveat coverage was zero for all 616 eligible audit
  rows, including the late-run window. [E8]

### What the trained model can do

- **Verified capability:** Generate a Chinese report from the prepared
  `conversations` prompt schema, with a learned tendency to emit the six target
  report sections. [E2, E4, E5, E7, E8]
- **Verified capability:** Reproduce or mention some prompt-derived subject
  identifiers, numeric targets, and business anchors often enough to raise the
  corresponding mechanical verifier components on training rollouts. [E2, E8]
- **Inferred capability:** It may be useful as a draft-report generator for
  catalog states with the same compact schema, provided outputs are treated as
  untrusted drafts and checked externally. This inference is bounded by the
  absence of held-out and human evaluation. [E5, E7, E8]
- **Not established:** Reliable semantic reasoning, factuality, price or
  competitor correctness, calibrated recommendations, robust missing-evidence
  disclosure, transfer to unseen heroes/skins, or superiority over the SFT
  initializer. [E2, E7, E8]
- **Not established:** Direct image understanding, web crawling, database
  updates, or dashboard use. Those are outside the trained text-policy
  interface and no application integration was found in the inspected entry
  point. [E6]

### Decision

The artifact is retained as a training-policy checkpoint and diagnostic result.
It may be used for offline draft generation after an explicit inference smoke
test, but it must not be presented as a validated reporter, evidence-aware
analyst, or production dashboard model. [E7-E10]

## Technical Design or Experimental Plan

The policy and a frozen reference model were initialized from the `full_sft`
MiniMind checkpoint. The model has hidden size 768, eight layers, and about
63.9M trainable parameters. CUDA execution used bfloat16 autocast. Prompts were
left-truncated to the trainer's default `max_seq_len=768`; each rollout sampled
four completions at temperature `0.8` with at most 1,024 new tokens. [E3, E7]

For each four-sample group, rewards were centered and scaled as:

```text
advantage_i = (reward_i - group_mean) / (group_std + 1e-4)
```

The GRPO branch used the clipped policy-ratio objective with `epsilon=0.2` and
added the reference-policy token KL penalty with `beta=0.1`. The optimizer was
AdamW at learning rate `3e-7`, gradient clipping `1.0`, and accumulation `1`.
A cosine schedule decayed the learning rate to `3e-8` over the 960 updates. [E3]

The verifier reward assigned weights `2.0` to exact sections, `0.5` to heading
progress, `1.0` to subject grounding, `0.75` to evidence caveats, `0.5` to
numeric coverage, and `0.75` to business anchors. It normalized the attainable
weighted score to `[-1, 1]`. These are mechanical string/coverage checks, not a
semantic judge. [E2]

## Evidence Ledger

| ID | Class | Kind | Locator | SHA-256 | Supported conclusion |
| --- | --- | --- | --- | --- | --- |
| E1 | user-stated | user | Current user request: record the GRPO result and explain model capability | N/A | Authorizes this durable result and capability record. |
| E2 | verified | repository | `scripts/train_reporter_grpo.py` | `7a6dacf3dd62bfa74c8798217abbcb3de103debfbca689f84cd579c061f79a31` | Defines the prepared prompt, verifier reward, reward weights, and launcher behavior. |
| E3 | verified | repository | `minimind/trainer/train_grpo.py` | `5023557dc0d7f0d49a5917dfadae22568da21d189a517689f8ddc783b9da7f0f` | Defines model initialization, defaults, rollout, GRPO objective, optimizer, scheduler, and checkpointing. |
| E4 | verified | repository | `minimind/dataset/lm_dataset.py` | `e6dd56b5745015ddc1579932c292ec7a5e1cc33796a5eadf4911065d92f1c6b8` | Defines `RLAIFDataset` chat-template handling and assistant-turn removal. |
| E5 | verified | repository | `data/reporter_grpo.jsonl` | `9dcb50666c99fdb8b7e35158d03c659f320464bd6d3ccf8104a5d14cf8f832e8` | Binds the prepared reporter prompt dataset used by the run. |
| E6 | verified | repository | `app.py` inspected for reporter-checkpoint integration | `f3482b38403b5bbb67d2175d5274106766d9348329f95459c8582aea188bf007` | Supports the bounded statement that the inspected entry point does not directly wire this checkpoint. |
| E7 | verified | artifact | `minimind/out/grpo_rerun_gen1024.log` | `7e820bbe6021863cd8f0eaa1518573c11d562fe64de63bf26b6ec1ffc9feb608` | Binds run settings, 960/960 completion, per-step metrics, and 3,840 audit-row completion. |
| E8 | verified | artifact | `minimind/out/grpo_reward_audit_20260917T174040Z.jsonl` | `078f0ab6850af0957d70fe1fd447dd83e6eca3fd5e4cdd62c38d5e1a9e17eb23` | Binds per-generation verifier components, reward movement, and zero caveat successes among eligible rows. |
| E9 | verified | artifact | `minimind/out/reporter_grpo_20260917T174040Z_768.pth` | `f7124ee4f2f70ebf0705e6aa8efeefb6f252c6376990b89e5cdcd8f9e30e9256` | Binds the final policy-weight artifact. |
| E10 | verified | artifact | `minimind/out/reporter_grpo_20260917T174040Z_768_resume.pth` | `268a4babc23642fed67dbfb54621f1572d964d1ca6c4fd22307be8c41841a4fe` | Binds the optimizer/scheduler resume artifact. |

## Evidence Boundary

The log and audit establish an executed training run and in-training mechanical
reward behavior. The repository sources establish the prompt contract, reward
definition, optimization path, and default truncation behavior. The evidence
does not include a held-out hero split, fixed-prompt checkpoint comparison,
human review, semantic evaluator, production inference smoke test, or dashboard
integration. The reported capability is therefore conditional on the prepared
prompt schema and should be treated as draft generation rather than validated
analysis. [E2-E10]

## Next Steps

1. Add a real inference entry point that loads the retained checkpoint and run a
   small deterministic smoke test on representative prompts.
2. Create a hero-disjoint held-out evaluation and compare the SFT initializer,
   intermediate checkpoints, and final checkpoint using both the verifier and
   human/semantic quality criteria.
3. Address the 768-token prompt truncation risk before another run; choose the
   context limit from measured tokenized prompts rather than the current default.
4. Add an explicit evidence-caveat metric and do not integrate the model into
   the dashboard until caveat behavior and held-out report quality are verified.
