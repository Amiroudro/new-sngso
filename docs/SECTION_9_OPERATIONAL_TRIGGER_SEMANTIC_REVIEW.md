# Section 9 Operational Trigger Semantic Review

Status: **source-level review and PostgreSQL trigger implementations are present for all 23 pipeline-run, stage-checkpoint, and worker-lease triggers (migrations 0012–0014). Direct-SQL conformance coverage has expanded, but the full acceptance suite, restricted-role proof, and concurrency/failure testing remain incomplete.**

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
| `pipeline_stage_checkpoint_revision_guard` | Revision increments by one; run/stage/input/idempotency/provider identity immutable; provider operation reference immutable once set; only executing→succeeded/failed/indeterminate/blocked, indeterminate→executing/succeeded/blocked, failed→blocked; output fingerprint present iff succeeded | Implemented in PostgreSQL migration 0013 as a BEFORE INSERT/UPDATE trigger plus table CHECK constraints; UPDATE row locking supplies serialization | SQLite lifecycle tests plus PostgreSQL `test_postgres_stage_checkpoints_are_idempotent_revisioned_and_immutable`, including provider-reference replacement and completed-stage replay rejection (run #1968) |
| `pipeline_stage_checkpoint_revision_snapshot` | Every head update appends a matching revision snapshot atomically | AFTER UPDATE trigger in same transaction | Checkpoint history tests and rollback test |
| `pipeline_stage_checkpoint_metadata_guard_insert` | Metadata must be a JSON object with only adapter_version/provider_state/failure_code/reconciliation/attempt; bounded scalar types and values | JSONB validation trigger or constrained normalized columns; reject unknown keys | `test_checkpoint_rejects_free_form_metadata_that_could_leak_content` |
| `pipeline_stage_checkpoint_metadata_guard_update` | Same metadata allow-list applies on update | Same JSONB validation trigger | `test_checkpoint_identity_and_revision_guards_reject_tampering` |
| `pipeline_stage_checkpoint_revision_matches_head` | A revision row must exactly match the current checkpoint head | Implemented in PostgreSQL migration 0013 as a BEFORE INSERT trigger with null-safe comparison | PostgreSQL direct-SQL forged future revision rejected; exact revision snapshot fields asserted (run #1967) |
| `pipeline_stage_checkpoint_revisions_no_update` | Revision history cannot be changed | BEFORE UPDATE trigger and restricted application role | Direct revision tamper assertion in checkpoint identity test |
| `pipeline_stage_checkpoint_revisions_no_delete` | Revision history cannot be deleted | Implemented in PostgreSQL migration 0013 as a BEFORE UPDATE OR DELETE trigger | Direct-SQL UPDATE and DELETE rejection tests; DELETE rejection passed in run #1967 |
| `pipeline_stage_checkpoints_no_delete` | Checkpoint heads cannot be deleted | Implemented in PostgreSQL migration 0013 as a BEFORE DELETE trigger | Direct-SQL negative test added in commit `aa65d756`; awaiting exact-head CI |

## 3. Worker lease ownership and history (7 triggers)

| Trigger | Enforced invariant | PostgreSQL enforcement design | Existing test evidence |
|---|---|---|---|
| `pipeline_worker_lease_initial_revision` | Lease acquisition creates initial immutable history snapshot atomically | AFTER INSERT trigger in same transaction | `test_lease_acquire_persists_only_token_hash_and_blocks_second_live_owner`; revision history tests |
| `pipeline_worker_lease_revision_guard` | Revision +1; run binding immutable; active heartbeat preserves worker/token/fencing/acquisition identity and advances heartbeat; active terminal transitions preserve ownership; released/expired takeover increments fencing token and changes token hash; cancellation request immutable after set and can only be added to active lease with complete fields | Implemented in PostgreSQL migration 0014 as a BEFORE INSERT/UPDATE trigger; updates are serialized by the row lock | PostgreSQL direct-SQL tests cover fencing takeover, attempted cancellation clearing, and preservation of pending cancellation through expired-lease handoff (run #1969) |
| `pipeline_worker_lease_revision_snapshot` | Every lease head update appends an exact history snapshot | AFTER UPDATE trigger in same transaction | Lease heartbeat/history and takeover tests |
| `pipeline_worker_lease_revision_matches_head` | Inserted revision exactly matches current lease head, including nullable cancellation fields | Implemented in PostgreSQL migration 0014 as a BEFORE INSERT trigger with null-safe comparisons | PostgreSQL direct-SQL forged future revision rejected (run #1967) |
| `pipeline_worker_lease_revisions_no_update` | Lease history cannot be changed | Implemented in PostgreSQL migration 0014 as a BEFORE UPDATE OR DELETE trigger | Direct-SQL UPDATE rejection added in commit `aa65d756`; awaiting exact-head CI |
| `pipeline_worker_lease_revisions_no_delete` | Lease history cannot be deleted | Implemented in PostgreSQL migration 0014 as a BEFORE UPDATE OR DELETE trigger | Direct-SQL DELETE rejection passed in run #1967 |
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

- Direct-SQL payload/index mismatch tests for pipeline-run INSERT and UPDATE.
- Direct-SQL run-revision UPDATE denial; run-revision DELETE denial and forged future-revision rejection now pass in run #1972.
- Checkpoint-head DELETE and worker-lease revision UPDATE denial now pass in run #1970.
- Direct-SQL checkpoint and lease mutation tests must also run under the eventual restricted production application role.
- Concurrency tests proving no duplicate revision number, no stale fencing acceptance, and no head/history divergence after process or database failure.
- Failure-injection tests for transaction rollback across head, snapshot, and audit writes.

The latest exact-head CI, [run #1972](https://github.com/Amiroudro/new-sngso/actions/runs/45413983961), passed both jobs on commit `1245cd8652bf79ebc1fbfd001aff1cfa45d079e5`: **84 PostgreSQL integration tests passed**. It includes forged pipeline-run revision rejection and revision-delete denial. [Run #1970](https://github.com/Amiroudro/new-sngso/actions/runs/45413841397) also passed both jobs and 84 PostgreSQL tests for checkpoint-head DELETE and worker-lease revision UPDATE denial. Runs #1967–#1969 verified the earlier checkpoint and lease cases.

## 6. Review boundary

The 23-trigger source-level review is complete as an engineering analysis of the current SQLite SQL, and PostgreSQL implementations exist in migrations 0012–0014. This is not a claim that every invariant has a dedicated negative test or that the production restricted role and concurrency/failure properties are accepted. The other 116 triggers still require equivalent review. P0 is therefore not complete.
