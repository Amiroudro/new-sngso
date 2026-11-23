# Section 9 Publication, Staging, and Audit Trigger Semantic Review

Status: **source-level semantic review completed for the 17 edition/publication/staging triggers, 2 audit-event immutability triggers, and 8 story-decision/timeline-event triggers. PostgreSQL implementation remains outstanding.**

This review is based on the migration SQL in `src/sngso_engine/registry.py`, the edition storage/decision tests, and the staging transaction tests. It is an engineering review of current SQLite enforcement, not proof that the same controls already exist in PostgreSQL.

## 1. Immutable edition artifacts (3 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_artifacts_payload_guard` | Manifest identity, edition ID/revision, and payload digest columns must exactly match manifest JSON | JSONB validation plus generated/typed columns or BEFORE INSERT trigger using null-safe comparison | `test_sqlite_insert_guard_binds_decision_to_exact_stored_artifact_and_role` exercises artifact binding; artifact payload identity and hash tests |
| `edition_artifacts_no_update` | Stored artifact bytes/identity cannot be rewritten | BEFORE UPDATE trigger plus application-role privileges | `test_edition_artifact_rows_reject_updates_and_deletes` |
| `edition_artifacts_no_delete` | Stored artifacts cannot be deleted | BEFORE DELETE trigger plus application-role privileges | Same immutable-row test |

## 2. Append-only human decisions (3 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_decisions_payload_guard` | Decision payload matches indexed identity and is limited to the staging-review scope; it binds an exact stored artifact and an active human principal with the approver role | JSONB schema validation plus FK/unique constraints and a transactionally checked principal/artifact binding trigger | `test_sqlite_insert_guard_binds_decision_to_exact_stored_artifact_and_role`; role, stale-authentication and self-approval tests |
| `edition_decisions_no_update` | Decisions cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_edition_decision_requires_authenticated_human_approver_and_is_append_only` |
| `edition_decisions_no_delete` | Decisions cannot be deleted | BEFORE DELETE trigger plus restricted role | Same append-only decision test |

**Boundary:** the trigger checks that the principal is active and has the role at insertion. Authentication freshness, session validity, and identity-provider verification are application/facade responsibilities. A PostgreSQL port must preserve both layers, not treat a database role string as proof of a live authenticated human.

## 3. Preventing generic publication (3 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_domain_records_no_published_insert` | Generic domain-record insertion cannot create an edition already marked published | BEFORE INSERT trigger checking typed edition status | `test_generic_edition_insert_cannot_publish_and_sqlite_keeps_edition_immutable` |
| `edition_domain_records_no_update` | Edition snapshot records cannot be updated, including attempts to change a non-edition row into an edition row | BEFORE UPDATE trigger on old or new record type | Same test plus edition registry verification |
| `edition_domain_records_no_delete` | Edition snapshots cannot be deleted | BEFORE DELETE trigger | Same test |

This closes the generic domain-record path; controlled release is a separate workflow. PostgreSQL must also ensure alternate write paths and privileged application roles cannot bypass the guard.

## 4. Two-person release authorization (3 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_release_authorizations_insert_guard` | Authorization payload matches indexed columns, exact artifact and prior approve decision; release authorizer is a distinct active human with release-authorizer role; publication remains unauthorized | Transactional BEFORE INSERT trigger joining locked artifact, decision, and principal rows; unique/FK constraints for references | Missing/rejected approval, same-person rejection, active-human authorizer, stale-authentication and facade tests |
| `edition_release_authorizations_no_update` | Release authorization cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_two_person_release_authorization_requires_prior_approval_and_is_append_only` |
| `edition_release_authorizations_no_delete` | Release authorization cannot be deleted | BEFORE DELETE trigger plus restricted role | Same append-only test |

The approver and release authorizer are separated in the authorization row. The staging operator is checked separately when a staging event is inserted. Preserve this three-person separation and the exact artifact digest binding.

## 5. Staging event authorization and compare-and-swap (3 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_staging_events_insert_guard` | Event and export payloads bind to exact staging artifact; approval and release authorization reference one another; approver, release authorizer and operator are active humans with distinct roles/identities; state revision and previous-current pointer match current state; LKG pointer is preserved; promotion/rollback history is valid; publication is never authorized | One transaction: lock channel-state row, validate artifact/decision/authorization/principals, insert immutable event, update channel pointer using revision CAS. Keep all writes and audit entries in same transaction | `test_verified_staging_export_requires_dual_authorization_and_is_in_memory_only`; revoked approver/operator tests; role separation; CAS; first/second promotion; rollback; independent-connection race |
| `edition_staging_events_no_update` | Staging event history cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_staging_event_history_is_append_only_and_state_cannot_be_deleted` |
| `edition_staging_events_no_delete` | Staging event history cannot be deleted | BEFORE DELETE trigger plus restricted role | Same history test |

The event guard contains the most complex publication invariant in this group. PostgreSQL translation must not split validation, event append, and state-pointer update across commits. The two-independent-connection promotion test must be ported to actual PostgreSQL connections, not simulated with one connection.

## 6. Staging channel state (2 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `edition_staging_state_update_guard` | Channel identity stays fixed; revision increments exactly one; new pointer must reference a matching event; prior-current and last-known-good pointers advance consistently | BEFORE UPDATE trigger plus row lock and CAS; use `IS DISTINCT FROM` for nullable pointers | State/CAS, rollback, failure atomicity and independent-connection tests |
| `edition_staging_state_no_delete` | Channel state cannot be deleted | BEFORE DELETE trigger plus restricted role | `test_staging_event_history_is_append_only_and_state_cannot_be_deleted` |

**Review question for PostgreSQL migration:** the SQLite trigger set has an UPDATE guard and DELETE guard, but no trigger named as an INSERT guard for the staging channel-state table. Inspect the table's initial-state creation path and constraints before porting. The PostgreSQL design must explicitly prevent unauthorized creation or duplicate initialization of the singleton staging channel state.

## 7. Audit-event immutability (2 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `audit_events_no_update` | Retained audit events cannot be altered | BEFORE UPDATE trigger and application-role privilege denial | Audit-chain verification and external-checkpoint tamper test |
| `audit_events_no_delete` | Retained audit events cannot be deleted | BEFORE DELETE trigger and application-role privilege denial | Audit-tail truncation test deliberately removes trigger to simulate privileged tampering; normal-role direct denial should be separately asserted |

The triggers protect rows from ordinary SQL mutation, but do not make the audit chain tamper-proof against a database owner who can drop triggers or rewrite the database file. The independent external checkpoint comparison is the control for detecting a truncated or replaced chain tip. PostgreSQL operations must retain external checkpoint custody and restricted DDL privileges.

## 8. Story decisions and timeline events (8 triggers)

| Trigger | Invariant | PostgreSQL mapping | Existing test evidence |
|---|---|---|---|
| `story_decisions_no_update` | Story merge/split decisions cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_story_merge_decision_is_append_only_and_visible_in_each_story_history`; story split, scope, stale revision and completeness tests |
| `story_decisions_no_delete` | Story decisions cannot be deleted | BEFORE DELETE trigger plus restricted role | Same append-only merge/split test |
| `story_decision_applications_no_update` | Application ledger entries cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_story_decision_application_ledger_is_unique_and_append_only` |
| `story_decision_applications_no_delete` | Application ledger entries cannot be deleted | BEFORE DELETE trigger plus restricted role | Same application-ledger test |
| `timeline_events_validate_insert` | Event must reference an active registered story; claim list must be non-empty and every claim must belong to that story | BEFORE INSERT trigger with FK/relational validation; normalize claim membership into a join table where useful | Timeline event and story evidence/provenance tests; direct SQLite boundary validation |
| `timeline_events_review_validate_insert` | Approved/rejected event requires reviewer, timestamp and note; unreviewed/review-required event cannot carry review metadata; review timestamp cannot predate event | BEFORE INSERT trigger with typed timestamp checks and explicit status constraint | `tests/test_timeline_event_review.py` covers required/missing/unreviewed/pre-dating metadata |
| `timeline_events_no_update` | Timeline events are immutable after insertion | BEFORE UPDATE trigger plus restricted role | Event review and append-only story evidence tests |
| `timeline_events_no_delete` | Timeline events cannot be deleted | BEFORE DELETE trigger plus restricted role | Direct-SQL negative test should be explicit for delete denial |

**Translation hazard:** the timeline validator currently compares JSON claim IDs against the claim records and verifies story ownership at insertion. PostgreSQL must preserve array element type validation, duplicate claim behavior, empty/missing arrays, and same-story ownership. Decide explicitly whether duplicates are rejected by a normalized uniqueness constraint or retained with current semantics. Do not silently change behavior during porting.

Story decisions are append-only, but their application ledger also has a uniqueness/idempotency contract. The PostgreSQL migration must ensure a retry cannot apply one decision twice and that a partial merge/split application cannot leave only some affected story histories updated.

## 9. PostgreSQL translation hazards

1. **Null-safe comparison:** SQLite `IS` / `IS NOT` checks must become PostgreSQL `IS NOT DISTINCT FROM` / `IS DISTINCT FROM` where nullable fields are involved.
2. **JSON types:** SQLite `json_extract`, `json_type`, `json_each`, and `json_array_length` are not textually interchangeable with PostgreSQL JSONB operators and functions. Test missing keys, explicit JSON null, wrong scalar types, arrays, and numeric/boolean values.
3. **Serialization:** staging state CAS must lock one channel row and serialize revision changes. Independent-connection races must be tested against the target PostgreSQL isolation level.
4. **Authorization timing:** role activity checked at insertion is not a substitute for authenticated session freshness at request time. Both checks must remain.
5. **Atomicity:** staging event, channel state pointer, run/checkpoint state where applicable, and audit append must obey the documented single consistency boundary.
6. **Privileges:** immutability must survive direct SQL from the production application role. Application conventions and ORM hooks are insufficient.
7. **Audit anchor:** a local hash chain cannot prove that its tail was not removed. The externally held sequence/hash checkpoint must remain outside the registry's administrative trust boundary.

## 10. Remaining acceptance work

- Add direct-SQL negative tests for each protected table's UPDATE and DELETE path under the normal SQLite application connection.
- Add direct-SQL negative tests for every JSON/indexed-column mismatch, including wrong JSON scalar types and missing keys.
- Explicitly test staging-state initial insert/duplicate initialization and record the intended singleton constraint.
- Add PostgreSQL DDL, migrations, role grants, and transaction implementation for this entire consistency boundary.
- Run all direct-SQL and concurrency tests against independent PostgreSQL connections and the restricted production application role.
- Test crash/failure between staging event insertion and state-pointer update; the transaction must roll back both.
- Test audit checkpoint mismatch after database restore, truncation, and point-in-time recovery.

## 11. Review boundary

The 27-trigger source-level review in this document and the 23-trigger operational review together cover 50 of the 138 SQLite triggers in schema v35. This is not a claim that all 50 have dedicated direct-SQL negative tests, nor that PostgreSQL equivalence has been implemented. The other 88 triggers remain to be reviewed. Section 9 P0 and production acceptance remain open.

## Additional direct-SQL acceptance tests committed on the Section 9 branch

The following adversarial cases have now been added to the automated test suite:

- `tests/storage/test_registry.py::test_staging_channel_state_is_a_singleton_and_cannot_be_forged_or_deleted` verifies duplicate singleton initialization is rejected, a state revision cannot advance without a matching append-only event, and the state row cannot be deleted.
- `tests/storage/test_registry.py::test_staging_event_direct_sql_rejects_payload_identity_mismatch` attempts a direct event insert whose row ID disagrees with the payload ID, then verifies that neither an event nor a state transition was persisted.
- `tests/test_timeline_event_review.py::test_timeline_event_rows_reject_direct_sql_update_and_delete` verifies event immutability under direct SQL.
- `tests/test_timeline_event_review.py::test_sqlite_boundary_rejects_empty_or_unregistered_timeline_claims` verifies empty and unregistered claim references are rejected by the database trigger.
- `tests/storage/test_registry.py::test_edition_artifact_direct_sql_rejects_manifest_identity_mismatch` parametrizes mismatches across manifest ID, edition ID, edition revision, and payload digest, and verifies no artifact row is persisted.
- `tests/storage/test_registry.py::test_edition_decision_direct_sql_rejects_payload_identity_or_scope_mismatch` parametrizes all indexed decision identity fields plus role, scope, and publication-authorization metadata; each forged direct INSERT must fail without persisting a decision.
- `tests/storage/test_registry.py::test_staging_event_direct_sql_rejects_each_payload_column_mismatch` adds 17 adversarial cases covering the event's JSON-to-column bindings, including channel, operation, revision, authority flag, and target.
- `tests/storage/test_registry.py::test_staging_event_direct_sql_rejects_export_package_binding_mismatch` adds five cases covering export identity, manifest binding, payload digest, target, and publication-authorization metadata.
- `tests/storage/test_registry.py::test_edition_release_authorization_direct_sql_rejects_payload_binding_mismatch` adds 12 cases covering the exact artifact, prior approval, two-person principal identities, authorization timestamp, release scope, and the strict separation between release authorization and publication authorization.

GitHub Actions run #1463 passed the complete Python 3.12 suite with **1,099 tests** in 21.51 seconds on code commit `d49373872c7d22c1ec49758d38edec315f6603c0`. It includes the 12 release-authorization binding cases, all 22 staging payload/export binding cases, and the new valid staging lifecycle and rollback tests. The run generated and uploaded schema-v35 inspection artifact `14814905301`. The new lifecycle tests prove that a valid event and matching state pointer commit together, and that an invalid state advance rolls back the preceding event insert. They exercise the database transaction boundary directly; they do not establish a higher-level staging service/API or PostgreSQL equivalence. PostgreSQL implementation remains unstarted.
