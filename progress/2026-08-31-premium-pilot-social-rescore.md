# Premium-pilot target-aware social re-score

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-08-31`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

The frozen 50-skin premium cohort was re-scored from the new target-aware real
Weibo comment artifact. All 50 rows now have a numeric social score, the new run
contains reconstructable feature traces, and a refreshed radar report exposes
the social axis. The run used the deterministic rule scorer without remote LLM
enrichment. Forty-six rows now have complete visual, official-context, and
social evidence; four remain partial only because their official-context score
is unavailable. [E2, E5, E6, V3, V4]

The input comments remain an automatically collected public-data sample rather
than a manually adjudicated media map. One target, `孙膑-小动物乐团`, uses the
recorded hero-level fallback after its exact-skin post returned no comments;
that linkage remains visible in `media_labels.jsonl` and should be interpreted
more cautiously than the 49 exact-skin targets. [E2, V3]

## Task and Scope

The plan source was: "Current conversation: use the newly collected Weibo
comments to re-score the social-media score for the frozen radar cohort." The
target was `data/premium_pilot/runs/20260828-seed42-partial-v3`. [E1]

### Constraints and decisions

- Reuse the frozen manifest and VLM outputs rather than rerun image models.
  [E3, E4]
- Use only real target-aware comments from the bounded crawl; synthetic media
  remains forbidden. [E2]
- Preserve per-target comment subsets when multiple skins share one Weibo MID,
  because post-level SQLite unioning would mix distinct associations.
- Enforce at most 25 comments per target and accept only `official` or `general`
  source types.
- Keep revenue held out from score construction and do not retune the frozen
  visual/context/social weights.
- Write a new run, `20260831-seed42-social-v1`, without replacing the partial-v3
  artifacts. [E5, E6]

The confirmed plan source was the current user request and the existing frozen
pilot artifacts. Before this delivery, the runner accepted a manually reviewed
media map backed by the shared Weibo SQLite store; it did not accept the
target-aware radar-crawl JSON contract directly. [E1, E2, E3, E4]

## Interface and Behavior Changes

- `scripts/run_premium_pilot.py` now accepts mutually exclusive
  `--media-comments-json` and `--media-map` inputs.
- Target-aware input validation rejects synthetic markers, malformed post
  references, source-type mismatches, empty or duplicate comments, negative
  likes, and target batches above 25 comments.
- Scoring receives only comment text and like count. User identifiers and raw
  crawler metadata remain in the source artifact.
- Shared MIDs retain distinct per-target comment subsets and provenance fields
  `collection_match_scope`, `collection_source_type`, and
  `comment_association=target_specific`.
- Run metadata records the target-aware input SHA-256 and media input mode.
- Feature-trace reconstruction now sums full-precision contributions before
  final rounding; serialized contribution fields remain rounded for display.

## Implementation

### Plan and Starting Status

The plan was to adapt the new crawl artifact into the existing canonical
community rule scorer, keep the cohort and VLM evidence frozen, regenerate the
score/trace artifacts, and refresh the radar. The direct JSON input path was a
new capability, so this record is classified as a fresh implementation. [E1,
E2, E3, E4]

### Core Functions and Result

| Path | Core symbols | Result and role |
| --- | --- | --- |
| `scripts/run_premium_pilot.py` | `CollectedMediaPost`, `load_collected_media`, `label_collected_media` | Loads, validates, sanitizes, labels, and records target-specific real comments without post-level cross-target unioning. |
| `scripts/run_premium_pilot.py` | `parse_args`, `_async_main_unlocked`, `build_run_metadata` | Adds the mutually exclusive CLI mode, selects the target-aware scoring path, and hashes its input provenance. |
| `models/premium_pilot.py` | `build_feature_trace` | Fixes full-precision reconstruction at half-cent rounding boundaries exposed by complete three-modal scores. |
| `tests/test_premium_pilot.py` | target-aware media and trace regression tests | Covers shared-MID isolation, the 25-comment cap, association provenance, and the half-cent reconstruction case. |
| `docs/12-perceived-premium-pilot.md` | evidence contract and runbook | Documents the new input mode, privacy boundary, validation gates, and reproducible re-score command. |

The completed run contains 50 social scores on the 0--100 source scale, ranging
from 30.0 to 89.4 with mean 64.95 and median 68.4. The 0--10 radar axis uses
`media_score / 10`. The old visual and official-context component values were
not changed; the overall perceived-premium score was recomputed because the
new social modality participates in the frozen fusion. [E5, V3, V4]

## Validation

### Test Result

Completed. The focused suite passed 22 tests, the successful 50-skin scorer run
and radar generation completed, the full repository-root suite passed 237
tests, and `git diff --check` passed. The first scorer run intentionally remains
recorded as a failed check because it exposed the trace-rounding defect that was
then fixed and regression-tested. [V1, V2, V3, V4, V5, V6]

### V1 - pass

```text
.venv/bin/python -m unittest tests.test_premium_pilot
```

22 premium-pilot tests passed, including target-aware input isolation,
25-comment cap, provenance, and half-cent trace reconstruction.

### V2 - fail

```text
.venv/bin/python scripts/run_premium_pilot.py --manifest data/premium_pilot/runs/20260828-seed42-partial-v3/manifest.json --vlm-results data/premium_pilot/runs/20260828-seed42-partial-v3/vlm_outputs.jsonl --media-comments-json data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json --force-media --output-dir data/premium_pilot/runs/20260831-seed42-social-v1
```

First execution stopped at detail-0007-56304 because rounded trace
contributions reconstructed 71.67 while the full-precision score was 71.68.

### V3 - pass

```text
.venv/bin/python scripts/run_premium_pilot.py --manifest data/premium_pilot/runs/20260828-seed42-partial-v3/manifest.json --vlm-results data/premium_pilot/runs/20260828-seed42-partial-v3/vlm_outputs.jsonl --media-comments-json data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json --force-media --output-dir data/premium_pilot/runs/20260831-seed42-social-v1
```

50-skin run completed after the trace fix: 46 complete-evidence rows, 4
partial-no-official-context rows, complete feature provenance, and held-out
revenue n=25. The hashed `report.json` artifact is
`f5382ea773653a37bfa6d77da245e9f1a38824a4e4861f818161baab8f0fafb1`.

### V4 - pass

```text
.venv/bin/python scripts/plot_premium_radars.py data/premium_pilot/runs/20260831-seed42-social-v1/scores.jsonl
```

Generated 50 radar charts with media available for all 50 and revenue available
for 25.

### V5 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

237 repository-root tests passed; only expected dependency and bare-Streamlit
warnings were emitted.

### V6 - pass

```text
git diff --check
```

Git diff whitespace validation passed with no output.

## Evidence Ledger

| ID | Class | Locator or check | Supported conclusion |
| --- | --- | --- | --- |
| E1 | user-stated | Current conversation: use the newly collected Weibo comments to re-score the social-media score for the frozen radar cohort | Requested use of the new Weibo comments to re-score the frozen radar cohort. |
| E2 | verified | `data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json`, SHA-256 `541e237306abb68db60d6648b5a1d12131ff1b8c7bea9003def01f4501f0606f` | Bounded target-aware real-comment input and its exact/fallback linkage metadata. |
| E3 | verified | `data/premium_pilot/runs/20260828-seed42-partial-v3/manifest.json`, SHA-256 `0f8f4518015f0e42a8e350130f2a8076141e333f2f57dde6c72020c0d45b16bc` | Frozen 50-skin cohort input. |
| E4 | verified | `data/premium_pilot/runs/20260828-seed42-partial-v3/vlm_outputs.jsonl`, SHA-256 `a6c0211be7d813edeab0ff15fa6bb347222322b69b0970580f4679755d05f172` | Reused precomputed VLM input. |
| E5 | verified | `data/premium_pilot/runs/20260831-seed42-social-v1/scores.jsonl`, SHA-256 `6e2831946a97f4e8b8c525a0e9a6558456fe4d6c8ed0833935096225b074d8d0` | Completed social and perceived-premium score rows. |
| E6 | verified | `data/premium_pilot/runs/20260831-seed42-social-v1/radar_plots.html`, SHA-256 `1ef04ece28769d90fef1939b2659b4931d0dd3193dd584e239260280c4704373` | Refreshed 50-card radar artifact. |
| V1 | verified | Focused premium-pilot tests | New input and trace regression behavior passes. |
| V2 | verified | First scorer execution | Observed the pre-fix reconstruction failure. |
| V3 | verified | Successful scorer execution and hashed `report.json` | Completed run counts, provenance gate, and held-out validation coverage. |
| V4 | verified | Radar generation and hashed HTML | All 50 radar cards have media scores. |
| V5 | verified | Full root test suite | 237 tests pass after the changes. |
| V6 | verified | Whitespace check | No diff whitespace errors. |

## Git Custody

- Branch: `main`
- Baseline HEAD: `f0e5c14b4787f43c105064aab9ce7e4191b9cf09`
- Final HEAD: `f0e5c14b4787f43c105064aab9ce7e4191b9cf09`
- History relation: `same`
- Commits since baseline: none
- Explicit implementation scopes: `docs/12-perceived-premium-pilot.md`,
  `models/premium_pilot.py`, `scripts/run_premium_pilot.py`, and
  `tests/test_premium_pilot.py`
- Task-scoped diff token: `files=4; insertions=428; deletions=12; binary_files=0; untracked_files=0`
- Record path: `progress/2026-08-31-premium-pilot-social-rescore.md`

Evidence capture began after implementation. Consequently, the recorder marks
all four scoped files as pre-existing overlap at capture time even though they
are the declared current-task scope; the late snapshot cannot independently
separate their within-turn edits. The ignored run artifacts under
`data/premium_pilot/runs/20260831-seed42-social-v1/` are task outputs but do not
appear in Git status.

Out-of-scope working-tree changes were preserved and not modified for this
re-score: `agent-lightning`, `crawlers/weibo_skin_comment_crawler.py`,
`tests/test_weibo_skin_comment_crawler.py`,
`scripts/crawl_radar_weibo_comments.py`,
`tests/test_crawl_radar_weibo_comments.py`, and
`progress/2026-08-30-premium-pilot-weibo-comments.md`.

No commit or push was requested or performed.

## Evidence Boundary

This work establishes code-level validation, exact input/output provenance, a
deterministic rule-based social re-score, and a refreshed visualization for the
fixed cohort. It does not establish that the automatically selected posts are
equivalent to a manually adjudicated media map, that 25 comments represent the
broader player population, or that the new score predicts causal or future
revenue. The held-out Spearman result remains descriptive and weights were not
retuned. The one hero-level fallback should not be interpreted as exact-skin
evidence.

## Next Steps

None required for the requested re-score. A later evidence-quality pass could
manually adjudicate the 50 post mappings, especially the hero-level fallback,
without changing the current reproducible artifacts.
