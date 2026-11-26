# Section 9 Complete SQLite Trigger Inventory

Status: **mechanically extracted from the repository's migration SQL; all 139 trigger definitions have source-level semantic review records in the companion review documents.**

This inventory expands the live trigger ledger into one reviewable record per trigger. It is generated from migration definitions in `src/sngso_engine/registry.py`; the CI live schema-v36 artifact is authoritative for the migrated database.

**Important:** table references below are lexical candidates extracted from SQL tokens, not a parser-proven dependency graph. They may include false positives (for example, column or CTE names) and omit dynamic references. This file does not claim PostgreSQL equivalence or complete direct-SQL negative test coverage.

## Event distribution

| Timing and event | Count |
|---|---:|
| AFTER INSERT | 6 |
| AFTER UPDATE | 6 |
| BEFORE DELETE | 38 |
| BEFORE INSERT | 38 |
| BEFORE UPDATE | 48 |
| BEFORE UPDATE OF payload_json | 2 |
| BEFORE UPDATE OF principal_kind | 1 |
| **Total** | **139** |

## Trigger records

### `benchmark_artifacts_no_update`

- **Table:** `benchmark_artifacts`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_artifacts_no_updateBEFORE UPDATE ON benchmark_artifacts
        BEGIN SELECT RAISE(ABORT, 'benchmark artifacts are immutable'); END
```

### `benchmark_artifacts_no_delete`

- **Table:** `benchmark_artifacts`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_artifacts_no_deleteBEFORE DELETE ON benchmark_artifacts
        BEGIN SELECT RAISE(ABORT, 'benchmark artifacts are immutable'); END
```

### `benchmark_adjudications_no_update`

- **Table:** `benchmark_adjudications`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_adjudications_no_updateBEFORE UPDATE ON benchmark_adjudications
        BEGIN SELECT RAISE(ABORT, 'benchmark adjudications are immutable'); END
```

### `benchmark_adjudications_no_delete`

- **Table:** `benchmark_adjudications`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_adjudications_no_deleteBEFORE DELETE ON benchmark_adjudications
        BEGIN SELECT RAISE(ABORT, 'benchmark adjudications are immutable'); END
```

### `benchmark_adjudications_one_per_artifact`

- **Table:** `benchmark_adjudications`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `EXISTS ( SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = NEW.benchmark_id )`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_adjudications_one_per_artifactBEFORE INSERT ON benchmark_adjudications
        WHEN EXISTS (
            SELECT 1 FROM benchmark_adjudications
            WHERE benchmark_id = NEW.benchmark_id
        )
        BEGIN SELECT RAISE(ABORT, 'benchmark artifact already has a final adjudication'); END
```

### `benchmark_review_assignment_revision_guard`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_revision_guardBEFORE UPDATE ON benchmark_review_assignments
       BEGIN
         SELECT CASE WHEN NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'assignment revisions must increment sequentially') END;
         SELECT CASE WHEN NEW.assignment_id != OLD.assignment_id
              OR NEW.artifact_id != OLD.artifact_id
              OR NEW.artifact_fingerprint != OLD.artifact_fingerprint
              OR NEW.language != OLD.language
              OR NEW.reviewer_id != OLD.reviewer_id
              OR NEW.assigned_by != OLD.assigned_by
              OR NEW.assigned_at != OLD.assigned_at
              OR NEW.due_at != OLD.due_at
           THEN RAISE(ABORT, 'assignment identity and artifact binding are immutable') END;
       END
```

### `benchmark_review_assignment_payload_guard_insert`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_payload_guard_insertBEFORE INSERT ON benchmark_review_assignments
       WHEN json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id
         OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id
         OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'assignment payload does not match indexed fields'); END
```

### `benchmark_review_assignment_payload_guard_update`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status`
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_payload_guard_updateBEFORE UPDATE ON benchmark_review_assignments
       WHEN json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id
         OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id
         OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'assignment payload does not match indexed fields'); END
```

### `benchmark_review_assignments_no_delete`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignments_no_deleteBEFORE DELETE ON benchmark_review_assignments
       BEGIN SELECT RAISE(ABORT, 'benchmark review assignments cannot be deleted'); END
```

### `benchmark_review_assignment_initial_revision`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `benchmark_review_assignment_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_initial_revisionAFTER INSERT ON benchmark_review_assignments
       BEGIN
         INSERT INTO benchmark_review_assignment_revisions
         (assignment_id, revision, created_at, actor, reason, payload_json)
         VALUES (NEW.assignment_id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
       END
```

### `benchmark_review_assignment_update_requires_revision`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM benchmark_review_assignment_revisions WHERE assignment_id = NEW.assignment_id AND revision = NEW.revision AND actor = NEW.last_actor AND reason = NEW.last_reason AND payload_json = NEW.payload_json )`
- **Lexically referenced tables:** `ON`, `benchmark_review_assignment_revisions`, `requires`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_update_requires_revisionBEFORE UPDATE ON benchmark_review_assignments
       WHEN NOT EXISTS (
         SELECT 1 FROM benchmark_review_assignment_revisions
         WHERE assignment_id = NEW.assignment_id AND revision = NEW.revision
           AND actor = NEW.last_actor AND reason = NEW.last_reason
           AND payload_json = NEW.payload_json
       )
       BEGIN SELECT RAISE(ABORT, 'assignment update requires matching append-only revision'); END
```

### `benchmark_review_assignment_revisions_no_update`

- **Table:** `benchmark_review_assignment_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_revisions_no_updateBEFORE UPDATE ON benchmark_review_assignment_revisions
       BEGIN SELECT RAISE(ABORT, 'assignment revisions are append-only'); END
```

### `benchmark_review_assignment_revisions_no_delete`

- **Table:** `benchmark_review_assignment_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_revisions_no_deleteBEFORE DELETE ON benchmark_review_assignment_revisions
       BEGIN SELECT RAISE(ABORT, 'assignment revisions are append-only'); END
```

### `benchmark_principal_kind_immutable`

- **Table:** `benchmark_principals`
- **Timing/event:** BEFORE UPDATE OF principal_kind
- **WHEN predicate:** None
- **Lexically referenced tables:** `OF`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_principal_kind_immutableBEFORE UPDATE OF principal_kind ON benchmark_principals
       BEGIN SELECT RAISE(ABORT, 'benchmark principal classification is immutable'); END
```

### `benchmark_review_identity_attestations_no_update`

- **Table:** `benchmark_review_identity_attestations`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_identity_attestations_no_updateBEFORE UPDATE ON benchmark_review_identity_attestations
       BEGIN SELECT RAISE(ABORT, 'identity attestations are immutable'); END
```

### `benchmark_review_identity_attestations_no_delete`

- **Table:** `benchmark_review_identity_attestations`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_identity_attestations_no_deleteBEFORE DELETE ON benchmark_review_identity_attestations
       BEGIN SELECT RAISE(ABORT, 'identity attestations are immutable'); END
```

### `benchmark_scores_payload_guard_insert`

- **Table:** `benchmark_scores`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.artifact_id OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.artifact_fingerprint OR json_extract(NEW.payload_json, '$.item_id') IS NOT NEW.item_id OR json_extract(NEW.payload_json, '$.task_type') IS NOT NEW.task_type OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language OR json_extract(NEW.payload_json, '$.evaluator_id') IS NOT NEW.evaluator_id OR json_extract(NEW.payload_json, '$.evaluated_at') IS NOT NEW.evaluated_at`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_scores_payload_guard_insertBEFORE INSERT ON benchmark_scores
       WHEN json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.item_id') IS NOT NEW.item_id
         OR json_extract(NEW.payload_json, '$.task_type') IS NOT NEW.task_type
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.evaluator_id') IS NOT NEW.evaluator_id
         OR json_extract(NEW.payload_json, '$.evaluated_at') IS NOT NEW.evaluated_at
       BEGIN SELECT RAISE(ABORT, 'score payload does not match indexed score fields'); END
```

### `benchmark_scores_no_update`

- **Table:** `benchmark_scores`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_scores_no_updateBEFORE UPDATE ON benchmark_scores
       BEGIN SELECT RAISE(ABORT, 'benchmark score records are immutable'); END
```

### `benchmark_scores_no_delete`

- **Table:** `benchmark_scores`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_scores_no_deleteBEFORE DELETE ON benchmark_scores
       BEGIN SELECT RAISE(ABORT, 'benchmark score records are append-only'); END
```

### `benchmark_study_protocols_payload_guard`

- **Table:** `benchmark_study_protocols`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_protocols_payload_guardBEFORE INSERT ON benchmark_study_protocols
       WHEN json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id
         OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version
         OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint
         OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'study protocol payload does not match indexed fields'); END
```

### `benchmark_study_protocols_version_guard`

- **Table:** `benchmark_study_protocols`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `(NEW.study_version = 1 AND EXISTS ( SELECT 1 FROM benchmark_study_protocols WHERE study_id = NEW.study_id )) OR (NEW.study_version > 1 AND NOT EXISTS ( SELECT 1 FROM benchmark_study_protocols WHERE study_id = NEW.study_id AND study_version = NEW.study_version - 1 AND benchmark_id = NEW.benchmark_id AND benchmark_fingerprint = NEW.benchmark_fingerprint AND rubric_version = NEW.rubric_version ))`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_protocols_version_guardBEFORE INSERT ON benchmark_study_protocols
       WHEN (NEW.study_version = 1 AND EXISTS (
                SELECT 1 FROM benchmark_study_protocols WHERE study_id = NEW.study_id
             ))
         OR (NEW.study_version > 1 AND NOT EXISTS (
                SELECT 1 FROM benchmark_study_protocols
                WHERE study_id = NEW.study_id AND study_version = NEW.study_version - 1
                  AND benchmark_id = NEW.benchmark_id
                  AND benchmark_fingerprint = NEW.benchmark_fingerprint
                  AND rubric_version = NEW.rubric_version
             ))
       BEGIN SELECT RAISE(ABORT, 'study protocol versions must be sequential and preserve benchmark/rubric binding'); END
```

### `benchmark_study_protocols_no_update`

- **Table:** `benchmark_study_protocols`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_protocols_no_updateBEFORE UPDATE ON benchmark_study_protocols
       BEGIN SELECT RAISE(ABORT, 'study protocol versions are immutable'); END
```

### `benchmark_study_protocols_no_delete`

- **Table:** `benchmark_study_protocols`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_protocols_no_deleteBEFORE DELETE ON benchmark_study_protocols
       BEGIN SELECT RAISE(ABORT, 'study protocol history is append-only'); END
```

### `benchmark_study_executions_payload_guard`

- **Table:** `benchmark_study_executions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.execution_id') IS NOT NEW.execution_id OR json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version OR json_extract(NEW.payload_json, '$.protocol_fingerprint') IS NOT NEW.protocol_fingerprint OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version OR json_extract(NEW.payload_json, '$.analysis.methodologist_id') IS NOT NEW.methodologist_id`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_executions_payload_guardBEFORE INSERT ON benchmark_study_executions
       WHEN json_extract(NEW.payload_json, '$.execution_id') IS NOT NEW.execution_id
         OR json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id
         OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version
         OR json_extract(NEW.payload_json, '$.protocol_fingerprint') IS NOT NEW.protocol_fingerprint
         OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint
         OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version
         OR json_extract(NEW.payload_json, '$.analysis.methodologist_id') IS NOT NEW.methodologist_id
       BEGIN SELECT RAISE(ABORT, 'study execution payload does not match indexed fields'); END
```

### `benchmark_study_executions_no_update`

- **Table:** `benchmark_study_executions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_executions_no_updateBEFORE UPDATE ON benchmark_study_executions
       BEGIN SELECT RAISE(ABORT, 'study execution records are immutable'); END
```

### `benchmark_study_executions_no_delete`

- **Table:** `benchmark_study_executions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_study_executions_no_deleteBEFORE DELETE ON benchmark_study_executions
       BEGIN SELECT RAISE(ABORT, 'study execution history is append-only'); END
```

### `benchmark_acceptance_reviews_payload_guard`

- **Table:** `benchmark_acceptance_reviews`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.review_id') IS NOT NEW.review_id OR json_extract(NEW.payload_json, '$.execution_id') IS NOT NEW.execution_id OR json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version OR json_extract(NEW.payload_json, '$.protocol_fingerprint') IS NOT NEW.protocol_fingerprint OR json_extract(NEW.payload_json, '$.execution_fingerprint') IS NOT NEW.execution_fingerprint OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id OR json_extract(NEW.payload_json, '$.reviewer_role') IS NOT NEW.reviewer_role OR json_extract(NEW.payload_json, '$.decision') IS NOT NEW.decision`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_acceptance_reviews_payload_guardBEFORE INSERT ON benchmark_acceptance_reviews
       WHEN json_extract(NEW.payload_json, '$.review_id') IS NOT NEW.review_id
         OR json_extract(NEW.payload_json, '$.execution_id') IS NOT NEW.execution_id
         OR json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id
         OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version
         OR json_extract(NEW.payload_json, '$.protocol_fingerprint') IS NOT NEW.protocol_fingerprint
         OR json_extract(NEW.payload_json, '$.execution_fingerprint') IS NOT NEW.execution_fingerprint
         OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint
         OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version
         OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id
         OR json_extract(NEW.payload_json, '$.reviewer_role') IS NOT NEW.reviewer_role
         OR json_extract(NEW.payload_json, '$.decision') IS NOT NEW.decision
       BEGIN SELECT RAISE(ABORT, 'acceptance review payload does not match indexed evidence'); END
```

### `benchmark_acceptance_reviews_no_update`

- **Table:** `benchmark_acceptance_reviews`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_acceptance_reviews_no_updateBEFORE UPDATE ON benchmark_acceptance_reviews
       BEGIN SELECT RAISE(ABORT, 'benchmark acceptance decisions are immutable'); END
```

### `benchmark_acceptance_reviews_no_delete`

- **Table:** `benchmark_acceptance_reviews`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_acceptance_reviews_no_deleteBEFORE DELETE ON benchmark_acceptance_reviews
       BEGIN SELECT RAISE(ABORT, 'benchmark acceptance decisions are append-only'); END
```

### `learning_object_vocabulary_source_evidence_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'vocabulary'`
- **Lexically referenced tables:** `json_each`, `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_vocabulary_source_evidence_insertBEFORE INSERT ON learning_objects
       WHEN json_extract(NEW.payload_json, '$.kind') = 'vocabulary'
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.claim_ids') object_claim
           JOIN domain_records claim_record
             ON claim_record.record_type = 'claim' AND claim_record.id = object_claim.value
           JOIN domain_records evidence_record
             ON evidence_record.record_type = 'evidence'
            AND evidence_record.id = json_extract(NEW.payload_json, '$.content.source_evidence_id')
            AND evidence_record.revision = json_extract(NEW.payload_json, '$.content.source_evidence_revision')
           WHERE json_extract(evidence_record.payload_json, '$.claim_id') = claim_record.id
             AND EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.evidence_ids') claim_evidence
               WHERE claim_evidence.value = evidence_record.id
             )
             AND EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.provenance') provenance
               WHERE json_extract(provenance.value, '$.source_id') =
                     json_extract(evidence_record.payload_json, '$.source_id')
                 AND (
                   json_extract(evidence_record.payload_json, '$.article_id') IS NULL
                   OR json_extract(provenance.value, '$.article_id') =
                      json_extract(evidence_record.payload_json, '$.article_id')
                 )
                 AND (
                   json_extract(evidence_record.payload_json, '$.observation_id') IS NULL
                   OR json_extract(provenance.value, '$.observation_id') =
                      json_extract(evidence_record.payload_json, '$.observation_id')
                 )
             )
         ) THEN RAISE(ABORT, 'vocabulary source evidence must be current and linked to an object claim') END;
       END
```

### `learning_object_vocabulary_source_evidence_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE OF payload_json
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'vocabulary'`
- **Lexically referenced tables:** `OF`, `json_each`, `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_vocabulary_source_evidence_updateBEFORE UPDATE OF payload_json ON learning_objects
       WHEN json_extract(NEW.payload_json, '$.kind') = 'vocabulary'
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.claim_ids') object_claim
           JOIN domain_records claim_record
             ON claim_record.record_type = 'claim' AND claim_record.id = object_claim.value
           JOIN domain_records evidence_record
             ON evidence_record.record_type = 'evidence'
            AND evidence_record.id = json_extract(NEW.payload_json, '$.content.source_evidence_id')
            AND evidence_record.revision = json_extract(NEW.payload_json, '$.content.source_evidence_revision')
           WHERE json_extract(evidence_record.payload_json, '$.claim_id') = claim_record.id
             AND EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.evidence_ids') claim_evidence
               WHERE claim_evidence.value = evidence_record.id
             )
             AND EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.provenance') provenance
               WHERE json_extract(provenance.value, '$.source_id') =
                     json_extract(evidence_record.payload_json, '$.source_id')
                 AND (
                   json_extract(evidence_record.payload_json, '$.article_id') IS NULL
                   OR json_extract(provenance.value, '$.article_id') =
                      json_extract(evidence_record.payload_json, '$.article_id')
                 )
                 AND (
                   json_extract(evidence_record.payload_json, '$.observation_id') IS NULL
                   OR json_extract(provenance.value, '$.observation_id') =
                      json_extract(evidence_record.payload_json, '$.observation_id')
                 )
             )
         ) THEN RAISE(ABORT, 'vocabulary source evidence must be current and linked to an object claim') END;
       END
```

### `learning_object_question_answer_evidence_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') IN ( 'comprehension_question', 'analysis_question', 'mcq_question' )`
- **Lexically referenced tables:** `json_each`, `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_question_answer_evidence_insertBEFORE INSERT ON learning_objects
       WHEN json_extract(NEW.payload_json, '$.kind') IN (
           'comprehension_question', 'analysis_question', 'mcq_question'
       )
       BEGIN
         SELECT CASE WHEN json_array_length(NEW.payload_json, '$.content.answer_evidence') IS NULL
           OR json_array_length(NEW.payload_json, '$.content.answer_evidence') = 0
         THEN RAISE(ABORT, 'question answer evidence is required') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_ref
           GROUP BY json_extract(answer_ref.value, '$.claim_id'),
                    json_extract(answer_ref.value, '$.evidence_id')
           HAVING COUNT(*) > 1
         ) THEN RAISE(ABORT, 'question answer evidence references must be unique') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_claim_ids') answer_claim
           WHERE NOT EXISTS (
             SELECT 1 FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_evidence
             WHERE json_extract(answer_evidence.value, '$.claim_id') = answer_claim.value
           )
         ) THEN RAISE(ABORT, 'question answer evidence must cover every answer claim') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_ref
           LEFT JOIN domain_records claim_record
             ON claim_record.record_type = 'claim'
            AND claim_record.id = json_extract(answer_ref.value, '$.claim_id')
           LEFT JOIN domain_records evidence_record
             ON evidence_record.record_type = 'evidence'
            AND evidence_record.id = json_extract(answer_ref.value, '$.evidence_id')
           WHERE claim_record.id IS NULL OR evidence_record.id IS NULL
             OR json_type(answer_ref.value, '$.evidence_revision') IS NOT 'integer'
             OR NOT EXISTS (
               SELECT 1 FROM json_each(NEW.payload_json, '$.content.answer_claim_ids') answer_claim
               WHERE answer_claim.value = claim_record.id
             )
             OR NOT EXISTS (
               SELECT 1 FROM json_each(NEW.payload_json, '$.claim_ids') object_claim
               WHERE object_claim.value = claim_record.id
             )
             OR json_extract(evidence_record.payload_json, '$.claim_id') IS NOT claim_record.id
             OR evidence_record.revision IS NOT json_extract(answer_ref.value, '$.evidence_revision')
             OR json_extract(evidence_record.payload_json, '$.relationship') IS NOT 'supports'
             OR json_extract(evidence_record.payload_json, '$.locator.uri') IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.evidence_ids') claim_evidence
               WHERE claim_evidence.value = evidence_record.id
             )
             OR NOT EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.provenance') provenance
               WHERE json_extract(provenance.value, '$.source_id') =
                     json_extract(evidence_record.payload_json, '$.source_id')
                 AND (
                   json_extract(evidence_record.payload_json, '$.article_id') IS NULL
                   OR json_extract(provenance.value, '$.article_id') =
                      json_extract(evidence_record.payload_json, '$.article_id')
                 )
                 AND (
                   json_extract(evidence_record.payload_json, '$.observation_id') IS NULL
                   OR json_extract(provenance.value, '$.observation_id') =
                      json_extract(evidence_record.payload_json, '$.observation_id')
                 )
             )
         ) THEN RAISE(ABORT, 'question answer evidence must be current and linked to its answer claim') END;
       END
```

### `learning_object_question_answer_evidence_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE OF payload_json
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') IN ( 'comprehension_question', 'analysis_question', 'mcq_question' )`
- **Lexically referenced tables:** `OF`, `json_each`, `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_question_answer_evidence_updateBEFORE UPDATE OF payload_json ON learning_objects
       WHEN json_extract(NEW.payload_json, '$.kind') IN (
           'comprehension_question', 'analysis_question', 'mcq_question'
       )
       BEGIN
         SELECT CASE WHEN json_array_length(NEW.payload_json, '$.content.answer_evidence') IS NULL
           OR json_array_length(NEW.payload_json, '$.content.answer_evidence') = 0
         THEN RAISE(ABORT, 'question answer evidence is required') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_ref
           GROUP BY json_extract(answer_ref.value, '$.claim_id'),
                    json_extract(answer_ref.value, '$.evidence_id')
           HAVING COUNT(*) > 1
         ) THEN RAISE(ABORT, 'question answer evidence references must be unique') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_claim_ids') answer_claim
           WHERE NOT EXISTS (
             SELECT 1 FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_evidence
             WHERE json_extract(answer_evidence.value, '$.claim_id') = answer_claim.value
           )
         ) THEN RAISE(ABORT, 'question answer evidence must cover every answer claim') END;
         SELECT CASE WHEN EXISTS (
           SELECT 1
           FROM json_each(NEW.payload_json, '$.content.answer_evidence') answer_ref
           LEFT JOIN domain_records claim_record
             ON claim_record.record_type = 'claim'
            AND claim_record.id = json_extract(answer_ref.value, '$.claim_id')
           LEFT JOIN domain_records evidence_record
             ON evidence_record.record_type = 'evidence'
            AND evidence_record.id = json_extract(answer_ref.value, '$.evidence_id')
           WHERE claim_record.id IS NULL OR evidence_record.id IS NULL
             OR json_type(answer_ref.value, '$.evidence_revision') IS NOT 'integer'
             OR NOT EXISTS (
               SELECT 1 FROM json_each(NEW.payload_json, '$.content.answer_claim_ids') answer_claim
               WHERE answer_claim.value = claim_record.id
             )
             OR NOT EXISTS (
               SELECT 1 FROM json_each(NEW.payload_json, '$.claim_ids') object_claim
               WHERE object_claim.value = claim_record.id
             )
             OR json_extract(evidence_record.payload_json, '$.claim_id') IS NOT claim_record.id
             OR evidence_record.revision IS NOT json_extract(answer_ref.value, '$.evidence_revision')
             OR json_extract(evidence_record.payload_json, '$.relationship') IS NOT 'supports'
             OR json_extract(evidence_record.payload_json, '$.locator.uri') IS NULL
             OR NOT EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.evidence_ids') claim_evidence
               WHERE claim_evidence.value = evidence_record.id
             )
             OR NOT EXISTS (
               SELECT 1 FROM json_each(claim_record.payload_json, '$.provenance') provenance
               WHERE json_extract(provenance.value, '$.source_id') =
                     json_extract(evidence_record.payload_json, '$.source_id')
                 AND (
                   json_extract(evidence_record.payload_json, '$.article_id') IS NULL
                   OR json_extract(provenance.value, '$.article_id') =
                      json_extract(evidence_record.payload_json, '$.article_id')
                 )
                 AND (
                   json_extract(evidence_record.payload_json, '$.observation_id') IS NULL
                   OR json_extract(provenance.value, '$.observation_id') =
                      json_extract(evidence_record.payload_json, '$.observation_id')
                 )
             )
         ) THEN RAISE(ABORT, 'question answer evidence must be current and linked to its answer claim') END;
       END
```

### `pipeline_run_payload_guard_insert`

- **Table:** `pipeline_runs`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.id') IS NOT NEW.id OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012 and parity correction 0026; direct-SQL mismatch test added, pending exact-head CI
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_payload_guard_insertBEFORE INSERT ON pipeline_runs
       WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
         OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at
       BEGIN SELECT RAISE(ABORT, 'pipeline run indexed fields must match payload'); END
```

### `pipeline_run_payload_guard_update`

- **Table:** `pipeline_runs`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.id') IS NOT NEW.id OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at`
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012 and parity correction 0026; direct-SQL mismatch test added, pending exact-head CI
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_payload_guard_updateBEFORE UPDATE ON pipeline_runs
       WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
         OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at
       BEGIN SELECT RAISE(ABORT, 'pipeline run indexed fields must match payload'); END
```

### `pipeline_run_identity_guard`

- **Table:** `pipeline_runs`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migrations 0012/0026; parity correction removes unsupported attempt/retry_of immutability
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_identity_guardBEFORE UPDATE ON pipeline_runs
       BEGIN
         SELECT CASE WHEN NEW.id != OLD.id
           OR NEW.started_at != OLD.started_at
           OR json_extract(NEW.payload_json, '$.software_version') IS NOT json_extract(OLD.payload_json, '$.software_version')
           OR json_extract(NEW.payload_json, '$.config_hash') IS NOT json_extract(OLD.payload_json, '$.config_hash')
           OR json_extract(NEW.payload_json, '$.input_hashes') IS NOT json_extract(OLD.payload_json, '$.input_hashes')
           OR json_extract(NEW.payload_json, '$.initiated_by') IS NOT json_extract(OLD.payload_json, '$.initiated_by')
         THEN RAISE(ABORT, 'pipeline run identity and inputs are immutable') END;
         SELECT CASE WHEN NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'pipeline run revision must increment by one') END;
         SELECT CASE WHEN NOT (
           (OLD.status = 'queued' AND NEW.status IN ('running', 'cancelled', 'rejected'))
           OR (OLD.status = 'running' AND NEW.status IN ('succeeded', 'failed', 'cancelled'))
         ) THEN RAISE(ABORT, 'invalid pipeline run status transition') END;
       END
```

### `pipeline_runs_no_delete`

- **Table:** `pipeline_runs`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012; direct-SQL rejection verified in CI run #1972
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_runs_no_deleteBEFORE DELETE ON pipeline_runs
       BEGIN SELECT RAISE(ABORT, 'pipeline run records cannot be deleted'); END
```

### `pipeline_run_revision_no_update`

- **Table:** `pipeline_run_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012; direct-SQL rejection verified in CI run #1972
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_revision_no_updateBEFORE UPDATE ON pipeline_run_revisions
       BEGIN SELECT RAISE(ABORT, 'pipeline run revisions are append-only'); END
```

### `pipeline_run_revision_no_delete`

- **Table:** `pipeline_run_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012; direct-SQL rejection verified in CI run #1972
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_revision_no_deleteBEFORE DELETE ON pipeline_run_revisions
       BEGIN SELECT RAISE(ABORT, 'pipeline run revisions are append-only'); END
```

### `pipeline_run_revision_matches_current`

- **Table:** `pipeline_run_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `pipeline_runs`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Implemented by PostgreSQL migration 0012; forged future revision and mismatched payload tests added, latter pending exact-head CI
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_run_revision_matches_currentBEFORE INSERT ON pipeline_run_revisions
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM pipeline_runs
           WHERE id = NEW.pipeline_run_id AND revision = NEW.revision
             AND payload_json = NEW.payload_json
         ) THEN RAISE(ABORT, 'pipeline run revision must match current record') END;
       END
```

### `edition_artifacts_payload_guard`

- **Table:** `edition_artifacts`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.manifest_json, '$.manifest_id') IS NOT NEW.manifest_id OR json_extract(NEW.manifest_json, '$.edition_id') IS NOT NEW.edition_id OR json_extract(NEW.manifest_json, '$.edition_revision') IS NOT NEW.edition_revision OR json_extract(NEW.manifest_json, '$.payload_sha256') IS NOT NEW.payload_sha256`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_artifacts_payload_guardBEFORE INSERT ON edition_artifacts
       WHEN json_extract(NEW.manifest_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.manifest_json, '$.edition_id') IS NOT NEW.edition_id
         OR json_extract(NEW.manifest_json, '$.edition_revision') IS NOT NEW.edition_revision
         OR json_extract(NEW.manifest_json, '$.payload_sha256') IS NOT NEW.payload_sha256
       BEGIN SELECT RAISE(ABORT, 'stored edition artifact columns must match manifest'); END
```

### `edition_artifacts_no_update`

- **Table:** `edition_artifacts`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_artifacts_no_updateBEFORE UPDATE ON edition_artifacts
       BEGIN SELECT RAISE(ABORT, 'edition artifacts are immutable'); END
```

### `edition_artifacts_no_delete`

- **Table:** `edition_artifacts`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_artifacts_no_deleteBEFORE DELETE ON edition_artifacts
       BEGIN SELECT RAISE(ABORT, 'edition artifacts are immutable'); END
```

### `edition_decisions_payload_guard`

- **Table:** `edition_decisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.decision_id') IS NOT NEW.decision_id OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256 OR json_extract(NEW.payload_json, '$.decision') IS NOT NEW.decision OR json_extract(NEW.payload_json, '$.principal_id') IS NOT NEW.principal_id OR json_extract(NEW.payload_json, '$.decided_at') IS NOT NEW.decided_at OR json_extract(NEW.payload_json, '$.role') IS NOT 'edition_approver' OR json_extract(NEW.payload_json, '$.scope') IS NOT 'staging_candidate_review' OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0 OR NOT EXISTS ( SELECT 1 FROM edition_artifacts artifact WHERE artifact.manifest_id = NEW.manifest_id AND artifact.edition_id = NEW.edition_id AND artifact.edition_revision = NEW.edition_revision AND artifact.payload_sha256 = NEW.payload_sha256 ) OR NOT EXISTS ( SELECT 1 FROM benchmark_principals principal WHERE principal.principal_id = NEW.principal_id AND principal.active = 1 AND principal.principal_kind = 'human' AND EXISTS ( SELECT 1 FROM json_each(principal.roles_json) role WHERE role.value = 'edition_approver' ) )`
- **Lexically referenced tables:** `edition_artifacts`, `benchmark_principals`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_decisions_payload_guardBEFORE INSERT ON edition_decisions
       WHEN json_extract(NEW.payload_json, '$.decision_id') IS NOT NEW.decision_id
         OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id
         OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision
         OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256
         OR json_extract(NEW.payload_json, '$.decision') IS NOT NEW.decision
         OR json_extract(NEW.payload_json, '$.principal_id') IS NOT NEW.principal_id
         OR json_extract(NEW.payload_json, '$.decided_at') IS NOT NEW.decided_at
         OR json_extract(NEW.payload_json, '$.role') IS NOT 'edition_approver'
         OR json_extract(NEW.payload_json, '$.scope') IS NOT 'staging_candidate_review'
         OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0
         OR NOT EXISTS (
           SELECT 1 FROM edition_artifacts artifact
           WHERE artifact.manifest_id = NEW.manifest_id
             AND artifact.edition_id = NEW.edition_id
             AND artifact.edition_revision = NEW.edition_revision
             AND artifact.payload_sha256 = NEW.payload_sha256
         )
         OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals principal
           WHERE principal.principal_id = NEW.principal_id
             AND principal.active = 1
             AND principal.principal_kind = 'human'
             AND EXISTS (
               SELECT 1 FROM json_each(principal.roles_json) role
               WHERE role.value = 'edition_approver'
             )
         )
       BEGIN SELECT RAISE(ABORT, 'edition decision must bind an exact artifact and active human approver'); END
```

### `edition_decisions_no_update`

- **Table:** `edition_decisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_decisions_no_updateBEFORE UPDATE ON edition_decisions
       BEGIN SELECT RAISE(ABORT, 'edition decisions are append-only'); END
```

### `edition_decisions_no_delete`

- **Table:** `edition_decisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_decisions_no_deleteBEFORE DELETE ON edition_decisions
       BEGIN SELECT RAISE(ABORT, 'edition decisions are append-only'); END
```

### `edition_domain_records_no_published_insert`

- **Table:** `domain_records`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NEW.record_type = 'edition' AND json_extract(NEW.payload_json, '$.status') = 'published'`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_domain_records_no_published_insertBEFORE INSERT ON domain_records
       WHEN NEW.record_type = 'edition'
         AND json_extract(NEW.payload_json, '$.status') = 'published'
       BEGIN SELECT RAISE(ABORT, 'published editions require the controlled release workflow'); END
```

### `edition_domain_records_no_update`

- **Table:** `domain_records`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `OLD.record_type = 'edition' OR NEW.record_type = 'edition'`
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_domain_records_no_updateBEFORE UPDATE ON domain_records
       WHEN OLD.record_type = 'edition' OR NEW.record_type = 'edition'
       BEGIN SELECT RAISE(ABORT, 'edition snapshots are immutable'); END
```

### `edition_domain_records_no_delete`

- **Table:** `domain_records`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** `OLD.record_type = 'edition'`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_domain_records_no_deleteBEFORE DELETE ON domain_records
       WHEN OLD.record_type = 'edition'
       BEGIN SELECT RAISE(ABORT, 'edition snapshots are immutable'); END
```

### `edition_release_authorizations_insert_guard`

- **Table:** `edition_release_authorizations`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.authorization_id') IS NOT NEW.authorization_id OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256 OR json_extract(NEW.payload_json, '$.approval_decision_id') IS NOT NEW.approval_decision_id OR json_extract(NEW.payload_json, '$.approver_principal_id') IS NOT NEW.approver_principal_id OR json_extract(NEW.payload_json, '$.authorizer_principal_id') IS NOT NEW.authorizer_principal_id OR json_extract(NEW.payload_json, '$.authorized_at') IS NOT NEW.authorized_at OR json_extract(NEW.payload_json, '$.scope') IS NOT 'controlled_release_gate' OR json_extract(NEW.payload_json, '$.release_authorized') IS NOT 1 OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0 OR NOT EXISTS ( SELECT 1 FROM edition_artifacts artifact WHERE artifact.manifest_id = NEW.manifest_id AND artifact.edition_id = NEW.edition_id AND artifact.edition_revision = NEW.edition_revision AND artifact.payload_sha256 = NEW.payload_sha256 ) OR NOT EXISTS ( SELECT 1 FROM edition_decisions decision WHERE decision.decision_id = NEW.approval_decision_id AND decision.manifest_id = NEW.manifest_id AND decision.edition_id = NEW.edition_id AND decision.edition_revision = NEW.edition_revision AND decision.payload_sha256 = NEW.payload_sha256 AND decision.decision = 'approve' AND decision.principal_id = NEW.approver_principal_id ) OR NOT EXISTS ( SELECT 1 FROM benchmark_principals principal WHERE principal.principal_id = NEW.authorizer_principal_id AND principal.active = 1 AND principal.principal_kind = 'human' AND EXISTS ( SELECT 1 FROM json_each(principal.roles_json) role WHERE role.value = 'edition_release_authorizer' ) )`
- **Lexically referenced tables:** `edition_artifacts`, `edition_decisions`, `benchmark_principals`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_release_authorizations_insert_guardBEFORE INSERT ON edition_release_authorizations
       WHEN json_extract(NEW.payload_json, '$.authorization_id') IS NOT NEW.authorization_id
         OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id
         OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision
         OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256
         OR json_extract(NEW.payload_json, '$.approval_decision_id') IS NOT NEW.approval_decision_id
         OR json_extract(NEW.payload_json, '$.approver_principal_id') IS NOT NEW.approver_principal_id
         OR json_extract(NEW.payload_json, '$.authorizer_principal_id') IS NOT NEW.authorizer_principal_id
         OR json_extract(NEW.payload_json, '$.authorized_at') IS NOT NEW.authorized_at
         OR json_extract(NEW.payload_json, '$.scope') IS NOT 'controlled_release_gate'
         OR json_extract(NEW.payload_json, '$.release_authorized') IS NOT 1
         OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0
         OR NOT EXISTS (
           SELECT 1 FROM edition_artifacts artifact
           WHERE artifact.manifest_id = NEW.manifest_id
             AND artifact.edition_id = NEW.edition_id
             AND artifact.edition_revision = NEW.edition_revision
             AND artifact.payload_sha256 = NEW.payload_sha256
         )
         OR NOT EXISTS (
           SELECT 1 FROM edition_decisions decision
           WHERE decision.decision_id = NEW.approval_decision_id
             AND decision.manifest_id = NEW.manifest_id
             AND decision.edition_id = NEW.edition_id
             AND decision.edition_revision = NEW.edition_revision
             AND decision.payload_sha256 = NEW.payload_sha256
             AND decision.decision = 'approve'
             AND decision.principal_id = NEW.approver_principal_id
         )
         OR NOT EXISTS (
           SELECT 1 FROM benchmark_principals principal
           WHERE principal.principal_id = NEW.authorizer_principal_id
             AND principal.active = 1
             AND principal.principal_kind = 'human'
             AND EXISTS (
               SELECT 1 FROM json_each(principal.roles_json) role
               WHERE role.value = 'edition_release_authorizer'
             )
         )
       BEGIN SELECT RAISE(ABORT, 'release authorization must bind an approved artifact and independent active human authorizer'); END
```

### `edition_release_authorizations_no_update`

- **Table:** `edition_release_authorizations`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_release_authorizations_no_updateBEFORE UPDATE ON edition_release_authorizations
       BEGIN SELECT RAISE(ABORT, 'edition release authorizations are append-only'); END
```

### `edition_release_authorizations_no_delete`

- **Table:** `edition_release_authorizations`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_release_authorizations_no_deleteBEFORE DELETE ON edition_release_authorizations
       BEGIN SELECT RAISE(ABORT, 'edition release authorizations are append-only'); END
```

### `edition_staging_events_insert_guard`

- **Table:** `edition_staging_events`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.event_id') IS NOT NEW.event_id OR json_extract(NEW.payload_json, '$.state_revision') IS NOT NEW.state_revision OR json_extract(NEW.payload_json, '$.channel') IS NOT 'staging' OR json_extract(NEW.payload_json, '$.operation') IS NOT NEW.operation OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256 OR json_extract(NEW.payload_json, '$.export_id') IS NOT NEW.export_id OR json_extract(NEW.payload_json, '$.export_sha256') IS NOT NEW.export_sha256 OR json_extract(NEW.payload_json, '$.previous_current_export_id') IS NOT NEW.previous_current_export_id OR json_extract(NEW.payload_json, '$.lkg_export_id') IS NOT NEW.lkg_export_id OR json_extract(NEW.payload_json, '$.operator_principal_id') IS NOT NEW.operator_principal_id OR json_extract(NEW.payload_json, '$.occurred_at') IS NOT NEW.occurred_at OR json_extract(NEW.payload_json, '$.reason') IS NOT NEW.reason OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0 OR json_extract(NEW.payload_json, '$.target') IS NOT 'staging' OR NOT EXISTS ( SELECT 1 FROM edition_artifacts a JOIN edition_decisions d ON d.manifest_id = a.manifest_id JOIN edition_release_authorizations r ON r.manifest_id = a.manifest_id WHERE a.manifest_id = NEW.manifest_id AND a.edition_id = NEW.edition_id AND a.edition_revision = NEW.edition_revision AND a.payload_sha256 = NEW.payload_sha256 AND d.decision = 'approve' AND d.decision_id = json_extract(NEW.payload_json, '$.staging_decision.decision_id') AND r.authorization_id = json_extract(NEW.payload_json, '$.release_authorization.authorization_id') AND r.approval_decision_id = d.decision_id AND r.approver_principal_id = d.principal_id AND r.authorizer_principal_id <> d.principal_id ) OR json_extract(NEW.export_package_json, '$.export_id') IS NOT NEW.export_id OR json_extract(NEW.export_package_json, '$.manifest_id') IS NOT NEW.manifest_id OR json_extract(NEW.export_package_json, '$.payload_sha256') IS NOT NEW.payload_sha256 OR json_extract(NEW.export_package_json, '$.target') IS NOT 'staging' OR json_extract(NEW.export_package_json, '$.publication_authorized') IS NOT 0 OR NOT EXISTS ( SELECT 1 FROM benchmark_principals approver WHERE approver.principal_id = json_extract(NEW.payload_json, '$.staging_decision.principal_id') AND approver.active = 1 AND approver.principal_kind = 'human' AND EXISTS (SELECT 1 FROM json_each(approver.roles_json) role WHERE role.value = 'edition_approver') ) OR NOT EXISTS ( SELECT 1 FROM benchmark_principals authorizer WHERE authorizer.principal_id = json_extract(NEW.payload_json, '$.release_authorization.authorizer_principal_id') AND authorizer.active = 1 AND authorizer.principal_kind = 'human' AND EXISTS (SELECT 1 FROM json_each(authorizer.roles_json) role WHERE role.value = 'edition_release_authorizer') ) OR NOT EXISTS ( SELECT 1 FROM benchmark_principals p WHERE p.principal_id = NEW.operator_principal_id AND p.active = 1 AND p.principal_kind = 'human' AND EXISTS (SELECT 1 FROM json_each(p.roles_json) role WHERE role.value = 'edition_staging_operator') ) OR NEW.operator_principal_id = json_extract(NEW.payload_json, '$.staging_decision.principal_id') OR NEW.operator_principal_id = json_extract(NEW.payload_json, '$.release_authorization.authorizer_principal_id') OR NEW.state_revision <> (SELECT state_revision + 1 FROM edition_staging_channel_state WHERE channel = 'staging') OR NEW.previous_current_export_id IS NOT (SELECT current_export_id FROM edition_staging_channel_state WHERE channel = 'staging') OR NEW.lkg_export_id IS NOT NEW.previous_current_export_id OR (NEW.operation = 'promote' AND EXISTS (SELECT 1 FROM edition_staging_events prior WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote')) OR (NEW.operation = 'rollback' AND NOT EXISTS ( SELECT 1 FROM edition_staging_events prior WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote' ))`
- **Lexically referenced tables:** `edition_artifacts`, `edition_decisions`, `edition_release_authorizations`, `benchmark_principals`, `json_each`, `edition_staging_channel_state`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_staging_events_insert_guardBEFORE INSERT ON edition_staging_events
       WHEN json_extract(NEW.payload_json, '$.event_id') IS NOT NEW.event_id
         OR json_extract(NEW.payload_json, '$.state_revision') IS NOT NEW.state_revision
         OR json_extract(NEW.payload_json, '$.channel') IS NOT 'staging'
         OR json_extract(NEW.payload_json, '$.operation') IS NOT NEW.operation
         OR json_extract(NEW.payload_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.payload_json, '$.edition_id') IS NOT NEW.edition_id
         OR json_extract(NEW.payload_json, '$.edition_revision') IS NOT NEW.edition_revision
         OR json_extract(NEW.payload_json, '$.payload_sha256') IS NOT NEW.payload_sha256
         OR json_extract(NEW.payload_json, '$.export_id') IS NOT NEW.export_id
         OR json_extract(NEW.payload_json, '$.export_sha256') IS NOT NEW.export_sha256
         OR json_extract(NEW.payload_json, '$.previous_current_export_id') IS NOT NEW.previous_current_export_id
         OR json_extract(NEW.payload_json, '$.lkg_export_id') IS NOT NEW.lkg_export_id
         OR json_extract(NEW.payload_json, '$.operator_principal_id') IS NOT NEW.operator_principal_id
         OR json_extract(NEW.payload_json, '$.occurred_at') IS NOT NEW.occurred_at
         OR json_extract(NEW.payload_json, '$.reason') IS NOT NEW.reason
         OR json_extract(NEW.payload_json, '$.publication_authorized') IS NOT 0
         OR json_extract(NEW.payload_json, '$.target') IS NOT 'staging'
         OR NOT EXISTS (
             SELECT 1 FROM edition_artifacts a
             JOIN edition_decisions d ON d.manifest_id = a.manifest_id
             JOIN edition_release_authorizations r ON r.manifest_id = a.manifest_id
             WHERE a.manifest_id = NEW.manifest_id
               AND a.edition_id = NEW.edition_id
               AND a.edition_revision = NEW.edition_revision
               AND a.payload_sha256 = NEW.payload_sha256
               AND d.decision = 'approve'
               AND d.decision_id = json_extract(NEW.payload_json, '$.staging_decision.decision_id')
               AND r.authorization_id = json_extract(NEW.payload_json, '$.release_authorization.authorization_id')
               AND r.approval_decision_id = d.decision_id
               AND r.approver_principal_id = d.principal_id
               AND r.authorizer_principal_id <> d.principal_id
         )
         OR json_extract(NEW.export_package_json, '$.export_id') IS NOT NEW.export_id
         OR json_extract(NEW.export_package_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.export_package_json, '$.payload_sha256') IS NOT NEW.payload_sha256
         OR json_extract(NEW.export_package_json, '$.target') IS NOT 'staging'
         OR json_extract(NEW.export_package_json, '$.publication_authorized') IS NOT 0
         OR NOT EXISTS (
             SELECT 1 FROM benchmark_principals approver
             WHERE approver.principal_id = json_extract(NEW.payload_json, '$.staging_decision.principal_id')
               AND approver.active = 1 AND approver.principal_kind = 'human'
               AND EXISTS (SELECT 1 FROM json_each(approver.roles_json) role WHERE role.value = 'edition_approver')
         )
         OR NOT EXISTS (
             SELECT 1 FROM benchmark_principals authorizer
             WHERE authorizer.principal_id = json_extract(NEW.payload_json, '$.release_authorization.authorizer_principal_id')
               AND authorizer.active = 1 AND authorizer.principal_kind = 'human'
               AND EXISTS (SELECT 1 FROM json_each(authorizer.roles_json) role WHERE role.value = 'edition_release_authorizer')
         )
         OR NOT EXISTS (
             SELECT 1 FROM benchmark_principals p
             WHERE p.principal_id = NEW.operator_principal_id
               AND p.active = 1 AND p.principal_kind = 'human'
               AND EXISTS (SELECT 1 FROM json_each(p.roles_json) role WHERE role.value = 'edition_staging_operator')
         )
         OR NEW.operator_principal_id = json_extract(NEW.payload_json, '$.staging_decision.principal_id')
         OR NEW.operator_principal_id = json_extract(NEW.payload_json, '$.release_authorization.authorizer_principal_id')
         OR NEW.state_revision <> (SELECT state_revision + 1 FROM edition_staging_channel_state WHERE channel = 'staging')
         OR NEW.previous_current_export_id IS NOT (SELECT current_export_id FROM edition_staging_channel_state WHERE channel = 'staging')
         OR NEW.lkg_export_id IS NOT NEW.previous_current_export_id
         OR (NEW.operation = 'promote' AND EXISTS (SELECT 1 FROM edition_staging_events prior WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote'))
         OR (NEW.operation = 'rollback' AND NOT EXISTS (
             SELECT 1 FROM edition_staging_events prior
             WHERE prior.export_id = NEW.export_id AND prior.operation = 'promote'
         ))
       BEGIN SELECT RAISE(ABORT, 'staging event failed immutable artifact, authority, operator, or state guard'); END
```

### `edition_staging_events_no_update`

- **Table:** `edition_staging_events`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_staging_events_no_updateBEFORE UPDATE ON edition_staging_events
       BEGIN SELECT RAISE(ABORT, 'staging event history is append-only'); END
```

### `edition_staging_events_no_delete`

- **Table:** `edition_staging_events`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_staging_events_no_deleteBEFORE DELETE ON edition_staging_events
       BEGIN SELECT RAISE(ABORT, 'staging event history is append-only'); END
```

### `edition_staging_state_update_guard`

- **Table:** `edition_staging_channel_state`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `NEW.channel IS NOT OLD.channel OR NEW.state_revision <> OLD.state_revision + 1 OR NOT EXISTS ( SELECT 1 FROM edition_staging_events e WHERE e.event_id = NEW.current_event_id AND e.state_revision = NEW.state_revision AND e.export_id = NEW.current_export_id AND e.lkg_export_id IS NEW.lkg_export_id AND e.previous_current_export_id IS OLD.current_export_id AND NEW.lkg_event_id IS OLD.current_event_id )`
- **Lexically referenced tables:** `ON`, `edition_staging_events`, `a`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_staging_state_update_guardBEFORE UPDATE ON edition_staging_channel_state
       WHEN NEW.channel IS NOT OLD.channel
         OR NEW.state_revision <> OLD.state_revision + 1
         OR NOT EXISTS (
             SELECT 1 FROM edition_staging_events e
             WHERE e.event_id = NEW.current_event_id
               AND e.state_revision = NEW.state_revision
               AND e.export_id = NEW.current_export_id
               AND e.lkg_export_id IS NEW.lkg_export_id
               AND e.previous_current_export_id IS OLD.current_export_id
               AND NEW.lkg_event_id IS OLD.current_event_id
         )
       BEGIN SELECT RAISE(ABORT, 'staging state must advance atomically from a matching append-only event'); END
```

### `edition_staging_state_no_delete`

- **Table:** `edition_staging_channel_state`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER edition_staging_state_no_deleteBEFORE DELETE ON edition_staging_channel_state
       BEGIN SELECT RAISE(ABORT, 'staging channel state cannot be deleted'); END
```

### `audit_events_no_update`

- **Table:** `audit_events`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER audit_events_no_updateBEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
```

### `audit_events_no_delete`

- **Table:** `audit_events`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER audit_events_no_deleteBEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END
```

### `grant_revisions_no_update`

- **Table:** `permission_grant_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER grant_revisions_no_updateBEFORE UPDATE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END
```

### `grant_revisions_no_delete`

- **Table:** `permission_grant_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER grant_revisions_no_deleteBEFORE DELETE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END
```

### `domain_revisions_no_update`

- **Table:** `domain_record_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER domain_revisions_no_updateBEFORE UPDATE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END
```

### `domain_revisions_no_delete`

- **Table:** `domain_record_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER domain_revisions_no_deleteBEFORE DELETE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END
```

### `collection_observations_no_update`

- **Table:** `collection_observations`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER collection_observations_no_updateBEFORE UPDATE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END
```

### `collection_observations_no_delete`

- **Table:** `collection_observations`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER collection_observations_no_deleteBEFORE DELETE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END
```

### `ingestion_decisions_validate_article`

- **Table:** `ingestion_decisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `domain_records`, `collection_observations`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_decisions_validate_articleBEFORE INSERT ON ingestion_decisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'article' AND id = NEW.article_id
                  AND json_extract(payload_json, '$.source_id') = NEW.source_id
                  AND json_extract(payload_json, '$.canonical_uri') = NEW.canonical_uri
            ) THEN RAISE(ABORT, 'ingestion decision article/source/URI mismatch') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM collection_observations
                WHERE observation_id = NEW.observation_id AND source_id = NEW.source_id
            ) THEN RAISE(ABORT, 'ingestion decision observation/source mismatch') END;
            SELECT CASE WHEN NEW.duplicate_of_article_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'article' AND id = NEW.duplicate_of_article_id
                  AND json_extract(payload_json, '$.source_id') != NEW.source_id
            ) THEN RAISE(ABORT, 'ingestion duplicate target must be a different source article') END;
        END
```

### `ingestion_decisions_no_update`

- **Table:** `ingestion_decisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_decisions_no_updateBEFORE UPDATE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END
```

### `ingestion_decisions_no_delete`

- **Table:** `ingestion_decisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_decisions_no_deleteBEFORE DELETE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END
```

### `ingestion_current_validate`

- **Table:** `ingestion_current`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `ingestion_decisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_current_validateBEFORE INSERT ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END
```

### `ingestion_current_validate_update`

- **Table:** `ingestion_current`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `ingestion_decisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_current_validate_updateBEFORE UPDATE ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END
```

### `ingestion_current_no_delete`

- **Table:** `ingestion_current`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER ingestion_current_no_deleteBEFORE DELETE ON ingestion_current
        BEGIN SELECT RAISE(ABORT, 'current ingestion state cannot be deleted'); END
```

### `story_decisions_no_update`

- **Table:** `story_decisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER story_decisions_no_updateBEFORE UPDATE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END
```

### `story_decisions_no_delete`

- **Table:** `story_decisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER story_decisions_no_deleteBEFORE DELETE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END
```

### `story_decision_applications_no_update`

- **Table:** `story_decision_applications`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER story_decision_applications_no_updateBEFORE UPDATE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END
```

### `story_decision_applications_no_delete`

- **Table:** `story_decision_applications`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER story_decision_applications_no_deleteBEFORE DELETE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END
```

### `timeline_events_validate_insert`

- **Table:** `timeline_events`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `domain_records`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER timeline_events_validate_insertBEFORE INSERT ON timeline_events
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'timeline event requires an active registered story') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR json_array_length(NEW.payload_json, '$.claim_ids') = 0
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'timeline event claims must belong to its story') END;
        END
```

### `timeline_events_no_update`

- **Table:** `timeline_events`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER timeline_events_no_updateBEFORE UPDATE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
```

### `timeline_events_no_delete`

- **Table:** `timeline_events`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER timeline_events_no_deleteBEFORE DELETE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END
```

### `perspectives_validate_insert`

- **Table:** `perspectives`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `domain_records`, `sources`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER perspectives_validate_insertBEFORE INSERT ON perspectives
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'perspective requires an active registered story') END;
            SELECT CASE WHEN NEW.source_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM sources WHERE id = NEW.source_id
            ) THEN RAISE(ABORT, 'perspective source is not registered') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.kind') = 'publisher_framing'
                AND (NEW.source_id IS NULL OR NEW.article_id IS NULL)
            THEN RAISE(ABORT, 'publisher framing requires its source and article') END;
            SELECT CASE WHEN NEW.article_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM domain_records article
                WHERE article.record_type = 'article' AND article.id = NEW.article_id
                  AND json_extract(article.payload_json, '$.source_id') = NEW.source_id
                  AND EXISTS (
                    SELECT 1 FROM domain_records story
                    WHERE story.record_type = 'story' AND story.id = NEW.story_id
                      AND EXISTS (
                        SELECT 1 FROM json_each(story.payload_json, '$.article_ids') member
                        WHERE member.value = NEW.article_id
                      )
                  )
            ) THEN RAISE(ABORT, 'perspective article must belong to its source and story') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('approved', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed perspective requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('unreviewed', 'review_required')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed perspective cannot contain review metadata') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'perspective claims must belong to its story') END;
        END
```

### `perspectives_no_update`

- **Table:** `perspectives`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER perspectives_no_updateBEFORE UPDATE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END
```

### `perspectives_no_delete`

- **Table:** `perspectives`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER perspectives_no_deleteBEFORE DELETE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END
```

### `claim_relations_validate_insert`

- **Table:** `claim_relations`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER claim_relations_validate_insertBEFORE INSERT ON claim_relations
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'claim relation requires an active registered story') END;
            SELECT CASE WHEN NEW.claim_id = NEW.related_claim_id
                THEN RAISE(ABORT, 'claim relation must connect two different claims') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM domain_records
                WHERE record_type = 'claim' AND id IN (NEW.claim_id, NEW.related_claim_id)
                  AND json_extract(payload_json, '$.story_id') = NEW.story_id
            ) != 2
            THEN RAISE(ABORT, 'claim relation endpoints must belong to its story') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('accepted', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed claim relation requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') = 'unreviewed'
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed claim relation cannot contain review metadata') END;
        END
```

### `claim_relations_no_update`

- **Table:** `claim_relations`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER claim_relations_no_updateBEFORE UPDATE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END
```

### `claim_relations_no_delete`

- **Table:** `claim_relations`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER claim_relations_no_deleteBEFORE DELETE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END
```

### `timeline_events_review_validate_insert`

- **Table:** `timeline_events`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER timeline_events_review_validate_insertBEFORE INSERT ON timeline_events
        BEGIN
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('approved', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed timeline event requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('unreviewed', 'review_required')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed timeline event cannot contain review metadata') END;
            SELECT CASE WHEN json_extract(NEW.payload_json, '$.review_status') IN ('approved', 'rejected')
                AND julianday(json_extract(NEW.payload_json, '$.reviewed_at')) < julianday(NEW.created_at)
            THEN RAISE(ABORT, 'timeline event review cannot predate its creation') END;
        END
```

### `learning_objects_variant_group_validate_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NEW.variant_group_id IS NOT NULL`
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_variant_group_validate_insertBEFORE INSERT ON learning_objects
        WHEN NEW.variant_group_id IS NOT NULL
        BEGIN
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM learning_objects existing
                WHERE existing.variant_group_id = NEW.variant_group_id
                  AND (existing.story_id != NEW.story_id OR existing.kind != NEW.kind)
            ) THEN RAISE(ABORT, 'learning object variants must share the same story and kind') END;
        END
```

### `learning_objects_variant_group_validate_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `NEW.variant_group_id IS NOT NULL`
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_variant_group_validate_updateBEFORE UPDATE ON learning_objects
        WHEN NEW.variant_group_id IS NOT NULL
        BEGIN
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM learning_objects existing
                WHERE existing.variant_group_id = NEW.variant_group_id
                  AND existing.id != NEW.id
                  AND (existing.story_id != NEW.story_id OR existing.kind != NEW.kind)
            ) THEN RAISE(ABORT, 'learning object variants must share the same story and kind') END;
        END
```

### `learning_objects_validate_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `domain_records`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_validate_insertBEFORE INSERT ON learning_objects
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'learning object requires an active registered story') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'learning object claims must belong to its story') END;
            SELECT CASE WHEN NEW.status NOT IN ('draft', 'review_required')
                THEN RAISE(ABORT, 'new learning object must start in draft or review-required state') END;
            SELECT CASE WHEN json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
            THEN RAISE(ABORT, 'new learning object cannot contain review metadata') END;
        END
```

### `learning_objects_validate_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `time`, `domain_records`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_validate_updateBEFORE UPDATE ON learning_objects
        BEGIN
            SELECT CASE WHEN NEW.id != OLD.id OR NEW.story_id != OLD.story_id
                OR NEW.kind != OLD.kind OR NEW.language != OLD.language
                OR NEW.variant_group_id IS NOT OLD.variant_group_id
                OR NEW.created_at != OLD.created_at
            THEN RAISE(ABORT, 'learning object identity is immutable') END;
            SELECT CASE WHEN NEW.revision != OLD.revision + 1
                THEN RAISE(ABORT, 'learning object revision must increment by one') END;
            SELECT CASE WHEN trim(NEW.last_actor) = '' OR trim(NEW.last_reason) = ''
                THEN RAISE(ABORT, 'learning object revision requires actor and reason') END;
            SELECT CASE WHEN julianday(NEW.updated_at) < julianday(OLD.updated_at)
                THEN RAISE(ABORT, 'learning object update time cannot move backwards') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM domain_records
                WHERE record_type = 'story' AND id = NEW.story_id
                  AND json_extract(payload_json, '$.status') != 'archived'
            ) THEN RAISE(ABORT, 'learning object requires an active registered story') END;
            SELECT CASE WHEN json_array_length(NEW.payload_json, '$.claim_ids') IS NULL
                OR (
                    SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.claim_ids') claim_ref
                    JOIN domain_records claim_record
                      ON claim_record.record_type = 'claim'
                     AND claim_record.id = claim_ref.value
                     AND json_extract(claim_record.payload_json, '$.story_id') = NEW.story_id
                ) != json_array_length(NEW.payload_json, '$.claim_ids')
            THEN RAISE(ABORT, 'learning object claims must belong to its story') END;
            SELECT CASE WHEN NEW.status IN ('approved', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed learning object requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN NEW.status IN ('approved', 'rejected')
                AND julianday(json_extract(NEW.payload_json, '$.reviewed_at')) < julianday(NEW.created_at)
            THEN RAISE(ABORT, 'learning object review cannot predate its creation') END;
            SELECT CASE WHEN NEW.status IN ('draft', 'review_required')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed learning object cannot contain review metadata') END;
        END
```

### `learning_objects_no_delete`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_no_deleteBEFORE DELETE ON learning_objects
        BEGIN SELECT RAISE(ABORT, 'learning objects cannot be deleted'); END
```

### `learning_object_revisions_validate_insert`

- **Table:** `learning_object_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `learning_objects`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_revisions_validate_insertBEFORE INSERT ON learning_object_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM learning_objects
                WHERE id = NEW.learning_object_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'learning object revision must match the current record') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'learning object revision requires actor and reason') END;
        END
```

### `learning_objects_record_insert`

- **Table:** `learning_objects`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `learning_object_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_record_insertAFTER INSERT ON learning_objects
        BEGIN
            INSERT INTO learning_object_revisions
            (learning_object_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `learning_objects_record_update`

- **Table:** `learning_objects`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `learning_object_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_record_updateAFTER UPDATE ON learning_objects
        BEGIN
            INSERT INTO learning_object_revisions
            (learning_object_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `learning_object_revisions_no_update`

- **Table:** `learning_object_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_revisions_no_updateBEFORE UPDATE ON learning_object_revisions
        BEGIN SELECT RAISE(ABORT, 'learning object revisions are append-only'); END
```

### `learning_object_revisions_no_delete`

- **Table:** `learning_object_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_object_revisions_no_deleteBEFORE DELETE ON learning_object_revisions
        BEGIN SELECT RAISE(ABORT, 'learning object revisions are append-only'); END
```

### `glossary_entries_validate_insert`

- **Table:** `glossary_entries`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entries_validate_insertBEFORE INSERT ON glossary_entries
        BEGIN
            SELECT CASE WHEN NEW.status NOT IN ('draft', 'review_required')
                THEN RAISE(ABORT, 'new glossary entry must start in draft or review-required state') END;
            SELECT CASE WHEN json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
            THEN RAISE(ABORT, 'new glossary entry cannot contain review metadata') END;
        END
```

### `glossary_entries_validate_update`

- **Table:** `glossary_entries`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `time`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entries_validate_updateBEFORE UPDATE ON glossary_entries
        BEGIN
            SELECT CASE WHEN NEW.id != OLD.id OR NEW.lemma != OLD.lemma
                OR NEW.lemma_key != OLD.lemma_key OR NEW.term_language != OLD.term_language
                OR NEW.part_of_speech != OLD.part_of_speech OR NEW.created_at != OLD.created_at
            THEN RAISE(ABORT, 'glossary entry identity is immutable') END;
            SELECT CASE WHEN NEW.revision != OLD.revision + 1
                THEN RAISE(ABORT, 'glossary entry revision must increment by one') END;
            SELECT CASE WHEN trim(NEW.last_actor) = '' OR trim(NEW.last_reason) = ''
                THEN RAISE(ABORT, 'glossary entry revision requires actor and reason') END;
            SELECT CASE WHEN julianday(NEW.updated_at) < julianday(OLD.updated_at)
                THEN RAISE(ABORT, 'glossary entry update time cannot move backwards') END;
            SELECT CASE WHEN NEW.status IN ('approved', 'rejected')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NULL
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.reviewed_by'), '')) = ''
                    OR trim(COALESCE(json_extract(NEW.payload_json, '$.review_note'), '')) = ''
                )
            THEN RAISE(ABORT, 'reviewed glossary entry requires reviewer, timestamp, and note') END;
            SELECT CASE WHEN NEW.status IN ('approved', 'rejected')
                AND julianday(json_extract(NEW.payload_json, '$.reviewed_at')) < julianday(NEW.created_at)
            THEN RAISE(ABORT, 'glossary entry review cannot predate its creation') END;
            SELECT CASE WHEN NEW.status IN ('draft', 'review_required')
                AND (
                    json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                    OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
                )
            THEN RAISE(ABORT, 'unreviewed glossary entry cannot contain review metadata') END;
        END
```

### `glossary_entries_no_delete`

- **Table:** `glossary_entries`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entries_no_deleteBEFORE DELETE ON glossary_entries
        BEGIN SELECT RAISE(ABORT, 'glossary entries cannot be deleted'); END
```

### `glossary_entries_record_insert`

- **Table:** `glossary_entries`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `glossary_entry_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entries_record_insertAFTER INSERT ON glossary_entries
        BEGIN
            INSERT INTO glossary_entry_revisions
            (glossary_entry_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `glossary_entries_record_update`

- **Table:** `glossary_entries`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `glossary_entry_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entries_record_updateAFTER UPDATE ON glossary_entries
        BEGIN
            INSERT INTO glossary_entry_revisions
            (glossary_entry_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `glossary_entry_revisions_validate_insert`

- **Table:** `glossary_entry_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `glossary_entries`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entry_revisions_validate_insertBEFORE INSERT ON glossary_entry_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM glossary_entries
                WHERE id = NEW.glossary_entry_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'glossary revision must match the current entry') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'glossary revision requires actor and reason') END;
        END
```

### `glossary_entry_revisions_no_update`

- **Table:** `glossary_entry_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entry_revisions_no_updateBEFORE UPDATE ON glossary_entry_revisions
        BEGIN SELECT RAISE(ABORT, 'glossary revisions are append-only'); END
```

### `glossary_entry_revisions_no_delete`

- **Table:** `glossary_entry_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER glossary_entry_revisions_no_deleteBEFORE DELETE ON glossary_entry_revisions
        BEGIN SELECT RAISE(ABORT, 'glossary revisions are append-only'); END
```

### `learning_objects_mcq_validate_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'mcq_question'`
- **Lexically referenced tables:** `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_mcq_validate_insertBEFORE INSERT ON learning_objects
        WHEN json_extract(NEW.payload_json, '$.kind') = 'mcq_question'
        BEGIN
            SELECT CASE WHEN COALESCE(json_array_length(NEW.payload_json, '$.content.options'), 0) < 2
                OR json_array_length(NEW.payload_json, '$.content.options') > 6
            THEN RAISE(ABORT, 'MCQ must contain between two and six options') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.content.options')
            ) != (
                SELECT COUNT(DISTINCT json_extract(option.value, '$.id'))
                FROM json_each(NEW.payload_json, '$.content.options') option
            ) THEN RAISE(ABORT, 'MCQ option IDs must be unique') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.content.options')
            ) != (
                SELECT COUNT(DISTINCT lower(trim(json_extract(option.value, '$.text'))))
                FROM json_each(NEW.payload_json, '$.content.options') option
            ) THEN RAISE(ABORT, 'MCQ option text must be unique') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM json_each(NEW.payload_json, '$.content.options') option
                WHERE json_extract(option.value, '$.id') = json_extract(NEW.payload_json, '$.content.correct_option_id')
            ) THEN RAISE(ABORT, 'MCQ correct option must reference one of its options') END;
        END
```

### `learning_objects_mcq_validate_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'mcq_question'`
- **Lexically referenced tables:** `ON`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_mcq_validate_updateBEFORE UPDATE ON learning_objects
        WHEN json_extract(NEW.payload_json, '$.kind') = 'mcq_question'
        BEGIN
            SELECT CASE WHEN COALESCE(json_array_length(NEW.payload_json, '$.content.options'), 0) < 2
                OR json_array_length(NEW.payload_json, '$.content.options') > 6
            THEN RAISE(ABORT, 'MCQ must contain between two and six options') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.content.options')
            ) != (
                SELECT COUNT(DISTINCT json_extract(option.value, '$.id'))
                FROM json_each(NEW.payload_json, '$.content.options') option
            ) THEN RAISE(ABORT, 'MCQ option IDs must be unique') END;
            SELECT CASE WHEN (
                SELECT COUNT(*) FROM json_each(NEW.payload_json, '$.content.options')
            ) != (
                SELECT COUNT(DISTINCT lower(trim(json_extract(option.value, '$.text'))))
                FROM json_each(NEW.payload_json, '$.content.options') option
            ) THEN RAISE(ABORT, 'MCQ option text must be unique') END;
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM json_each(NEW.payload_json, '$.content.options') option
                WHERE json_extract(option.value, '$.id') = json_extract(NEW.payload_json, '$.content.correct_option_id')
            ) THEN RAISE(ABORT, 'MCQ correct option must reference one of its options') END;
        END
```

### `learning_objects_glossary_validate_insert`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'vocabulary'`
- **Lexically referenced tables:** `glossary_entries`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_glossary_validate_insertBEFORE INSERT ON learning_objects
        WHEN json_extract(NEW.payload_json, '$.kind') = 'vocabulary'
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM glossary_entries glossary
                WHERE glossary.id = json_extract(NEW.payload_json, '$.content.glossary_entry_id')
                  AND json_extract(glossary.payload_json, '$.lemma') = json_extract(NEW.payload_json, '$.content.term')
                  AND json_extract(glossary.payload_json, '$.term_language') = json_extract(NEW.payload_json, '$.content.term_language')
                  AND json_extract(glossary.payload_json, '$.part_of_speech') = json_extract(NEW.payload_json, '$.content.part_of_speech')
                  AND (
                    json_extract(NEW.payload_json, '$.content.pronunciation') IS NULL
                    OR json_extract(glossary.payload_json, '$.pronunciation') IS NULL
                    OR json_extract(glossary.payload_json, '$.pronunciation') = json_extract(NEW.payload_json, '$.content.pronunciation')
                  )
                  AND EXISTS (
                    SELECT 1 FROM json_each(glossary.payload_json, '$.senses') sense
                    WHERE json_extract(sense.value, '$.language') = json_extract(NEW.payload_json, '$.language')
                      AND json_extract(sense.value, '$.definition') = json_extract(NEW.payload_json, '$.content.meaning')
                  )
            ) THEN RAISE(ABORT, 'vocabulary learning object must match a registered glossary entry') END;
            SELECT CASE WHEN NEW.status = 'approved' AND (
                SELECT status FROM glossary_entries
                WHERE id = json_extract(NEW.payload_json, '$.content.glossary_entry_id')
            ) != 'approved'
            THEN RAISE(ABORT, 'approved vocabulary requires an approved glossary entry') END;
        END
```

### `learning_objects_glossary_validate_update`

- **Table:** `learning_objects`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `json_extract(NEW.payload_json, '$.kind') = 'vocabulary'`
- **Lexically referenced tables:** `ON`, `glossary_entries`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER learning_objects_glossary_validate_updateBEFORE UPDATE ON learning_objects
        WHEN json_extract(NEW.payload_json, '$.kind') = 'vocabulary'
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM glossary_entries glossary
                WHERE glossary.id = json_extract(NEW.payload_json, '$.content.glossary_entry_id')
                  AND json_extract(glossary.payload_json, '$.lemma') = json_extract(NEW.payload_json, '$.content.term')
                  AND json_extract(glossary.payload_json, '$.term_language') = json_extract(NEW.payload_json, '$.content.term_language')
                  AND json_extract(glossary.payload_json, '$.part_of_speech') = json_extract(NEW.payload_json, '$.content.part_of_speech')
                  AND (
                    json_extract(NEW.payload_json, '$.content.pronunciation') IS NULL
                    OR json_extract(glossary.payload_json, '$.pronunciation') IS NULL
                    OR json_extract(glossary.payload_json, '$.pronunciation') = json_extract(NEW.payload_json, '$.content.pronunciation')
                  )
                  AND EXISTS (
                    SELECT 1 FROM json_each(glossary.payload_json, '$.senses') sense
                    WHERE json_extract(sense.value, '$.language') = json_extract(NEW.payload_json, '$.language')
                      AND json_extract(sense.value, '$.definition') = json_extract(NEW.payload_json, '$.content.meaning')
                  )
            ) THEN RAISE(ABORT, 'vocabulary learning object must match a registered glossary entry') END;
            SELECT CASE WHEN NEW.status = 'approved' AND (
                SELECT status FROM glossary_entries
                WHERE id = json_extract(NEW.payload_json, '$.content.glossary_entry_id')
            ) != 'approved'
            THEN RAISE(ABORT, 'approved vocabulary requires an approved glossary entry') END;
        END
```

### `benchmark_reviews_validate_insert`

- **Table:** `benchmark_reviews`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_reviews_validate_insertBEFORE INSERT ON benchmark_reviews
        BEGIN
            SELECT CASE WHEN NEW.revision != 1
                THEN RAISE(ABORT, 'benchmark review must start at revision one') END;
        END
```

### `benchmark_reviews_validate_update`

- **Table:** `benchmark_reviews`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `time`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_reviews_validate_updateBEFORE UPDATE ON benchmark_reviews
        BEGIN
            SELECT CASE WHEN NEW.review_id != OLD.review_id
                OR NEW.benchmark_id != OLD.benchmark_id
                OR NEW.benchmark_fingerprint != OLD.benchmark_fingerprint
                OR NEW.language != OLD.language
                OR NEW.created_at != OLD.created_at
                THEN RAISE(ABORT, 'benchmark review identity and fingerprint binding are immutable') END;
            SELECT CASE WHEN NEW.revision != OLD.revision + 1
                THEN RAISE(ABORT, 'benchmark review revision must increment by one') END;
            SELECT CASE WHEN julianday(NEW.updated_at) < julianday(OLD.updated_at)
                THEN RAISE(ABORT, 'benchmark review update time cannot move backwards') END;
            SELECT CASE WHEN trim(NEW.last_actor) = '' OR trim(NEW.last_reason) = ''
                THEN RAISE(ABORT, 'benchmark review revision requires actor and reason') END;
        END
```

### `benchmark_reviews_no_delete`

- **Table:** `benchmark_reviews`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_reviews_no_deleteBEFORE DELETE ON benchmark_reviews
        BEGIN SELECT RAISE(ABORT, 'benchmark reviews cannot be deleted'); END
```

### `benchmark_reviews_record_insert`

- **Table:** `benchmark_reviews`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `benchmark_review_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_reviews_record_insertAFTER INSERT ON benchmark_reviews
        BEGIN
            INSERT INTO benchmark_review_revisions
            (review_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.review_id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `benchmark_reviews_record_update`

- **Table:** `benchmark_reviews`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `benchmark_review_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_reviews_record_updateAFTER UPDATE ON benchmark_reviews
        BEGIN
            INSERT INTO benchmark_review_revisions
            (review_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.review_id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END
```

### `benchmark_review_revisions_validate_insert`

- **Table:** `benchmark_review_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `benchmark_reviews`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_revisions_validate_insertBEFORE INSERT ON benchmark_review_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM benchmark_reviews
                WHERE review_id = NEW.review_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'benchmark review revision must match the current review') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'benchmark review revision requires actor and reason') END;
        END
```

### `benchmark_review_revisions_no_update`

- **Table:** `benchmark_review_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_revisions_no_updateBEFORE UPDATE ON benchmark_review_revisions
        BEGIN SELECT RAISE(ABORT, 'benchmark review revisions are append-only'); END
```

### `benchmark_review_revisions_no_delete`

- **Table:** `benchmark_review_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_revisions_no_deleteBEFORE DELETE ON benchmark_review_revisions
        BEGIN SELECT RAISE(ABORT, 'benchmark review revisions are append-only'); END
```

### `pipeline_stage_checkpoint_initial_revision`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `pipeline_stage_checkpoint_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_initial_revisionAFTER INSERT ON pipeline_stage_checkpoints
       BEGIN
         INSERT INTO pipeline_stage_checkpoint_revisions
         (pipeline_run_id, stage_ordinal, revision, created_at, actor, reason,
          status, provider_operation_ref, output_fingerprint, metadata_json)
         VALUES (NEW.pipeline_run_id, NEW.stage_ordinal, NEW.revision, NEW.updated_at,
                 NEW.actor, NEW.reason, NEW.status, NEW.provider_operation_ref,
                 NEW.output_fingerprint, NEW.metadata_json);
       END
```

### `pipeline_stage_checkpoint_revision_guard`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_revision_guardBEFORE UPDATE ON pipeline_stage_checkpoints
       BEGIN
         SELECT CASE WHEN NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'stage checkpoint revisions must increment sequentially') END;
         SELECT CASE WHEN NEW.pipeline_run_id != OLD.pipeline_run_id
              OR NEW.stage_ordinal != OLD.stage_ordinal
              OR NEW.stage_name != OLD.stage_name
              OR NEW.input_fingerprint != OLD.input_fingerprint
              OR NEW.idempotency_key != OLD.idempotency_key
              OR NEW.provider_namespace IS NOT OLD.provider_namespace
           THEN RAISE(ABORT, 'stage checkpoint identity is immutable') END;
         SELECT CASE WHEN OLD.provider_operation_ref IS NOT NULL
              AND NEW.provider_operation_ref IS NOT OLD.provider_operation_ref
           THEN RAISE(ABORT, 'provider operation reference is immutable once recorded') END;
         SELECT CASE WHEN NOT (
           (OLD.status = 'executing' AND NEW.status IN ('succeeded', 'failed', 'indeterminate', 'blocked'))
           OR (OLD.status = 'indeterminate' AND NEW.status IN ('executing', 'succeeded', 'blocked'))
           OR (OLD.status = 'failed' AND NEW.status = 'blocked')
         ) THEN RAISE(ABORT, 'invalid stage checkpoint status transition') END;
         SELECT CASE WHEN NEW.status = 'succeeded' AND NEW.output_fingerprint IS NULL
           THEN RAISE(ABORT, 'successful checkpoint requires an output fingerprint') END;
         SELECT CASE WHEN NEW.status != 'succeeded' AND NEW.output_fingerprint IS NOT NULL
           THEN RAISE(ABORT, 'non-successful checkpoint cannot carry an output fingerprint') END;
       END
```

### `pipeline_stage_checkpoint_revision_snapshot`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `pipeline_stage_checkpoint_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_revision_snapshotAFTER UPDATE ON pipeline_stage_checkpoints
       BEGIN
         INSERT INTO pipeline_stage_checkpoint_revisions
         (pipeline_run_id, stage_ordinal, revision, created_at, actor, reason,
          status, provider_operation_ref, output_fingerprint, metadata_json)
         VALUES (NEW.pipeline_run_id, NEW.stage_ordinal, NEW.revision, NEW.updated_at,
                 NEW.actor, NEW.reason, NEW.status, NEW.provider_operation_ref,
                 NEW.output_fingerprint, NEW.metadata_json);
       END
```

### `pipeline_stage_checkpoint_metadata_guard_insert`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `json_type(NEW.metadata_json) IS NOT 'object' OR EXISTS ( SELECT 1 FROM json_each(NEW.metadata_json) WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt') OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000)) OR (key != 'attempt' AND (type != 'text' OR length(value) NOT BETWEEN 1 AND 128 OR value GLOB '*[^A-Za-z0-9_.:-]*')) )`
- **Lexically referenced tables:** `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_insertBEFORE INSERT ON pipeline_stage_checkpoints
       WHEN json_type(NEW.metadata_json) IS NOT 'object'
         OR EXISTS (
           SELECT 1 FROM json_each(NEW.metadata_json)
           WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt')
              OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000))
              OR (key != 'attempt' AND
                  (type != 'text' OR length(value) NOT BETWEEN 1 AND 128
                   OR value GLOB '*[^A-Za-z0-9_.:-]*'))
         )
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint metadata contains unsupported fields or types'); END
```

### `pipeline_stage_checkpoint_metadata_guard_update`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** `json_type(NEW.metadata_json) IS NOT 'object' OR EXISTS ( SELECT 1 FROM json_each(NEW.metadata_json) WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt') OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000)) OR (key != 'attempt' AND (type != 'text' OR length(value) NOT BETWEEN 1 AND 128 OR value GLOB '*[^A-Za-z0-9_.:-]*')) )`
- **Lexically referenced tables:** `ON`, `json_each`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_updateBEFORE UPDATE ON pipeline_stage_checkpoints
       WHEN json_type(NEW.metadata_json) IS NOT 'object'
         OR EXISTS (
           SELECT 1 FROM json_each(NEW.metadata_json)
           WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt')
              OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000))
              OR (key != 'attempt' AND
                  (type != 'text' OR length(value) NOT BETWEEN 1 AND 128
                   OR value GLOB '*[^A-Za-z0-9_.:-]*'))
         )
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint metadata contains unsupported fields or types'); END
```

### `pipeline_stage_checkpoint_revision_matches_head`

- **Table:** `pipeline_stage_checkpoint_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM pipeline_stage_checkpoints c WHERE c.pipeline_run_id = NEW.pipeline_run_id AND c.stage_ordinal = NEW.stage_ordinal AND c.revision = NEW.revision AND c.updated_at = NEW.created_at AND c.actor = NEW.actor AND c.reason = NEW.reason AND c.status = NEW.status AND c.provider_operation_ref IS NEW.provider_operation_ref AND c.output_fingerprint IS NEW.output_fingerprint AND c.metadata_json = NEW.metadata_json )`
- **Lexically referenced tables:** `pipeline_stage_checkpoints`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_revision_matches_headBEFORE INSERT ON pipeline_stage_checkpoint_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM pipeline_stage_checkpoints c
         WHERE c.pipeline_run_id = NEW.pipeline_run_id
           AND c.stage_ordinal = NEW.stage_ordinal
           AND c.revision = NEW.revision
           AND c.updated_at = NEW.created_at
           AND c.actor = NEW.actor
           AND c.reason = NEW.reason
           AND c.status = NEW.status
           AND c.provider_operation_ref IS NEW.provider_operation_ref
           AND c.output_fingerprint IS NEW.output_fingerprint
           AND c.metadata_json = NEW.metadata_json
       )
       BEGIN SELECT RAISE(ABORT, 'checkpoint revision must match its current head'); END
```

### `pipeline_stage_checkpoint_revisions_no_update`

- **Table:** `pipeline_stage_checkpoint_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_updateBEFORE UPDATE ON pipeline_stage_checkpoint_revisions
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint revisions are append-only'); END
```

### `pipeline_stage_checkpoint_revisions_no_delete`

- **Table:** `pipeline_stage_checkpoint_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_deleteBEFORE DELETE ON pipeline_stage_checkpoint_revisions
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint revisions are append-only'); END
```

### `pipeline_stage_checkpoints_no_delete`

- **Table:** `pipeline_stage_checkpoints`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_stage_checkpoints_no_deleteBEFORE DELETE ON pipeline_stage_checkpoints
       BEGIN SELECT RAISE(ABORT, 'stage checkpoints cannot be deleted'); END
```

### `pipeline_worker_lease_initial_revision`

- **Table:** `pipeline_worker_leases`
- **Timing/event:** AFTER INSERT
- **WHEN predicate:** None
- **Lexically referenced tables:** `pipeline_worker_lease_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_initial_revisionAFTER INSERT ON pipeline_worker_leases
       BEGIN
         INSERT INTO pipeline_worker_lease_revisions
         (pipeline_run_id, revision, created_at, worker_id, token_hash, fencing_token,
          status, acquired_at, heartbeat_at, expires_at, cancel_requested_at,
          cancel_requested_by, cancel_reason_code, actor, reason)
         VALUES (NEW.pipeline_run_id, NEW.revision, NEW.updated_at, NEW.worker_id,
          NEW.token_hash, NEW.fencing_token, NEW.status, NEW.acquired_at,
          NEW.heartbeat_at, NEW.expires_at, NEW.cancel_requested_at,
          NEW.cancel_requested_by, NEW.cancel_reason_code, NEW.actor, NEW.reason);
       END
```

### `pipeline_worker_lease_revision_guard`

- **Table:** `pipeline_worker_leases`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_revision_guardBEFORE UPDATE ON pipeline_worker_leases
       BEGIN
         SELECT CASE WHEN NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'worker lease revisions must increment sequentially') END;
         SELECT CASE WHEN NEW.pipeline_run_id != OLD.pipeline_run_id
           THEN RAISE(ABORT, 'worker lease run identity is immutable') END;
         SELECT CASE WHEN NOT (
           (OLD.status = 'active' AND NEW.status = 'active'
             AND NEW.worker_id = OLD.worker_id
             AND NEW.token_hash = OLD.token_hash
             AND NEW.fencing_token = OLD.fencing_token
             AND NEW.acquired_at = OLD.acquired_at
             AND NEW.heartbeat_at >= OLD.heartbeat_at
             AND NEW.expires_at > NEW.heartbeat_at)
           OR (OLD.status = 'active' AND NEW.status IN ('released', 'expired', 'cancelled')
             AND NEW.worker_id = OLD.worker_id
             AND NEW.token_hash = OLD.token_hash
             AND NEW.fencing_token = OLD.fencing_token
             AND NEW.acquired_at = OLD.acquired_at)
           OR (OLD.status IN ('released', 'expired') AND NEW.status = 'active'
             AND NEW.fencing_token = OLD.fencing_token + 1
             AND NEW.token_hash != OLD.token_hash
             AND NEW.acquired_at = NEW.heartbeat_at
             AND NEW.expires_at > NEW.heartbeat_at)
         ) THEN RAISE(ABORT, 'invalid worker lease ownership or status transition') END;
         SELECT CASE WHEN OLD.cancel_requested_at IS NOT NULL
             AND (NEW.cancel_requested_at IS NOT OLD.cancel_requested_at
               OR NEW.cancel_requested_by IS NOT OLD.cancel_requested_by
               OR NEW.cancel_reason_code IS NOT OLD.cancel_reason_code)
           THEN RAISE(ABORT, 'worker cancellation request is immutable once recorded') END;
         SELECT CASE WHEN OLD.cancel_requested_at IS NULL
             AND NEW.cancel_requested_at IS NOT NULL
             AND (NEW.status != 'active' OR NEW.cancel_requested_by IS NULL
               OR NEW.cancel_reason_code IS NULL)
           THEN RAISE(ABORT, 'cancellation request requires an active lease and complete request') END;
       END
```

### `pipeline_worker_lease_revision_snapshot`

- **Table:** `pipeline_worker_leases`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `pipeline_worker_lease_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_revision_snapshotAFTER UPDATE ON pipeline_worker_leases
       BEGIN
         INSERT INTO pipeline_worker_lease_revisions
         (pipeline_run_id, revision, created_at, worker_id, token_hash, fencing_token,
          status, acquired_at, heartbeat_at, expires_at, cancel_requested_at,
          cancel_requested_by, cancel_reason_code, actor, reason)
         VALUES (NEW.pipeline_run_id, NEW.revision, NEW.updated_at, NEW.worker_id,
          NEW.token_hash, NEW.fencing_token, NEW.status, NEW.acquired_at,
          NEW.heartbeat_at, NEW.expires_at, NEW.cancel_requested_at,
          NEW.cancel_requested_by, NEW.cancel_reason_code, NEW.actor, NEW.reason);
       END
```

### `pipeline_worker_lease_revision_matches_head`

- **Table:** `pipeline_worker_lease_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM pipeline_worker_leases l WHERE l.pipeline_run_id = NEW.pipeline_run_id AND l.revision = NEW.revision AND l.updated_at = NEW.created_at AND l.worker_id = NEW.worker_id AND l.token_hash = NEW.token_hash AND l.fencing_token = NEW.fencing_token AND l.status = NEW.status AND l.acquired_at = NEW.acquired_at AND l.heartbeat_at = NEW.heartbeat_at AND l.expires_at = NEW.expires_at AND l.cancel_requested_at IS NEW.cancel_requested_at AND l.cancel_requested_by IS NEW.cancel_requested_by AND l.cancel_reason_code IS NEW.cancel_reason_code AND l.actor = NEW.actor AND l.reason = NEW.reason )`
- **Lexically referenced tables:** `pipeline_worker_leases`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_revision_matches_headBEFORE INSERT ON pipeline_worker_lease_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM pipeline_worker_leases l
         WHERE l.pipeline_run_id = NEW.pipeline_run_id
           AND l.revision = NEW.revision AND l.updated_at = NEW.created_at
           AND l.worker_id = NEW.worker_id AND l.token_hash = NEW.token_hash
           AND l.fencing_token = NEW.fencing_token AND l.status = NEW.status
           AND l.acquired_at = NEW.acquired_at AND l.heartbeat_at = NEW.heartbeat_at
           AND l.expires_at = NEW.expires_at
           AND l.cancel_requested_at IS NEW.cancel_requested_at
           AND l.cancel_requested_by IS NEW.cancel_requested_by
           AND l.cancel_reason_code IS NEW.cancel_reason_code
           AND l.actor = NEW.actor AND l.reason = NEW.reason
       )
       BEGIN SELECT RAISE(ABORT, 'worker lease revision must match current head'); END
```

### `pipeline_worker_lease_revisions_no_update`

- **Table:** `pipeline_worker_lease_revisions`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_revisions_no_updateBEFORE UPDATE ON pipeline_worker_lease_revisions
       BEGIN SELECT RAISE(ABORT, 'worker lease revisions are append-only'); END
```

### `pipeline_worker_lease_revisions_no_delete`

- **Table:** `pipeline_worker_lease_revisions`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_lease_revisions_no_deleteBEFORE DELETE ON pipeline_worker_lease_revisions
       BEGIN SELECT RAISE(ABORT, 'worker lease revisions are append-only'); END
```

### `pipeline_worker_leases_no_delete`

- **Table:** `pipeline_worker_leases`
- **Timing/event:** BEFORE DELETE
- **WHEN predicate:** None
- **Lexically referenced tables:** None detected
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER pipeline_worker_leases_no_deleteBEFORE DELETE ON pipeline_worker_leases
       BEGIN SELECT RAISE(ABORT, 'worker leases cannot be deleted'); END
```

### `domain_record_revisions_validate_insert`

- **Table:** `domain_record_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM domain_records d WHERE d.record_type = NEW.record_type AND d.id = NEW.record_id AND d.revision = NEW.revision AND d.payload_json = NEW.payload_json ) OR length(trim(NEW.actor)) = 0 OR length(trim(NEW.reason)) = 0`
- **Lexically referenced tables:** `domain_records`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER domain_record_revisions_validate_insertBEFORE INSERT ON domain_record_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM domain_records d
         WHERE d.record_type = NEW.record_type
           AND d.id = NEW.record_id
           AND d.revision = NEW.revision
           AND d.payload_json = NEW.payload_json
       )
       OR length(trim(NEW.actor)) = 0
       OR length(trim(NEW.reason)) = 0
       BEGIN SELECT RAISE(ABORT, 'domain revision must match current head and include actor/reason'); END
```

### `permission_grant_revisions_validate_insert`

- **Table:** `permission_grant_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM permission_grants g WHERE g.id = NEW.grant_id AND ( (g.revision = NEW.revision AND g.payload_json = NEW.payload_json) OR ( g.revision + 1 = NEW.revision AND json_extract(NEW.payload_json, '$.id') IS g.id AND json_extract(NEW.payload_json, '$.source_id') IS g.source_id AND ( (g.status = 'pending' AND json_extract(NEW.payload_json, '$.status') IN ('approved', 'denied', 'suspended', 'revoked')) OR (g.status = 'approved' AND json_extract(NEW.payload_json, '$.status') IN ('suspended', 'revoked')) OR (g.status = 'suspended' AND json_extract(NEW.payload_json, '$.status') IN ('approved', 'revoked')) ) ) ) ) OR length(trim(NEW.actor)) = 0 OR length(trim(NEW.reason)) = 0`
- **Lexically referenced tables:** `permission_grants`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER permission_grant_revisions_validate_insertBEFORE INSERT ON permission_grant_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM permission_grants g
         WHERE g.id = NEW.grant_id
           AND (
             (g.revision = NEW.revision AND g.payload_json = NEW.payload_json)
             OR (
               g.revision + 1 = NEW.revision
               AND json_extract(NEW.payload_json, '$.id') IS g.id
               AND json_extract(NEW.payload_json, '$.source_id') IS g.source_id
               AND (
                 (g.status = 'pending' AND json_extract(NEW.payload_json, '$.status')
                   IN ('approved', 'denied', 'suspended', 'revoked'))
                 OR (g.status = 'approved' AND json_extract(NEW.payload_json, '$.status')
                   IN ('suspended', 'revoked'))
                 OR (g.status = 'suspended' AND json_extract(NEW.payload_json, '$.status')
                   IN ('approved', 'revoked'))
               )
             )
           )
       )
       OR length(trim(NEW.actor)) = 0
       OR length(trim(NEW.reason)) = 0
       BEGIN SELECT RAISE(ABORT, 'grant revision must match current or next legal head and include actor/reason'); END
```

### `permission_grants_revision_guard`

- **Table:** `permission_grants`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `permission_grant_revisions`, `requires`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER permission_grants_revision_guardBEFORE UPDATE ON permission_grants
       BEGIN
         SELECT CASE WHEN NEW.id IS NOT OLD.id
             OR NEW.source_id IS NOT OLD.source_id
             OR NEW.effective_from IS NOT OLD.effective_from
             OR NEW.effective_until IS NOT OLD.effective_until
             OR NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'permission grant identity is immutable and revision must increment by one') END;
         SELECT CASE WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
             OR json_extract(NEW.payload_json, '$.source_id') IS NOT NEW.source_id
             OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
           THEN RAISE(ABORT, 'permission grant payload does not match indexed fields') END;
         SELECT CASE WHEN json_remove(
               NEW.payload_json, '$.status', '$.reviewed_at', '$.reviewed_by',
               '$.revoked_at', '$.revocation_reason', '$.notes'
             ) IS NOT json_remove(
               OLD.payload_json, '$.status', '$.reviewed_at', '$.reviewed_by',
               '$.revoked_at', '$.revocation_reason', '$.notes'
             )
           THEN RAISE(ABORT, 'permission grant scope and evidence are immutable across status transitions') END;
         SELECT CASE WHEN NOT EXISTS (
             SELECT 1 FROM permission_grant_revisions r
             WHERE r.grant_id = NEW.id AND r.revision = NEW.revision
               AND r.payload_json = NEW.payload_json
           )
           THEN RAISE(ABORT, 'permission grant update requires matching revision') END;
         SELECT CASE WHEN NOT (
             (OLD.status = 'pending' AND NEW.status IN ('approved', 'denied', 'suspended', 'revoked'))
             OR (OLD.status = 'approved' AND NEW.status IN ('suspended', 'revoked'))
             OR (OLD.status = 'suspended' AND NEW.status IN ('approved', 'revoked'))
           )
           THEN RAISE(ABORT, 'invalid permission grant status transition') END;
       END
```

### `benchmark_review_assignment_revisions_validate_insert`

- **Table:** `benchmark_review_assignment_revisions`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** `NOT EXISTS ( SELECT 1 FROM benchmark_review_assignments a WHERE a.assignment_id = NEW.assignment_id AND ( (a.revision = NEW.revision AND a.payload_json = NEW.payload_json) OR ( a.revision + 1 = NEW.revision AND json_extract(NEW.payload_json, '$.assignment_id') IS a.assignment_id AND json_extract(NEW.payload_json, '$.artifact_id') IS a.artifact_id AND json_extract(NEW.payload_json, '$.artifact_fingerprint') IS a.artifact_fingerprint AND json_extract(NEW.payload_json, '$.reviewer_id') IS a.reviewer_id AND json_extract(NEW.payload_json, '$.assigned_by') IS a.assigned_by AND json_extract(NEW.payload_json, '$.language') IS a.language AND ( (a.status = 'assigned' AND json_extract(NEW.payload_json, '$.status') IN ('accepted', 'cancelled', 'expired')) OR (a.status = 'accepted' AND json_extract(NEW.payload_json, '$.status') IN ('submitted', 'cancelled', 'expired')) ) ) ) ) OR length(trim(NEW.actor)) = 0 OR length(trim(NEW.reason)) = 0`
- **Lexically referenced tables:** `benchmark_review_assignments`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_revisions_validate_insertBEFORE INSERT ON benchmark_review_assignment_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM benchmark_review_assignments a
         WHERE a.assignment_id = NEW.assignment_id
           AND (
             (a.revision = NEW.revision AND a.payload_json = NEW.payload_json)
             OR (
               a.revision + 1 = NEW.revision
               AND json_extract(NEW.payload_json, '$.assignment_id') IS a.assignment_id
               AND json_extract(NEW.payload_json, '$.artifact_id') IS a.artifact_id
               AND json_extract(NEW.payload_json, '$.artifact_fingerprint') IS a.artifact_fingerprint
               AND json_extract(NEW.payload_json, '$.reviewer_id') IS a.reviewer_id
               AND json_extract(NEW.payload_json, '$.assigned_by') IS a.assigned_by
               AND json_extract(NEW.payload_json, '$.language') IS a.language
               AND (
                 (a.status = 'assigned' AND json_extract(NEW.payload_json, '$.status')
                   IN ('accepted', 'cancelled', 'expired'))
                 OR (a.status = 'accepted' AND json_extract(NEW.payload_json, '$.status')
                   IN ('submitted', 'cancelled', 'expired'))
               )
             )
           )
       )
       OR length(trim(NEW.actor)) = 0
       OR length(trim(NEW.reason)) = 0
       BEGIN SELECT RAISE(ABORT, 'assignment revision must match current or next legal head and include actor/reason'); END
```

### `benchmark_review_assignment_status_transition_guard`

- **Table:** `benchmark_review_assignments`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `benchmark_principals`, `json_each`, `benchmark_reviews`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER benchmark_review_assignment_status_transition_guardBEFORE UPDATE ON benchmark_review_assignments
       BEGIN
         SELECT CASE WHEN NEW.status IS NOT OLD.status AND (
             length(trim(NEW.last_actor)) = 0
             OR length(trim(NEW.last_reason)) = 0
             OR json_extract(NEW.payload_json, '$.last_transition.actor') IS NOT NEW.last_actor
             OR json_extract(NEW.payload_json, '$.last_transition.reason') IS NOT NEW.last_reason
           )
           THEN RAISE(ABORT, 'assignment transition receipt must match actor and reason') END;
         SELECT CASE WHEN NEW.status IS NOT OLD.status AND NOT (
             (OLD.status = 'assigned' AND NEW.status IN ('accepted', 'cancelled', 'expired'))
             OR (OLD.status = 'accepted' AND NEW.status IN ('submitted', 'cancelled', 'expired'))
           )
           THEN RAISE(ABORT, 'invalid benchmark assignment status transition') END;
         SELECT CASE WHEN NEW.status IN ('accepted', 'submitted')
             AND NEW.last_actor IS NOT OLD.reviewer_id
           THEN RAISE(ABORT, 'reviewer must perform accept and submit transitions') END;
         SELECT CASE WHEN NEW.status = 'cancelled'
             AND NEW.last_actor NOT IN (OLD.reviewer_id, OLD.assigned_by)
           THEN RAISE(ABORT, 'only reviewer or assigner may cancel assignment') END;
         SELECT CASE WHEN NEW.status = 'expired'
             AND NEW.last_actor IS NOT OLD.assigned_by
           THEN RAISE(ABORT, 'only assigner may expire assignment') END;
         SELECT CASE WHEN NEW.status IN ('accepted', 'submitted')
             AND NOT EXISTS (
               SELECT 1 FROM benchmark_principals p
               WHERE p.principal_id = NEW.last_actor AND p.active = 1
                                  AND EXISTS (SELECT 1 FROM json_each(p.roles_json) role
                             WHERE role.value = 'benchmark_reviewer')
                 AND EXISTS (SELECT 1 FROM json_each(p.languages_json) language
                             WHERE language.value = NEW.language)
             )
           THEN RAISE(ABORT, 'assignment reviewer must be active and language-qualified') END;
         SELECT CASE WHEN NEW.status IN ('cancelled', 'expired')
             AND NEW.last_actor = OLD.assigned_by
             AND NOT EXISTS (
               SELECT 1 FROM benchmark_principals p
               WHERE p.principal_id = NEW.last_actor AND p.active = 1
                                  AND EXISTS (SELECT 1 FROM json_each(p.roles_json) role
                             WHERE role.value = 'benchmark_curator')
             )
           THEN RAISE(ABORT, 'assignment curator must be active and qualified') END;
         SELECT CASE WHEN NEW.status = 'cancelled'
             AND NEW.last_actor = OLD.reviewer_id
             AND NOT EXISTS (
               SELECT 1 FROM benchmark_principals p
               WHERE p.principal_id = NEW.last_actor AND p.active = 1
                                  AND EXISTS (SELECT 1 FROM json_each(p.roles_json) role
                             WHERE role.value = 'benchmark_reviewer')
                 AND EXISTS (SELECT 1 FROM json_each(p.languages_json) language
                             WHERE language.value = NEW.language)
             )
           THEN RAISE(ABORT, 'assignment reviewer must be active and language-qualified') END;
         SELECT CASE WHEN NEW.status = 'accepted'
             AND (json_extract(NEW.payload_json, '$.accepted_at') IS NULL
               OR julianday(json_extract(NEW.payload_json, '$.accepted_at')) >= julianday(OLD.due_at))
           THEN RAISE(ABORT, 'assignment acceptance must occur before deadline') END;
         SELECT CASE WHEN NEW.status = 'submitted'
             AND (json_extract(NEW.payload_json, '$.accepted_at') IS NULL
               OR json_extract(NEW.payload_json, '$.closed_at') IS NULL
               OR json_extract(NEW.payload_json, '$.submitted_review_id') IS NULL
               OR julianday(json_extract(NEW.payload_json, '$.closed_at')) >= julianday(OLD.due_at)
               OR julianday(json_extract(NEW.payload_json, '$.closed_at')) < julianday(json_extract(NEW.payload_json, '$.accepted_at'))
               OR NOT EXISTS (
                 SELECT 1 FROM benchmark_reviews r
                 WHERE r.review_id = json_extract(NEW.payload_json, '$.submitted_review_id')
                   AND r.benchmark_id = OLD.artifact_id
                   AND r.benchmark_fingerprint = OLD.artifact_fingerprint
                   AND r.language = OLD.language
                   AND json_extract(r.payload_json, '$.reviewer.reviewer_id') = OLD.reviewer_id
               ))
           THEN RAISE(ABORT, 'submitted assignment requires a timely review for its exact assignee and artifact') END;
         SELECT CASE WHEN NEW.status IN ('cancelled', 'expired')
             AND (json_extract(NEW.payload_json, '$.closed_at') IS NULL
               OR length(trim(COALESCE(json_extract(NEW.payload_json, '$.closure_reason'), ''))) = 0)
           THEN RAISE(ABORT, 'closed assignment requires timestamp and reason') END;
         SELECT CASE WHEN NEW.status = 'expired'
             AND julianday(json_extract(NEW.payload_json, '$.closed_at')) < julianday(OLD.due_at)
           THEN RAISE(ABORT, 'assignment cannot expire before its deadline') END;
         SELECT CASE WHEN NEW.status = 'cancelled' AND OLD.status = 'accepted'
             AND julianday(json_extract(NEW.payload_json, '$.closed_at')) < julianday(json_extract(OLD.payload_json, '$.accepted_at'))
           THEN RAISE(ABORT, 'assignment cancellation cannot predate acceptance') END;
       END
```

### `domain_records_revision_guard`

- **Table:** `domain_records`
- **Timing/event:** BEFORE UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER domain_records_revision_guardBEFORE UPDATE ON domain_records
       BEGIN
         SELECT CASE WHEN NEW.record_type IS NOT OLD.record_type
             OR NEW.id IS NOT OLD.id
             OR NEW.created_at IS NOT OLD.created_at
           THEN RAISE(ABORT, 'domain record identity and creation time are immutable') END;
         SELECT CASE WHEN NEW.revision != OLD.revision + 1
           THEN RAISE(ABORT, 'domain record revision must increment by one') END;
         SELECT CASE WHEN length(trim(NEW.last_actor)) = 0
             OR length(trim(NEW.last_reason)) = 0
             OR length(trim(NEW.last_revision_at)) = 0
           THEN RAISE(ABORT, 'domain record revision requires actor, reason and timestamp') END;
         SELECT CASE WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
           THEN RAISE(ABORT, 'domain record payload ID must match indexed identity') END;
         SELECT CASE WHEN julianday(NEW.last_revision_at) < julianday(OLD.last_revision_at)
           THEN RAISE(ABORT, 'domain record revision timestamp cannot move backwards') END;
         SELECT CASE WHEN OLD.record_type = 'article'
             AND (
               json_extract(NEW.payload_json, '$.source_id') IS NOT json_extract(OLD.payload_json, '$.source_id')
               OR json_extract(NEW.payload_json, '$.canonical_uri') IS NOT json_extract(OLD.payload_json, '$.canonical_uri')
             )
           THEN RAISE(ABORT, 'article source and canonical identity are immutable') END;
         SELECT CASE WHEN OLD.record_type = 'evidence'
             AND (
               json_extract(NEW.payload_json, '$.source_id') IS NOT json_extract(OLD.payload_json, '$.source_id')
               OR json_extract(NEW.payload_json, '$.claim_id') IS NOT json_extract(OLD.payload_json, '$.claim_id')
               OR json_extract(NEW.payload_json, '$.article_id') IS NOT json_extract(OLD.payload_json, '$.article_id')
               OR json_extract(NEW.payload_json, '$.observation_id') IS NOT json_extract(OLD.payload_json, '$.observation_id')
               OR json_extract(NEW.payload_json, '$.locator') IS NOT json_extract(OLD.payload_json, '$.locator')
             )
           THEN RAISE(ABORT, 'evidence provenance identity is immutable') END;
       END
```

### `domain_records_record_update`

- **Table:** `domain_records`
- **Timing/event:** AFTER UPDATE
- **WHEN predicate:** None
- **Lexically referenced tables:** `ON`, `domain_record_revisions`
- **Semantic review:** See the linked review document for its trigger family
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** Tracked in the relevant review document; not all cases are closed

```sql
CREATE TRIGGER domain_records_record_updateAFTER UPDATE ON domain_records
       BEGIN
         INSERT INTO domain_record_revisions
         (record_type, record_id, revision, created_at, actor, reason, payload_json)
         VALUES (NEW.record_type, NEW.id, NEW.revision, NEW.last_revision_at,
                 NEW.last_actor, NEW.last_reason, NEW.payload_json);
       END
```

### `edition_staging_events_authority_binding_guard`

- **Table:** `edition_staging_events`
- **Timing/event:** BEFORE INSERT
- **WHEN predicate:** Referenced immutable decision and release-authorization rows must bind to the exact approver and authorizer identities declared in the event payload and to the event manifest; the release authorization must reference the same decision
- **Lexically referenced tables:** `edition_decisions`, `edition_release_authorizations`
- **Semantic review:** See the schema-v36 corrective review in `SECTION_9_PUBLICATION_AUDIT_TRIGGER_REVIEW.md`
- **PostgreSQL mapping:** Pending implementation and conformance testing
- **Direct-SQL negative test:** `test_staging_event_direct_sql_rejects_authority_identity_rebinding`; v35 upgrade validation also rejects legacy mismatches

```sql
CREATE TRIGGER edition_staging_events_authority_binding_guard
BEFORE INSERT ON edition_staging_events
WHEN NOT EXISTS (
  SELECT 1
  FROM edition_decisions d
  JOIN edition_release_authorizations r
    ON r.approval_decision_id = d.decision_id
  WHERE d.decision_id = json_extract(NEW.payload_json, '$.staging_decision.decision_id')
    AND d.principal_id = json_extract(NEW.payload_json, '$.staging_decision.principal_id')
    AND d.manifest_id = NEW.manifest_id
    AND r.authorization_id = json_extract(NEW.payload_json, '$.release_authorization.authorization_id')
    AND r.authorizer_principal_id = json_extract(NEW.payload_json, '$.release_authorization.authorizer_principal_id')
    AND r.manifest_id = NEW.manifest_id
)
BEGIN
  SELECT RAISE(ABORT, 'staging event authority identities must match recorded approval and release authorization');
END
```

## Review completion criteria

A reviewer must retain evidence for:
- invariant and threat model;
- exact read/write dependencies and lock order;
- PostgreSQL constraint, trigger, stored procedure, or application transaction mapping;
- exact SQLSTATE/error mapping;
- positive and negative direct-SQL tests under the restricted application role;
- concurrency tests with independent PostgreSQL connections;
- reviewer identity, review date, implementation commit, and CI evidence.

A source SQL record is not proof of PostgreSQL implementation. The companion documents contain source-level review findings and open gaps.

