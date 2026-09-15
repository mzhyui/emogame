# Current skin scorer: economic and emotional value

Status: current implementation and artifact snapshot
Verified: 2026-09-04
Catalog: Honor of Kings, 960 skins
Primary identity: `source_key`

## 1. Purpose and scope

EmoGame does not produce one blended "skin value" number. The current system
keeps economic evidence, perceived premium, and emotional evidence separate so
that a revenue observation cannot silently become an emotion label, and a
visual or community score cannot be presented as cash revenue.

There are four relevant outputs:

| Output | Scale | Meaning | Current use/status |
|---|---:|---|---|
| Cash-value record | CNY, estimated units, uplift, confidence | Release-window attribution from daily app revenue | Available for 49 skins; descriptive, not exact skin revenue |
| Perceived-premium pilot | 0–100 | Visual, official-context, and linked-community perception in a frozen 50-skin pilot | Exploratory pilot; separate complete and partial evidence groups |
| Observed emotion score | 0–100 or `null` | Mean human/model polarity over only the emotional aspects actually observed | Available for dashboard exploration; source and coverage must be shown |
| Published six-aspect emotion score | 0–100 or `null` | Strict six-aspect community-perception estimand with qualification and uncertainty gates | No published scores; ranking remains disabled at 0/100 and 0/960 |

The terms are not interchangeable:

- **Economic/cash value** is a modeled revenue attribution, stored in currency.
- **Perceived premium** is a multimodal perception score, not money and not a
  revenue predictor.
- **Observed emotion** is a partial summary of labeled public comments.
- **Published emotion** is the claim-bearing score allowed only after the full
  evidence protocol passes.

The main implementation entry points are:

- cash attribution: [`data/cash_value.py`](../data/cash_value.py);
- perceived-premium pilot: [`models/premium_pilot.py`](../models/premium_pilot.py)
  and [`scripts/run_premium_pilot.py`](../scripts/run_premium_pilot.py);
- human final-truth scoring:
  [`models/final_truth_emotion.py`](../models/final_truth_emotion.py) and
  [`scripts/score_final_truth_emotion.py`](../scripts/score_final_truth_emotion.py);
- production qualification: [`models/emotion_evidence.py`](../models/emotion_evidence.py)
  and [`models/rule_engine.py`](../models/rule_engine.py);
- dashboard merge and source precedence: [`dashboard/query.py`](../dashboard/query.py).

## 2. System map

```text
Official catalog + images ──> perceived-premium VLM/context ─┐
Real linked comments ───────> community premium ─────────────┤
                                                             └─> 50-skin pilot score
                                                                  (revenue excluded)

Daily China iPhone revenue + release dates + spend estimate ───> cash-value records
                                                                  (CNY attribution)

Exact-mapped public comments ─> human or frozen-model labels ──> observed emotion score
                                                          └────> qualification profile
                                                                 └─> published six-aspect score

Dashboard per skin: human final truth > selected comment model > published RuleEngine
Cash and emotion remain separate columns, filters, charts, and detail payloads.
```

All paths join through `source_key`. Hero name and skin name are checked again
when a generated emotional-score artifact is loaded; an identity mismatch
makes that score source unavailable.

## 3. Economic scorer

### 3.1 What "economic score" means in the current product

The portfolio dashboard does not use a trained, normalized economic score.
Its primary economic object is a **cash-value record** containing attributed
revenue, estimated unit volume, acquisition spend, signed uplift, method, and
confidence. This is intentional: retaining the native unit and provenance is
more honest than compressing weak revenue evidence into a generic 0–100 score.

The repository also has two secondary 0–100 economic-adjacent tools:

1. the perceived-premium pilot, described in section 4;
2. a sales-evidence proxy and optional RBF calibration layer, described in
   section 3.8. It is not active because the expected model and report files
   under `outputs/` do not currently exist.

### 3.2 Cash input data

The current production import uses:

- source file: `data/WZRY_IPHONE_revenue.csv`;
- meaning: estimated China-region iPhone app revenue for Honor of Kings;
- currency: native CNY, with no FX conversion;
- period: 2025-07-16 through 2026-07-15;
- rows: 365 daily facts;
- catalog inputs: each skin's `online_date`, quality, acquisition method, and
  price text from `data/wzry_skins/skins.sqlite3`;
- optional operator inputs: exact/estimated volume, spend, allocation weights,
  and gacha-pity overrides.

The importer hashes the CSV, stores one `revenue_import_batches` record, and
upserts daily facts into `app_revenue_daily`. Re-importing the same file and
market contract is idempotent.

### 3.3 Release-window attribution model

For each catalog release date inside the revenue period, the implementation
constructs a short post-release window:

- nominal window: release day through release day + 6 days;
- if another skin release occurs earlier, the window ends the day before the
  next release;
- baseline pool: the 28 days before release;
- minimum coverage: at least 21 baseline days and 5 attribution days.

For attribution day $d$, the expected baseline $b_d$ is the median of available
baseline-day revenues having the same weekday as $d$. If observed revenue is
$y_d$, the scorer retains both:

- signed daily uplift: $u_d = y_d - b_d$;
- positive daily uplift: $u_d^+ = \max(0, y_d - b_d)$.

Over the release window:

- `baseline_revenue` is $\sum_d b_d$;
- `signed_uplift` is $\sum_d u_d$;
- the distributable pool is $\sum_d u_d^+$.

If several skins share a release date, the pool is divided by supplied positive
allocation weights. Without complete supplied weights, it is divided equally.
For skin $i$ with share $s_i$:

$R_i = s_i \sum_d u_d^+$

`R_i` becomes `attributed_revenue`. A skin can therefore have positive
`attributed_revenue` while its total `signed_uplift` is negative: positive days
are attributed, while the signed field separately records the complete window.
This distinction must be preserved in analysis.

### 3.4 Acquisition-spend resolver

The acquisition-spend estimate is resolved in this order:

1. explicit per-run gacha override;
2. a manually stored period record;
3. explicit point price, converted at 10 points per CNY;
4. explicit CNY price text;
5. free/event acquisition, represented as CNY 0;
6. shard-only acquisition, represented as unknown;
7. quality override from `config/cash_value_costs.json`;
8. tier default from the same configuration.

Current defaults are CNY 28.8, 48.8, 88.8, 128.8, 168.8, and 500 for tiers
0–5. `无双` uses CNY 500 and `荣耀典藏` uses CNY 2,000 as explicit quality
overrides.

When spend $c_i$ is known and non-zero, estimated units are:

$\hat q_i = \operatorname{round}(R_i / c_i)$

This is an implied unit estimate, not observed transactions or ownership.

### 3.5 Cash confidence

CSV attribution starts at confidence 0.60 and applies additive penalties:

| Condition | Penalty |
|---|---:|
| Multiple same-day skins with equal allocation | -0.10 |
| Spend came from a tier default | -0.10 |
| Window truncated by the next release | -0.05 |
| Fewer than 28 baseline days or 7 attribution days | -0.05 |

The result is clipped to 0–0.75. Confidence measures the quality of the
attribution construction; it is not a probability that the attributed revenue
is correct.

### 3.6 Persistence, resolution, and API

Cash data is stored in:

- `revenue_import_batches`: source-file and import-contract custody;
- `app_revenue_daily`: immutable-style daily facts by batch;
- `skin_value_records`: per-skin period attribution and manual evidence.

When multiple value records exist for a skin, the read priority is:

1. `manual_exact`;
2. `manual_estimated`;
3. `csv_release_window_uplift`.

The newest record wins inside one priority. `sales_volume` and `avg_spend_cny`
are also resolved field-by-field so a sparse higher-priority record does not
hide a useful lower-priority field.

The HTTP read interface is:

```text
GET /skins/{source_key}/cash-value?start=YYYY-MM-DD&end=YYYY-MM-DD
```

The service output contains `resolved`, all overlapping `records`, derived
`metrics`, a daily baseline/observed chart, and warnings. Derived metrics are:

- `revenue_lift_percent = signed_uplift / baseline_revenue * 100`;
- `effective_spend_per_estimated_unit_cny`;
- `emotional_value_efficiency_per_cny100`, only when a published validated
  emotional score exists. Its label explicitly says it is descriptive score
  efficiency, not monetary valuation.

### 3.7 Current cash outcomes

The live SQLite snapshot contains:

| Measure | Current value |
|---|---:|
| Catalog skins | 960 |
| Daily revenue facts | 365 |
| Skins with cash records | 49 |
| Cash coverage | 5.10% |
| Positive signed release windows | 22 |
| Negative signed release windows | 27 |
| Sum of positive-day attributed revenue | CNY 1,118,977.00 |
| Earliest/last stored release window | 2025-08-08 to 2026-07-09 |
| Average attribution confidence | 0.463 |

All 49 current records use `csv_release_window_uplift`. The sum above is an
allocation of estimated app-level positive-day uplift. It is not audited gross
skin revenue and must not be presented as such.

### 3.8 Sales proxy and optional ML calibration

[`models/sales_deviation.py`](../models/sales_deviation.py) can convert a public
sales volume, estimate, bound, or rank into a 0–100 `sales_score`, then compare
it with a sales-blind evaluation score. The result is a diagnostic `gap`, not
the portfolio cash value.

[`models/sales_calibration.py`](../models/sales_calibration.py) implements an
RBF kernel-ridge calibrator using sales-blind catalog, evidence-coverage, and
aspect features. The training loop searches fixed `gamma` and regularization
grids and reports both in-sample and leave-one-out gap statistics.

This layer is currently inactive:

- `outputs/sales_calibration_model.json` is absent;
- `outputs/sales_calibration_report.json` is absent;
- the workbench therefore warns that no calibration model is loaded;
- the portfolio dashboard uses native cash records, not a calibrated 0–100
  economic score.

The conceptual XGBoost/ensemble examples in older documents are design history,
not the present deployed scorer.

## 4. Perceived-premium pilot

### 4.1 Estimand and boundary

The perceived-premium pilot estimates how premium a skin appears from visual
quality, official context, and linked real-community response. It operates on a
frozen, seed-42 cohort of 50 skins. It is neither the cash-value model nor the
production emotion-evidence protocol.

Revenue and reviewer ratings are prohibited from score construction. They are
read only after score generation for descriptive validation.

### 4.2 Visual model

The accepted frozen run used:

- local L1/L2 primary model: `qwen2.5vl:3b`;
- local fallback: `llama3.2-vision:11b`;
- remote L3 model: `gpt-5.4-mini` through the configured AutoDL-compatible
  endpoint.

The scoring path uses seven L2 dimensions, each required to be in 1–10:

- model detail;
- effect quality;
- color scheme;
- composition;
- uniqueness;
- costume design;
- background quality.

At least four valid dimensions are required. With valid set $D$:

$V = 10 \cdot \operatorname{mean}_{j \in D}(x_j)$

L1 classification supports the pipeline but does not enter the fusion formula
directly. L3 outputs design style, cultural references, target audience,
similar skins, and differentiation as `non_scoring_rationale`; they do not add
points.

Every accepted VLM tier is bound to the image hash, effective model, prompt
hash, schema version, upstream input hash, producer signature, and retrieval
source. Invalid or unsigned cache rows fail closed or are recomputed.

### 4.3 Official-context component

The official component is a transparent prior, not observed emotion. Canonical
quality bases are:

| Quality | Base score |
|---|---:|
| 伴生 | 15 |
| 勇者 | 25 |
| 史诗 | 45 |
| 传说 | 65 |
| 无双 | 80 |
| 荣耀典藏 | 95 |

Limited/time/collection markers add 7 points; gacha/treasure/prayer/box markers
add 5 points; the result is capped at 100. If quality is unknown but one of
those markers exists, the fallback base is 35. If neither quality nor a marker
is available, the component is missing.

### 4.4 Community component

The current social rescore uses target-aware real Weibo comments. Synthetic
media, unknown references, source mismatches, empty or duplicate comments,
negative like counts, and targets above the 25-comment cap are rejected.

For each linked post, low-quality comments are filtered. Positive and negative
keyword matches produce:

$S = 100 \cdot n_{pos} / (n_{pos} + n_{neg})$

When there is no positive or negative keyword signal, $S=50$. Mean likes over
meaningful comments produce log-scaled engagement:

$E = 100 \cdot \log(1 + \overline{likes}) / \log(5001)$

clipped to 0–100. The deterministic post-level community score is:

$M = 0.60S + 0.40E$

When several valid posts map to one skin, their community scores are weighted
by meaningful-comment count. At least five meaningful comments in total are
required. The optional LLM enrichment path exists, but the current social-v1
rescore used the deterministic rule path.

### 4.5 Fusion and missingness

Configured weights are:

- visual: 0.55;
- official context: 0.20;
- media/community: 0.25.

For available components $A$ with score $z_k$ and configured weight $w_k$:

$P = \frac{\sum_{k \in A} w_k z_k}{\sum_{k \in A} w_k}$

`evidence_coverage` is the sum of configured weights present. Complete rows
receive confidence 0.90 and ranking group `complete_evidence`. Partial rows use:

$confidence = \min(0.65, 0.25 + 0.65 \cdot evidence\_coverage)$

and ranking group `partial_evidence`. Complete and partial rows must not be
mixed in an unrestricted ranking. A feature-trace row records every configured
weight, effective weight, contribution, missing component, and reconstructed
score.

### 4.6 Current pilot outcomes

The frozen visual/context baseline is
`data/premium_pilot/runs/20260828-seed42-partial-v3/`. The later target-aware
social rescore is `data/premium_pilot/runs/20260831-seed42-social-v1/`.

| Measure | Frozen partial-v3 | Social-v1 rescore |
|---|---:|---:|
| Rows | 50 | 50 |
| Complete | 0 | 46 |
| Partial | 50 | 4 |
| Insufficient | 0 | 0 |
| Minimum score | 65.33 | 61.71 |
| Median score | 79.87 | 76.18 |
| Mean score | 79.77 | 75.92 |
| Maximum score | 91.33 | 85.85 |
| Revenue-overlap skins | 25 | 25 |
| Held-out Spearman rho | 0.4987 | 0.0962 |

The baseline had no usable media component. Social-v1 added 1,112 target-comment
associations and produced media scores for all targets; four rows remained
partial because official context was missing. The revenue correlations are
descriptive cohort observations. They were not used to tune weights and do not
establish causal value or future revenue prediction.

## 5. Emotional scorer

### 5.1 Estimand

The production estimand is public community perception of a selected skin from
2024-09-02 through 2026-09-01. It is not private psychological state, true
emotion, willingness to pay, revenue impact, or a catalog-wide population
estimate.

The six locked aspects are:

| Aspect | Weight |
|---|---:|
| `visual_appeal` | 0.18 |
| `in_game_feel` | 0.18 |
| `craftsmanship_quality` | 0.18 |
| `collection_value` | 0.16 |
| `value_for_money` | 0.15 |
| `purchase_intent` | 0.10 |

The weights sum to 0.95. `market_heat`, engagement, official price/quality,
cash, revenue, perceived-premium scores, and synthetic comments are not allowed
to qualify an emotion score.

### 5.2 Annotation data

The unit is one exact target skin, one parent context, and one public comment.
An annotation contains:

- relevance: `relevant`, `irrelevant`, or `uncertain`;
- zero or more locked aspects;
- one polarity from -2 through +2 per selected aspect;
- actual-use flag;
- confidence and optional notes;
- immutable evidence and identity references.

Polarity $p$ is mapped to the 0–100 scale by:

$g(p) = 25(p + 2)$

Therefore -2, -1, 0, +1, and +2 map to 0, 25, 50, 75, and 100.
Missing aspects are absent; they are never assigned 50.

The active project-owner-declared truth artifact is
`data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv`. Its
policy is `single_human_final_truth_v1`: it is an explicit internal
single-source final-truth decision, not independent two-reviewer adjudication.
The import binds both the file SHA-256 and a canonical semantic annotation
digest. Later label or identity drift invalidates the binding.

### 5.3 Direct human final-truth scorer

[`score_final_truth_rows`](../models/final_truth_emotion.py) groups rows by
`source_key`, keeps only `relevance == relevant`, and calculates each aspect as
the rounded mean of all supplied polarity scores for that aspect.

It emits two different composites.

**Observed emotion score**

For observed aspects $O$:

$E_{observed} = \operatorname{round}\left(\frac{\sum_{a \in O} w_a e_a}{\sum_{a \in O} w_a}\right)$

The weights are renormalized over what the declared artifact actually covers.
This gives a direct summary, but a one-aspect score is not equivalent to a
six-aspect score.

**Complete six-aspect score**

$E_{complete} = \operatorname{round}\left(\frac{\sum_{a=1}^{6} w_a e_a}{0.95}\right)$

This field is `null` if any one of the six aspects is missing. No relevant rows
also produce `null`, not a neutral score.

Each output row includes `review_row_count`, `relevant_row_count`, per-aspect
counts and means, `aspect_coverage`, both composite fields, and `score_status`.

### 5.4 Frozen selected-comment model

The active extractor is `qwen3.5:4b`, frozen with its model digest, prompt hash,
and annotation schema. Dashboard model scoring accepts a skin only when:

- every usable exact-mapped comment has a model annotation;
- no accepted row is synthetic or quarantined;
- annotation source key, model digest, prompt hash, and annotator kind match the
  frozen run contract.

The same observed-aspect aggregation is applied to these model annotations.
The extractor is selected by the configured pass-first ranking, but it did not
pass the quality gate. On the 209-row declared development truth:

| Metric | qwen3.5:4b | Required gate |
|---|---:|---:|
| Relevance precision | 0.7786 | at least 0.90 |
| Relevance recall | 0.7569 | at least 0.80 |
| Aspect macro-F1 | 0.3213 | at least 0.75 |
| Polarity weighted kappa | 0.2419 | at least 0.65, or MAE at most 0.5 |
| Polarity MAE | 1.0308 | at most 0.5, or kappa at least 0.65 |

Consequently, selected-model numbers are exploratory displays, not validated
emotion evidence. Sparse-aspect scores can also reach an extreme such as 100;
coverage and source must accompany every value.

### 5.5 Production qualification and RuleEngine

The production scorer aggregates one contribution per author and aspect,
limits a parent document to at most 60% of an aspect's observations, and
requires all of the following for a skin:

- exact target mapping and no forbidden lineage;
- at least 2 independent parent documents;
- at least 20 relevant comments;
- at least 15 distinct pseudonymous authors;
- all 6 aspects with at least 5 authors per aspect;
- at least 5 actual-use authors for `in_game_feel`;
- passed locked calibration;
- passed production audit;
- protocol hash and an author-level bootstrap 95% confidence interval.

Bootstrap uses 2,000 author-level resamples with seed 42. The whole 100-skin
run can publish only when at least 80 skins qualify. Publication is atomic and
requires a verified backup.

[`RuleEngine`](../models/rule_engine.py) accepts only a matching published
qualification profile for `evidence_validated`. Numeric market signals without
that profile remain audit-only. It reconstructs the strict six-aspect score and
rejects profile/source or score mismatches.

The engine also emits an `official_prior_score` for diagnostic context:

$prior = 30I_{limited\ or\ gacha} + 45 \cdot quality + 15I_{detail} + 10I_{asset}$

This prior is not an emotional score and cannot satisfy the evidence gate.

### 5.6 Dashboard source precedence

For every catalog skin, the dashboard selects exactly one emotional source:

1. declared human final truth for every reviewed skin, including explicit
   no-relevant results;
2. frozen selected-comment model for other fully annotated sources;
3. a published RuleEngine result;
4. otherwise `null`.

Human truth therefore blocks a model fallback even when the human conclusion
is `no_relevant_final_truth`. This prevents a model from overriding an explicit
human absence result.

Compatibility fields named `emotion_validated`, `validated_emotion_count`, or
`EmotionStatus.VALIDATED` currently mean **a numeric source-backed dashboard
display is available**. They do not mean that the production publication gate
passed. The separate cohort release fields are authoritative for publication.

### 5.7 Current emotion outcomes

The live run is `20260902-current100-v1`:

| Run measure | Current value |
|---|---:|
| Cohort size | 100 |
| Required for publication | 80 |
| Evidence items | 1,346 |
| All stored annotations | 3,029 |
| Declared final-truth annotations | 209 |
| Declared final-truth skins | 20 |
| Calibration | Pending |
| Production audit | Pending |
| Release | Staged |
| Published qualifying profiles | 0 |

The catalog-wide human-final-truth artifact reports:

| Human-truth status | Skins |
|---|---:|
| `partial_final_truth` with a numeric observed score | 17 |
| `no_relevant_final_truth` | 3 |
| `no_final_truth_rows` | 940 |
| Complete six-aspect scores | 0 |

The current dashboard projection reports:

| Selected display source/status | Skins |
|---|---:|
| Human final truth, numeric | 17 |
| Selected comment model, partial numeric | 37 |
| Selected comment model, complete numeric | 2 |
| Explicit human no-relevant result | 3 |
| Explicit model no-relevant result | 3 |
| Published RuleEngine | 0 |
| Total numeric source-backed emotion displays | 56 |

Human-final-truth numeric scores currently range from 25 to 77, with mean
61.59. Selected-model numeric scores range from 31 to 100, with mean 73.10.
These distributions must not be interpreted as comparable model performance or
as a public ranking: the human rows cover only 20 development skins, model rows
can cover sparse aspects, and the extractor failed its gate.

Publication coverage remains:

- cohort: 0/100;
- catalog: 0/960;
- complete six-aspect direct human scores: 0.

## 6. Combined dashboard outcome

The current portfolio projection contains:

| Coverage | Skins | Catalog rate |
|---|---:|---:|
| Source-backed numeric emotion display | 56 | 5.83% |
| Cash record | 49 | 5.10% |
| Both numeric emotion and cash | 23 | 2.40% |

The 23-skin overlap enables a descriptive emotion-versus-attributed-revenue
scatter plot. It does not authorize a causal relationship, a single fused
score, a price recommendation, or a catalog-wide ranking.

## 7. Output contracts

### 7.1 Cash-value output

Important fields are:

| Field | Meaning |
|---|---|
| `period_start`, `period_end` | Release-attribution observation window |
| `sales_volume` | Estimated units when attributed revenue and spend permit it |
| `volume_relation` | `exact` or `estimated`; current CSV records are estimated |
| `avg_spend_cny`, `spend_basis` | Resolved acquisition-spend estimate and source |
| `baseline_revenue` | Allocated expected revenue across the window |
| `signed_uplift` | Allocated signed difference over all window days |
| `attributed_revenue` | Allocated sum of positive daily differences |
| `revenue_currency` | Native currency of the attribution, currently CNY |
| `attribution_method` | Evidence/estimation method |
| `confidence` | Construction-quality score, not correctness probability |
| `provenance` | File hash, market, platform, allocation, spend basis, penalties, daily chart |

### 7.2 Perceived-premium output

Each `scores.jsonl` row contains identity and cohort fields plus:

- `perceived_premium_score`;
- `visual_score`, `official_context_score`, `media_score`;
- `evidence_coverage`, `confidence`, `evidence_status`, `ranking_group`;
- linked media IDs and meaningful-comment count;
- VLM payload and tier provenance;
- warnings.

`feature_trace.jsonl` is the reconstructable audit source. `report.json`
contains cohort, model-execution, trace, reviewer, and held-out revenue
summaries.

### 7.3 Emotional output

Each catalog row in `final-truth-scores.json` contains:

- identity: `source_key`, `hero_name`, `skin_name`;
- counts: total review rows, relevant rows, and per-aspect observations;
- values: per-aspect means and aspect coverage;
- `observed_emotion_score`;
- `complete_six_aspect_score`;
- `score_status`.

The report header binds scorer version, truth policy, artifact hash, semantic
annotation digest, catalog count, and status counts. The dashboard recomputes
truth scores from the bound CSV before accepting the generated JSON; malformed,
duplicated, drifted, or identity-mismatched artifacts fail closed.

### 7.4 Artifact custody and portability

The revenue source CSV is tracked, but the live SQLite database, collected
comments, review CSV, generated emotional report, and premium-pilot run
directories are local ignored artifacts. Therefore:

- the current counts and outcome distributions are a verified local snapshot,
  not values guaranteed by a clean Git checkout;
- a deployment needs the matching data bundle as well as application code;
- generated scores should be accepted only with their bound input hashes and
  run metadata;
- missing local artifacts should produce empty states, not reconstructed or
  synthetic values.

## 8. Reproduction and audit commands

Read the current emotion-run state:

```bash
.venv/bin/python scripts/run_emotion_evidence.py status
```

Recompute the final-truth report in memory without writing:

```bash
.venv/bin/python scripts/score_final_truth_emotion.py
```

Write the final-truth artifact only after reviewing the dry-run summary:

```bash
.venv/bin/python scripts/score_final_truth_emotion.py --apply
```

Inspect one skin's cash-value contract:

```bash
curl 'http://127.0.0.1:8000/skins/135-10/cash-value'
```

Run the relevant regression tests:

```bash
.venv/bin/python -m unittest \
  tests.test_cash_value \
  tests.test_premium_pilot \
  tests.test_emotion_evidence \
  tests.test_dashboard_models
```

For premium-pilot execution and artifact-resume commands, use
[`12-perceived-premium-pilot.md`](12-perceived-premium-pilot.md). For the
immutable emotional evidence contract, use
[`13-emotion-evidence-protocol.md`](13-emotion-evidence-protocol.md). For
collection and declared-truth operations, use
[`14-emotion-evidence-operations.md`](14-emotion-evidence-operations.md).

## 9. Interpretation rules

1. Never label `attributed_revenue` as exact skin revenue.
2. Never convert missing cash or emotion evidence to zero.
3. Never fuse cash, perceived premium, and emotion into one total score without
   a separately specified and validated estimand.
4. Never use premium-pilot comments or scores to qualify production emotion
   evidence.
5. Never present selected-model dashboard values as validated while
   `selected_quality_gate_passed` is false.
6. Never rank complete and partial premium rows together without an explicit
   evidence-group restriction.
7. Never call 56 source-backed dashboard scores "56 published scores"; the
   published count is currently zero.
8. Preserve source, status, aspect coverage, counts, uncertainty, and warnings
   next to every displayed score.
9. Treat the current cash/emotion overlap as descriptive only.
10. Re-run the status and artifact summaries before quoting counts in a later
    report because local ignored data can change independently of Git.
