# Section 9 Operational Trigger Semantic Review

Status: **source-level semantic review completed for the 23 pipeline-run, stage-checkpoint, and worker-lease triggers. PostgreSQL implementation and complete direct-SQL conformance testing remain outstanding.**

Scope: 23 triggers in the `pipeline_runs`, `pipeline_run_revisions`, `pipeline_stage_checkpoints`, `pipeline_stage_checkpoint_revisions`, `pipeline_worker_leases`, and `pipeline_worker_lease_revisions` tables. The authoritative SQL is reproduced in [the complete trigger inventory](SECTION_9_TRIGGER_INVENTORY_COMPLETE.md).

## 1. Pipeline run identity and lifecycle (7 triggers)

| Trigger | Enforced invariant | PostgreSQL enforcement design | Existing/new test evidence |
|---|---|---|---|
| `pipeline_run_payload_guard_insert` | Indexed id/status/started_at must equal JSON payload fields | Typed columns plus INSERT trigger or generated/validated JSONB projection | Pipeline-run creation and round-trip tests; direct mismatch test still needed |
| `pipeline_run_payload_guard_update` | Same projection invariant on every update | UPDATE trigger; reject mismatched JSONB projection | New identity/lifecycle SQL test covers related identity guard, not every projection field |
| `pipeline_run_identity_guard` | Run ID, start time, software version, config hash, input hashes, and initiator immutable; revision increments exactly one; only queued→running/cancelled/rejected and running→succeeded/failed/cancelled transitions | Row-lock/CAS update plus BEFORE UPDATE trigger using `IS DISTINCT FROM` semantics; explicit transition table | New direct-SQL identity and invalid-transition test; checkpointed pipeline lifecycle tests |
| `pipeline_runs_no_delete` | Pipeline run rows cannot be deleted | BEFORE DELETE trigger and restricted application role | Direct-SQL negative test still needed |
| `pipeline_run_revision_no_update` | Run revision history is append-only | BEFORE UPDATE trigger plus restricted application role | Direct-SQL negative test still needed |
| `pipeline_run_revision_no_delete` | Run revision history is append-only | BEFORE DELETE trigger plus restricted application role | Direct-SQL negative test still needed |
| `pipeline_run_revision_matches_current` | Inserted revision must match current run head revision and payload | BEFORE INSERT trigger reading locked current head | Transaction/revision tests exist; direct mismatched-revision insert test still needed |

## 2. Stage checkpoint state and history (9 triggers)

| Trigger | Enforced invariant | PostgreSQL enforcement design | Existing test evidence |
|---|---|---|---|
| `pipeline_stage_checkpoint_initial_revision` | Every new checkpoint automatically creates revision 1 in the same transaction | AFTER INSERT trigger writes revision snapshot in same PostgreSQL transaction | `test_checkpoint_is_created_before_handler_and_only_hashes_are_persisted`; history assertions |
| `pipeline_stage_checkpoint_revision_guard` | Revision increments by one; run/stage/input/idempotency/provider identity immutable; provider operation reference immutable once set; only executing→succeeded/failed/indeterminate/blocked, indeterminate→executing/succeeded/blocked, failed→blocked; output fingerprint present iff succeeded | BEFORE UPDATE trigger with row lock; `IS DISTINCT FROM` for nullable fields; CHECK constraint for status/output pairing where expressible | `test_checkpoint_identity_and_revision_guards_reject_tampering`; `test_indeterminate_checkpoint_can_later_reconcile_to_completed_without_replay`; `test_checkpoint_transition_and_audit_event_roll_back_atomically` |
| `pipeline_stage_checkpoint_revision_snapshot` | Every head update appends a matching revision snapshot atomically | AFTER UPDATE trigger in same transaction | Checkpoint history tests and rollback test |
| `pipeline_stage_checkpoint_metadata_guard_insert` | Metadata must be a JSON object with only adapter_version/provider_state/failure_code/reconciliation/attempt; bounded scalar types and values | JSONB validation trigger or constrained normalized columns; reject unknown keys | `test_checkpoint_rejects_free_form_metadata_that_could_leak_content` |
| `pipeline_stage_checkpoint_metadata_guard_update` | Same metadata allow-list applies on update | Same JSONB validation trigger | `test_checkpoint_identity_and_revision_guards_reject_tampering` |
| `pipeline_stage_checkpoint_revision_matches_head` | A revision row must exactly match the current checkpoint head | BEFORE INSERT trigger using null-safe equality and locked head | Checkpoint history and tamper tests |
| `pipeline_stage_checkpoint_revisions_no_update` | Revision history cannot be changed | BEFORE UPDATE trigger and restricted application role | Direct revision tamper assertion in checkpoint identity test |
| `pipeline_stage_checkpoint_revisions_no_delete` | Revision history cannot be deleted | BEFORE DELETE trigger and restricted application role | Direct-SQL negative test still needed |
| `pipeline_stage_checkpoints_no_delete` | Checkpoint heads cannot be deleted | BEFORE DELETE trigger and restricted application role | Direct-SQL negative test still needed |

## 3. Worker lease ownership and history (7 triggers)

| Trigger | Enforced invariant | PostgreSQL enforcement design | Existing test evidence |
|---|---|---|---|
| `pipeline_worker_lease_initial_revision` | Lease acquisition creates initial immutable history snapshot atomically | AFTER INSERT trigger in same transaction | `test_lease_acquire_persists_only_token_hash_and_blocks_second_live_owner`; revision history tests |
| `pipeline_worker_lease_revision_guard` | Revision +1; run binding immutable; active heartbeat preserves worker/token/fencing/acquisition identity and advances heartbeat; active terminal transitions preserve ownership; released/expired takeover increments fencing token and changes token hash; cancellation request immutable after set and can only be added to active lease with complete fields | BEFORE UPDATE trigger with row-level lock; transactionally increment fencing generation; preserve pending cancellation fields across expiry/takeover | `test_expired_lease_can_be_taken_over_with_new_fencing_generation`; `test_cancellation_request_fields_cannot_be_rewritten_in_sql`; `test_pending_cancellation_survives_expired_worker_handoff`; `test_sqlite_rejects_worker_lease_revision_tampering` |
| `pipeline_worker_lease_revision_snapshot` | Every lease head update appends an exact history snapshot | AFTER UPDATE trigger in same transaction | Lease heartbeat/history and takeover tests |
| `pipeline_worker_lease_revision_matches_head` | Inserted revision exactly matches current lease head, including nullable cancellation fields | BEFORE INSERT trigger with null-safe equality and locked head | Lease revision tamper test |
| `pipeline_worker_lease_revisions_no_update` | Lease history cannot be changed | BEFORE UPDATE trigger and restricted application role | Lease revision tamper test |
| `pipeline_worker_lease_revisions_no_delete` | Lease history cannot be deleted | BEFORE DELETE trigger and restricted application role | Direct-SQL negative test still needed |
| `pipeline_worker_leases_no_delete` | Lease head cannot be deleted | BEFORE DELETE trigger and restricted application role | Direct-SQL negative test still needed |

The combined reviewed scope is 23 triggers: 7 pipeline-run, 9 checkpoint, and 7 worker-lease triggers.

## 4. PostgreSQL translation hazards

1. **Null semantics:** SQLite `IS` / `IS NOT` comparisons must be translated deliberately. PostgreSQL should use `IS NOT DISTINCT FROM` / `IS DISTINCT FROM`, not ordinary equality, where nullable values are compared.
2. **JSON typing:** SQLite `json_extract` and `json_type` have different return/type behavior from PostgreSQL `jsonb ->>`, `jsonb_typeof`, and casts. Do not translate text extraction blindly. Numeric, boolean, null, and missing-key cases require explicit tests.
3. **Revision snapshots:** head mutation and revision insert must be in one database transaction. A separate worker or asynchronous event is not equivalent.
4. **Concurrent writers:** row-level locks or compare-and-swap predicates must serialize revision increments and lease fencing generation. Tests must use independent PostgreSQL connections and race the same run/checkpoint/lease.
5. **Role bypass:** triggers and constraints must remain effective for the production application role. Application code conventions alone do not preserve SQLite's direct-SQL guard behavior.
6. **Error contract:** map PostgreSQL SQLSTATE and named constraints/triggers into stable engine exceptions without swallowing integrity failures.
7. **Audit boundary:** checkpoint/lease/run transitions and audit writes must remain in the same operational consistency boundary, as already required by the backend contract.

## 5. Outstanding tests before accepting this trigger family

- Direct-SQL payload/index mismatch tests for run INSERT and UPDATE.
- Direct-SQL run and revision DELETE/UPDATE denial tests.
- Direct-SQL forged run-revision insert rejection.
- Direct-SQL checkpoint revision deletion denial and head deletion denial.
- Direct-SQL worker lease revision deletion denial and head deletion denial.
- PostgreSQL conformance versions of every test above, executed under the restricted production application role.
- Concurrency tests proving no duplicate revision number, no stale fencing acceptance, and no head/history divergence after process or database failure.

## 6. Review boundary

The 24-trigger source-level review is complete as an engineering analysis of the current SQLite SQL. This is not a claim that every invariant has a dedicated negative test, and it is not a PostgreSQL implementation. The other 107 triggers still require equivalent review. P0 is therefore not complete.
