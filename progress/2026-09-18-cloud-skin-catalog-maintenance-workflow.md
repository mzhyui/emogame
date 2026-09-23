# Cloud skin-catalog maintenance workflow design

- Record format: `3`
- Record ID: `RCP-20260918T073422Z-aa12c785`
- Mode: `session-documentation`
- Task type: `design`
- Task slug: `cloud-skin-catalog-maintenance-workflow`
- Date: `2026-09-18`
- Project: /home/mzhyui/git/emogame
- Priority: `normal`
- Owner: Unassigned
- Components: crawlers, data, cloud-workflow
- Labels: design, cloud-portable, official-source-only
- Status category: `done`
- Status: `done`
- Resolution: `completed`
- Created at: `2026-09-18T07:34:22Z`
- Started at: `unavailable`
- Updated at: `2026-09-18T07:34:23Z`
- Completed at: `2026-09-18T07:34:23Z`
- Due date: `Not applicable`
- Evidence state: `mixed`
- Validation state: `not-applicable`

## Outcome

The agreed design is a cloud-portable, daily configurable maintenance workflow
for the official WZRY skin catalog and image cache. It uses scheduled container
workers, a workflow queue, managed object storage, a canonical metadata
registry, and a human review queue. It is official-source-only: missing or
invalid official image records are flagged for review rather than replaced from
third-party sources. This is a recorded design decision; no cloud deployment,
schema migration, crawler change, or live retrieval was performed. [E1]

## Context and Scope

The user selected scheduled full-catalog maintenance rather than on-demand UI
retrieval, constrained images to official sources, and requested a cloud-based,
portable arrangement built from scheduled containers and managed object
storage. [E1]

The current collector already reads two official `pvp.qq.com` catalog sources,
normalizes skin records, derives asset candidates, persists skin and asset
identities, and records download outcomes. [E2] The existing reconciliation
path provides relevant fail-closed patterns: it checks stable identities,
decodes images, records hashes and dimensions, and refuses unsafe binding
states. [E3] The current documented agent architecture is an evaluation flow;
this record deliberately defines a separate catalog-maintenance control plane,
not an implementation claim about that existing graph. [E4]

## Lifecycle

Current blocker: `None`


| ID | At                   | Action     | From        | To          | Actor       | Reason                                                                                            |
| ---- | ---------------------- | ------------ | ------------- | ------------- | ------------- | --------------------------------------------------------------------------------------------------- |
| L1 | 2026-09-18T07:34:22Z | created    | none        | in-progress | record-tool | record created                                                                                    |
| L2 | 2026-09-18T07:34:23Z | transition | in-progress | done        | Codex       | Recorded the agreed cloud-portable workflow design; implementation was intentionally not started. |

No local relationships were recorded.

## Findings and Decisions

- **Operating model — proposed.** A managed scheduler launches one maintenance
  workflow per configured interval. The workflow controller creates an
  immutable `run_id`, obtains a distributed run lock, and emits a terminal
  status of `completed`, `completed_with_review`, or `failed`. [E1]
- **Acquisition boundary — user-stated.** Catalog JSON and image bytes may be
  fetched only from allowlisted official Tencent endpoints. Redirect targets
  must be rechecked against the allowlist. Missing URLs, failed retrievals, and
  invalid content become review cases; the workflow must not search the web for
  substitutes. [E1]
- **Catalog handling — inferred.** The cloud normalizer should reuse the
  collector's stable skin identity and normalized catalog model, but compare a
  newly staged snapshot with the canonical registry before it promotes any
  delta. A transient or malformed source response must not delete a previously
  verified record. [E2]
- **Image handling — inferred.** Download workers should process only new,
  changed, missing, or previously invalid assets. They stage each asset, apply
  MIME/byte-limit/decode/dimension/hash checks, then promote verified bytes to
  content-addressed object storage. The asset binding must retain the official
  URL, source key, object digest, and run ID. [E2, E3]
- **Review and presentation — proposed.** A dead-letter/review queue stores
  source key, source snapshot, official URL, failure class, retry history, and
  prior verified binding. The dashboard or a future API reads approved catalog
  snapshots and review cases; it is not a cloud worker control surface. [E1]

## Technical Design or Experimental Plan

### Cloud arrangement

```text
Managed scheduler
  -> workflow controller and run lock
  -> official catalog snapshot worker
  -> normalizer and delta planner
  -> bounded-concurrency official-image workers
  -> validator and staging registry
  -> atomic promotion to metadata registry and object storage
  -> review queue, alert, and read-only export/API
```

The controller is an agent only in the constrained orchestration sense. It may
call typed operations for catalog fetch, normalization, delta planning, image
retrieval, validation, promotion, and review-case creation. It may not execute
arbitrary URLs, SQL, or shell commands; delete verified bindings; or decide
that a visually similar third-party image is acceptable.

### Storage and provenance model

Use object storage with immutable, run-scoped and content-addressed prefixes:

```text
skins/raw/{run_id}/herolist.json
skins/raw/{run_id}/heroskinlist.json
skins/staging/{run_id}/{source_key}
skins/verified/sha256/{digest}
manifests/{run_id}.json
```

Use a managed relational registry for `catalog_runs`, `skins`, `skin_assets`,
and `review_cases`. Each promoted asset records at least its stable source key,
official remote URL, retrieval time, object key, SHA-256 digest, MIME type,
dimensions, byte size, validation result, and creating run ID. Catalog records
retain the source snapshot digest that established their current state.

### Promotion and failure gates

1. Fetch and persist both source snapshots before any catalog or image change.
2. Parse, normalize, deduplicate, and compare the staged catalog against the
   prior canonical registry.
3. Abort the run and create review cases for malformed snapshots, duplicate
   identities, suspicious broad deletions, or unsupported source changes.
4. Retrieve only official asset URLs that require work; retry transient
   failures with bounded exponential backoff.
5. Promote an image binding only after strict validation succeeds. Keep the
   old verified binding when a replacement fails.
6. Publish an immutable manifest and a read-only approved export/API only after
   promotion. A run with review cases may be complete, but its unresolved gaps
   remain visible.

### Initial acceptance gates

- Unit coverage for official URL allowlisting, redirects, normalizer identity
  resolution, delta detection, image decoding, checksum persistence, and
  non-destructive failed-run behavior.
- A small official-source smoke run before the first full-catalog job.
- A full dry run that writes snapshots, manifests, and review cases without
  promoting metadata or image bindings.
- A failure simulation showing that a source outage preserves the previously
  approved catalog and routes the failed records to review.

## Evidence Ledger


| ID | Class       | Locator                                                                                                                                                                | Supported conclusion                                                                                             |
| ---- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| E1 | user-stated | Current conversation: scheduled full-catalog maintenance; official-source-only gaps flagged for review; cloud-portable scheduled containers and managed object storage | Defines the intended operating model and source boundary.                                                        |
| E2 | verified    | crawlers/wzry_skin_crawler.py; SHA-256`f44e41f7bf754fe2a790607c754310f4b8cbb44bbac255b54a806ab3009f0584`                                                               | Existing collector behavior supports reuse of official-source normalization and asset identities.                |
| E3 | verified    | data/image_reconciliation.py; SHA-256`12e521bfe09be2db8157ea3c8428c6cad6847ce4a2bfb1850e72751e394bdf47`                                                                | Existing reconciliation behavior supports the proposed fail-closed asset-validation gates.                       |
| E4 | verified    | docs/06-agent-architecture.md; SHA-256`d9ae3349d6f6db889731ed214d6909b0e44f8486718afe84b0e627fbcaae9a18`                                                               | Existing documented agents focus on evaluation, so the cloud maintenance design is a separate proposed workflow. |

## Evidence Boundary

Repository observations are limited to the recorded source files. The cloud
components, schemas, scheduler, retention policy, retry parameters, object
store prefixes, and acceptance gates are proposed design choices, not deployed
infrastructure or tested behavior. This record does not establish catalog
completeness, image correctness, cloud cost, provider compatibility, or live
retrieval success.

## Next Steps

1. Select the cloud provider and workflow/queue services while retaining the
   typed tool contract and provider-independent data model.
2. Write a versioned registry schema and manifest JSON schema.
3. Containerize the deterministic collector and image validator without giving
   the orchestrator broad database or network permissions.
4. Implement staging, promotion, review-queue, and read-only export/API paths.
5. Run the stated smoke, dry-run, and failure-preservation gates before enabling
   the scheduled full-catalog job.
