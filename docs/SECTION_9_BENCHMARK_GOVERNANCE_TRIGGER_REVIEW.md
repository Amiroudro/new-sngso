# Section 9 Benchmark Governance Trigger Semantic Review

Status: **source-level semantic review completed for all 39 benchmark-governance triggers, including two schema-v34 SQL-boundary guards. This review identifies database-boundary gaps that must be closed before a PostgreSQL port can be accepted.**

Scope includes immutable benchmark evidence, adjudication, reviewer assignment and assignment revisions, principal classification and identity attestations, score records, study protocols/executions, acceptance decisions, and editorial review revision history.

The source SQL is in [the complete trigger inventory](SECTION_9_TRIGGER_INVENTORY_COMPLETE.md). Existing application, registry, identity, and schema tests were inspected to distinguish database-enforced rules from facade-only checks.

## 1. Benchmark artifacts and adjudications (5 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_artifacts_no_update` | Registered benchmark artifact cannot be rewritten | BEFORE UPDATE trigger and restricted application-role grants | Artifact registry, canonical artifact, and fingerprint tests |
| `benchmark_artifacts_no_delete` | Registered benchmark artifact cannot be deleted | BEFORE DELETE trigger and restricted role | Immutable artifact lifecycle tests |
| `benchmark_adjudications_no_update` | Final adjudication is immutable | BEFORE UPDATE trigger and restricted role | Adjudication contract and registry tests |
| `benchmark_adjudications_no_delete` | Final adjudication cannot be deleted | BEFORE DELETE trigger and restricted role | Adjudication registry tests |
| `benchmark_adjudications_one_per_artifact` | Only one adjudication row may exist per benchmark ID | UNIQUE constraint on benchmark ID, retained as a database-level uniqueness invariant | Registry one-final-adjudication tests |

**Boundary finding:** this trigger family has no INSERT payload-binding trigger for artifacts or adjudications. The artifact schema and adjudication contract are strongly validated by application code and JSON Schema, but PostgreSQL must inspect actual table constraints and explicitly preserve the artifact fingerprint, exact review IDs, independent adjudicator identity, rationale, and evidence binding at the database boundary where those values are indexed. A unique adjudication row alone does not prove the adjudication is valid.

## 2. Reviewer assignments and assignment revision ledger (10 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_review_assignment_revision_guard` | Every update increments revision by exactly one; assignment/artifact/language/reviewer/assigner/time/deadline identity is immutable | BEFORE UPDATE trigger with row lock and null-safe immutable-field checks | Assignment lifecycle and revision tests |
| `benchmark_review_assignment_payload_guard_insert` | Payload identity, artifact fingerprint, language, reviewer, assigner and status match indexed columns | JSONB validation trigger and typed columns | Assignment registry and payload-binding tests |
| `benchmark_review_assignment_payload_guard_update` | Same payload/index binding after every update | JSONB validation trigger | Assignment transition and tamper tests |
| `benchmark_review_assignments_no_delete` | Assignment history cannot be deleted | BEFORE DELETE trigger plus restricted role | Assignment cancellation/terminal-state tests |
| `benchmark_review_assignment_initial_revision` | Initial assignment automatically creates its first revision snapshot | AFTER INSERT trigger in same transaction | Assignment history tests |
| `benchmark_review_assignment_update_requires_revision` | Updated head must have a matching pre-existing revision row | BEFORE UPDATE trigger, with a locked current head and a revision insert protocol | Assignment actor/reason/revision tests |
| `benchmark_review_assignment_revisions_no_update` | Revision rows are append-only | BEFORE UPDATE trigger and restricted role | Revision history tests |
| `benchmark_review_assignment_revisions_no_delete` | Revision rows cannot be deleted | BEFORE DELETE trigger and restricted role | Revision history tests |
| `benchmark_review_assignment_revisions_validate_insert` | Initial revision must exactly match current head; a subsequent revision must be exactly next and preserve immutable assignment/artifact/reviewer identity while following the legal state graph | BEFORE INSERT trigger with current-head and next-transition validation | New schema-v34 forged illegal revision insertion test |
| `benchmark_review_assignment_status_transition_guard` | Direct head updates may only follow assigned→accepted/cancelled/expired or accepted→submitted/cancelled/expired; actor identity, human role/language qualification, deadline, close metadata and exact submitted review binding are checked | BEFORE UPDATE trigger with principal/review joins and explicit state transition table | Existing registry assignment lifecycle and submission tests; new SQL-boundary guard test |

**High-priority gap:** the trigger SQL does not itself enumerate the legal assignment status-transition graph. The application layer tests assignee-only acceptance/submission, deadline handling, cancellation participants, and terminal submission. A direct SQL writer that can insert a matching revision row and then update the head may bypass those application checks unless a database transition guard or tightly restricted stored procedure/privilege boundary prevents it.

**Second gap:** the assignment revision table has no trigger in the current inventory that independently validates every inserted revision against the prior head and legal transition. The BEFORE UPDATE trigger checks that a matching revision row already exists, but the revision row's own INSERT path is not guarded by a corresponding current-head trigger. PostgreSQL must not copy this ordering without adding a robust atomic protocol: lock old head, validate transition and actor, insert exactly one matching next revision, update head, all in one transaction. Reject direct forged revision inserts.

## 3. Principal classification and identity attestations (3 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_principal_kind_immutable` | Human/synthetic/service classification cannot be changed after principal creation | BEFORE UPDATE OF principal_kind trigger and restricted principal-management procedure | Principal provisioning, synthetic reviewer, and access-control tests |
| `benchmark_review_identity_attestations_no_update` | Persisted identity attestations cannot be rewritten | BEFORE UPDATE trigger plus restricted role | Provider identity attestation and stale-authentication tests |
| `benchmark_review_identity_attestations_no_delete` | Identity attestations cannot be deleted | BEFORE DELETE trigger plus restricted role | Identity-attestation retention tests |

Principal role grants, active/revoked status, and authentication validity are broader identity-governance concerns. The immutable-kind trigger does not freeze roles or active status; those changes must remain authenticated, explicitly authorized, and audited through the principal-management lifecycle. An immutable attestation is evidence of what was recorded, not proof that the external identity provider remains available or that the session is currently valid.

## 4. Score records (3 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_scores_payload_guard_insert` | Benchmark ID/fingerprint, item, task, language, evaluator and evaluation time in JSON match indexed fields | JSONB validation trigger; typed foreign keys to artifact/item/assignment as appropriate | Score registry tests for exact artifact, accepted assignment, item and task binding |
| `benchmark_scores_no_update` | Scores cannot be rewritten | BEFORE UPDATE trigger and restricted role | `test_score_records_cannot_be_updated_or_deleted` |
| `benchmark_scores_no_delete` | Scores are append-only | BEFORE DELETE trigger and restricted role | Same test |

The payload guard binds indexed columns to JSON but does not alone prove that evaluator identity was authenticated recently, that the evaluator was the accepted assignee, or that the item/task exists in the exact registered artifact. Those checks currently live in the registry/facade and must remain in the PostgreSQL transaction path.

## 5. Study protocol, execution, and acceptance evidence (10 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_study_protocols_payload_guard` | Study ID/version, benchmark identity/fingerprint, rubric version and status match payload | JSONB/typed-column binding trigger | Protocol registry exactness tests |
| `benchmark_study_protocols_version_guard` | Version 1 cannot duplicate an existing study; later versions must immediately follow prior version and preserve benchmark/rubric binding | UNIQUE(study_id, study_version), FK to prior version, and transactionally locked validation trigger | Sequential immutable protocol and stale-fingerprint tests |
| `benchmark_study_protocols_no_update` | Protocol versions cannot be rewritten | BEFORE UPDATE trigger plus restricted role | Protocol history tests |
| `benchmark_study_protocols_no_delete` | Protocol history cannot be deleted | BEFORE DELETE trigger plus restricted role | Protocol history tests |
| `benchmark_study_executions_payload_guard` | Execution ID, protocol/version/fingerprint, benchmark/rubric and methodologist ID match payload | JSONB binding trigger and typed references | Execution registry binding tests |
| `benchmark_study_executions_no_update` | Execution records are immutable | BEFORE UPDATE trigger plus restricted role | Execution registry tests |
| `benchmark_study_executions_no_delete` | Execution history cannot be deleted | BEFORE DELETE trigger plus restricted role | Execution registry tests |
| `benchmark_acceptance_reviews_payload_guard` | Review ID, execution/study/protocol/benchmark/rubric bindings, reviewer, role and decision match payload | JSONB binding trigger with exact FK references | Acceptance review registry and facade tests |
| `benchmark_acceptance_reviews_no_update` | Acceptance decisions cannot be rewritten | BEFORE UPDATE trigger plus restricted role | `test_acceptance_decision_persists_exact_evidence_and_is_append_only` |
| `benchmark_acceptance_reviews_no_delete` | Acceptance decisions cannot be deleted | BEFORE DELETE trigger plus restricted role | Same append-only acceptance test |

**Boundary finding:** the JSON payload guards verify indexed-field agreement, not the full scientific validity of the evidence. Independent methodologist/reviewer separation, domain-pass completeness, evaluator conflicts, protocol eligibility, exact score membership, and authenticated recent human identity are enforced across the registry and facade. PostgreSQL must keep those checks inside the same transaction as the immutable execution/acceptance insert.

## 6. Editorial benchmark reviews and revision history (8 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `benchmark_reviews_validate_insert` | A review starts at revision one | CHECK(revision = 1) plus BEFORE INSERT trigger | Review registry lifecycle tests |
| `benchmark_reviews_validate_update` | Review identity/fingerprint/language/creation time immutable; revision increments by one; update timestamp never moves backward; actor and reason required | BEFORE UPDATE trigger using row lock, null-safe comparisons, and typed timestamps | Review correction and actor/reason tests |
| `benchmark_reviews_no_delete` | Review head cannot be deleted | BEFORE DELETE trigger plus restricted role | Review lifecycle tests |
| `benchmark_reviews_record_insert` | Initial review head automatically appends revision snapshot | AFTER INSERT trigger in same transaction | Review revision history tests |
| `benchmark_reviews_record_update` | Every head update appends a matching revision snapshot | AFTER UPDATE trigger in same transaction | Review correction/history tests |
| `benchmark_review_revisions_validate_insert` | Revision row must match current review head and have non-empty actor/reason | BEFORE INSERT trigger with locked head; enforce exact revision uniqueness | Review revision integrity tests |
| `benchmark_review_revisions_no_update` | Revision history cannot be rewritten | BEFORE UPDATE trigger plus restricted role | Review append-only tests |
| `benchmark_review_revisions_no_delete` | Revision history cannot be deleted | BEFORE DELETE trigger plus restricted role | Review append-only tests |

**Boundary finding:** the review-head trigger enforces monotonic revision/time and identity binding but does not enumerate the full review status-transition graph. Approval eligibility, source-material permissions, language qualification, reviewer identity, and correction rationale are validated by application contracts. Add explicit database-level transition rules or restrict writes to a stored procedure that validates them. Keep the automatic head/revision insert in one transaction.

## 7. PostgreSQL translation hazards

1. SQLite `IS` / `IS NOT` comparisons need explicit null-safe PostgreSQL `IS [NOT] DISTINCT FROM` semantics.
2. JSON extraction must preserve missing-key versus explicit-null versus wrong-type behavior. Test string, number, boolean, array, and null payloads.
3. Revision/head coupling must be atomic. Do not replace AFTER triggers with asynchronous revision writers or outbox consumers.
4. Use row locks or compare-and-swap updates for assignment/review revisions and protocol version creation.
5. PostgreSQL application roles must not be able to forge revision rows, bypass status transitions, or disable/drop integrity triggers.
6. Map SQLSTATE and named constraint failures into stable engine exceptions without converting integrity failures into success.
7. Keep principal authorization and identity-provider checks separate from immutable record storage. Database role labels do not authenticate a human.
8. Concurrency tests must use independent PostgreSQL connections, including simultaneous assignment transitions, competing adjudications, protocol version races, and duplicate review submissions.

## 8. Required direct-SQL and PostgreSQL acceptance tests

- Reject direct UPDATE/DELETE on artifact, adjudication, identity-attestation, score, protocol, execution, acceptance-review, review-head, and revision-history rows.
- Reject duplicate adjudication for the same benchmark under concurrent connections.
- Reject a forged assignment revision row and any assignment status transition not in the documented state graph.
- Reject a review status transition not in the documented state graph, even when revision/actor/reason fields are otherwise valid.
- Reject mismatched payload/index fields for every guarded INSERT and UPDATE, including missing keys, explicit JSON null, and wrong JSON scalar types.
- Reject forged protocol versions, execution-to-protocol mismatches, and acceptance rows whose reviewer/methodologist or evidence binding is invalid.
- Verify immutable head plus revision snapshot roll back together under injected failure.
- Run all cases against PostgreSQL with the restricted production application role, not a superuser.

## 9. Review boundary

This is a source-level semantic review of all 39 benchmark triggers, including two schema-v34 guards that close direct-SQL assignment revision and status-transition gaps. Together with the 23 operational triggers, 28 publication/audit/story/timeline triggers, and 49 content/provenance/ingestion triggers reviewed separately, all 139 of 139 triggers now have source-level semantic review records. PostgreSQL remains unimplemented and Section 9 P0 is not complete.

## Additional direct-SQL acceptance tests committed on the Section 9 branch

- `tests/storage/test_registry.py::test_assignment_direct_sql_head_update_cannot_skip_accepted_state` attempts to move an assignment directly from `assigned` to `submitted` through SQL, with a forged transition receipt. The database transition guard must reject the skipped `accepted` state, and the stored assignment must remain at revision 1.

The direct-SQL assignment transition test is included in the successful full-suite GitHub Actions run #1458 (1,097 passed). Independent-connection PostgreSQL concurrency and restricted-role acceptance remain outstanding.
