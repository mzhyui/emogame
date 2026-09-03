# P1 current-community emotion evidence protocol

Protocol version: `emotion-evidence-v1`

Status: implementation contract. A run is not claim-bearing until its immutable
human-validation, production-audit, lineage, per-skin, and release gates pass.

## Estimand and claim boundary

The estimand is public community perception of each selected skin from
2024-09-02 through 2026-09-01. It is not true emotion, willingness to pay,
revenue impact, causal value, or a catalog-wide estimate. The cohort is a fixed,
purposive 100-skin cohort and is not statistically representative of all 960
catalog skins.

The existing 50-row perceived-premium pilot is an independent product. Its
scores, reviewer cards, VLM outputs, cash data, revenue data, official priors,
quality tiers, prices, and engagement counts cannot qualify emotion evidence.

## Ethics and privacy preflight

Before any new network collection, record one of `ready`, `exempt`, or
`not_applicable` in the run contract after the responsible project owner has
documented the public-data/privacy or institutional ethics determination.
`pending` blocks live collection.

Only public comments and minimum parent context are retained. The writer stores
a run-scoped author hash and removes account names, numeric account identifiers,
location strings, and raw platform user objects. Raw captures, if separately
required for audit, remain local, ignored, access-controlled, and outside the
scoring database.

## Frozen cohort

The ordered manifest contains exactly 100 unique `source_key` values:

- 50 warm-start targets from the frozen social-pilot manifest;
- 20 additional distinct heroes released by 2020;
- 20 additional distinct heroes released from 2021 through 2024;
- 10 additional distinct heroes with missing release dates.

The manifest stores only identity, cohort role, release-era fields, the
observation window, selection seed, and a canonical SHA-256 hash. Re-running
the same inputs and seed must yield the same ordering and hash.

## Collection

Each target has six aspect-specific exact hero/skin queries. A target retains at
most 60 Weibo comments from three parent posts and 40 Bilibili replies from two
parent videos. Normal access is attempted first; SOCKS5 at `127.0.0.1:7890` is
used only after a transport failure and is recorded in provenance.

Parent content must name the target skin. Hero-only and unverifiable parents,
out-of-window comments, duplicates, spam, missing identities, synthetic rows,
and non-comment inputs are retained only as quarantined audit records. Shared
multi-skin parents still require comment-level target relevance.

## Blinded annotation and extractor selection

The annotation unit is target skin, parent context, and one comment. Reviewers
independently assign relevance (`relevant`, `irrelevant`, or `uncertain`), zero
or more locked aspects, an integer polarity from $-2$ through $+2$ for each
selected aspect, and an actual-use flag. Packs hide model output, official
quality and price, revenue, premium-pilot results, and the other reviewer.

Thirty stratified skins are frozen: 20 development skins and 10 locked skins.
Both human originals are immutable. Consensus adjudication is a third record
and is accepted only when two independent human records already exist.

Deterministic extraction, `qwen3.5:4b`, and `qwen3.5:27b` are compared only on
development adjudications. The selected model digest, prompt hash, and JSON
schema are frozen before locked labels are evaluated. Locked acceptance needs:

- relevance precision at least 0.90 and recall at least 0.80;
- aspect macro-F1 at least 0.75;
- polarity weighted kappa at least 0.65 or MAE at most 0.5.

At least 200 locked judgments must be shared between truth and predictions.
Ten percent of retained production predictions are selected by a stable hash
for a blinded human audit. A failed audit or any target-mapping error blocks
publication.

## Per-skin qualification and scoring

A skin qualifies only with all of the following:

- exact target mapping and no accepted synthetic or forbidden lineage;
- at least two independent parent posts or videos;
- at least 20 relevant comments by 15 distinct pseudonymous authors;
- all six aspect scores, each based on five distinct authors;
- five distinct authors describing post-release actual use for `in_game_feel`;
- passed locked calibration and production audit;
- a protocol hash and author-level bootstrap 95% confidence interval.

Only one contribution per author/aspect is retained. No parent may supply over
60% of an aspect's retained observations. Polarity maps to 0, 25, 50, 75, and
100. The composite is the weighted mean of the six locked subjective weights,
divided by their fixed total of 0.95. Engagement and `market_heat` are
descriptive only. Bootstrap sampling uses 2,000 author-level resamples and seed
42. Overlapping confidence intervals do not support a pairwise-difference
claim.

## Publication gate

Publication is a single SQLite transaction after an integrity-checked,
hash-verified backup. It requires a complete 100-target cohort, passed model
selection, locked calibration and production audit, and at least 80 eligible
skins. Thresholds are never weakened. If fewer than 80 qualify, the release
stays staged, catalog-wide and cohort rankings stay disabled, and validation
produces per-skin reasons plus a prioritized recollection report.

Read paths never create schema. The dashboard and API can validate only a
profile from the active published run; aggregate market signals alone are
audit-only. Catalog coverage is always shown as validated over 960, alongside
cohort coverage as validated over 100.

## Operator sequence

All mutating commands require `--apply`.

```bash
python3 scripts/run_emotion_evidence.py manifest
python3 scripts/run_emotion_evidence.py manifest --apply
python3 scripts/run_emotion_evidence.py record-ethics ethics-determination.md --status ready --apply
python3 scripts/run_emotion_evidence.py stage-warm --apply
python3 scripts/collect_emotion_evidence.py
python3 scripts/collect_emotion_evidence.py --apply --report data/emotion_evidence/runs/20260902-current100-v1/collection-report.json
python3 scripts/run_emotion_evidence.py review-pack --phase development --output data/emotion_evidence/runs/20260902-current100-v1/development.csv --apply
python3 scripts/run_emotion_evidence.py annotate-deterministic --apply
python3 scripts/run_emotion_evidence.py annotate-local --model qwen3.5:4b --apply
python3 scripts/run_emotion_evidence.py annotate-local --model qwen3.5:27b --apply
python3 scripts/run_emotion_evidence.py import-review development-a.csv --reviewer-id reviewer-a --kind human --apply
python3 scripts/run_emotion_evidence.py import-review development-b.csv --reviewer-id reviewer-b --kind human --apply
python3 scripts/run_emotion_evidence.py import-review development-consensus.csv --reviewer-id consensus --kind adjudicated --apply
python3 scripts/run_emotion_evidence.py select-model --apply
python3 scripts/run_emotion_evidence.py review-pack --phase locked --output data/emotion_evidence/runs/20260902-current100-v1/locked.csv --apply
python3 scripts/run_emotion_evidence.py import-review locked-a.csv --reviewer-id reviewer-a --kind human --apply
python3 scripts/run_emotion_evidence.py import-review locked-b.csv --reviewer-id reviewer-b --kind human --apply
python3 scripts/run_emotion_evidence.py import-review locked-consensus.csv --reviewer-id consensus --kind adjudicated --apply
python3 scripts/run_emotion_evidence.py gate --gate calibration --phase locked --prediction-id qwen3.5:4b --apply
python3 scripts/run_emotion_evidence.py audit-pack --output production-audit.csv --apply
python3 scripts/run_emotion_evidence.py import-review production-audit-a.csv --reviewer-id reviewer-a --kind human --apply
python3 scripts/run_emotion_evidence.py import-review production-audit-b.csv --reviewer-id reviewer-b --kind human --apply
python3 scripts/run_emotion_evidence.py import-review production-audit-consensus.csv --reviewer-id consensus --kind adjudicated --apply
python3 scripts/run_emotion_evidence.py gate --gate audit --phase production_audit --prediction-id qwen3.5:4b --apply
python3 scripts/run_emotion_evidence.py validate --report data/emotion_evidence/runs/20260902-current100-v1/validation-report.json --apply
python3 scripts/run_emotion_evidence.py publish --backup data/emotion_evidence/runs/20260902-current100-v1/prepublish.sqlite3 --apply
```
