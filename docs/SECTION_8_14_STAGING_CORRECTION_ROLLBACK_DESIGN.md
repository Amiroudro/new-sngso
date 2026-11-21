# Section 8.14: staging correction, supersession, rollback and last-known-good design

## Purpose

A verified staging export is not yet a safe staging release process. The next layer must allow a corrected edition to replace a defective staging edition without destroying the previous trusted version or exposing a partially updated state.

This document defines the implementation contract for the staging registry only. It does not authorize or describe a live website, Android app, CDN, publisher destination, or public release.

## Vocabulary

- **Candidate**: one immutable stored edition artifact identified by manifest ID, edition revision and payload fingerprint.
- **Staging export**: the immutable package produced by the existing staging-export builder after independent approval and release-authorization gates.
- **Current staging pointer**: the registry-held reference to the export currently selected for the staging channel.
- **Last-known-good (LKG)**: the most recent earlier staging export that completed a verified promotion.
- **Correction**: a newly assembled edition artifact that fixes an earlier candidate. The earlier artifact is never edited.
- **Rollback**: a new staging event that points the channel back to an earlier eligible export. It never erases the intervening promotion.

## Required implementation invariants

1. **Staging only.** The channel identifier is a fixed literal staging. No caller-supplied destination URL, filesystem path, app endpoint, or live channel is accepted.
2. **Fresh gate on every operation.** Promotion and rollback must freshly verify the staging export at operation time. A previously generated export object is not proof that permissions, reviewer eligibility, or approvals remain current.
3. **Exact immutable binding.** Every event binds manifest ID, edition ID, edition revision, payload fingerprint, export ID and export fingerprint.
4. **Independent approval remains mandatory.** The exact artifact must still have an approve decision and a separate release authorization from a different active human. Both principals must still hold their respective roles.
5. **Distinct staging operator.** The operator must be an active, recently authenticated human with a dedicated edition_staging_operator role and must differ from both approver and release authorizer.
6. **Append-only history.** Promotion, correction, and rollback events are never updated or deleted. Corrections create new artifacts and new approval/authorization records.
7. **Atomic pointer change.** Event append and current-pointer update occur in one SQLite transaction. A compare-and-swap expected-current value prevents stale operators from replacing newer staging state.
8. **Preserve LKG.** On successful promotion, the previous current export becomes LKG. The new export becomes current only after all validation succeeds. Failed validation or a stale pointer leaves both values unchanged.
9. **Rollback is a new event.** Rollback may target only an earlier export recorded as successfully promoted in this staging channel. It must pass fresh integrity, rights, approval and release-authorization checks.
10. **No implicit correction.** A correction must be assembled as a new edition revision and manifest. Never mutate a stored artifact to repair it.
11. **No claim of public availability.** Staging pointer state is internal registry state. It does not set an edition record to published, set publication_authorized to true, or notify/update any consumer.
12. **Auditable operator intent.** Each operation records principal, provider identity evidence, authentication time, operation, reason, timestamp, expected prior pointer, resulting pointer, and exact artifact/export fingerprints. Raw session secrets are never persisted.

## Planned persistence model

The next migration after schema version 30 should add:
- An append-only edition_staging_events table containing the complete event record and immutable artifact/export bindings.
- A single-row edition_staging_channel_state table for the staging channel, holding current event/export and LKG event/export references plus a monotonically increasing state revision.
- Foreign keys from events to existing immutable edition artifact, approval and release-authorization records. Export package bytes/fingerprint must be stored immutably or deterministically rebuilt and fingerprint-compared before promotion.
- Database triggers that reject event update/deletion, constrain the channel to staging, verify payload/index consistency, and prevent pointer changes unless they reference a valid event.
- Audit-chain events for successful pointer changes.

The current-state row is a projection, not the history. The append-only event table remains the authoritative record.

## Operation semantics

### First promotion

Requires an empty staging pointer, no prior LKG, a fresh verified export and expected state revision zero. Atomically append the promotion event and set current to the export. LKG remains empty.

### Subsequent promotion

Requires the caller's expected state revision and expected current export to match the database. Atomically append the promotion event, move prior current to LKG, and set current to the new export.

### Correction

Build a new immutable candidate and obtain fresh approval and release authorization for that exact new manifest. Promotion follows the normal subsequent-promotion path. Earlier artifacts and decisions remain available for audit.

### Rollback

Requires a target that appears in earlier successful promotion history and is still freshly verifiable. Atomically append a rollback event, set current to the target, and move prior current to LKG. A rollback does not revive revoked permissions or stale human review evidence.

## Failure behavior

Fail closed on missing/expired/revoked rights, changed story or learning-object revisions, stale pipeline output, missing or rejected approval, revoked approver/authorizer/operator, role overlap, stale expected pointer, unknown rollback target, corrupted export fingerprint, malformed timestamps, or database constraint failure.

No pointer or LKG update may survive a failed operation. The transaction must roll back as a unit.

## Acceptance tests required before calling 8.14 implemented

- Migration 30 to the next schema version.
- First promotion creates current and leaves LKG empty.
- Second promotion moves prior current to LKG.
- Stale expected revision/current pointer is rejected without changing state.
- Two concurrent promotion attempts cannot both win.
- Rollback to a previously promoted export creates a new event and updates current/LKG atomically.
- Rollback to an unknown or never-promoted export is rejected.
- Expired/revoked source grants and revoked human principals block promotion and rollback.
- Same person cannot act as approver, release authorizer and staging operator.
- Artifact/export fingerprint tampering is rejected.
- Direct SQL update/delete of event history is rejected.
- Failed validation leaves current and LKG unchanged.
- Audit chain records successful state changes.
- Tests prove no code path sets publication_authorized to true, writes a live destination, or changes an edition record to published.

## Current status

**Core registry implementation complete; production acceptance remains open.** Migration 31 adds an append-only staging event ledger and guarded single-row staging pointer. Promotion and rollback re-run the full staging export gate inside a SQLite write transaction, require a fresh active human staging operator distinct from the approver and release authorizer, and use an expected revision/current-pointer compare-and-swap. The export identifier is deterministic for the exact manifest/revision/payload, allowing rollback to reproduce and fingerprint-check the same package. The authenticated application facade exposes both operations. Tests cover migration, first and subsequent promotion, LKG preservation, rollback as a new event, stale-state rejection, role separation, append-only history and non-deletable channel state. GitHub Actions run [#44932795855](https://github.com/Amiroudro/new-sngso/actions/runs/44932795855) passed the full Python 3.12 suite: **824 passed in 9.62 seconds** on code/test head `11bb329f8a207914160ab6f335d2eedd2b5bcf9f`. This is repository CI evidence only. The staging channel has not been operated against genuine human-reviewed editions or live permission grants; separate-database concurrency, deployed identity/revocation, backup/restore and downstream staging-consumer tests remain acceptance gates. No live destination or public publication is implemented.