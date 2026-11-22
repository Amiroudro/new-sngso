# Section 9 Publication, Staging, and Audit Trigger Semantic Review

Status: **source-level semantic review completed for the 22 edition/publication/staging triggers and 2 audit-event immutability triggers. PostgreSQL implementation remains outstanding.**

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

## 8. PostgreSQL translation hazards

1. **Null-safe comparison:** SQLite `IS` / `IS NOT` checks must become PostgreSQL `IS NOT DISTINCT FROM` / `IS DISTINCT FROM` where nullable fields are involved.
2. **JSON types:** SQLite `json_extract`, `json_type`, `json_each`, and `json_array_length` are not textually interchangeable with PostgreSQL JSONB operators and functions. Test missing keys, explicit JSON null, wrong scalar types, arrays, and numeric/boolean values.
3. **Serialization:** staging state CAS must lock one channel row and serialize revision changes. Independent-connection races must be tested against the target PostgreSQL isolation level.
4. **Authorization timing:** role activity checked at insertion is not a substitute for authenticated session freshness at request time. Both checks must remain.
5. **Atomicity:** staging event, channel state pointer, run/checkpoint state where applicable, and audit append must obey the documented single consistency boundary.
6. **Privileges:** immutability must survive direct SQL from the production application role. Application conventions and ORM hooks are insufficient.
7. **Audit anchor:** a local hash chain cannot prove that its tail was not removed. The externally held sequence/hash checkpoint must remain outside the registry's administrative trust boundary.

## 9. Remaining acceptance work

- Add direct-SQL negative tests for each protected table's UPDATE and DELETE path under the normal SQLite application connection.
- Add direct-SQL negative tests for every JSON/indexed-column mismatch, including wrong JSON scalar types and missing keys.
- Explicitly test staging-state initial insert/duplicate initialization and record the intended singleton constraint.
- Add PostgreSQL DDL, migrations, role grants, and transaction implementation for this entire consistency boundary.
- Run all direct-SQL and concurrency tests against independent PostgreSQL connections and the restricted production application role.
- Test crash/failure between staging event insertion and state-pointer update; the transaction must roll back both.
- Test audit checkpoint mismatch after database restore, truncation, and point-in-time recovery.

## 10. Review boundary

The 24-trigger source-level review in this document and the operational trigger review together cover 47 of the 131 SQLite triggers. This is not a claim that all 47 have dedicated direct-SQL negative tests, nor that PostgreSQL equivalence has been implemented. The other 84 triggers remain to be reviewed. Section 9 P0 and production acceptance remain open.
