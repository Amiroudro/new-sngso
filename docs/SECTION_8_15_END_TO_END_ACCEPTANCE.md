# Section 8.15: end-to-end acceptance and operational readiness register

## Purpose

This is the final Section 8 acceptance checkpoint. It distinguishes repository implementation and automated test evidence from real editorial, rights, identity, database, and downstream-consumer acceptance. Passing the Python suite is necessary, but it is not proof that a genuine edition can be safely operated.

## Verified repository state

- Pull request #7 is open, draft, and unmerged.
- The Section 8.14 implementation is present in `src/sngso_engine/edition_staging.py`, with SQLite migration 31, a staging-only event ledger, a guarded current/LKG state projection, authenticated operator checks, fresh export verification, and compare-and-swap state updates.
- The full Python 3.12 test suite passed on PR head `b1ceaa39ac0ef9d64277aa928cb0711639f55d46`: **824 passed in 11.04 seconds**, GitHub Actions run [#44932902651](https://github.com/Amiroudro/new-sngso/actions/runs/44932902651).
- The staging lifecycle tests are in `tests/storage/test_edition_decisions.py`. They use synthetic records, a fixture-inserted artifact, and a monkeypatched registry-backed verifier. They verify state transitions and guard behavior, but do not constitute a fully connected genuine-record lifecycle test.
- The code path for promotion/rollback calls the fresh staging export builder inside the SQLite write transaction. This is a meaningful implementation boundary, not evidence of production concurrency or deployed identity.

## Acceptance matrix

| Gate | Repository evidence | Acceptance status |
|---|---|---|
| Strict artifact and provenance contracts | Versioned schemas and contract tests | Implemented; CI passed |
| Canonical payload integrity and immutable storage | Hash verification, SQLite artifact store and mutation guards | Implemented; CI passed |
| Successful pipeline binding | Registry-backed pipeline run verifier and tests | Implemented; CI passed |
| Complete bilingual assembly | BN/EN object pairing and explicit story coverage | Implemented; fixture-level tests |
| Whole-edition current rights and reviewer recheck | Registry-backed verifier used by decisions, export and staging operations | Implemented; synthetic fixtures |
| Independent staging approval | Append-only decision ledger and authenticated facade | Implemented; synthetic identities |
| Separate release authorization | Append-only two-person authorization ledger | Implemented; synthetic identities |
| Staging export package | Strict staging-only package with deterministic fingerprint | Implemented; fixture-level tests |
| Atomic staging pointer and LKG projection | Migration 31, transaction, compare-and-swap and append-only event ledger | Implemented; single-connection fixture tests |
| Cross-connection/process concurrency | No demonstrated separate-connection race test in the current acceptance suite | Open |
| Full end-to-end run using genuine registry records | Existing staging tests mock the whole-edition verifier and insert fixture artifacts | Open |
| Real independent Bangla and English human reviews | No genuine review set supplied | Open |
| Actual current publisher republication permissions | No genuine permission evidence/grants supplied | Open |
| Deployed OIDC/JWKS, revocation and protected role provisioning | Adapter contracts and tests exist; no production identity deployment configured | Open |
| Backup/restore and operational recovery drill | No deployed database or drill evidence | Open |
| Downstream staging consumer and runtime/golden tests | No staging consumer integration | Open |
| Live publication | Not implemented or authorized | Explicitly out of scope |

## Required next acceptance work

1. Add a two-connection concurrency test using one temporary SQLite database and two independent registry instances. Prove that two operators using the same expected revision cannot both promote.
2. Add a transaction-failure test proving that a failure after event insertion but before pointer update rolls back both the event and the pointer.
3. Add a rights/principal revocation test between candidate preparation and the promotion attempt, verifying fail-closed behavior and unchanged channel state.
4. Build one integrated test fixture that uses the real artifact store, real registry-backed edition verifier, real approval and release authorization methods, real staging export builder, and real promotion/rollback methods. Do not monkeypatch the whole-edition verifier in this integrated test.
5. Before operational use, run the same flow with genuine independent human reviews, documented publisher grants, a deployed identity provider, a separately deployed staging consumer, and tested backup/restore.

## Decision

**Section 8 implementation is substantially complete, but end-to-end acceptance is not complete.** The repository contains the intended registry-level staging workflow and the full Python suite passes. The acceptance matrix still has open real-world and integration gates. Do not label Section 8 production-ready or operationally accepted until those gates are evidenced.

No live destination, Android app, website, existing published edition, or schedule has been modified. The staging channel remains an internal SQLite registry pointer, not a deployed staging environment. No code in this section sets `publication_authorized` to true.
