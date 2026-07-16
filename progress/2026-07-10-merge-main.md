# Merge origin/main into codex/data-ingestion-foundation

## Context

Branch `codex/data-ingestion-foundation` had diverged from `origin/main` by 6
commits (our Phase 1–2 work) vs 10 commits on main (sales evidence, Bilibili
ingestion, evaluation workbench, etc.).  Merged `origin/main` to stay current.

## Merge commit

`5a54c87` — all conflicts were auto-resolved by git; no manual intervention needed.

## Conflicts (auto-resolved)

None reported — git handled the merge cleanly.

## Post-merge adjustments (uncommitted at merge time, included in the commit)

These were unstaged changes that existed before the merge commit was finalized:

### Quality tier remapping (`feature_engineering/official_extractor.py`)

The `QUALITY_TO_TIER` dict was rebased to align with the game's official tier
system after the WZRY 2025 quality classification update:

| Quality | Old Tier | New Tier |
|---------|----------|----------|
| 无双限定 / 无双 | 4 | 5 |
| 传说限定 / 传说 | 3 | 4 |
| 史诗限定 | 2 | 3 |
| 勇者限定 | 1 | 2 |
| 珍品史诗 | — | 3 (new) |
| 荣耀典藏 | 5 | 5 (unchanged) |
| 伴生 | 0 | 0 (unchanged) |

Also pre-computed `_QUALITY_TO_TIER_SORTED` (longest-first) as a module-level
constant to avoid re-sorting on every `extract_official_tier` call.

### Provenance refactor (`feature_engineering/pipeline.py`)

- `online_date` moved from a top-level `SkinFeatureVector` field into the
  `provenance` dict — keeps raw source metadata separate from computed features.
- Cache-hit path now reconstructs vectors via `SkinFeatureVector(**cached.payload)`
  instead of manually expanding kwargs — ensures constructor validation runs.

### Test sync

- `tests/test_feature_pipeline.py`: `official_tier` assertion for "诗剑行" (传说限定)
  updated from 3 → 4.
- `tests/test_official_extractor.py`: all expected tier values updated, plus
  new "珍品史诗" → 3 case added.

## Verification

- `git status` clean after commit.
- Branch is 6 commits ahead of `origin/main` (our Phase 1–2 work) and 0 behind.

---

## DB Schema Fix

### Problem

Opening `app.py` (Streamlit frontend) crashed with `OperationalError: no such table: skin_assets` and subsequently `no such column: s.hero_id`.

Root cause: `data/wzry_skins/skins.sqlite3` was created during an early dev phase with a minimal schema (only `heroes` and `skins`, both missing columns the code now expects — no `hero_id`, `catalog_source`, `has_detail_record`, etc.). The crawler's `ensure_schema()` (`crawlers/wzry_skin_crawler.py:273`) had since evolved to add `skin_assets`, `crawl_runs`, and additional columns/foreign keys, but the on-disk DB was never re-created.

### Fix (re-crawl)

```bash
rm data/wzry_skins/skins.sqlite3
python crawlers/wzry_skin_crawler.py --skip-images
```

Deleted the stale DB and re-ran the crawler with metadata-only mode (no image downloads). This produced a fresh DB with the full current schema:

| Table | Rows |
|-------|------|
| heroes | 131 |
| skins | 960 |
| skin_assets | 1,731 |
| crawl_runs | 1 |

All indexes and foreign keys now match the application code (`SkinRepository.list_skins()`, `stats()`, etc.).

### Lesson

The DB is a product of `ensure_schema()` in the crawler, not maintained as a separate migration. When the schema definition in the crawler changes, the existing on-disk DB must be dropped and re-crawled — no migration path exists, and none is needed since all source data comes from remote APIs. A fresh re-crawl is fast with `--skip-images` (~20s).