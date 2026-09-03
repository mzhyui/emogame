# Public-data and privacy determination

## Study identification

- Project: Emogame current-community emotion evidence
- Run ID: `20260902-current100-v1`
- Protocol: `emotion-evidence-v1`
- Observation window: 2024-09-02 through 2026-09-01
- Determination date: 2026-09-03
- Responsible owner: mzhyui
- Institutional reference or exemption number: NOT APPLICABLE

## Decision

- Institutional review requirement: APPROVED
- Pipeline collection status: `ready`
- Decision authority: NA
- Basis: limited usage

This determination authorizes the bounded collection described below. It does
not authorize private-account access, interaction with users, platform-control
circumvention, publication of identifiable comments, or collection outside the
approved protocol.

## Activity covered

The project performs observational analysis of publicly accessible Weibo
comments and Bilibili replies concerning a fixed cohort of 100+ game skins.

The project:

- does not recruit or contact platform users;
- does not send messages, conduct interventions, or alter user experiences;
- does not access private accounts or restricted content;
- does not infer protected or sensitive personal characteristics;
- does not upload comments to external model providers;
- uses only the observation window defined above;
- reports aggregate, cohort-scoped community perception.

The results are not treated as true emotion, willingness to pay, revenue
impact, causal value, or a catalog-wide population estimate.

## Data minimization

The scoring database may retain:

- sanitized comment text;
- platform and parent-content identifiers needed for provenance;
- publication timestamp;
- exact target-skin association;
- run-scoped pseudonymized author hash;
- annotation and validation records.

The scoring database must not retain:

- usernames or display names;
- raw numeric user identifiers;
- profile URLs or avatars;
- location strings;
- raw platform user objects;
- private messages or non-public content.

Raw captures required for debugging or provenance remain local,
access-controlled, ignored by version control, and excluded from scoring.

## Privacy and security controls

- Author identifiers are transformed into run-scoped pseudonymous hashes.
- Direct identifiers and location fields are removed before persistence.
- Public outputs contain aggregate scores and counts, not identifiable comments.
- Verbatim quotations are not displayed publicly where they could facilitate
  search-based re-identification.
- Local comments are not sent to external APIs or hosted language models.
- Access to raw artifacts is limited to: [AUTHORIZED ROLES].
- Storage location: ACCESS-CONTROLLED LOCATION.
- Retention period: 12 months.
- Deletion or archival procedure: Deletion after expiration.

## Human review

Human reviewers receive blinded, sanitized annotation packs. They do not receive
usernames, user IDs, locations, premium scores, revenue data, official priors,
or another reviewer's labels.

Reviewers must:

- use the material only for this project;
- avoid attempting to identify authors;
- avoid copying evidence into external services;
- report accidental identifier exposure to the project owner.

## Risk assessment

Primary risks are accidental identifier retention, quotation-based
re-identification, collection of out-of-scope content, and platform-policy
violations.

Mitigations include data minimization, pseudonymization, local processing,
access control, exact date and target filters, quarantining unverifiable
records, and aggregate-only publication.

The project owner must suspend collection if private data is captured,
sanitization fails, platform authorization changes, or a privacy incident is
detected.

## Platform and legal compliance

Collection must comply with applicable institutional rules, platform terms,
rate limits, and data-protection requirements. This document is an operational
ethics record and does not replace legal or institutional advice.

## Determination

Based on the scope and safeguards above, the responsible authority determines
that the bounded collection may proceed with pipeline status `ready`.

Any material change—including private-data access, user contact, external model
upload, expanded platforms, a wider observation window, or identifiable
publication—requires a new determination before collection.

## Approval

- Name: mzhyui
- Role: Admin
- Decision: APPROVED
- Signature or recorded approval reference: mzhyui
- Date: 2026-08-01