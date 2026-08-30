# Perceived-premium pilot

This pilot estimates a provisional perceived/emotional premium score for a fixed
50-skin cohort. It is evidence-weighted, not a fitted revenue model. Revenue
and blind reviewer ratings are held out from score construction and are only
used after scores have been frozen.

## Evidence contract

- Local Qwen L1/L2 produces visual features for every selected skin.
- The configured AutoDL-compatible L3 API supplies semantic/cultural rationale.
- A manually reviewed media map links real public Weibo posts to at most a
  subset of the cohort. The script strips user IDs and names before optional
  remote comment enrichment.
- Synthetic media is rejected. A missing media link remains missing; it is not
  converted to a zero or a generated label.
- Revenue is read only from pre-existing signed release-window records after
  score creation. It is never passed to the fusion function.

The frozen fusion weights are visual 55%, official context 20%, and linked
media 25%. A partial score is normalized only across its available modalities,
has confidence capped at 0.65, and belongs to a separate ranking group from a
complete-evidence score.

## Runbook

1. Check the deterministic cohort and cache reconciliation without writing
   artifacts:

   ```bash
   python scripts/run_premium_pilot.py --dry-run
   ```

2. Run a three-skin live smoke test. This uses local L1/L2 and full-mode L3;
   `--require-remote` makes a missing AutoDL credential fail before work starts.

   ```bash
   python scripts/run_premium_pilot.py --limit 3 --run-vlm --require-remote --output-dir /tmp/premium-smoke
   ```

   Inspect `report.json` before expanding the cohort. `vlm_execution.status`
   must be `completed` and `invalid_local_output_count` must be zero. A
   `review_required` state means that an L1 or L2 reply was empty, malformed,
   incomplete, or outside the L2 1--10 prompt range. The runner records only
   its length, hash, and structural error; it does not persist raw model text.
   Invalid cache entries are evicted only for the affected tier and image, then
   retried once. Do not continue to the 50-skin batch until the smoke is clean.

3. Copy `docs/premium-pilot-media-mapping.example.json`, replace the placeholder
   with manually verified post-to-skin links, and run the 50-skin batch. Add
   `--media-use-llm` only when remote de-identified-comment synthesis is wanted.

   ```bash
   python scripts/run_premium_pilot.py --run-vlm --require-remote --media-map media-map.json --media-use-llm
   ```

4. Give the generated `reviewer_cards.csv` to the single reviewer. Do not show
   them model scores, media summaries, or revenue. Merge their ratings into a
   CSV with the header in `docs/premium-pilot-reviewer-template.csv`, then rerun
   using `--vlm-results data/premium_pilot/vlm_outputs.jsonl --reviewer-csv ratings.csv`.

Generated pilot artifacts are written below `data/premium_pilot/` and ignored by
Git. They include the immutable manifest, raw VLM outputs, media-label
provenance, reviewer cards, score rows, an explicit `feature_trace.jsonl`,
`run_metadata.json`, and JSON/Markdown validation reports. Each feature trace
records the seven L2 visual inputs, official-context derivation, media status,
effective fusion weights, weighted contributions, final score, and tier-level
model/prompt/cache lineage. L3 semantics are retained with
`score_role: non_scoring_rationale`.

Every VLM cache entry used by this workflow is bound to its effective model,
prompt hash, output-schema version, image content, and relevant upstream input.
Legacy unsigned entries and signature mismatches are recomputed instead of
being attributed to the current run.

The local tiers first use `qwen2.5vl:3b`. If its response remains invalid
after the bounded retry, the runner may use the installed
`llama3.2-vision:11b` fallback. The accepted model and every failed/accepted
attempt are written into tier provenance; an invalid response is never cached
or scored. If both local models fail L2 in full mode, the existing remote
combined L2/L3 path is the final validated fallback. Such runs remain marked
`degraded`, and the local rejection attempts remain visible in the trace.

To enrich an existing cohort without allowing database drift to change its
membership, pass its manifest back to the runner:

```bash
python scripts/run_premium_pilot.py \
  --manifest data/premium_pilot/runs/20260828-seed42-partial/manifest.json \
  --vlm-results data/premium_pilot/runs/20260828-seed42-partial/vlm_outputs.jsonl \
  --media-map media-map.json \
  --media-use-llm \
  --output-dir data/premium_pilot/runs/20260828-seed42-media
```

The manifest loader checks record cardinality, unique source keys, image
existence, and every image SHA-256 before scoring.

If a batch ends with missing, partial, or incomplete-provenance VLM rows, reuse
the frozen manifest and accepted rows while rerunning only the failed subset:

```bash
python scripts/run_premium_pilot.py \
  --manifest previous/manifest.json \
  --run-vlm --require-remote --execution-mode full \
  --vlm-results previous/vlm_outputs.jsonl \
  --resume-vlm-results \
  --output-dir new-run
```

Resume accepts only `ok` or `degraded` rows containing signed L1/L2 lineage
and, in full mode, L3 lineage. Other rows are rerun rather than trusted.

Each non-dry run holds `.run.lock` in its output directory. A second process
targeting the same directory fails before model calls instead of racing to
overwrite artifacts; use a new run directory for retries and enrichment.

## Interpretation boundary

One reviewer provides a descriptive audit anchor, not a consensus ground truth.
Revenue validation is reported only when enough pre-existing signed-window
outcomes overlap the cohort. Neither a score/revenue correlation nor a passed
smoke test establishes causal value or future-revenue prediction.
