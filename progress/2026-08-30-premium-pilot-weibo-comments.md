# Premium Pilot Weibo Comment Collection

- Record format: `2`
- Mode: `coding-progress`
- Implementation class: `fresh-implementation`
- Date: `2026-08-30`
- Project: `/home/mzhyui/git/emogame`
- Status: `completed`
- Evidence state: `verified`

## Outcome

Collected a bounded, target-aware Weibo comment artifact for all 50 skin/hero entries represented by `data/premium_pilot/runs/20260828-seed42-partial-v3/radar_plots.html`. The final artifact contains 1,112 target-comment associations: 685 from 王者荣耀 official-account posts and 427 from general posts. Every target has at least one retained comment, and no target has more than 25 raw or saved comments. [E1, E2, E4, V1, V2]

Forty-nine targets use exact skin-name post matches. `孙膑-小动物乐团` had an exact matching general post but no retrievable comments, so its 24 comments use the explicitly labelled `hero_skin_fallback_after_empty_exact` scope from an official 孙膑 skin post. The artifact is local and ignored by Git under `data/premium_pilot/`. [E4]

## Task and Scope

The request was to use the Weibo comment scraper to collect at most 25 comments for each skin/hero in the named radar page, searching official-account or general posts. [E1]

The radar page is backed by a 50-record manifest containing each target's `source_key`, hero, skin, and release metadata. [E2] Before this task, the generic crawler searched and stored posts and comments but enforced its ceiling per post and did not preserve keyword-to-target attribution in its JSON output. [E3] The confirmed implementation plan in the current session was therefore to retain the existing HTTP/search functions while adding a resumable per-target runner, exact-name matching, official/general source attribution, and an aggregate 25-comment budget.

The task excluded importing this collection into the premium score, changing the Weibo SQLite store, synthesizing comments, committing changes, and modifying the pre-existing dirty `agent-lightning` submodule. The task-start status observation showed only `agent-lightning` as dirty. [E5]

## Implementation

### Plan and Starting Status

This is a fresh implementation because the repository did not have a radar-run-aware batch interface or a target-level output schema. The existing crawler supplied cookie loading, search, post expansion, rate limiting, filtering, and hot/all-comment endpoints; the new runner composes those functions instead of replacing them. [E1, E2, E3]

### Core Functions and Result

| Path | Core symbols | Result and role |
|---|---|---|
| `scripts/crawl_radar_weibo_comments.py` | `load_targets`, `rank_matching_posts`, `discover_posts`, `crawl_target`, `_write_json_atomic` | Validates radar/manifest agreement, searches exact and qualified keywords, prioritizes exact hero/skin and official posts, applies one raw budget per target, retains target/post provenance, checkpoints atomically, and supports resume plus empty-target retry. |
| `crawlers/weibo_skin_comment_crawler.py` | `WeiboClient._request`, `fetch_hot_comments` | Retries transient request/protocol failures, stops repeated cursors, preserves earlier hot pages when a later page fails, and treats explicit no-comment responses as empty results. |
| `tests/test_crawl_radar_weibo_comments.py` | target loading, ranking, qualified fallback, empty-exact fallback tests | Covers the new target-level selection and bounded fallback behavior. |
| `tests/test_weibo_skin_comment_crawler.py` | request retry and pagination regression tests | Covers transient disconnects, repeated cursors, partial-page preservation, and explicit no-comment responses. |
| `data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json` | schema version 1 target records | Stores the completed 50-target crawl with query, match-scope, post, comment, count, and source provenance. [E4] |

The live run considered 1,221 raw target-comment rows, filtered 109 low-quality rows, and retained 1,112 target associations representing 1,048 unique public comment identities. Shared multi-skin posts can intentionally associate the same public comment with more than one relevant target. [E4]

## Interface and Behavior Changes

The new CLI is:

```text
.venv/bin/python scripts/crawl_radar_weibo_comments.py <radar_plots.html> [--output PATH] [--max-comments N] [--search-limit N] [--max-posts N] [--min-signals N] [--resume] [--retry-empty] [--quiet]
```

The output schema records the collection policy, summary, and one record per target. Each target includes its source key, hero, skin, queries, match scope, status, raw/filtered/saved counts, selected posts, and comments. Comments retain `post_mid` and `post_source_type`.

The shared Weibo client now retries all `httpx.RequestError` subclasses, rather than timeouts alone. Hot-comment pagination terminates on a repeated cursor, returns accumulated comments when a later page fails, and returns an empty list for Weibo's explicit no-comment response.

## Validation

### Test Result

Pass. The live batch, structural integrity gate, focused regression tests, full root suite, and credential-marker scan all passed. [V1, V2, V3, V4, V5]

### V1 - pass

```text
.venv/bin/python scripts/crawl_radar_weibo_comments.py data/premium_pilot/runs/20260828-seed42-partial-v3/radar_plots.html --max-comments 25 --search-limit 8 --max-posts 3 --resume --retry-empty --output data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json
```

Completed all 50 targets with status ok and 1112 retained target-comment associations: 685 from official-account posts and 427 from general posts.

### V2 - pass

```text
jq -e '(.targets | length) == 50 and ([.targets[].source_key] | unique | length) == 50 and all(.targets[]; .status == "ok") and all(.targets[]; .comment_count == (.comments | length)) and all(.targets[]; .comment_count <= 25 and .raw_comments_considered <= 25) and ([.targets[].comments[] | select((.text // "") == "")] | length) == 0' data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json
```

Returned true: 50 unique successful targets, no count mismatches or empty comment text, and both raw and saved per-target counts are at most 25.

### V3 - pass

```text
.venv/bin/python -m unittest tests.test_crawl_radar_weibo_comments tests.test_weibo_skin_comment_crawler
```

17 focused tests passed, covering target loading and ranking, qualified and hero-skin fallbacks, request retries, pagination cycles, partial pages, and explicit no-comment responses.

### V4 - pass

```text
.venv/bin/python -m unittest discover -s tests
```

233 root tests passed in 16.919 seconds; only existing deprecation and runtime-context warnings were emitted.

### V5 - pass

```text
if rg -n 'WEIBO_COOKIE|SUB=|SSOLoginState|SCF=' data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json; then exit 1; else echo 'No cookie/session-token markers found in artifact'; fi
```

No cookie or session-token markers were found in the collected JSON artifact.

## Evidence Ledger

| ID | Class | Locator or check | Supported conclusion |
|---|---|---|---|
| E1 | user-stated | Current user request: collect at most 25 Weibo comments for every skin/hero in the named radar_plots.html, using official-account or general keyword-search posts. | Defines the targets, source scope, and 25-comment ceiling. |
| E2 | verified | `data/premium_pilot/runs/20260828-seed42-partial-v3/manifest.json`, SHA-256 `0f8f4518015f0e42a8e350130f2a8076141e333f2f57dde6c72020c0d45b16bc` | Defines the 50 radar-backed target records. |
| E3 | verified | `crawlers/weibo_skin_comment_crawler.py`, SHA-256 `e6f1298a483e72750c54aeba26411d8fb45c5736d59a5fff4128ed49c2386dbe` | Records the final shared crawler implementation. |
| E4 | verified | `data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json`, SHA-256 `541e237306abb68db60d6648b5a1d12131ff1b8c7bea9003def01f4501f0606f` | Records the final collected targets, posts, comments, scopes, and counts. |
| E5 | verified | Observed task-start git status --short output: M agent-lightning; no crawler, script, or test paths were changed at that point. | Establishes `agent-lightning` as the only observed pre-existing dirty path. |
| V1 | verified | Final resumable crawl command | Establishes live completion and final source/count summary. |
| V2 | verified | Final `jq -e` integrity gate | Establishes uniqueness, successful status, count consistency, non-empty text, and cap compliance. |
| V3 | verified | Focused unit tests | Establishes regression coverage for the new runner and crawler hardening. |
| V4 | verified | Root unit-test discovery | Establishes repository test health after the changes. |
| V5 | verified | Credential-marker scan | Establishes that named cookie/session markers are absent from the output artifact. |

## Git Custody

- Branch: `main`
- Baseline HEAD: unavailable because progress capture began after implementation
- Final HEAD: `f0e5c14b4787f43c105064aab9ce7e4191b9cf09`
- History relation and commits since baseline: unavailable
- Explicit task scopes: `crawlers/weibo_skin_comment_crawler.py`, `scripts/crawl_radar_weibo_comments.py`, `tests/test_weibo_skin_comment_crawler.py`, `tests/test_crawl_radar_weibo_comments.py`, and the ignored local output `data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json`
- Task-owned Git-visible changes: the four crawler/script/test paths above plus `progress/2026-08-30-premium-pilot-weibo-comments.md`; the output artifact is task-owned but ignored by Git
- Record path: `progress/2026-08-30-premium-pilot-weibo-comments.md`
- Pre-existing outside-scope change: `agent-lightning` [E5]
- Overlap caveat: late evidence capture caused the manifest to classify all four Git-visible task paths as pre-existing overlaps; E5 preserves the earlier task-start observation showing they were not dirty then
- Scoped-diff token: `files=0; insertions=0; deletions=0; binary_files=0; untracked_files=2` (limited by the unavailable late baseline)
- Commits: none created by this task

## Evidence Boundary

This work establishes that public Weibo post/comment APIs returned the stored data during the run, that every radar target has a bounded target record, and that post titles satisfy either exact-skin matching or the one explicitly labelled hero-skin fallback. It does not establish that every individual comment discusses the target skin, because the configured `min_signals=0` policy removes simple spam/low-quality rows but does not require skin keywords in each comment.

The 1,112 count is a target-comment association count, not a globally deduplicated comment count; the artifact contains 1,048 unique public comment identities. The collection was not imported into premium scoring or the Weibo SQLite store, and no claim is made about sentiment quality, representativeness, causal effects, revenue validity, or scientific validation.

## Next Steps

None required for the requested collection. Before using the comments as premium-score evidence, add a separate relevance/sentiment review and preserve the exact-vs-hero-fallback distinction.
