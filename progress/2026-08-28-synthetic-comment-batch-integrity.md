# Synthetic comment batch integrity repair

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `function-fix`
- Date: `2026-08-28`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Fixed the synthetic-comment generator so that every invocation receives a
unique traceable batch ID and batch metadata plus comments are persisted in one
transaction. The generator reports its actual inserted count. With explicit
user authorization, the inconsistent 100-row synthetic batch was removed and
a clean 50-row replacement was generated as
`synth-wc-20260827-20260828-191356-3d336842`.

## Task and Scope

The request was to fix and rerun synthetic comment generation after the
observed batch `synth-wc-20260827-20260828` held 100 distinct synthetic rows
while its metadata claimed 50 (E2). The verified pre-existing contract is one
batch record with count-provenance for its generated comment rows (E1). The
defect combined a date-and-seed-only batch ID with metadata replacement and
append-only comment inserts.

Scope: batch identity, transactional persistence, count accuracy, regression
coverage, the explicitly approved removal of that exact corrupted synthetic
batch, and one clean local rerun. The premium pilot, real comments, and
revenue/media evidence were not changed.

## Interface and Behavior Changes

Generated IDs now use `synth-wc-<seed>-<timestamp>-<nonce>`. New
`SyntheticWeiboRepository.save_generation` rejects reuse of an existing batch
ID rather than replacing its metadata, writes the metadata and comments in one
transaction, and records the actual inserted count in both `row_count` and
`cache_misses`.

## Implementation

### Preserved Contract

Synthetic comments remain isolated in `synthetic_weibo_comments`; existing
`save_batch` and `save_comments` retain their compatibility behavior for
callers and tests. No synthetic rows are promoted to `weibo_comments` or the
premium pilot.

### Corrective Change

| Path | Symbols | Result |
| --- | --- | --- |
| `data/weibo_comment_synthesizer.py` | `make_synth_batch_id`, `save_generation`, `generate` | Creates unique invocation IDs, atomically saves a new batch, rejects collisions, and reports the stored count. |
| `tests/test_weibo_comment_synthesizer.py` | batch-integrity cases | Verifies unique IDs, duplicate-ID rejection, and persisted row-count accuracy. |
| `data/weibo_comments/weibo.sqlite3` | synthetic batch records | Removed exactly the corrupted batch metadata and its 100 synthetic rows, then generated the clean 50-row replacement. |

## Validation

### Test Result

All recorded checks passed. The local synthesis run created development-only
synthetic text; it does not validate real community sentiment or the premium
pilot.

### V1 - pass

```text
python3 -m py_compile data/weibo_comment_synthesizer.py scripts/synthesize_weibo_comments.py tests/test_weibo_comment_synthesizer.py
```

Generator, CLI, and regression test modules compiled.

### V2 - pass

```text
.venv/bin/python -m pytest -q tests/test_weibo_comment_synthesizer.py
```

7 synthesizer tests passed, including batch-ID uniqueness and atomic
persisted-count coverage.

### V3 - pass

```text
.venv/bin/python scripts/synthesize_weibo_comments.py --per-skin 50 --seed 20260827 --model qwen2.5vl:3b --report --json
```

One eligible skin generated a clean UUID-suffixed batch with 50 accepted rows
and zero rejections in 25.09 seconds.

### V4 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

211 root tests passed; existing Streamlit and dependency deprecation warnings
were emitted.

## Evidence Ledger

| ID | Class | Locator | Supported conclusion |
| --- | --- | --- | --- |
| E1 | verified | `data/weibo_comment_synthesizer.py`, SHA-256 `df33add3e80dca63afdb239d0829a2b529ada68dab240cbd1db177eeee3b1b3d` | The former write path used a date-and-seed batch ID, metadata replacement, and separate comment persistence. |
| E2 | verified | `data/weibo_comments/weibo.sqlite3 batch synth-wc-20260827-20260828: metadata row_count=50 and stored synthetic rows=100` | The observed batch had inconsistent metadata and physical rows. |
| E3 | verified | `tests/test_weibo_comment_synthesizer.py`, SHA-256 `9a32b5037211037886c5f93d5fbf0c7de2f72a8b45e0fd263f1d9013cadc48ce` | The regression tests cover collision and count behavior. |
| V1 | verified | compile command above | Generator, CLI, and test modules compiled. |
| V2 | verified | focused pytest command above | 7 synthesizer tests passed. |
| V3 | verified | local generation command above | The clean 50-row synthetic batch completed. |
| V4 | verified | root unittest command above | 211 root tests passed. |

## Git Custody

Branch: `main`. Baseline HEAD and final HEAD are both
`5a2e826267b8ba8def1f0cadf6d258d4453a2818`. History relation: `same`.
There are no commits since baseline. The scoped-diff token is
`files=2; insertions=135; deletions=11; binary_files=0; untracked_files=0`.

Explicit task paths and task-owned changed paths are
`data/weibo_comment_synthesizer.py` and
`tests/test_weibo_comment_synthesizer.py`. The record path is
`progress/2026-08-28-synthetic-comment-batch-integrity.md`.

The repository was dirty before this work. Pre-existing paths were
`.dsh-tools/app_viewer_template.html`, `.dsh-tools/db_dashboard_template.html`,
`.dsh-tools/make_app_viewer.py`, `.dsh-tools/make_db_dashboard.py`,
`.dsh-tools/make_skins_dashboard.py`, `.dsh-tools/skins_dashboard_template.html`,
`.dsh-tools/test_viewer_logic.js`, `.gitignore`, `.gitmodules`, `.npmrc`,
`agent-lightning`, `all.log.2026-07-16`, `docs/10-agent-lightning-local.md`,
`docs/11-agentic-rl-method-survey-slides.md`,
`docs/11-agentic-rl-method-survey.md`,
`docs/11-agentic-rl-method-survey.pptx`,
`docs/12-perceived-premium-pilot.md`, `docs/README.md`,
`docs/premium-pilot-media-mapping.example.json`,
`docs/premium-pilot-reviewer-template.csv`, `models/premium_pilot.py`,
`progress/2026-08-11-emotional-value-framework-one-month-report.md`,
`progress/2026-08-11-emotional-value-framework-one-month-report.pdf`,
`progress/2026-08-27-perceived-premium-pilot.md`,
`progress/2026-08-27-vlm-output-hardening.md`,
`scripts/run_premium_pilot.py`, `tests/test_premium_pilot.py`,
`tests/test_vlm_output_validation.py`, `vlm/cache.py`, `vlm/l1_classifier.py`,
`vlm/l2_analyzer.py`, `vlm/output_validation.py`, `vlm/pipeline.py`, and
`vlm/schemas.py`.

There are no pre-existing overlaps. Outside-scope changed paths are
`.dsh-tools/app_viewer_template.html`, `.dsh-tools/db_dashboard_template.html`,
`.dsh-tools/make_app_viewer.py`, `.dsh-tools/make_db_dashboard.py`,
`.dsh-tools/make_skins_dashboard.py`, `.dsh-tools/skins_dashboard_template.html`,
`.dsh-tools/test_viewer_logic.js`, `.gitignore`, `.gitmodules`, `.npmrc`, `agent-lightning`,
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
`progress/2026-08-27-vlm-output-hardening.md`,
`scripts/run_premium_pilot.py`, `tests/test_premium_pilot.py`,
`tests/test_vlm_output_validation.py`, `vlm/cache.py`, `vlm/l1_classifier.py`,
`vlm/l2_analyzer.py`, `vlm/output_validation.py`, `vlm/pipeline.py`, and
`vlm/schemas.py`. No commit was created.

## Evidence Boundary

This establishes isolated synthetic-batch integrity and the stated local
generation result. It does not establish authenticity, sentiment validity, or
eligibility of synthetic text as premium-pilot media evidence.

## Next Steps

None. The generated rows remain usable only for development, simulation, and
test purposes.
