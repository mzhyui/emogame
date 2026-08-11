# Dashboard Amendment Plan

> Date: 2026-07-17
> Status: planned; implementation and revalidation pending
> Scope: dashboard correctness and README backlog reconciliation. Metadata/image backfill and VLM integration remain follow-up work.

## Review Verdict

The four-page analyst-facing dashboard structure is appropriate, but the current implementation is not ready to be marked complete. The root unit suite passes, yet page-level and semantic checks found defects outside its coverage:

- The Data Workbench crashes because it imports the removed `app.evidence_table` symbol.
- Explorer sorting places missing values before real values; clicking the fallback detail button after a zero-result search raises an exception.
- Portfolio rows treat any persisted market-signal row as validated without applying the real `RuleEngine` evidence gate, while leaving emotion and perceived-value scores empty.
- Detail evaluation can project cash-derived sales volume into the rule engine and display an insufficient result as a comprehensive emotion score.
- The analysis-period filter affects the revenue timeline but not portfolio cash KPIs, explorer rows, or detail resolution.
- Read-only dashboard queries call schema initializers, so a missing database can be partially created and subsequently fail because core skin tables are absent.
- The release/revenue view reports release counts but does not visibly plot release events on the selected-period timeline.

## Implementation Changes

### 1. Restore page runtime correctness

- Fix the workbench helper imports so every tab renders without referencing removed `app.py` symbols.
- Preserve the signatures of existing helpers imported by tests, including `build_payload`, `import_cash_value_upload`, `save_manual_cash_value`, `load_signals`, and `apply_calibration`.
- Use one shared descending-sort helper that always places `None` after real values.
- When explorer filters return no rows, show an empty state and do not render an enabled detail selector or button.

### 2. Enforce emotion-evidence boundaries

- Replace the current “signal row exists” cohort with batch evaluation through `FeatureBuilder` and `RuleEngine`.
- Set `emotion_status=validated` only when `validation_status == evidence_validated`.
- Populate `emotion_score` and `perceived_value` only for validated evaluations; insufficient results remain missing in portfolio KPIs, charts, and rankings.
- Add an opinion-only signal read path that does not inject cash-derived `sales_volume` or acquisition spend into emotional evaluation.
- On the detail page, withhold the headline emotion score when the result is insufficient while retaining the raw evaluation in the audit section.

### 3. Apply consistent period and currency semantics

- Extend portfolio and detail query interfaces with `period_start` and `period_end`.
- Resolve cash records within the selected period for overview KPIs, explorer rows, charts, and detail content.
- Apply the selected period to both revenue facts and release events; reject an end date earlier than the start date with a clear UI message.
- Include native CNY revenue directly. Include USD revenue only when the selected record contains a valid positive `cny_per_usd`; keep unconvertible records visible for audit but exclude them from the CNY aggregate.
- Plot release events visibly on the revenue timeline instead of showing only a caption count.

### 4. Make analysis reads fail closed

- Remove `ensure_schema()` calls from read-only dashboard query paths.
- Detect missing files and required tables before querying and return a structured empty/cold-start state without creating or changing the database.
- Keep the database initializer as a separate README follow-up rather than silently initializing a partial schema from the dashboard.

### 5. Correct documentation state

- Update `progress/2026-07-17-dashboard-backbone-impl.md` so it no longer claims final acceptance before amendment tests pass.
- After implementation, record the fixed behavior, exact verification commands, current data snapshot, and any remaining evidence limitations.

## README Backlog

Rewrite the root `README` as a prioritized checkbox backlog with measurable outcomes:

### P0 — Dashboard correctness

- Data Workbench runtime and all-tab page test.
- Validated emotion-score binding with cash/emotion separation.
- Period-consistent cash resolution and release timeline.
- Missing-last sorting and safe empty-result behavior.
- Missing/partial database failure behavior.

### P1 — Metadata coverage

Record the 2026-07-17 baseline and define enrichment acceptance separately from UI binding:

- 831 of 960 skins lack quality.
- 326 of 960 skins lack an online date.
- 834 of 960 skins lack an acquisition method.
- 947 of 960 skins lack official price text.

### P1 — Image coverage

- 154 skins have no image URL.
- The other 806 primary assets are recorded as remote-only/skipped rather than validated local downloads.
- Track URL coverage, successful local download, and renderability as separate completion criteria.

### P1 — Emotion evidence

- Collect and persist sufficient aspect evidence before enabling rankings.
- Do not describe official priors, cash-derived fields, or insufficient rule-engine results as validated emotional evidence.

### P2 — Platform follow-ups

- Retain the cold-start database initializer as a separate deliverable covering every required schema.
- Rewrite the AutoDL/VLM item with a validated endpoint/model choice, credential contract, smoke test, and fallback behavior.

## Test and Acceptance Plan

- Add Streamlit `AppTest` coverage for overview, explorer, detail, and workbench pages; assert no uncaught exceptions and exercise every workbench tab.
- Regress the zero-result explorer click and missing-first sorting failures.
- Test insufficient and sufficient signal rows against the real rule-engine gate, including score population and ranking eligibility.
- Test that a cash-only skin has no emotional score in portfolio or detail views.
- Test period-sensitive cash selection, release boundaries, native CNY, convertible USD, and unconvertible USD aggregation.
- Test missing and partial SQLite databases without creating files or tables from read paths.
- Run `python3 -m py_compile app.py dashboard/*.py pages/*.py`.
- Run `python3 -m unittest discover -s tests`.
- Run page-level AppTests and a real-database Streamlit smoke test.
- Recheck the live database before recording final counts. With the current snapshot, expected baseline behavior is 960 skins, 49 cash records, and zero validated emotion rows.

## Fixed Assumptions

- Keep the existing four-page layout: portfolio overview, explorer, detail, and separate data workbench.
- Keep cash value, perceived value-for-money, and emotional score as independent measures.
- Never replace missing values with zero or official metadata priors.
- Do not perform metadata/image backfill or online VLM work in this amendment.
- Leave unrelated worktree changes outside the implementation batch.
