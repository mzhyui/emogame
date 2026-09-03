# App Interface and Premium Radar Robustness Review

- Record format: `2`
- Mode: `session-documentation`
- Date: `2026-09-02`
- Project: `emogame`
- Status: `concluded`
- Evidence state: `mixed`

## Outcome

The review identified three actionable issues in the new premium-radar interface:
one P1 availability defect and two P2 correctness/compatibility defects. The page
works with the current local artifact and all 257 root tests pass, but the tracked
repository does not contain the required bundle, the loader does not bind the
rendered HTML to the validated run, and the embed uses an API scheduled for
removal. No code fix, commit, or deployment was performed. [E1, E2, E3, E9, E10]

## Context and Scope

The review covered the current `app.py` navigation and layout changes together
with `dashboard/premium_radar.py`, the artifact custody rules in `.gitignore`,
the Streamlit dependency declaration, and the two available 50-card radar
artifacts. It focused on interface availability, evidence-to-visual consistency,
cache/embed behavior, and failure handling. Other dashboard behavior and the
scientific validity of the underlying premium scores were outside scope.
[E1-E8]

The existing implementation does provide useful defenses: it uses a
repository-relative path, keys the cache with all three file modification times,
checks required files, validates canonical counts and evidence policy, and shows
an explicit Streamlit error for expected bundle failures. The findings below are
the remaining gaps rather than a rejection of those safeguards. [E2, E3]

## Findings and Decisions

### 1. P1 - The radar page is unavailable from a clean checkout

- **Verified:** `app.py` always registers the `溢价雷达` navigation page and
  hard-codes its bundle under
  `data/premium_pilot/runs/20260831-seed42-social-v1`. The whole
  `data/premium_pilot/` tree is ignored, and index inspection confirmed that
  `report.json`, `run_metadata.json`, and `radar_plots.html` are absent from the
  tracked repository. A clean clone can therefore navigate to the page but can
  only receive the unavailable-bundle error. The canonical-bundle test also
  depends on files that are not present in that checkout. [E2, E4, E10]
- **Proposed:** Provision the canonical bundle through a documented,
  configurable artifact location, or register the navigation entry only after
  availability is established. If the embedded images make committing the HTML
  inappropriate, use deployment-time artifact installation rather than assuming
  local generated data. [E2, E4]

### 2. P2 - The displayed HTML is not bound to the validated run

- **Verified:** `load_premium_radar_bundle` validates the JSON run ID, counts,
  manifest agreement, trace status, and evidence policy, but its only HTML
  consistency check is the number of `<section class="card">` markers. [E3]
- **Verified:** The older partial-v3 HTML and the canonical social-v1 HTML are
  distinct artifacts with different SHA-256 hashes, while both contain 50 radar
  cards. [E6, E7, E8]
- **Inferred consequence:** Replacing the canonical HTML with the older 50-card
  report would pass the current loader. The page would then show the social-v1
  run ID and 46/4 evidence metrics above charts generated from another run.
  This is especially plausible because scoring and HTML generation are separate
  workflow steps. [E3, E6-E8]
- **Proposed:** Record the expected `radar_plots.html` SHA-256 in validated
  metadata and check it before display. An alternative is to embed the run ID and
  manifest hash in the HTML and validate both, but a content hash provides the
  stronger complete-file binding. [E3]

### 3. P2 - The embed uses an API scheduled for removal

- **Verified:** `app.py` calls `streamlit.components.v1.html`, the root test run
  emits Streamlit's instruction to replace it with `st.iframe`, and
  `requirements.txt` does not pin Streamlit to a compatible version. The warning
  states that the old API will be removed after 2026-06-01. [E2, E5, E9]
- **Inferred consequence:** A fresh dependency installation can select a release
  that removes the compatibility shim and breaks only the new radar page even
  though it works in the current environment. [E2, E5, E9]
- **Proposed:** Replace the call with
  `st.iframe(bundle.html, height=1300)`, preserving the validated HTML and current
  fixed-height presentation while using the supported interface. [E2, E9]

## Evidence Ledger

| ID | Class | Kind | Locator | SHA-256 | Supported conclusion |
| --- | --- | --- | --- | --- | --- |
| E1 | user-stated | user | Current task: record the app.py interface and layout robustness review findings | N/A | Authorizes this durable diagnosis record. |
| E2 | verified | repository | `app.py` | `1b1fe52876a5c24aeb8e82e7954a439d82fe0d73c1b6ca4f7b268ffa613cba32` | Defines the hard-coded bundle path, unconditional navigation entry, metrics, cache adapter, and deprecated embed call. |
| E3 | verified | repository | `dashboard/premium_radar.py` | `0977780c2bbe5dc8dbd4a96d879c62628b91795e9deb384a53fbc71dfe0ccc1e` | Defines bundle validation and shows that HTML identity is checked only by card count. |
| E4 | verified | repository | `.gitignore` | `0c396458caff6a5e4ca70487befed1954e2bd574041680f4251f2b2c11ba2d96` | Confirms that `data/premium_pilot/` is generated, ignored data. |
| E5 | verified | repository | `requirements.txt` | `1df7b1aa27cabf1c18b600e9b42a9fe58978875a9393dddf191f60f099c71f6d` | Confirms that Streamlit is unpinned. |
| E6 | verified | repository | `progress/2026-08-31-premium-pilot-social-rescore.md` | `3d5dac3c001a74b6c0d197d687308974744db44bb38a8c528fd495d7bf874773` | Records the separate radar-generation step, ignored artifact custody, and canonical social-v1 HTML hash. |
| E7 | verified | artifact | `data/premium_pilot/runs/20260828-seed42-partial-v3/radar_plots.html` | `65031a33063623504c8d0d70b55f0112f940f79d6786944aa8ac10b1d8e709ad` | Identifies the older local 50-card radar artifact. |
| E8 | verified | artifact | `data/premium_pilot/runs/20260831-seed42-social-v1/radar_plots.html` | `1ef04ece28769d90fef1939b2659b4931d0dd3193dd584e239260280c4704373` | Identifies the distinct canonical local 50-card radar artifact. |
| E9 | verified | artifact | Observed review validation: .venv/bin/python -m unittest discover -s tests passed 257 tests and emitted the st.components.v1.html removal warning | N/A | Observed 257 passing tests and the `st.components.v1.html` removal warning. |
| E10 | verified | repository | Observed Git index/ignore inspection: canonical premium radar bundle files are ignored and absent from HEAD | N/A | Confirms that the required canonical bundle files are ignored and absent from the tracked tree. |

## Evidence Boundary

The current implementation was inspected and the existing local test suite was
run successfully. The review did not execute from a newly cloned repository,
deploy the Streamlit application, substitute the older HTML into the canonical
directory, or install a future Streamlit release. The clean-checkout absence and
current validation/API contracts are verified; the resulting stale-display and
future-removal scenarios are bounded inferences from those contracts. No issue
described here establishes a defect in the underlying premium-score computation.

## Next Steps

1. Choose and document a deployable provisioning strategy for the canonical
   radar bundle, then test the page from a clean checkout or deployment image.
2. Add a cryptographic binding between `radar_plots.html` and the validated run,
   with a regression test that supplies a different 50-card HTML artifact.
3. Replace `streamlit.components.v1.html` with `st.iframe` and assert that the
   page renders without the removal warning.
4. Retain the current missing/corrupt-bundle error handling and add a test for
   navigation behavior when the canonical artifact is intentionally absent.
