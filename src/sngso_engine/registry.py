"""SQLite-backed source registry with cross-record checks and an audit chain."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from urllib.parse import urlsplit, urlunsplit

from .canonical import canonical_json_bytes, sha256_fingerprint
from .contracts import validate_payload
from .domains import host_in_domain_allowlist, valid_domain_allowlist, valid_http_uri
from .models import (
    ArticleRecord,
    ClaimRecord,
    EditionRecord,
    EvidenceRecord,
    PermissionEvidenceRecord,
    PermissionGrantRecord,
    SourceEndpointRecord,
    SourceRecord,
    StoryDecisionRecord,
    StoryRecord,
    TimelineEventRecord,
    PerspectiveRecord,
    ClaimRelationRecord,
    LearningObjectRecord,
    GlossaryEntryRecord,
)
from .permissions import OPERATIONS, PermissionDecision, evaluate_permission as evaluate_permission_record
from .benchmark_reviews import validate_human_benchmark_review
from .benchmark_scoring import validate_benchmark_scoring
from .benchmark_agreement import benchmark_evaluator_agreement, BenchmarkAgreementError
from .benchmark_adjudication import validate_benchmark_adjudication
from .benchmark_study import validate_benchmark_study_protocol, BenchmarkStudyError
from .benchmark_study_execution import validate_benchmark_study_execution, BenchmarkStudyExecutionError
from .benchmark_acceptance_review import validate_benchmark_acceptance_review, BenchmarkAcceptanceReviewError
from .benchmark_assignments import (
    BenchmarkAssignmentError,
    transition_benchmark_assignment,
    validate_benchmark_assignment,
)


POLICY_VERSION = "permission-evaluator/1.0.0"
_SCHEMA_VERSION = 35


_MIGRATION_14 = (
    """CREATE TABLE benchmark_artifacts (
        artifact_id TEXT PRIMARY KEY,
        language TEXT NOT NULL CHECK (language IN ('bn', 'en')),
        canonicalization TEXT NOT NULL CHECK (canonicalization = 'canonical-json-v1'),
        content_fingerprint TEXT NOT NULL CHECK (content_fingerprint GLOB 'sha256:*'),
        content_json TEXT NOT NULL CHECK (json_valid(content_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0)
    )""",
    """CREATE TRIGGER benchmark_artifacts_no_update
        BEFORE UPDATE ON benchmark_artifacts
        BEGIN SELECT RAISE(ABORT, 'benchmark artifacts are immutable'); END""",
    """CREATE TRIGGER benchmark_artifacts_no_delete
        BEFORE DELETE ON benchmark_artifacts
        BEGIN SELECT RAISE(ABORT, 'benchmark artifacts are immutable'); END""",
)


_MIGRATION_15 = (
    """CREATE TABLE benchmark_principals (
        principal_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
        roles_json TEXT NOT NULL CHECK (json_valid(roles_json)),
        active INTEGER NOT NULL CHECK (active IN (0, 1)),
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL CHECK (length(trim(created_by)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0)
    )""",
    """CREATE TABLE benchmark_adjudications (
        adjudication_id TEXT PRIMARY KEY,
        benchmark_id TEXT NOT NULL,
        benchmark_fingerprint TEXT NOT NULL,
        adjudicator_id TEXT NOT NULL,
        resolved_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL
    )""",
    """CREATE TRIGGER benchmark_adjudications_no_update
        BEFORE UPDATE ON benchmark_adjudications
        BEGIN SELECT RAISE(ABORT, 'benchmark adjudications are immutable'); END""",
    """CREATE TRIGGER benchmark_adjudications_no_delete
        BEFORE DELETE ON benchmark_adjudications
        BEGIN SELECT RAISE(ABORT, 'benchmark adjudications are immutable'); END""",
)

_MIGRATION_16 = (
    """CREATE TRIGGER benchmark_adjudications_one_per_artifact
        BEFORE INSERT ON benchmark_adjudications
        WHEN EXISTS (
            SELECT 1 FROM benchmark_adjudications
            WHERE benchmark_id = NEW.benchmark_id
        )
        BEGIN SELECT RAISE(ABORT, 'benchmark artifact already has a final adjudication'); END""",
)


_MIGRATION_17 = (
    """ALTER TABLE benchmark_principals
       ADD COLUMN languages_json TEXT NOT NULL DEFAULT '[]'
       CHECK (json_valid(languages_json))""",
    """CREATE TABLE benchmark_review_assignments (
        assignment_id TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL,
        artifact_fingerprint TEXT NOT NULL,
        language TEXT NOT NULL CHECK (language IN ('bn', 'en')),
        reviewer_id TEXT NOT NULL,
        assigned_by TEXT NOT NULL,
        assigned_at TEXT NOT NULL,
        due_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('assigned', 'accepted', 'submitted', 'cancelled', 'expired')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        updated_at TEXT NOT NULL,
        last_actor TEXT NOT NULL,
        last_reason TEXT NOT NULL,
        FOREIGN KEY (artifact_id) REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
        FOREIGN KEY (reviewer_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
        FOREIGN KEY (assigned_by) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE benchmark_review_assignment_revisions (
        assignment_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (assignment_id, revision),
        FOREIGN KEY (assignment_id) REFERENCES benchmark_review_assignments(assignment_id) ON DELETE RESTRICT
    )""",
    """CREATE UNIQUE INDEX benchmark_review_assignment_one_active_per_reviewer
       ON benchmark_review_assignments(artifact_id, reviewer_id)
       WHERE status IN ('assigned', 'accepted')""",
    """CREATE INDEX benchmark_review_assignment_reviewer_status
       ON benchmark_review_assignments(reviewer_id, status, due_at)""",
    """CREATE TRIGGER benchmark_review_assignment_revision_guard
       BEFORE UPDATE ON benchmark_review_assignments
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
       END""",
    """CREATE TRIGGER benchmark_review_assignment_payload_guard_insert
       BEFORE INSERT ON benchmark_review_assignments
       WHEN json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id
         OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id
         OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'assignment payload does not match indexed fields'); END""",
    """CREATE TRIGGER benchmark_review_assignment_payload_guard_update
       BEFORE UPDATE ON benchmark_review_assignments
       WHEN json_extract(NEW.payload_json, '$.assignment_id') IS NOT NEW.assignment_id
         OR json_extract(NEW.payload_json, '$.artifact_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.artifact_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.reviewer_id') IS NOT NEW.reviewer_id
         OR json_extract(NEW.payload_json, '$.assigned_by') IS NOT NEW.assigned_by
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'assignment payload does not match indexed fields'); END""",
    """CREATE TRIGGER benchmark_review_assignments_no_delete
       BEFORE DELETE ON benchmark_review_assignments
       BEGIN SELECT RAISE(ABORT, 'benchmark review assignments cannot be deleted'); END""",
    """CREATE TRIGGER benchmark_review_assignment_initial_revision
       AFTER INSERT ON benchmark_review_assignments
       BEGIN
         INSERT INTO benchmark_review_assignment_revisions
         (assignment_id, revision, created_at, actor, reason, payload_json)
         VALUES (NEW.assignment_id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
       END""",
    """CREATE TRIGGER benchmark_review_assignment_update_requires_revision
       BEFORE UPDATE ON benchmark_review_assignments
       WHEN NOT EXISTS (
         SELECT 1 FROM benchmark_review_assignment_revisions
         WHERE assignment_id = NEW.assignment_id AND revision = NEW.revision
           AND actor = NEW.last_actor AND reason = NEW.last_reason
           AND payload_json = NEW.payload_json
       )
       BEGIN SELECT RAISE(ABORT, 'assignment update requires matching append-only revision'); END""",
    """CREATE TRIGGER benchmark_review_assignment_revisions_no_update
       BEFORE UPDATE ON benchmark_review_assignment_revisions
       BEGIN SELECT RAISE(ABORT, 'assignment revisions are append-only'); END""",
    """CREATE TRIGGER benchmark_review_assignment_revisions_no_delete
       BEFORE DELETE ON benchmark_review_assignment_revisions
       BEGIN SELECT RAISE(ABORT, 'assignment revisions are append-only'); END""",
)


_MIGRATION_18 = (
    """ALTER TABLE benchmark_principals
       ADD COLUMN principal_kind TEXT NOT NULL DEFAULT 'unverified'
       CHECK (principal_kind IN ('human', 'synthetic', 'automation', 'unverified'))""",
    """CREATE TRIGGER benchmark_principal_kind_immutable
       BEFORE UPDATE OF principal_kind ON benchmark_principals
       BEGIN SELECT RAISE(ABORT, 'benchmark principal classification is immutable'); END""",
)


_MIGRATION_19 = (
    """CREATE TABLE benchmark_review_identity_attestations (
        attestation_id TEXT PRIMARY KEY,
        review_id TEXT NOT NULL,
        review_revision INTEGER NOT NULL CHECK (review_revision >= 1),
        principal_id TEXT NOT NULL,
        issuer TEXT NOT NULL CHECK (length(trim(issuer)) > 0),
        audience TEXT NOT NULL CHECK (length(trim(audience)) > 0),
        subject TEXT NOT NULL CHECK (length(trim(subject)) > 0),
        authenticated_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        authentication_methods_json TEXT NOT NULL CHECK (json_valid(authentication_methods_json)),
        authentication_context TEXT NOT NULL CHECK (length(trim(authentication_context)) > 0),
        provider_key_id TEXT NOT NULL CHECK (length(trim(provider_key_id)) > 0),
        provider_adapter TEXT NOT NULL CHECK (length(trim(provider_adapter)) > 0),
        session_fingerprint TEXT NOT NULL CHECK (session_fingerprint GLOB 'sha256:*'),
        attested_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        UNIQUE (review_id, review_revision),
        FOREIGN KEY (review_id) REFERENCES benchmark_reviews(review_id) ON DELETE RESTRICT,
        FOREIGN KEY (principal_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT
    )""",
    """CREATE TRIGGER benchmark_review_identity_attestations_no_update
       BEFORE UPDATE ON benchmark_review_identity_attestations
       BEGIN SELECT RAISE(ABORT, 'identity attestations are immutable'); END""",
    """CREATE TRIGGER benchmark_review_identity_attestations_no_delete
       BEFORE DELETE ON benchmark_review_identity_attestations
       BEGIN SELECT RAISE(ABORT, 'identity attestations are immutable'); END""",
)


_MIGRATION_20 = (
    """CREATE TABLE benchmark_scores (
        score_id TEXT PRIMARY KEY,
        assignment_id TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        artifact_fingerprint TEXT NOT NULL,
        item_id TEXT NOT NULL,
        task_type TEXT NOT NULL,
        language TEXT NOT NULL CHECK (language IN ('bn', 'en')),
        evaluator_id TEXT NOT NULL,
        evaluated_at TEXT NOT NULL,
        weighted_score TEXT NOT NULL,
        eligible_for_comparison INTEGER NOT NULL CHECK (eligible_for_comparison IN (0, 1)),
        score_fingerprint TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        issuer TEXT NOT NULL,
        subject TEXT NOT NULL,
        provider_key_id TEXT NOT NULL,
        provider_adapter TEXT NOT NULL,
        authenticated_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        reason TEXT NOT NULL,
        FOREIGN KEY (assignment_id) REFERENCES benchmark_review_assignments(assignment_id) ON DELETE RESTRICT,
        FOREIGN KEY (artifact_id) REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
        FOREIGN KEY (evaluator_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
        UNIQUE (assignment_id, item_id)
    )""",
    """CREATE INDEX benchmark_scores_artifact_item
       ON benchmark_scores(artifact_id, item_id, language)""",
    """CREATE TRIGGER benchmark_scores_payload_guard_insert
       BEFORE INSERT ON benchmark_scores
       WHEN json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.artifact_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.artifact_fingerprint
         OR json_extract(NEW.payload_json, '$.item_id') IS NOT NEW.item_id
         OR json_extract(NEW.payload_json, '$.task_type') IS NOT NEW.task_type
         OR json_extract(NEW.payload_json, '$.language') IS NOT NEW.language
         OR json_extract(NEW.payload_json, '$.evaluator_id') IS NOT NEW.evaluator_id
         OR json_extract(NEW.payload_json, '$.evaluated_at') IS NOT NEW.evaluated_at
       BEGIN SELECT RAISE(ABORT, 'score payload does not match indexed score fields'); END""",
    """CREATE TRIGGER benchmark_scores_no_update
       BEFORE UPDATE ON benchmark_scores
       BEGIN SELECT RAISE(ABORT, 'benchmark score records are immutable'); END""",
    """CREATE TRIGGER benchmark_scores_no_delete
       BEFORE DELETE ON benchmark_scores
       BEGIN SELECT RAISE(ABORT, 'benchmark score records are append-only'); END""",
)


_MIGRATION_21 = (
    """CREATE TABLE benchmark_study_protocols (
        protocol_record_id TEXT PRIMARY KEY,
        study_id TEXT NOT NULL,
        study_version INTEGER NOT NULL CHECK (study_version >= 1),
        benchmark_id TEXT NOT NULL,
        benchmark_fingerprint TEXT NOT NULL,
        rubric_version TEXT NOT NULL,
        protocol_fingerprint TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status = 'protocol_only'),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        UNIQUE (study_id, study_version),
        FOREIGN KEY (benchmark_id) REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT
    )""",
    """CREATE INDEX benchmark_study_protocols_benchmark
       ON benchmark_study_protocols(benchmark_id, study_id, study_version)""",
    """CREATE TRIGGER benchmark_study_protocols_payload_guard
       BEFORE INSERT ON benchmark_study_protocols
       WHEN json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id
         OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version
         OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint
         OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
       BEGIN SELECT RAISE(ABORT, 'study protocol payload does not match indexed fields'); END""",
    """CREATE TRIGGER benchmark_study_protocols_version_guard
       BEFORE INSERT ON benchmark_study_protocols
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
       BEGIN SELECT RAISE(ABORT, 'study protocol versions must be sequential and preserve benchmark/rubric binding'); END""",
    """CREATE TRIGGER benchmark_study_protocols_no_update
       BEFORE UPDATE ON benchmark_study_protocols
       BEGIN SELECT RAISE(ABORT, 'study protocol versions are immutable'); END""",
    """CREATE TRIGGER benchmark_study_protocols_no_delete
       BEFORE DELETE ON benchmark_study_protocols
       BEGIN SELECT RAISE(ABORT, 'study protocol history is append-only'); END""",
)


_MIGRATION_22 = (
    """CREATE TABLE benchmark_study_executions (
        execution_id TEXT PRIMARY KEY,
        protocol_record_id TEXT NOT NULL,
        study_id TEXT NOT NULL,
        study_version INTEGER NOT NULL,
        protocol_fingerprint TEXT NOT NULL,
        benchmark_id TEXT NOT NULL,
        benchmark_fingerprint TEXT NOT NULL,
        rubric_version TEXT NOT NULL,
        execution_fingerprint TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL CHECK (status = 'analysis_reviewed'),
        methodologist_id TEXT NOT NULL,
        issuer TEXT NOT NULL CHECK (length(trim(issuer)) > 0),
        subject TEXT NOT NULL CHECK (length(trim(subject)) > 0),
        provider_key_id TEXT NOT NULL CHECK (length(trim(provider_key_id)) > 0),
        provider_adapter TEXT NOT NULL CHECK (length(trim(provider_adapter)) > 0),
        authenticated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        FOREIGN KEY (protocol_record_id) REFERENCES benchmark_study_protocols(protocol_record_id) ON DELETE RESTRICT,
        FOREIGN KEY (benchmark_id) REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
        FOREIGN KEY (methodologist_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT
    )""",
    """CREATE INDEX benchmark_study_executions_study
       ON benchmark_study_executions(study_id, study_version, created_at)""",
    """CREATE TRIGGER benchmark_study_executions_payload_guard
       BEFORE INSERT ON benchmark_study_executions
       WHEN json_extract(NEW.payload_json, '$.execution_id') IS NOT NEW.execution_id
         OR json_extract(NEW.payload_json, '$.study_id') IS NOT NEW.study_id
         OR json_extract(NEW.payload_json, '$.study_version') IS NOT NEW.study_version
         OR json_extract(NEW.payload_json, '$.protocol_fingerprint') IS NOT NEW.protocol_fingerprint
         OR json_extract(NEW.payload_json, '$.benchmark_id') IS NOT NEW.benchmark_id
         OR json_extract(NEW.payload_json, '$.benchmark_fingerprint') IS NOT NEW.benchmark_fingerprint
         OR json_extract(NEW.payload_json, '$.rubric_version') IS NOT NEW.rubric_version
         OR json_extract(NEW.payload_json, '$.analysis.methodologist_id') IS NOT NEW.methodologist_id
       BEGIN SELECT RAISE(ABORT, 'study execution payload does not match indexed fields'); END""",
    """CREATE TRIGGER benchmark_study_executions_no_update
       BEFORE UPDATE ON benchmark_study_executions
       BEGIN SELECT RAISE(ABORT, 'study execution records are immutable'); END""",
    """CREATE TRIGGER benchmark_study_executions_no_delete
       BEFORE DELETE ON benchmark_study_executions
       BEGIN SELECT RAISE(ABORT, 'study execution history is append-only'); END""",
)


_MIGRATION_23 = (
    """CREATE TABLE benchmark_acceptance_reviews (
        review_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL UNIQUE,
        protocol_record_id TEXT NOT NULL,
        study_id TEXT NOT NULL,
        study_version INTEGER NOT NULL,
        protocol_fingerprint TEXT NOT NULL,
        execution_fingerprint TEXT NOT NULL,
        acceptance_fingerprint TEXT NOT NULL UNIQUE,
        benchmark_id TEXT NOT NULL,
        benchmark_fingerprint TEXT NOT NULL,
        rubric_version TEXT NOT NULL,
        reviewer_id TEXT NOT NULL,
        reviewer_role TEXT NOT NULL CHECK (reviewer_role = 'independent_acceptance_reviewer'),
        decision TEXT NOT NULL CHECK (decision IN (
            'not_reviewed', 'needs_revision', 'rejected',
            'accepted_for_limited_use', 'accepted_for_proposed_use'
        )),
        issuer TEXT NOT NULL CHECK (length(trim(issuer)) > 0),
        subject TEXT NOT NULL CHECK (length(trim(subject)) > 0),
        provider_key_id TEXT NOT NULL CHECK (length(trim(provider_key_id)) > 0),
        provider_adapter TEXT NOT NULL CHECK (length(trim(provider_adapter)) > 0),
        authenticated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        FOREIGN KEY (execution_id) REFERENCES benchmark_study_executions(execution_id) ON DELETE RESTRICT,
        FOREIGN KEY (protocol_record_id) REFERENCES benchmark_study_protocols(protocol_record_id) ON DELETE RESTRICT,
        FOREIGN KEY (benchmark_id) REFERENCES benchmark_artifacts(artifact_id) ON DELETE RESTRICT,
        FOREIGN KEY (reviewer_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT
    )""",
    """CREATE INDEX benchmark_acceptance_reviews_study
       ON benchmark_acceptance_reviews(study_id, study_version, created_at)""",
    """CREATE TRIGGER benchmark_acceptance_reviews_payload_guard
       BEFORE INSERT ON benchmark_acceptance_reviews
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
       BEGIN SELECT RAISE(ABORT, 'acceptance review payload does not match indexed evidence'); END""",
    """CREATE TRIGGER benchmark_acceptance_reviews_no_update
       BEFORE UPDATE ON benchmark_acceptance_reviews
       BEGIN SELECT RAISE(ABORT, 'benchmark acceptance decisions are immutable'); END""",
    """CREATE TRIGGER benchmark_acceptance_reviews_no_delete
       BEFORE DELETE ON benchmark_acceptance_reviews
       BEGIN SELECT RAISE(ABORT, 'benchmark acceptance decisions are append-only'); END""",
)



_MIGRATION_24 = (
    """CREATE TRIGGER learning_object_vocabulary_source_evidence_insert
       BEFORE INSERT ON learning_objects
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
       END""",
    """CREATE TRIGGER learning_object_vocabulary_source_evidence_update
       BEFORE UPDATE OF payload_json ON learning_objects
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
       END""",
)


_MIGRATION_25 = (
    """CREATE TRIGGER learning_object_question_answer_evidence_insert
       BEFORE INSERT ON learning_objects
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
       END""",
    """CREATE TRIGGER learning_object_question_answer_evidence_update
       BEFORE UPDATE OF payload_json ON learning_objects
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
       END""",
)

_MIGRATION_26 = (
    """CREATE TABLE pipeline_runs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'rejected')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        started_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    """CREATE TABLE pipeline_run_revisions (
        pipeline_run_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (pipeline_run_id, revision),
        FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX pipeline_runs_status_started ON pipeline_runs(status, started_at)",
    """CREATE TRIGGER pipeline_run_payload_guard_insert
       BEFORE INSERT ON pipeline_runs
       WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
         OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at
       BEGIN SELECT RAISE(ABORT, 'pipeline run indexed fields must match payload'); END""",
    """CREATE TRIGGER pipeline_run_payload_guard_update
       BEFORE UPDATE ON pipeline_runs
       WHEN json_extract(NEW.payload_json, '$.id') IS NOT NEW.id
         OR json_extract(NEW.payload_json, '$.status') IS NOT NEW.status
         OR json_extract(NEW.payload_json, '$.started_at') IS NOT NEW.started_at
       BEGIN SELECT RAISE(ABORT, 'pipeline run indexed fields must match payload'); END""",
    """CREATE TRIGGER pipeline_run_identity_guard
       BEFORE UPDATE ON pipeline_runs
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
       END""",
    """CREATE TRIGGER pipeline_runs_no_delete
       BEFORE DELETE ON pipeline_runs
       BEGIN SELECT RAISE(ABORT, 'pipeline run records cannot be deleted'); END""",
    """CREATE TRIGGER pipeline_run_revision_no_update
       BEFORE UPDATE ON pipeline_run_revisions
       BEGIN SELECT RAISE(ABORT, 'pipeline run revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_run_revision_no_delete
       BEFORE DELETE ON pipeline_run_revisions
       BEGIN SELECT RAISE(ABORT, 'pipeline run revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_run_revision_matches_current
       BEFORE INSERT ON pipeline_run_revisions
       BEGIN
         SELECT CASE WHEN NOT EXISTS (
           SELECT 1 FROM pipeline_runs
           WHERE id = NEW.pipeline_run_id AND revision = NEW.revision
             AND payload_json = NEW.payload_json
         ) THEN RAISE(ABORT, 'pipeline run revision must match current record') END;
       END""",
)


_MIGRATION_27 = (
    """CREATE TABLE edition_artifacts (
        manifest_id TEXT PRIMARY KEY,
        edition_id TEXT NOT NULL,
        edition_revision INTEGER NOT NULL CHECK (edition_revision >= 1),
        payload_sha256 TEXT NOT NULL CHECK (payload_sha256 GLOB 'sha256:*'),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json)),
        verification_json TEXT NOT NULL CHECK (json_valid(verification_json)),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        UNIQUE (edition_id, edition_revision)
    )""",
    """CREATE TRIGGER edition_artifacts_payload_guard
       BEFORE INSERT ON edition_artifacts
       WHEN json_extract(NEW.manifest_json, '$.manifest_id') IS NOT NEW.manifest_id
         OR json_extract(NEW.manifest_json, '$.edition_id') IS NOT NEW.edition_id
         OR json_extract(NEW.manifest_json, '$.edition_revision') IS NOT NEW.edition_revision
         OR json_extract(NEW.manifest_json, '$.payload_sha256') IS NOT NEW.payload_sha256
       BEGIN SELECT RAISE(ABORT, 'stored edition artifact columns must match manifest'); END""",
    """CREATE TRIGGER edition_artifacts_no_update
       BEFORE UPDATE ON edition_artifacts
       BEGIN SELECT RAISE(ABORT, 'edition artifacts are immutable'); END""",
    """CREATE TRIGGER edition_artifacts_no_delete
       BEFORE DELETE ON edition_artifacts
       BEGIN SELECT RAISE(ABORT, 'edition artifacts are immutable'); END""",
)


_MIGRATION_28 = (
    """CREATE TABLE edition_decisions (
        decision_id TEXT PRIMARY KEY,
        manifest_id TEXT NOT NULL UNIQUE,
        edition_id TEXT NOT NULL,
        edition_revision INTEGER NOT NULL CHECK (edition_revision >= 1),
        payload_sha256 TEXT NOT NULL CHECK (payload_sha256 GLOB 'sha256:*'),
        decision TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
        principal_id TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        FOREIGN KEY (manifest_id) REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT
    )""",
    """CREATE TRIGGER edition_decisions_payload_guard
       BEFORE INSERT ON edition_decisions
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
       BEGIN SELECT RAISE(ABORT, 'edition decision must bind an exact artifact and active human approver'); END""",
    """CREATE TRIGGER edition_decisions_no_update
       BEFORE UPDATE ON edition_decisions
       BEGIN SELECT RAISE(ABORT, 'edition decisions are append-only'); END""",
    """CREATE TRIGGER edition_decisions_no_delete
       BEFORE DELETE ON edition_decisions
       BEGIN SELECT RAISE(ABORT, 'edition decisions are append-only'); END""",
)


_MIGRATION_29 = (
    """CREATE TRIGGER edition_domain_records_no_published_insert
       BEFORE INSERT ON domain_records
       WHEN NEW.record_type = 'edition'
         AND json_extract(NEW.payload_json, '$.status') = 'published'
       BEGIN SELECT RAISE(ABORT, 'published editions require the controlled release workflow'); END""",
    """CREATE TRIGGER edition_domain_records_no_update
       BEFORE UPDATE ON domain_records
       WHEN OLD.record_type = 'edition' OR NEW.record_type = 'edition'
       BEGIN SELECT RAISE(ABORT, 'edition snapshots are immutable'); END""",
    """CREATE TRIGGER edition_domain_records_no_delete
       BEFORE DELETE ON domain_records
       WHEN OLD.record_type = 'edition'
       BEGIN SELECT RAISE(ABORT, 'edition snapshots are immutable'); END""",
)


_MIGRATION_30 = (
    """CREATE TABLE edition_release_authorizations (
        authorization_id TEXT PRIMARY KEY,
        manifest_id TEXT NOT NULL UNIQUE,
        edition_id TEXT NOT NULL,
        edition_revision INTEGER NOT NULL CHECK (edition_revision >= 1),
        payload_sha256 TEXT NOT NULL CHECK (payload_sha256 GLOB 'sha256:*'),
        approval_decision_id TEXT NOT NULL,
        approver_principal_id TEXT NOT NULL,
        authorizer_principal_id TEXT NOT NULL,
        authorized_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        FOREIGN KEY (manifest_id) REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT,
        FOREIGN KEY (approval_decision_id) REFERENCES edition_decisions(decision_id) ON DELETE RESTRICT,
        FOREIGN KEY (approver_principal_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
        FOREIGN KEY (authorizer_principal_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT,
        CHECK (approver_principal_id <> authorizer_principal_id)
    )""",
    """CREATE TRIGGER edition_release_authorizations_insert_guard
       BEFORE INSERT ON edition_release_authorizations
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
       BEGIN SELECT RAISE(ABORT, 'release authorization must bind an approved artifact and independent active human authorizer'); END""",
    """CREATE TRIGGER edition_release_authorizations_no_update
       BEFORE UPDATE ON edition_release_authorizations
       BEGIN SELECT RAISE(ABORT, 'edition release authorizations are append-only'); END""",
    """CREATE TRIGGER edition_release_authorizations_no_delete
       BEFORE DELETE ON edition_release_authorizations
       BEGIN SELECT RAISE(ABORT, 'edition release authorizations are append-only'); END""",
)


_MIGRATION_31 = (
    """CREATE TABLE edition_staging_events (
        event_id TEXT PRIMARY KEY,
        state_revision INTEGER NOT NULL UNIQUE CHECK (state_revision >= 1),
        channel TEXT NOT NULL CHECK (channel = 'staging'),
        operation TEXT NOT NULL CHECK (operation IN ('promote', 'rollback')),
        manifest_id TEXT NOT NULL,
        edition_id TEXT NOT NULL,
        edition_revision INTEGER NOT NULL CHECK (edition_revision >= 1),
        payload_sha256 TEXT NOT NULL CHECK (payload_sha256 GLOB 'sha256:*'),
        export_id TEXT NOT NULL,
        export_sha256 TEXT NOT NULL CHECK (export_sha256 GLOB 'sha256:*'),
        previous_current_export_id TEXT,
        lkg_export_id TEXT,
        operator_principal_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        export_package_json TEXT NOT NULL CHECK (json_valid(export_package_json)),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        FOREIGN KEY (manifest_id) REFERENCES edition_artifacts(manifest_id) ON DELETE RESTRICT,
        FOREIGN KEY (operator_principal_id) REFERENCES benchmark_principals(principal_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE edition_staging_channel_state (
        channel TEXT PRIMARY KEY CHECK (channel = 'staging'),
        state_revision INTEGER NOT NULL CHECK (state_revision >= 0),
        current_event_id TEXT,
        current_export_id TEXT,
        lkg_event_id TEXT,
        lkg_export_id TEXT,
        FOREIGN KEY (current_event_id) REFERENCES edition_staging_events(event_id) ON DELETE RESTRICT,
        FOREIGN KEY (lkg_event_id) REFERENCES edition_staging_events(event_id) ON DELETE RESTRICT,
        CHECK ((current_event_id IS NULL) = (current_export_id IS NULL)),
        CHECK ((lkg_event_id IS NULL) = (lkg_export_id IS NULL))
    )""",
    """INSERT INTO edition_staging_channel_state
       (channel, state_revision, current_event_id, current_export_id, lkg_event_id, lkg_export_id)
       VALUES ('staging', 0, NULL, NULL, NULL, NULL)""",
    """CREATE TRIGGER edition_staging_events_insert_guard
       BEFORE INSERT ON edition_staging_events
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
       BEGIN SELECT RAISE(ABORT, 'staging event failed immutable artifact, authority, operator, or state guard'); END""",
    """CREATE TRIGGER edition_staging_events_no_update
       BEFORE UPDATE ON edition_staging_events
       BEGIN SELECT RAISE(ABORT, 'staging event history is append-only'); END""",
    """CREATE TRIGGER edition_staging_events_no_delete
       BEFORE DELETE ON edition_staging_events
       BEGIN SELECT RAISE(ABORT, 'staging event history is append-only'); END""",
    """CREATE TRIGGER edition_staging_state_update_guard
       BEFORE UPDATE ON edition_staging_channel_state
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
       BEGIN SELECT RAISE(ABORT, 'staging state must advance atomically from a matching append-only event'); END""",
    """CREATE TRIGGER edition_staging_state_no_delete
       BEFORE DELETE ON edition_staging_channel_state
       BEGIN SELECT RAISE(ABORT, 'staging channel state cannot be deleted'); END""",
)


class RegistryIntegrityError(ValueError):
    """Raised when a record references missing or incompatible registry data."""


class RegistryMigrationError(RuntimeError):
    """Raised when the database schema cannot be migrated safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _aware_datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str):
        raise RegistryIntegrityError(f"{field} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryIntegrityError(f"{field} must be a valid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RegistryIntegrityError(f"{field} must include a timezone offset")
    return parsed.astimezone(timezone.utc)



def _host_in_domains(host: str, domains: Any) -> bool:
    return host_in_domain_allowlist(host, domains)


def _json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


_MIGRATION_1 = (
    """CREATE TABLE schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )""",
    """CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        publisher_name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    """CREATE TABLE source_endpoints (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        endpoint_uri TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        UNIQUE (source_id, endpoint_uri),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_evidence (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        review_status TEXT NOT NULL,
        content_fingerprint TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_grants (
        id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        status TEXT NOT NULL,
        effective_from TEXT NOT NULL,
        effective_until TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (source_id, id),
        UNIQUE (id, source_id),
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_endpoints (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, endpoint_id),
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, source_id)
            REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_evidence (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, evidence_id),
        UNIQUE (grant_id, source_id, evidence_id),
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (evidence_id, source_id)
            REFERENCES permission_evidence(id, source_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE grant_operation_evidence (
        grant_id TEXT NOT NULL,
        source_id TEXT NOT NULL,
        operation TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        PRIMARY KEY (grant_id, operation, evidence_id),
        FOREIGN KEY (grant_id, source_id, evidence_id)
            REFERENCES grant_evidence(grant_id, source_id, evidence_id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE permission_grant_revisions (
        grant_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (grant_id, revision),
        FOREIGN KEY (grant_id) REFERENCES permission_grants(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE domain_records (
        record_type TEXT NOT NULL CHECK (record_type IN ('article', 'story', 'claim', 'evidence', 'edition')),
        id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (record_type, id)
    )""",
    """CREATE TABLE domain_record_revisions (
        record_type TEXT NOT NULL,
        record_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (record_type, record_id, revision),
        FOREIGN KEY (record_type, record_id)
            REFERENCES domain_records(record_type, id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE audit_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        policy_version TEXT,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        previous_hash TEXT,
        event_hash TEXT NOT NULL UNIQUE
    )""",
    "CREATE INDEX idx_endpoints_source ON source_endpoints(source_id)",
    "CREATE INDEX idx_evidence_source ON permission_evidence(source_id, review_status)",
    "CREATE INDEX idx_grants_source_status ON permission_grants(source_id, status)",
    "CREATE INDEX idx_audit_entity ON audit_events(entity_type, entity_id, sequence)",
    """CREATE TRIGGER audit_events_no_update
        BEFORE UPDATE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
    """CREATE TRIGGER audit_events_no_delete
        BEFORE DELETE ON audit_events
        BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END""",
    """CREATE TRIGGER grant_revisions_no_update
        BEFORE UPDATE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END""",
    """CREATE TRIGGER grant_revisions_no_delete
        BEFORE DELETE ON permission_grant_revisions
        BEGIN SELECT RAISE(ABORT, 'grant revisions are append-only'); END""",
    """CREATE TRIGGER domain_revisions_no_update
        BEFORE UPDATE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END""",
    """CREATE TRIGGER domain_revisions_no_delete
        BEFORE DELETE ON domain_record_revisions
        BEGIN SELECT RAISE(ABORT, 'domain record revisions are append-only'); END""",
)




_MIGRATION_2 = (
    """CREATE TABLE collection_observations (
        observation_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        endpoint_id TEXT NOT NULL,
        grant_id TEXT NOT NULL,
        grant_revision INTEGER NOT NULL CHECK (grant_revision >= 1),
        operation TEXT NOT NULL CHECK (operation IN ('discovery', 'metadata')),
        connector_name TEXT NOT NULL,
        connector_version TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        locator TEXT NOT NULL,
        final_locator TEXT,
        content_fingerprint TEXT,
        response_status INTEGER CHECK (response_status IS NULL OR response_status BETWEEN 100 AND 599),
        content_type TEXT,
        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
        permission_reason TEXT NOT NULL,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT,
        FOREIGN KEY (endpoint_id, source_id)
            REFERENCES source_endpoints(id, source_id) ON DELETE RESTRICT,
        FOREIGN KEY (grant_id, source_id)
            REFERENCES permission_grants(id, source_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_collection_observations_source_time ON collection_observations(source_id, observed_at)",
    "CREATE INDEX idx_collection_observations_endpoint_time ON collection_observations(endpoint_id, observed_at)",
    """CREATE TRIGGER collection_observations_no_update
        BEFORE UPDATE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END""",
    """CREATE TRIGGER collection_observations_no_delete
        BEFORE DELETE ON collection_observations
        BEGIN SELECT RAISE(ABORT, 'collection observations are append-only'); END""",
)


_MIGRATION_3 = (
    """ALTER TABLE collection_observations
       ADD COLUMN http_method TEXT NOT NULL DEFAULT 'GET'
       CHECK (http_method = 'GET')""",
)


_MIGRATION_4 = (
    """CREATE TABLE ingestion_decisions (
        decision_id TEXT PRIMARY KEY,
        observation_id TEXT NOT NULL,
        decision_revision INTEGER NOT NULL CHECK (decision_revision >= 1),
        source_id TEXT NOT NULL,
        article_id TEXT NOT NULL,
        outcome TEXT NOT NULL CHECK (outcome IN (
            'created', 'reused', 'possible_duplicate', 'confirmed_duplicate', 'distinct'
        )),
        duplicate_of_article_id TEXT,
        fingerprint TEXT CHECK (fingerprint IS NULL OR (length(fingerprint) = 71 AND substr(fingerprint, 1, 7) = 'sha256:' AND substr(fingerprint, 8) NOT GLOB '*[^0-9a-f]*')),
        canonical_uri TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
        decided_at TEXT NOT NULL,
        UNIQUE (observation_id, decision_revision),
        UNIQUE (observation_id, decision_id),
        FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    """CREATE TABLE ingestion_current (
        observation_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        FOREIGN KEY (observation_id) REFERENCES collection_observations(observation_id) ON DELETE RESTRICT,
        FOREIGN KEY (observation_id, decision_id)
            REFERENCES ingestion_decisions(observation_id, decision_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_ingestion_fingerprint ON ingestion_decisions(fingerprint, source_id, decided_at)",
    "CREATE INDEX idx_article_source_uri ON domain_records(json_extract(payload_json, '$.source_id'), json_extract(payload_json, '$.canonical_uri')) WHERE record_type = 'article'",
    """CREATE TRIGGER ingestion_decisions_validate_article
        BEFORE INSERT ON ingestion_decisions
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
        END""",
    """CREATE TRIGGER ingestion_decisions_no_update
        BEFORE UPDATE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END""",
    """CREATE TRIGGER ingestion_decisions_no_delete
        BEFORE DELETE ON ingestion_decisions
        BEGIN SELECT RAISE(ABORT, 'ingestion decisions are append-only'); END""",
    """CREATE TRIGGER ingestion_current_validate
        BEFORE INSERT ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END""",
    """CREATE TRIGGER ingestion_current_validate_update
        BEFORE UPDATE ON ingestion_current
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM ingestion_decisions
                WHERE observation_id = NEW.observation_id AND decision_id = NEW.decision_id
            ) THEN RAISE(ABORT, 'current ingestion decision does not match observation') END;
        END""",
    """CREATE TRIGGER ingestion_current_no_delete
        BEFORE DELETE ON ingestion_current
        BEGIN SELECT RAISE(ABORT, 'current ingestion state cannot be deleted'); END""",
)


_MIGRATION_5 = (
    """CREATE TABLE story_decisions (
        id TEXT PRIMARY KEY,
        operation TEXT NOT NULL CHECK (operation IN ('merge', 'split')),
        canonical_story_id TEXT NOT NULL,
        story_ids_json TEXT NOT NULL CHECK (json_valid(story_ids_json)),
        article_ids_json TEXT NOT NULL CHECK (json_valid(article_ids_json)),
        decided_by TEXT NOT NULL,
        rationale TEXT NOT NULL,
        decided_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    "CREATE INDEX idx_story_decisions_decided_at ON story_decisions(decided_at, id)",
    """CREATE TRIGGER story_decisions_no_update
        BEFORE UPDATE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END""",
    """CREATE TRIGGER story_decisions_no_delete
        BEFORE DELETE ON story_decisions
        BEGIN SELECT RAISE(ABORT, 'story decisions are append-only'); END""",
)


_MIGRATION_6 = (
    """CREATE TABLE story_decision_applications (
        application_id TEXT PRIMARY KEY,
        decision_id TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) BETWEEN 1 AND 200),
        rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 4000),
        decision_fingerprint TEXT NOT NULL CHECK (
            length(decision_fingerprint) = 71
            AND substr(decision_fingerprint, 1, 7) = 'sha256:'
            AND substr(decision_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        impact_fingerprint TEXT NOT NULL CHECK (
            length(impact_fingerprint) = 71
            AND substr(impact_fingerprint, 1, 7) = 'sha256:'
            AND substr(impact_fingerprint, 8) NOT GLOB '*[^0-9a-f]*'
        ),
        result_json TEXT NOT NULL CHECK (json_valid(result_json)),
        FOREIGN KEY (decision_id) REFERENCES story_decisions(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_story_decision_applications_applied_at ON story_decision_applications(applied_at, application_id)",
    """CREATE TRIGGER story_decision_applications_no_update
        BEFORE UPDATE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END""",
    """CREATE TRIGGER story_decision_applications_no_delete
        BEFORE DELETE ON story_decision_applications
        BEGIN SELECT RAISE(ABORT, 'story decision applications are append-only'); END""",
)


_MIGRATION_7 = (
    """CREATE TABLE timeline_events (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        occurred_sort_key TEXT,
        precision TEXT NOT NULL CHECK (precision IN (
            'instant', 'date', 'range', 'approximate', 'unknown'
        )),
        supersedes_event_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_event_id)
            REFERENCES timeline_events(story_id, id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_timeline_events_story_time ON timeline_events(story_id, occurred_sort_key, created_at, id)",
    """CREATE TRIGGER timeline_events_validate_insert
        BEFORE INSERT ON timeline_events
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
        END""",
    """CREATE TRIGGER timeline_events_no_update
        BEFORE UPDATE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END""",
    """CREATE TRIGGER timeline_events_no_delete
        BEFORE DELETE ON timeline_events
        BEGIN SELECT RAISE(ABORT, 'timeline events are append-only'); END""",
)


_MIGRATION_8 = (
    """CREATE TABLE perspectives (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        source_id TEXT,
        article_id TEXT,
        supersedes_perspective_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_perspective_id)
            REFERENCES perspectives(story_id, id) ON DELETE RESTRICT,
        FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_perspectives_story_created ON perspectives(story_id, created_at, id)",
    """CREATE TRIGGER perspectives_validate_insert
        BEFORE INSERT ON perspectives
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
        END""",
    """CREATE TRIGGER perspectives_no_update
        BEFORE UPDATE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END""",
    """CREATE TRIGGER perspectives_no_delete
        BEFORE DELETE ON perspectives
        BEGIN SELECT RAISE(ABORT, 'perspectives are append-only'); END""",
)


_MIGRATION_9 = (
    """CREATE TABLE claim_relations (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        related_claim_id TEXT NOT NULL,
        relationship TEXT NOT NULL CHECK (relationship IN (
            'contradicts', 'qualifies', 'narrows', 'reframes'
        )),
        supersedes_relation_id TEXT UNIQUE,
        created_at TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id),
        FOREIGN KEY (story_id, supersedes_relation_id)
            REFERENCES claim_relations(story_id, id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_claim_relations_story_created ON claim_relations(story_id, created_at, id)",
    """CREATE TRIGGER claim_relations_validate_insert
        BEFORE INSERT ON claim_relations
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
        END""",
    """CREATE TRIGGER claim_relations_no_update
        BEFORE UPDATE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END""",
    """CREATE TRIGGER claim_relations_no_delete
        BEFORE DELETE ON claim_relations
        BEGIN SELECT RAISE(ABORT, 'claim relations are append-only'); END""",
)


_MIGRATION_10 = (
    """CREATE TRIGGER timeline_events_review_validate_insert
        BEFORE INSERT ON timeline_events
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
        END""",
)


_MIGRATION_11 = (
    """CREATE TABLE learning_objects (
        id TEXT PRIMARY KEY,
        story_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN (
            'headline_summary', 'brief_summary', 'detailed_summary',
            'vocabulary', 'comprehension_question', 'analysis_question',
            'mcq_question', 'revision_card'
        )),
        language TEXT NOT NULL,
        variant_group_id TEXT,
        status TEXT NOT NULL CHECK (status IN ('draft', 'review_required', 'approved', 'rejected')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_actor TEXT NOT NULL CHECK (length(trim(last_actor)) > 0),
        last_reason TEXT NOT NULL CHECK (length(trim(last_reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (story_id, id)
    )""",
    """CREATE TABLE learning_object_revisions (
        learning_object_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (learning_object_id, revision),
        FOREIGN KEY (learning_object_id) REFERENCES learning_objects(id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX idx_learning_objects_story_kind_language ON learning_objects(story_id, kind, language, id)",
    """CREATE UNIQUE INDEX idx_learning_objects_variant_language
        ON learning_objects(story_id, kind, variant_group_id, language)
        WHERE variant_group_id IS NOT NULL""",
    """CREATE TRIGGER learning_objects_variant_group_validate_insert
        BEFORE INSERT ON learning_objects
        WHEN NEW.variant_group_id IS NOT NULL
        BEGIN
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM learning_objects existing
                WHERE existing.variant_group_id = NEW.variant_group_id
                  AND (existing.story_id != NEW.story_id OR existing.kind != NEW.kind)
            ) THEN RAISE(ABORT, 'learning object variants must share the same story and kind') END;
        END""",
    """CREATE TRIGGER learning_objects_variant_group_validate_update
        BEFORE UPDATE ON learning_objects
        WHEN NEW.variant_group_id IS NOT NULL
        BEGIN
            SELECT CASE WHEN EXISTS (
                SELECT 1 FROM learning_objects existing
                WHERE existing.variant_group_id = NEW.variant_group_id
                  AND existing.id != NEW.id
                  AND (existing.story_id != NEW.story_id OR existing.kind != NEW.kind)
            ) THEN RAISE(ABORT, 'learning object variants must share the same story and kind') END;
        END""",
    """CREATE TRIGGER learning_objects_validate_insert
        BEFORE INSERT ON learning_objects
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
        END""",
    """CREATE TRIGGER learning_objects_validate_update
        BEFORE UPDATE ON learning_objects
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
        END""",
    """CREATE TRIGGER learning_objects_no_delete
        BEFORE DELETE ON learning_objects
        BEGIN SELECT RAISE(ABORT, 'learning objects cannot be deleted'); END""",
    """CREATE TRIGGER learning_object_revisions_validate_insert
        BEFORE INSERT ON learning_object_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM learning_objects
                WHERE id = NEW.learning_object_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'learning object revision must match the current record') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'learning object revision requires actor and reason') END;
        END""",
    """CREATE TRIGGER learning_objects_record_insert
        AFTER INSERT ON learning_objects
        BEGIN
            INSERT INTO learning_object_revisions
            (learning_object_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER learning_objects_record_update
        AFTER UPDATE ON learning_objects
        BEGIN
            INSERT INTO learning_object_revisions
            (learning_object_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER learning_object_revisions_no_update
        BEFORE UPDATE ON learning_object_revisions
        BEGIN SELECT RAISE(ABORT, 'learning object revisions are append-only'); END""",
    """CREATE TRIGGER learning_object_revisions_no_delete
        BEFORE DELETE ON learning_object_revisions
        BEGIN SELECT RAISE(ABORT, 'learning object revisions are append-only'); END""",
)


_LANGUAGE_REVIEW_RUBRICS = {
    "bn": {
        "version": "bn-editorial-v1",
        "dimensions": {
            "grammar_and_spelling", "naturalness_and_register",
            "terminology_consistency", "factual_fidelity", "attribution",
            "omissions", "translation_fidelity",
        },
    },
    "en": {
        "version": "en-editorial-v1",
        "dimensions": {
            "grammar_and_style", "clarity_and_readability",
            "terminology_consistency", "factual_fidelity", "attribution",
            "omissions", "translation_fidelity",
        },
    },
}


def _language_review_passes(review: dict[str, Any] | None, language: str) -> bool:
    rubric = _LANGUAGE_REVIEW_RUBRICS.get(language)
    if not isinstance(review, dict) or rubric is None:
        return False
    dimensions = review.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != rubric["dimensions"]:
        return False
    if review.get("rubric_version") != rubric["version"] or review.get("language") != language:
        return False
    return all(
        value == "pass" or (name == "translation_fidelity" and value == "not_applicable")
        for name, value in dimensions.items()
    )

_MCQ_REVIEW_RUBRIC = {
    "version": "mcq-editorial-v1",
    "dimensions": {
        "single_defensible_key",
        "stem_clarity_and_precision",
        "distractor_plausibility",
        "distractors_unambiguously_incorrect",
        "distractor_rationale_quality",
        "learner_level_fit",
        "misconception_alignment",
        "option_parallelism",
        "no_accidental_clues",
        "explanation_fidelity",
    },
}


def _mcq_review_passes(review: dict[str, Any] | None, language: str) -> bool:
    if not isinstance(review, dict):
        return False
    dimensions = review.get("dimensions")
    return (
        review.get("language") == language
        and review.get("rubric_version") == _MCQ_REVIEW_RUBRIC["version"]
        and isinstance(dimensions, dict)
        and set(dimensions) == _MCQ_REVIEW_RUBRIC["dimensions"]
        and all(value == "pass" for value in dimensions.values())
    )


def _validate_learning_object_mcq_review(payload: dict[str, Any]) -> None:
    review = payload.get("mcq_review")
    if review is None:
        return
    if payload.get("kind") != "mcq_question":
        raise RegistryIntegrityError("MCQ pedagogical review requires an MCQ learning object")
    if payload.get("status") not in {"approved", "rejected"}:
        raise RegistryIntegrityError("MCQ pedagogical review requires an approved or rejected decision")
    if review.get("language") != payload.get("language"):
        raise RegistryIntegrityError("MCQ pedagogical review language mismatch")
    if review.get("rubric_version") != _MCQ_REVIEW_RUBRIC["version"]:
        raise RegistryIntegrityError("MCQ pedagogical review rubric version mismatch")
    dimensions = review.get("dimensions")
    if not isinstance(dimensions, dict) or set(dimensions) != _MCQ_REVIEW_RUBRIC["dimensions"]:
        raise RegistryIntegrityError("MCQ pedagogical review dimensions do not match the rubric")
    if review.get("reviewer_id") != payload.get("reviewed_by"):
        raise RegistryIntegrityError("MCQ pedagogical reviewer mismatch")
    if review.get("reviewed_at") != payload.get("reviewed_at"):
        raise RegistryIntegrityError("MCQ pedagogical review timestamp mismatch")
    _aware_datetime(review["reviewed_at"], "mcq_review.reviewed_at")
    attestation = review.get("identity_attestation")
    if attestation is not None:
        required_attestation = {
            "principal_id", "issuer", "subject", "provider_key_id",
            "provider_adapter", "authenticated_at", "attested_at",
            "authentication_methods", "authentication_context",
        }
        if set(attestation) != required_attestation:
            raise RegistryIntegrityError("MCQ review identity attestation is incomplete")
        if attestation["principal_id"] != review["reviewer_id"]:
            raise RegistryIntegrityError("MCQ review identity attestation principal mismatch")
        if attestation["attested_at"] != review["reviewed_at"]:
            raise RegistryIntegrityError("MCQ review identity attestation timestamp mismatch")
        _aware_datetime(attestation["authenticated_at"], "mcq_review.identity_attestation.authenticated_at")
        _aware_datetime(attestation["attested_at"], "mcq_review.identity_attestation.attested_at")
        if not attestation["authentication_methods"] or not attestation["subject"].strip():
            raise RegistryIntegrityError("MCQ review identity attestation lacks provider evidence")
    needs_revision = any(value == "needs_revision" for value in dimensions.values())
    if payload.get("status") == "approved" and needs_revision:
        raise RegistryIntegrityError("approved MCQ review cannot contain needs_revision findings")
    if payload.get("status") == "rejected" and not needs_revision:
        raise RegistryIntegrityError("rejected MCQ review must identify at least one needs_revision finding")


def _validate_learning_object_language_review(payload: dict[str, Any]) -> None:
    review = payload.get("language_review")
    if review is None:
        return
    if payload.get("status") not in {"approved", "rejected"}:
        raise RegistryIntegrityError("structured language review requires an approved or rejected decision")
    language = payload.get("language")
    rubric = _LANGUAGE_REVIEW_RUBRICS.get(language)
    if rubric is None or review.get("language") != language:
        raise RegistryIntegrityError("language review language mismatch")
    if review.get("rubric_version") != rubric["version"]:
        raise RegistryIntegrityError("language review rubric version mismatch")
    if set(review.get("dimensions", {})) != rubric["dimensions"]:
        raise RegistryIntegrityError("language review dimensions do not match the language rubric")
    if review.get("reviewer_id") != payload.get("reviewed_by"):
        raise RegistryIntegrityError("language review reviewer mismatch")
    if review.get("reviewed_at") != payload.get("reviewed_at"):
        raise RegistryIntegrityError("language review timestamp mismatch")
    _aware_datetime(review["reviewed_at"], "language_review.reviewed_at")
    attestation = review.get("identity_attestation")
    if attestation is not None:
        required_attestation = {
            "principal_id", "issuer", "subject", "provider_key_id",
            "provider_adapter", "authenticated_at", "attested_at",
            "authentication_methods", "authentication_context",
        }
        if set(attestation) != required_attestation:
            raise RegistryIntegrityError("language review identity attestation is incomplete")
        if attestation["principal_id"] != review["reviewer_id"]:
            raise RegistryIntegrityError("language review identity attestation principal mismatch")
        if attestation["attested_at"] != review["reviewed_at"]:
            raise RegistryIntegrityError("language review identity attestation timestamp mismatch")
        _aware_datetime(attestation["authenticated_at"], "language_review.identity_attestation.authenticated_at")
        _aware_datetime(attestation["attested_at"], "language_review.identity_attestation.attested_at")
        if not attestation["authentication_methods"] or not attestation["subject"].strip():
            raise RegistryIntegrityError("language review identity attestation lacks provider evidence")
    needs_revision = any(value == "needs_revision" for value in review["dimensions"].values())
    if payload.get("status") == "approved" and needs_revision:
        raise RegistryIntegrityError("approved language review cannot contain needs_revision findings")
    if payload.get("status") == "rejected" and not needs_revision:
        raise RegistryIntegrityError("rejected language review must identify at least one needs_revision finding")


def _validate_learning_object_traceability(payload: dict[str, Any]) -> None:
    """Require summary and revision-card segments to map exactly to object claims."""
    if payload.get("kind") not in {"headline_summary", "brief_summary", "detailed_summary", "revision_card"}:
        return
    content = payload.get("content", {})
    segments = content.get("segments", [])
    if not segments:
        raise RegistryIntegrityError("summary requires claim-mapped segments")
    if content.get("text") != " ".join(segment["text"] for segment in segments):
        raise RegistryIntegrityError("summary text must exactly match its claim-mapped segments")
    object_claims = set(payload.get("claim_ids", []))
    segment_claims: set[str] = set()
    for segment in segments:
        claims = set(segment["claim_ids"])
        if not claims.issubset(object_claims):
            raise RegistryIntegrityError("summary segment references a claim outside the learning object")
        segment_claims.update(claims)
    if segment_claims != object_claims:
        raise RegistryIntegrityError("summary claim links must exactly match its segment claim links")


def _validate_learning_object_answer_links(payload: dict[str, Any]) -> None:
    """Ensure question answers and MCQ keys are structurally grounded."""
    kind = payload.get("kind")
    if kind not in {"comprehension_question", "analysis_question", "mcq_question"}:
        return
    content = payload.get("content", {})
    answer_claims = set(content.get("answer_claim_ids", []))
    object_claims = set(payload.get("claim_ids", []))
    if not answer_claims or not answer_claims.issubset(object_claims):
        raise RegistryIntegrityError("question answer claim links must be a non-empty subset of the object's claims")
    evidence_refs = content.get("answer_evidence", [])
    evidence_claims = {item.get("claim_id") for item in evidence_refs}
    evidence_pairs = [(item.get("claim_id"), item.get("evidence_id")) for item in evidence_refs]
    if (
        not evidence_refs
        or evidence_claims != answer_claims
        or len(evidence_pairs) != len(set(evidence_pairs))
    ):
        raise RegistryIntegrityError(
            "question answer evidence must uniquely cover every answer claim and no unrelated claim"
        )
    if any(
        not isinstance(item.get("evidence_revision"), int)
        or isinstance(item.get("evidence_revision"), bool)
        or item["evidence_revision"] < 1
        for item in evidence_refs
    ):
        raise RegistryIntegrityError("question answer evidence must pin a positive evidence revision")
    if kind == "mcq_question":
        options = content.get("options", [])
        option_ids = [item["id"] for item in options]
        option_texts = [item["text"].strip().casefold() for item in options]
        if len(option_ids) != len(set(option_ids)):
            raise RegistryIntegrityError("MCQ option IDs must be unique")
        if len(option_texts) != len(set(option_texts)):
            raise RegistryIntegrityError("MCQ option text must be unique")
        if content.get("correct_option_id") not in set(option_ids):
            raise RegistryIntegrityError("MCQ correct option must reference one of its options")
        rationales = content.get("distractor_rationales", [])
        rationale_ids = [item.get("option_id") for item in rationales]
        expected_distractors = set(option_ids) - {content.get("correct_option_id")}
        if len(rationale_ids) != len(set(rationale_ids)):
            raise RegistryIntegrityError("MCQ distractor rationale option IDs must be unique")
        if set(rationale_ids) != expected_distractors:
            raise RegistryIntegrityError("MCQ must provide exactly one rationale for every incorrect option and none for the correct option")
        if any(not isinstance(item.get("rationale"), str) or not item["rationale"].strip() for item in rationales):
            raise RegistryIntegrityError("MCQ distractor rationales must be non-empty")
        object_claims = set(payload.get("claim_ids", []))
        if any(
            not item.get("basis_claim_ids")
            or not set(item["basis_claim_ids"]).issubset(object_claims)
            for item in rationales
        ):
            raise RegistryIntegrityError(
                "MCQ distractor rationale basis claims must be a non-empty subset of the object's claims"
            )


_MIGRATION_12 = (
    """CREATE TABLE glossary_entries (
        id TEXT PRIMARY KEY,
        lemma TEXT NOT NULL,
        lemma_key TEXT NOT NULL,
        term_language TEXT NOT NULL,
        part_of_speech TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('draft', 'review_required', 'approved', 'rejected')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_actor TEXT NOT NULL CHECK (length(trim(last_actor)) > 0),
        last_reason TEXT NOT NULL CHECK (length(trim(last_reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        UNIQUE (term_language, part_of_speech, lemma_key)
    )""",
    """CREATE TABLE glossary_entry_revisions (
        glossary_entry_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (glossary_entry_id, revision),
        FOREIGN KEY (glossary_entry_id) REFERENCES glossary_entries(id) ON DELETE RESTRICT
    )""",
    """CREATE TRIGGER glossary_entries_validate_insert
        BEFORE INSERT ON glossary_entries
        BEGIN
            SELECT CASE WHEN NEW.status NOT IN ('draft', 'review_required')
                THEN RAISE(ABORT, 'new glossary entry must start in draft or review-required state') END;
            SELECT CASE WHEN json_type(NEW.payload_json, '$.reviewed_at') IS NOT NULL
                OR json_type(NEW.payload_json, '$.reviewed_by') IS NOT NULL
                OR json_type(NEW.payload_json, '$.review_note') IS NOT NULL
            THEN RAISE(ABORT, 'new glossary entry cannot contain review metadata') END;
        END""",
    """CREATE TRIGGER glossary_entries_validate_update
        BEFORE UPDATE ON glossary_entries
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
        END""",
    """CREATE TRIGGER glossary_entries_no_delete
        BEFORE DELETE ON glossary_entries
        BEGIN SELECT RAISE(ABORT, 'glossary entries cannot be deleted'); END""",
    """CREATE TRIGGER glossary_entries_record_insert
        AFTER INSERT ON glossary_entries
        BEGIN
            INSERT INTO glossary_entry_revisions
            (glossary_entry_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER glossary_entries_record_update
        AFTER UPDATE ON glossary_entries
        BEGIN
            INSERT INTO glossary_entry_revisions
            (glossary_entry_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER glossary_entry_revisions_validate_insert
        BEFORE INSERT ON glossary_entry_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM glossary_entries
                WHERE id = NEW.glossary_entry_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'glossary revision must match the current entry') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'glossary revision requires actor and reason') END;
        END""",
    """CREATE TRIGGER glossary_entry_revisions_no_update
        BEFORE UPDATE ON glossary_entry_revisions
        BEGIN SELECT RAISE(ABORT, 'glossary revisions are append-only'); END""",
    """CREATE TRIGGER glossary_entry_revisions_no_delete
        BEFORE DELETE ON glossary_entry_revisions
        BEGIN SELECT RAISE(ABORT, 'glossary revisions are append-only'); END""",
    """CREATE TRIGGER learning_objects_mcq_validate_insert
        BEFORE INSERT ON learning_objects
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
        END""",
    """CREATE TRIGGER learning_objects_mcq_validate_update
        BEFORE UPDATE ON learning_objects
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
        END""",
    """CREATE TRIGGER learning_objects_glossary_validate_insert
        BEFORE INSERT ON learning_objects
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
        END""",
    """CREATE TRIGGER learning_objects_glossary_validate_update
        BEFORE UPDATE ON learning_objects
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
        END""",
)


_MIGRATION_13 = (
    """CREATE TABLE benchmark_reviews (
        review_id TEXT PRIMARY KEY,
        benchmark_id TEXT NOT NULL,
        benchmark_fingerprint TEXT NOT NULL CHECK (benchmark_fingerprint GLOB 'sha256:*'),
        language TEXT NOT NULL CHECK (language IN ('bn', 'en')),
        status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'rejected')),
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        last_actor TEXT NOT NULL CHECK (length(trim(last_actor)) > 0),
        last_reason TEXT NOT NULL CHECK (length(trim(last_reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json))
    )""",
    """CREATE TABLE benchmark_review_revisions (
        review_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
        PRIMARY KEY (review_id, revision),
        FOREIGN KEY (review_id) REFERENCES benchmark_reviews(review_id) ON DELETE RESTRICT
    )""",
    """CREATE TRIGGER benchmark_reviews_validate_insert
        BEFORE INSERT ON benchmark_reviews
        BEGIN
            SELECT CASE WHEN NEW.revision != 1
                THEN RAISE(ABORT, 'benchmark review must start at revision one') END;
        END""",
    """CREATE TRIGGER benchmark_reviews_validate_update
        BEFORE UPDATE ON benchmark_reviews
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
        END""",
    """CREATE TRIGGER benchmark_reviews_no_delete
        BEFORE DELETE ON benchmark_reviews
        BEGIN SELECT RAISE(ABORT, 'benchmark reviews cannot be deleted'); END""",
    """CREATE TRIGGER benchmark_reviews_record_insert
        AFTER INSERT ON benchmark_reviews
        BEGIN
            INSERT INTO benchmark_review_revisions
            (review_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.review_id, NEW.revision, NEW.created_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER benchmark_reviews_record_update
        AFTER UPDATE ON benchmark_reviews
        BEGIN
            INSERT INTO benchmark_review_revisions
            (review_id, revision, created_at, actor, reason, payload_json)
            VALUES (NEW.review_id, NEW.revision, NEW.updated_at, NEW.last_actor, NEW.last_reason, NEW.payload_json);
        END""",
    """CREATE TRIGGER benchmark_review_revisions_validate_insert
        BEFORE INSERT ON benchmark_review_revisions
        BEGIN
            SELECT CASE WHEN NOT EXISTS (
                SELECT 1 FROM benchmark_reviews
                WHERE review_id = NEW.review_id AND revision = NEW.revision
                  AND payload_json = NEW.payload_json
            ) THEN RAISE(ABORT, 'benchmark review revision must match the current review') END;
            SELECT CASE WHEN trim(NEW.actor) = '' OR trim(NEW.reason) = ''
                THEN RAISE(ABORT, 'benchmark review revision requires actor and reason') END;
        END""",
    """CREATE TRIGGER benchmark_review_revisions_no_update
        BEFORE UPDATE ON benchmark_review_revisions
        BEGIN SELECT RAISE(ABORT, 'benchmark review revisions are append-only'); END""",
    """CREATE TRIGGER benchmark_review_revisions_no_delete
        BEFORE DELETE ON benchmark_review_revisions
        BEGIN SELECT RAISE(ABORT, 'benchmark review revisions are append-only'); END""",
    "CREATE INDEX idx_benchmark_reviews_benchmark ON benchmark_reviews(benchmark_id, language, status)",
)


_MIGRATION_32 = (
    """CREATE TABLE pipeline_stage_checkpoints (
        pipeline_run_id TEXT NOT NULL,
        stage_ordinal INTEGER NOT NULL CHECK (stage_ordinal >= 0),
        stage_name TEXT NOT NULL CHECK (length(stage_name) BETWEEN 1 AND 100),
        input_fingerprint TEXT NOT NULL CHECK (input_fingerprint GLOB 'sha256:*'),
        idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key GLOB 'sha256:*'),
        status TEXT NOT NULL CHECK (status IN ('executing', 'succeeded', 'failed', 'indeterminate', 'blocked')),
        provider_namespace TEXT,
        provider_operation_ref TEXT,
        output_fingerprint TEXT,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
        PRIMARY KEY (pipeline_run_id, stage_ordinal),
        FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
        CHECK (provider_operation_ref IS NULL OR
            (length(provider_operation_ref) BETWEEN 1 AND 256
             AND provider_operation_ref NOT GLOB '*[^A-Za-z0-9_.:-]*')),
        CHECK (provider_namespace IS NULL OR
            (length(provider_namespace) BETWEEN 1 AND 100
             AND provider_namespace NOT GLOB '*[^a-z0-9_.-]*')),
        CHECK ((status = 'succeeded' AND output_fingerprint GLOB 'sha256:*')
            OR (status <> 'succeeded' AND output_fingerprint IS NULL))
    )""",
    """CREATE TABLE pipeline_stage_checkpoint_revisions (
        pipeline_run_id TEXT NOT NULL,
        stage_ordinal INTEGER NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        status TEXT NOT NULL CHECK (status IN ('executing', 'succeeded', 'failed', 'indeterminate', 'blocked')),
        provider_operation_ref TEXT,
        output_fingerprint TEXT,
        metadata_json TEXT NOT NULL CHECK (json_valid(metadata_json)),
        PRIMARY KEY (pipeline_run_id, stage_ordinal, revision),
        FOREIGN KEY (pipeline_run_id, stage_ordinal)
            REFERENCES pipeline_stage_checkpoints(pipeline_run_id, stage_ordinal) ON DELETE RESTRICT
    )""",
    """CREATE INDEX pipeline_stage_checkpoints_status
       ON pipeline_stage_checkpoints(status, updated_at)""",
    """CREATE TRIGGER pipeline_stage_checkpoint_initial_revision
       AFTER INSERT ON pipeline_stage_checkpoints
       BEGIN
         INSERT INTO pipeline_stage_checkpoint_revisions
         (pipeline_run_id, stage_ordinal, revision, created_at, actor, reason,
          status, provider_operation_ref, output_fingerprint, metadata_json)
         VALUES (NEW.pipeline_run_id, NEW.stage_ordinal, NEW.revision, NEW.updated_at,
                 NEW.actor, NEW.reason, NEW.status, NEW.provider_operation_ref,
                 NEW.output_fingerprint, NEW.metadata_json);
       END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_revision_guard
       BEFORE UPDATE ON pipeline_stage_checkpoints
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
       END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_revision_snapshot
       AFTER UPDATE ON pipeline_stage_checkpoints
       BEGIN
         INSERT INTO pipeline_stage_checkpoint_revisions
         (pipeline_run_id, stage_ordinal, revision, created_at, actor, reason,
          status, provider_operation_ref, output_fingerprint, metadata_json)
         VALUES (NEW.pipeline_run_id, NEW.stage_ordinal, NEW.revision, NEW.updated_at,
                 NEW.actor, NEW.reason, NEW.status, NEW.provider_operation_ref,
                 NEW.output_fingerprint, NEW.metadata_json);
       END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_insert
       BEFORE INSERT ON pipeline_stage_checkpoints
       WHEN json_type(NEW.metadata_json) IS NOT 'object'
         OR EXISTS (
           SELECT 1 FROM json_each(NEW.metadata_json)
           WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt')
              OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000))
              OR (key != 'attempt' AND
                  (type != 'text' OR length(value) NOT BETWEEN 1 AND 128
                   OR value GLOB '*[^A-Za-z0-9_.:-]*'))
         )
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint metadata contains unsupported fields or types'); END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_metadata_guard_update
       BEFORE UPDATE ON pipeline_stage_checkpoints
       WHEN json_type(NEW.metadata_json) IS NOT 'object'
         OR EXISTS (
           SELECT 1 FROM json_each(NEW.metadata_json)
           WHERE key NOT IN ('adapter_version', 'provider_state', 'failure_code', 'reconciliation', 'attempt')
              OR (key = 'attempt' AND (type != 'integer' OR CAST(value AS INTEGER) NOT BETWEEN 1 AND 1000))
              OR (key != 'attempt' AND
                  (type != 'text' OR length(value) NOT BETWEEN 1 AND 128
                   OR value GLOB '*[^A-Za-z0-9_.:-]*'))
         )
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint metadata contains unsupported fields or types'); END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_revision_matches_head
       BEFORE INSERT ON pipeline_stage_checkpoint_revisions
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
       BEGIN SELECT RAISE(ABORT, 'checkpoint revision must match its current head'); END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_update
       BEFORE UPDATE ON pipeline_stage_checkpoint_revisions
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_stage_checkpoint_revisions_no_delete
       BEFORE DELETE ON pipeline_stage_checkpoint_revisions
       BEGIN SELECT RAISE(ABORT, 'stage checkpoint revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_stage_checkpoints_no_delete
       BEFORE DELETE ON pipeline_stage_checkpoints
       BEGIN SELECT RAISE(ABORT, 'stage checkpoints cannot be deleted'); END""",
)


_MIGRATION_33 = (
    """CREATE TABLE pipeline_worker_leases (
        pipeline_run_id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 100
            AND worker_id NOT GLOB '*[^A-Za-z0-9_.:-]*'),
        token_hash TEXT NOT NULL CHECK (token_hash GLOB 'sha256:*'),
        fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
        status TEXT NOT NULL CHECK (status IN ('active', 'released', 'expired', 'cancelled')),
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        cancel_requested_at TEXT,
        cancel_requested_by TEXT,
        cancel_reason_code TEXT,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        updated_at TEXT NOT NULL,
        actor TEXT NOT NULL CHECK (length(trim(actor)) > 0),
        reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
        FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(id) ON DELETE RESTRICT,
        CHECK ((cancel_requested_at IS NULL AND cancel_requested_by IS NULL AND cancel_reason_code IS NULL)
            OR (cancel_requested_at IS NOT NULL AND cancel_requested_by IS NOT NULL
                AND cancel_reason_code IS NOT NULL
                AND cancel_reason_code GLOB '[a-z]*'
                AND length(cancel_reason_code) BETWEEN 1 AND 100))
    )""",
    """CREATE TABLE pipeline_worker_lease_revisions (
        pipeline_run_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK (revision >= 1),
        created_at TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        token_hash TEXT NOT NULL,
        fencing_token INTEGER NOT NULL,
        status TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        cancel_requested_at TEXT,
        cancel_requested_by TEXT,
        cancel_reason_code TEXT,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        PRIMARY KEY (pipeline_run_id, revision),
        FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_worker_leases(pipeline_run_id) ON DELETE RESTRICT
    )""",
    "CREATE INDEX pipeline_worker_leases_expiry ON pipeline_worker_leases(status, expires_at)",
    """CREATE TRIGGER pipeline_worker_lease_initial_revision
       AFTER INSERT ON pipeline_worker_leases
       BEGIN
         INSERT INTO pipeline_worker_lease_revisions
         (pipeline_run_id, revision, created_at, worker_id, token_hash, fencing_token,
          status, acquired_at, heartbeat_at, expires_at, cancel_requested_at,
          cancel_requested_by, cancel_reason_code, actor, reason)
         VALUES (NEW.pipeline_run_id, NEW.revision, NEW.updated_at, NEW.worker_id,
          NEW.token_hash, NEW.fencing_token, NEW.status, NEW.acquired_at,
          NEW.heartbeat_at, NEW.expires_at, NEW.cancel_requested_at,
          NEW.cancel_requested_by, NEW.cancel_reason_code, NEW.actor, NEW.reason);
       END""",
    """CREATE TRIGGER pipeline_worker_lease_revision_guard
       BEFORE UPDATE ON pipeline_worker_leases
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
       END""",
    """CREATE TRIGGER pipeline_worker_lease_revision_snapshot
       AFTER UPDATE ON pipeline_worker_leases
       BEGIN
         INSERT INTO pipeline_worker_lease_revisions
         (pipeline_run_id, revision, created_at, worker_id, token_hash, fencing_token,
          status, acquired_at, heartbeat_at, expires_at, cancel_requested_at,
          cancel_requested_by, cancel_reason_code, actor, reason)
         VALUES (NEW.pipeline_run_id, NEW.revision, NEW.updated_at, NEW.worker_id,
          NEW.token_hash, NEW.fencing_token, NEW.status, NEW.acquired_at,
          NEW.heartbeat_at, NEW.expires_at, NEW.cancel_requested_at,
          NEW.cancel_requested_by, NEW.cancel_reason_code, NEW.actor, NEW.reason);
       END""",
    """CREATE TRIGGER pipeline_worker_lease_revision_matches_head
       BEFORE INSERT ON pipeline_worker_lease_revisions
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
       BEGIN SELECT RAISE(ABORT, 'worker lease revision must match current head'); END""",
    """CREATE TRIGGER pipeline_worker_lease_revisions_no_update
       BEFORE UPDATE ON pipeline_worker_lease_revisions
       BEGIN SELECT RAISE(ABORT, 'worker lease revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_worker_lease_revisions_no_delete
       BEFORE DELETE ON pipeline_worker_lease_revisions
       BEGIN SELECT RAISE(ABORT, 'worker lease revisions are append-only'); END""",
    """CREATE TRIGGER pipeline_worker_leases_no_delete
       BEFORE DELETE ON pipeline_worker_leases
       BEGIN SELECT RAISE(ABORT, 'worker leases cannot be deleted'); END""",
)


_MIGRATION_34 = (
    """CREATE TRIGGER domain_record_revisions_validate_insert
       BEFORE INSERT ON domain_record_revisions
       WHEN NOT EXISTS (
         SELECT 1 FROM domain_records d
         WHERE d.record_type = NEW.record_type
           AND d.id = NEW.record_id
           AND d.revision = NEW.revision
           AND d.payload_json = NEW.payload_json
       )
       OR length(trim(NEW.actor)) = 0
       OR length(trim(NEW.reason)) = 0
       BEGIN SELECT RAISE(ABORT, 'domain revision must match current head and include actor/reason'); END""",
    """CREATE TRIGGER permission_grant_revisions_validate_insert
       BEFORE INSERT ON permission_grant_revisions
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
       BEGIN SELECT RAISE(ABORT, 'grant revision must match current or next legal head and include actor/reason'); END""",
    """CREATE TRIGGER permission_grants_revision_guard
       BEFORE UPDATE ON permission_grants
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
       END""",
    """CREATE TRIGGER benchmark_review_assignment_revisions_validate_insert
       BEFORE INSERT ON benchmark_review_assignment_revisions
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
       BEGIN SELECT RAISE(ABORT, 'assignment revision must match current or next legal head and include actor/reason'); END""",
    """CREATE TRIGGER benchmark_review_assignment_status_transition_guard
       BEFORE UPDATE ON benchmark_review_assignments
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
       END""",
)

_MIGRATION_35 = (
    """ALTER TABLE domain_records
       ADD COLUMN last_actor TEXT NOT NULL DEFAULT 'migration'
       CHECK (length(trim(last_actor)) > 0)""",
    """ALTER TABLE domain_records
       ADD COLUMN last_reason TEXT NOT NULL DEFAULT 'schema migration'
       CHECK (length(trim(last_reason)) > 0)""",
    """ALTER TABLE domain_records
       ADD COLUMN last_revision_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00Z'
       CHECK (length(trim(last_revision_at)) > 0)""",
    """UPDATE domain_records
       SET last_actor = COALESCE((
             SELECT r.actor FROM domain_record_revisions r
             WHERE r.record_type = domain_records.record_type
               AND r.record_id = domain_records.id
             ORDER BY r.revision DESC LIMIT 1
           ), 'migration'),
           last_reason = COALESCE((
             SELECT r.reason FROM domain_record_revisions r
             WHERE r.record_type = domain_records.record_type
               AND r.record_id = domain_records.id
             ORDER BY r.revision DESC LIMIT 1
           ), 'schema migration'),
           last_revision_at = COALESCE((
             SELECT r.created_at FROM domain_record_revisions r
             WHERE r.record_type = domain_records.record_type
               AND r.record_id = domain_records.id
             ORDER BY r.revision DESC LIMIT 1
           ), created_at)""",
    """CREATE TRIGGER domain_records_revision_guard
       BEFORE UPDATE ON domain_records
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
         SELECT CASE WHEN julianday(NEW.updated_at) < julianday(OLD.updated_at)
             OR julianday(NEW.last_revision_at) < julianday(OLD.last_revision_at)
           THEN RAISE(ABORT, 'domain record timestamps cannot move backwards') END;
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
       END""",
    """CREATE TRIGGER domain_records_record_update
       AFTER UPDATE ON domain_records
       BEGIN
         INSERT INTO domain_record_revisions
         (record_type, record_id, revision, created_at, actor, reason, payload_json)
         VALUES (NEW.record_type, NEW.id, NEW.revision, NEW.last_revision_at,
                 NEW.last_actor, NEW.last_reason, NEW.payload_json);
       END""",
)



class SQLiteRegistry:
    """Transactional SQLite registry; safe for local development and low-volume use.

    The class serializes access to its connection. Separate instances can safely
    share a file database because writes use BEGIN IMMEDIATE and SQLite locking.
    """

    def __init__(self, database: str | Path = ":memory:") -> None:
        self.database = str(database)
        self._lock = RLock()
        self._transaction_depth = 0
        self._connection = sqlite3.connect(
            self.database,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 10000")
        if self.database != ":memory:":
            self._connection.execute("PRAGMA journal_mode = WAL")
        try:
            self.migrate()
        except Exception:
            self._connection.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> "SQLiteRegistry":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @contextmanager
    def _transaction(self, mode: str = "IMMEDIATE") -> Iterator[sqlite3.Connection]:
        """Open an atomic transaction, nesting with savepoints on this connection.

        Registry workflows compose read/verification helpers inside larger
        write transactions. SQLite does not permit a second BEGIN on the same
        connection, so nested calls use savepoints and remain part of the
        outer transaction. The re-entrant lock is held for the full scope.
        """
        with self._lock:
            depth = self._transaction_depth
            savepoint = f"registry_nested_{depth}"
            if depth == 0:
                self._connection.execute(f"BEGIN {mode}")
            else:
                self._connection.execute(f"SAVEPOINT {savepoint}")
            self._transaction_depth = depth + 1
            try:
                yield self._connection
            except Exception:
                self._transaction_depth = depth
                if depth == 0:
                    self._connection.rollback()
                else:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                self._transaction_depth = depth
                if depth == 0:
                    try:
                        self._connection.commit()
                    except Exception:
                        # A deferred constraint or I/O error can fail at COMMIT,
                        # after the context body has returned successfully. Roll
                        # back explicitly so the connection is not left open.
                        self._connection.rollback()
                        raise
                else:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    def migrate(self) -> None:
        """Apply ordered migrations atomically; reject unknown future versions."""
        with self._transaction("EXCLUSIVE") as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > _SCHEMA_VERSION:
                raise RegistryMigrationError(
                    f"Database schema {version} is newer than supported {_SCHEMA_VERSION}"
                )
            if version == 0:
                try:
                    for statement in _MIGRATION_1:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (1, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 1")
                    version = 1
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 1 failed; transaction rolled back") from exc
            if version == 1:
                try:
                    for statement in _MIGRATION_2:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (2, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 2")
                    version = 2
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 2 failed; transaction rolled back") from exc
            if version == 2 and _SCHEMA_VERSION >= 3:
                try:
                    for statement in _MIGRATION_3:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (3, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 3")
                    version = 3
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 3 failed; transaction rolled back") from exc
            if version == 3:
                try:
                    for statement in _MIGRATION_4:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (4, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 4")
                    version = 4
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 4 failed; transaction rolled back") from exc
            if version == 4:
                try:
                    for statement in _MIGRATION_5:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (5, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 5")
                    version = 5
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 5 failed; transaction rolled back") from exc

            if version == 5:
                try:
                    for statement in _MIGRATION_6:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (6, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 6")
                    version = 6
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 6 failed; transaction rolled back") from exc

            if version == 6 and _SCHEMA_VERSION >= 7:
                try:
                    for statement in _MIGRATION_7:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (7, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 7")
                    version = 7
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 7 failed; transaction rolled back") from exc
            
            if version == 7 and _SCHEMA_VERSION >= 8:
                try:
                    for statement in _MIGRATION_8:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (8, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 8")
                    version = 8
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 8 failed; transaction rolled back") from exc

            if version == 8 and _SCHEMA_VERSION >= 9:
                try:
                    for statement in _MIGRATION_9:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (9, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 9")
                    version = 9
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 9 failed; transaction rolled back") from exc

            if version == 9 and _SCHEMA_VERSION >= 10:
                try:
                    for statement in _MIGRATION_10:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (10, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 10")
                    version = 10
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 10 failed; transaction rolled back") from exc

            if version == 10 and _SCHEMA_VERSION >= 11:
                try:
                    for statement in _MIGRATION_11:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (11, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 11")
                    version = 11
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 11 failed; transaction rolled back") from exc

            if version == 11 and _SCHEMA_VERSION >= 12:
                try:
                    for statement in _MIGRATION_12:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (12, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 12")
                    version = 12
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 12 failed; transaction rolled back") from exc

            if version == 12 and _SCHEMA_VERSION >= 13:
                try:
                    for statement in _MIGRATION_13:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (13, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 13")
                    version = 13
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 13 failed; transaction rolled back") from exc

            if version == 13 and _SCHEMA_VERSION >= 14:
                try:
                    for statement in _MIGRATION_14:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (14, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 14")
                    version = 14
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 14 failed; transaction rolled back") from exc

            if version == 14 and _SCHEMA_VERSION >= 15:
                try:
                    for statement in _MIGRATION_15:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (15, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 15")
                    version = 15
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 15 failed; transaction rolled back") from exc

            if version == 15 and _SCHEMA_VERSION >= 16:
                try:
                    for statement in _MIGRATION_16:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (16, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 16")
                    version = 16
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 16 failed; transaction rolled back") from exc

            if version == 16 and _SCHEMA_VERSION >= 17:
                try:
                    for statement in _MIGRATION_17:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (17, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 17")
                    version = 17
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 17 failed; transaction rolled back") from exc

            if version == 17 and _SCHEMA_VERSION >= 18:
                try:
                    for statement in _MIGRATION_18:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (18, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 18")
                    version = 18
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 18 failed; transaction rolled back") from exc

            if version == 18 and _SCHEMA_VERSION >= 19:
                try:
                    for statement in _MIGRATION_19:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (19, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 19")
                    version = 19
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 19 failed; transaction rolled back") from exc

            
            if version == 19 and _SCHEMA_VERSION >= 20:
                try:
                    for statement in _MIGRATION_20:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (20, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 20")
                    version = 20
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 20 failed; transaction rolled back") from exc


            if version == 20 and _SCHEMA_VERSION >= 21:
                try:
                    for statement in _MIGRATION_21:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (21, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 21")
                    version = 21
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 21 failed; transaction rolled back") from exc


            if version == 21 and _SCHEMA_VERSION >= 22:
                try:
                    for statement in _MIGRATION_22:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (22, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 22")
                    version = 22
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 22 failed; transaction rolled back") from exc



            if version == 22 and _SCHEMA_VERSION >= 23:
                try:
                    for statement in _MIGRATION_23:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (23, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 23")
                    version = 23
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 23 failed; transaction rolled back") from exc

            if version == 23 and _SCHEMA_VERSION >= 24:
                try:
                    for statement in _MIGRATION_24:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (24, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 24")
                    version = 24
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 24 failed; transaction rolled back") from exc

            if version == 24 and _SCHEMA_VERSION >= 25:
                try:
                    for statement in _MIGRATION_25:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (25, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 25")
                    version = 25
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 25 failed; transaction rolled back") from exc

            if version == 25 and _SCHEMA_VERSION >= 26:
                try:
                    for statement in _MIGRATION_26:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (26, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 26")
                    version = 26
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 26 failed; transaction rolled back") from exc


            if version == 26 and _SCHEMA_VERSION >= 27:
                try:
                    for statement in _MIGRATION_27:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (27, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 27")
                    version = 27
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 27 failed; transaction rolled back") from exc


            if version == 27 and _SCHEMA_VERSION >= 28:
                try:
                    for statement in _MIGRATION_28:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (28, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 28")
                    version = 28
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 28 failed; transaction rolled back") from exc


            if version == 28 and _SCHEMA_VERSION >= 29:
                try:
                    for statement in _MIGRATION_29:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (29, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 29")
                    version = 29
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 29 failed; transaction rolled back") from exc


            if version == 29 and _SCHEMA_VERSION >= 30:
                try:
                    for statement in _MIGRATION_30:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (30, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 30")
                    version = 30
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 30 failed; transaction rolled back") from exc

            if version == 30 and _SCHEMA_VERSION >= 31:
                try:
                    for statement in _MIGRATION_31:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (31, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 31")
                    version = 31
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 31 failed; transaction rolled back") from exc

            if version == 31 and _SCHEMA_VERSION >= 32:
                try:
                    for statement in _MIGRATION_32:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (32, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 32")
                    version = 32
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 32 failed; transaction rolled back") from exc

            if version == 32 and _SCHEMA_VERSION >= 33:
                try:
                    for statement in _MIGRATION_33:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (33, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 33")
                    version = 33
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 33 failed; transaction rolled back") from exc

            if version == 33 and _SCHEMA_VERSION >= 34:
                try:
                    for statement in _MIGRATION_34:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (34, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 34")
                    version = 34
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 34 failed; transaction rolled back") from exc

            if version == 34 and _SCHEMA_VERSION >= 35:
                try:
                    for statement in _MIGRATION_35:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (35, _utc_now()),
                    )
                    connection.execute("PRAGMA user_version = 35")
                    version = 35
                except sqlite3.Error as exc:
                    raise RegistryMigrationError("Migration 35 failed; transaction rolled back") from exc

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        *,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        policy_version: str | None = None,
    ) -> str:
        previous = connection.execute(
            "SELECT event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous["event_hash"] if previous else None
        event_id = f"audit:{uuid.uuid4().hex}"
        occurred_at = _utc_now()
        payload_json = _json_text(payload)
        event_hash = sha256_fingerprint(
            {
                "event_id": event_id,
                "event_type": event_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "occurred_at": occurred_at,
                "policy_version": policy_version,
                "payload": json.loads(payload_json),
                "previous_hash": previous_hash,
            }
        )
        connection.execute(
            """INSERT INTO audit_events
            (event_id, event_type, entity_type, entity_id, occurred_at,
             policy_version, payload_json, previous_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                entity_type,
                entity_id,
                occurred_at,
                policy_version,
                payload_json,
                previous_hash,
                event_hash,
            ),
        )
        return event_id

    @staticmethod
    def _check_record_times(payload: dict[str, Any], start: str, end: str) -> None:
        if _aware_datetime(payload[end], end) < _aware_datetime(payload[start], start):
            raise RegistryIntegrityError(f"{end} cannot be earlier than {start}")

    def add_source(self, payload: dict[str, Any]) -> SourceRecord:
        validate_payload("source", payload)
        if not valid_domain_allowlist(payload.get("domains")):
            raise RegistryIntegrityError("source domain allowlist must contain unique canonical DNS names")
        self._check_record_times(payload, "created_at", "updated_at")
        record = SourceRecord.from_payload(payload)
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO sources
                (id, publisher_name, status, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.publisher_name,
                    record.status,
                    payload["created_at"],
                    payload["updated_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="source.created",
                entity_type="source",
                entity_id=record.id,
                payload={"record": payload},
            )
        return record

    def add_endpoint(self, payload: dict[str, Any]) -> SourceEndpointRecord:
        validate_payload("source-endpoint", payload)
        self._check_record_times(payload, "created_at", "updated_at")
        record = SourceEndpointRecord.from_payload(payload)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (record.source_id,)
            ).fetchone() is None:
                raise RegistryIntegrityError("endpoint references an unknown source")
            connection.execute(
                """INSERT INTO source_endpoints
                (id, source_id, endpoint_uri, status, created_at, updated_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.endpoint_uri,
                    record.status,
                    payload["created_at"],
                    payload["updated_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="source_endpoint.created",
                entity_type="source_endpoint",
                entity_id=record.id,
                payload={"source_id": record.source_id, "record": payload},
            )
        return record

    def add_permission_evidence(
        self, payload: dict[str, Any]
    ) -> PermissionEvidenceRecord:
        validate_payload("permission-evidence", payload)
        _aware_datetime(payload["captured_at"], "captured_at")
        record = PermissionEvidenceRecord.from_payload(payload)
        with self._transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (record.source_id,)
            ).fetchone() is None:
                raise RegistryIntegrityError("permission evidence references an unknown source")
            connection.execute(
                """INSERT INTO permission_evidence
                (id, source_id, review_status, content_fingerprint, captured_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.review_status,
                    record.content_fingerprint,
                    payload["captured_at"],
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type="permission_evidence.created",
                entity_type="permission_evidence",
                entity_id=record.id,
                payload={"source_id": record.source_id, "record": payload},
            )
        return record

    @staticmethod
    def _load_payload(row: sqlite3.Row) -> dict[str, Any]:
        return json.loads(row["payload_json"])

    def _validate_grant_references(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        source_id = payload["source_id"]
        source = connection.execute(
            "SELECT status FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise RegistryIntegrityError("permission grant references an unknown source")

        endpoint_ids = payload["endpoint_ids"]
        placeholders = ",".join("?" for _ in endpoint_ids)
        endpoints = connection.execute(
            f"""SELECT id, status FROM source_endpoints
                WHERE source_id = ? AND id IN ({placeholders})""",
            (source_id, *endpoint_ids),
        ).fetchall()
        endpoint_map = {row["id"]: row["status"] for row in endpoints}
        missing_endpoints = sorted(set(endpoint_ids) - set(endpoint_map))
        if missing_endpoints:
            raise RegistryIntegrityError(
                "permission grant endpoint is missing or belongs to another source: "
                + ", ".join(missing_endpoints)
            )

        evidence_ids = payload["evidence_ids"]
        placeholders = ",".join("?" for _ in evidence_ids)
        evidence_rows = connection.execute(
            f"""SELECT id, review_status FROM permission_evidence
                WHERE source_id = ? AND id IN ({placeholders})""",
            (source_id, *evidence_ids),
        ).fetchall()
        evidence_map = {row["id"]: row["review_status"] for row in evidence_rows}
        missing_evidence = sorted(set(evidence_ids) - set(evidence_map))
        if missing_evidence:
            raise RegistryIntegrityError(
                "permission grant evidence is missing or belongs to another source: "
                + ", ".join(missing_evidence)
            )

        approved_grant = payload["status"] == "approved"
        if approved_grant:
            if source["status"] != "active":
                raise RegistryIntegrityError("an approved grant requires an active source")
            if any(status != "active" for status in endpoint_map.values()):
                raise RegistryIntegrityError("an approved grant requires active endpoints")
            if any(status != "verified" for status in evidence_map.values()):
                raise RegistryIntegrityError("an approved grant requires verified evidence")

        for operation, decision in payload["operations"].items():
            if decision["allowed"]:
                operation_evidence = decision.get("evidence_ids", [])
                if not set(operation_evidence).issubset(set(evidence_ids)):
                    raise RegistryIntegrityError(
                        f"{operation} evidence must also appear in grant evidence_ids"
                    )
                if any(evidence_map.get(item) != "verified" for item in operation_evidence):
                    raise RegistryIntegrityError(
                        f"{operation} requires verified, same-source evidence"
                    )

    def _insert_grant_links(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        grant_id = payload["id"]
        source_id = payload["source_id"]
        for endpoint_id in payload["endpoint_ids"]:
            connection.execute(
                "INSERT INTO grant_endpoints(grant_id, source_id, endpoint_id) VALUES (?, ?, ?)",
                (grant_id, source_id, endpoint_id),
            )
        for evidence_id in payload["evidence_ids"]:
            connection.execute(
                "INSERT INTO grant_evidence(grant_id, source_id, evidence_id) VALUES (?, ?, ?)",
                (grant_id, source_id, evidence_id),
            )
        for operation, decision in payload["operations"].items():
            for evidence_id in decision.get("evidence_ids", []):
                connection.execute(
                    """INSERT INTO grant_operation_evidence
                    (grant_id, source_id, operation, evidence_id) VALUES (?, ?, ?, ?)""",
                    (grant_id, source_id, operation, evidence_id),
                )

    def _insert_grant_revision(
        self,
        connection: sqlite3.Connection,
        *,
        grant_id: str,
        revision: int,
        payload: dict[str, Any],
        actor: str,
        reason: str,
        created_at: str | None = None,
    ) -> None:
        connection.execute(
            """INSERT INTO permission_grant_revisions
            (grant_id, revision, created_at, actor, reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                grant_id,
                revision,
                created_at or _utc_now(),
                actor,
                reason,
                _json_text(payload),
            ),
        )

    def add_permission_grant(self, payload: dict[str, Any]) -> PermissionGrantRecord:
        validate_payload("source-permission-grant", payload)
        starts = _aware_datetime(payload["effective_from"], "effective_from")
        ends = _aware_datetime(payload["effective_until"], "effective_until")
        if ends <= starts:
            raise RegistryIntegrityError("effective_until must be later than effective_from")
        record = PermissionGrantRecord.from_payload(payload)
        with self._transaction() as connection:
            self._validate_grant_references(connection, payload)
            connection.execute(
                """INSERT INTO permission_grants
                (id, source_id, status, effective_from, effective_until, revision, payload_json)
                VALUES (?, ?, ?, ?, ?, 1, ?)""",
                (
                    record.id,
                    record.source_id,
                    record.status,
                    record.effective_from,
                    record.effective_until,
                    record.payload_json,
                ),
            )
            self._insert_grant_links(connection, payload)
            self._insert_grant_revision(
                connection,
                grant_id=record.id,
                revision=1,
                payload=payload,
                actor=payload.get("reviewed_by", "system"),
                reason="grant created",
            )
            self._append_audit(
                connection,
                event_type="permission_grant.created",
                entity_type="permission_grant",
                entity_id=record.id,
                payload={"revision": 1, "record": payload},
            )
        return record

    def get_source(self, source_id: str) -> SourceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        return SourceRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def get_endpoint(self, endpoint_id: str) -> SourceEndpointRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM source_endpoints WHERE id = ?", (endpoint_id,)
            ).fetchone()
        return SourceEndpointRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def get_permission_evidence(self, evidence_id: str) -> PermissionEvidenceRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM permission_evidence WHERE id = ?", (evidence_id,)
            ).fetchone()
        return (
            PermissionEvidenceRecord.from_payload(json.loads(row["payload_json"]))
            if row
            else None
        )

    def get_permission_grant(self, grant_id: str) -> PermissionGrantRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
        return PermissionGrantRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def transition_permission_grant(
        self,
        grant_id: str,
        new_status: str,
        *,
        actor: str,
        reason: str,
        at: str | None = None,
    ) -> PermissionGrantRecord:
        """Append a grant revision for approval, denial, suspension, or revocation."""
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        if not isinstance(new_status, str):
            raise ValueError("new_status must be a string")
        allowed_transitions = {
            "pending": {"approved", "denied", "suspended", "revoked"},
            "approved": {"suspended", "revoked"},
            "suspended": {"approved", "revoked"},
            "denied": set(),
            "revoked": set(),
        }
        stamp = _utc_now() if at is None else at
        _aware_datetime(stamp, "transition timestamp")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("unknown permission grant")
            payload = self._load_payload(row)
            old_status = payload["status"]
            if new_status not in allowed_transitions.get(old_status, set()):
                raise RegistryIntegrityError(
                    f"invalid permission grant transition: {old_status} -> {new_status}"
                )
            payload["status"] = new_status
            if new_status == "approved":
                payload["reviewed_at"] = stamp
                payload["reviewed_by"] = actor
                payload.pop("revoked_at", None)
                payload.pop("revocation_reason", None)
                self._validate_grant_references(connection, payload)
            elif new_status == "revoked":
                payload["revoked_at"] = stamp
                payload["revocation_reason"] = reason
            elif new_status == "suspended":
                payload["notes"] = (payload.get("notes", "") + f"\nSuspended: {reason}").strip()
            validate_payload("source-permission-grant", payload)
            revision = row["revision"] + 1
            self._insert_grant_revision(
                connection,
                grant_id=grant_id,
                revision=revision,
                payload=payload,
                actor=actor,
                reason=reason,
                created_at=stamp,
            )
            connection.execute(
                """UPDATE permission_grants
                SET status = ?, revision = ?, payload_json = ?
                WHERE id = ? AND revision = ?""",
                (new_status, revision, _json_text(payload), grant_id, row["revision"]),
            )
            self._append_audit(
                connection,
                event_type="permission_grant.transitioned",
                entity_type="permission_grant",
                entity_id=grant_id,
                payload={
                    "from": old_status,
                    "to": new_status,
                    "revision": revision,
                    "actor": actor,
                    "reason": reason,
                    "record": payload,
                },
            )
        return PermissionGrantRecord.from_payload(payload)

    def evaluate_grant(
        self,
        grant_id: str,
        operation: str,
        *,
        endpoint_id: str,
        now: datetime | None = None,
    ) -> PermissionDecision:
        """Resolve and verify stored scope/evidence before evaluating a grant."""
        if not isinstance(grant_id, str) or not grant_id.strip():
            return PermissionDecision(False, "invalid_permission_id")
        evaluation_time = now if now is not None else datetime.now(timezone.utc)
        grant_revision = None
        permission_record_hash = None
        evidence_snapshot: dict[str, str] = {}
        source_status = None
        endpoint_status = None
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM permission_grants WHERE id = ?", (grant_id,)
            ).fetchone()
            if row is None:
                decision = PermissionDecision(False, "no_permission_record", grant_id)
                source_id = None
            else:
                grant_revision = row["revision"]
                payload = self._load_payload(row)
                permission_record_hash = sha256_fingerprint(payload)
                source_id = payload["source_id"]
                source = connection.execute(
                    "SELECT status FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
                source_status = source["status"] if source else None
                endpoint = None
                if isinstance(endpoint_id, str) and endpoint_id.strip():
                    endpoint = connection.execute(
                        """SELECT status FROM source_endpoints
                        WHERE id = ? AND source_id = ?""",
                        (endpoint_id, source_id),
                    ).fetchone()
                endpoint_status = endpoint["status"] if endpoint else None
                if not isinstance(operation, str) or operation not in OPERATIONS:
                    decision = PermissionDecision(False, "unknown_operation", grant_id)
                elif not isinstance(endpoint_id, str) or not endpoint_id.strip():
                    decision = PermissionDecision(False, "endpoint_scope_mismatch", grant_id)
                elif source is None:
                    decision = PermissionDecision(False, "source_record_missing", grant_id)
                elif source["status"] != "active":
                    decision = PermissionDecision(False, "source_not_active", grant_id)
                elif endpoint is None or endpoint_id not in payload.get("endpoint_ids", []):
                    decision = PermissionDecision(False, "endpoint_scope_mismatch", grant_id)
                elif endpoint["status"] != "active":
                    decision = PermissionDecision(False, "endpoint_not_active", grant_id)
                else:
                    all_evidence = payload.get("evidence_ids", [])
                    evidence_map: dict[str, str] = {}
                    for evidence_id in all_evidence:
                        evidence = connection.execute(
                            """SELECT review_status FROM permission_evidence
                            WHERE id = ? AND source_id = ?""",
                            (evidence_id, source_id),
                        ).fetchone()
                        if evidence is not None:
                            evidence_map[evidence_id] = evidence["review_status"]
                    evidence_snapshot = dict(evidence_map)
                    if len(evidence_map) != len(set(all_evidence)):
                        decision = PermissionDecision(
                            False, "permission_evidence_missing", grant_id
                        )
                    elif any(status != "verified" for status in evidence_map.values()) and payload.get("status") == "approved":
                        decision = PermissionDecision(
                            False, "permission_evidence_not_verified", grant_id
                        )
                    else:
                        operation_record = payload.get("operations", {}).get(operation, {})
                        operation_evidence = operation_record.get("evidence_ids", [])
                        if operation_record.get("allowed") is True and any(
                            evidence_map.get(item) != "verified" for item in operation_evidence
                        ):
                            decision = PermissionDecision(
                                False, "operation_evidence_not_verified", grant_id
                            )
                        else:
                            decision = evaluate_permission_record(
                                payload,
                                operation,
                                source_id=source_id,
                                endpoint_id=endpoint_id,
                                now=evaluation_time,
                            )
            self._append_audit(
                connection,
                event_type="permission.decision",
                entity_type="permission_grant",
                entity_id=grant_id,
                policy_version=POLICY_VERSION,
                payload={
                    "operation": operation,
                    "source_id": source_id,
                    "endpoint_id": endpoint_id,
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "permission_id": decision.permission_id,
                    "grant_revision": grant_revision,
                    "permission_record_hash": permission_record_hash,
                    "source_status": source_status,
                    "endpoint_status": endpoint_status,
                    "evidence_statuses": evidence_snapshot,
                    "evaluated_at": (
                        evaluation_time.isoformat()
                        if isinstance(evaluation_time, datetime)
                        else str(evaluation_time)
                    ),
                },
            )
        if grant_revision is not None:
            decision = PermissionDecision(
                decision.allowed,
                decision.reason,
                decision.permission_id,
                grant_revision,
            )
        return decision

    def grant_history(self, grant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM permission_grant_revisions WHERE grant_id = ?
                ORDER BY revision""",
                (grant_id,),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def append_many(self, observations: tuple[Any, ...]) -> None:
        """Persist a batch only while its exact permission grant remains current."""
        payloads: list[dict[str, Any]] = []
        for observation in observations:
            if is_dataclass(observation):
                payload = asdict(observation)
            elif isinstance(observation, dict):
                payload = dict(observation)
            else:
                raise RegistryIntegrityError("collection observation must be a dataclass or object")
            payloads.append(payload)
        if not payloads:
            return

        required = {
            "id", "source_id", "endpoint_id", "grant_id", "grant_revision",
            "operation", "connector_name", "connector_version", "observed_at",
            "locator", "final_locator", "content_fingerprint", "response_status",
            "content_type", "metadata", "permission_reason", "http_method",
        }
        for payload in payloads:
            if set(payload) != required:
                raise RegistryIntegrityError("collection observation fields are incomplete or unexpected")
            if payload["operation"] not in {"discovery", "metadata"}:
                raise RegistryIntegrityError("collection operation is not implemented")
            if not isinstance(payload["grant_revision"], int) or isinstance(payload["grant_revision"], bool):
                raise RegistryIntegrityError("collection observation requires a grant revision")
            for field in ("id", "source_id", "endpoint_id", "grant_id", "connector_name", "connector_version"):
                if not isinstance(payload[field], str) or not payload[field].strip():
                    raise RegistryIntegrityError(f"collection {field} is required")
            if not isinstance(payload["metadata"], dict):
                raise RegistryIntegrityError("collection metadata must be an object")
            metadata_keys = {"title", "byline", "published_at", "summary_metadata"}
            if set(payload["metadata"]) - metadata_keys:
                raise RegistryIntegrityError("collection metadata contains unsupported fields")
            if any(
                not isinstance(value, str) or len(value) > 1000
                for value in payload["metadata"].values()
            ):
                raise RegistryIntegrityError("collection metadata values must be bounded strings")
            if payload["operation"] == "discovery" and payload["metadata"]:
                raise RegistryIntegrityError("discovery observations cannot retain article metadata")
            for uri_field in ("locator", "final_locator"):
                uri = payload[uri_field]
                if uri is None and uri_field == "final_locator":
                    continue
                if not valid_http_uri(uri, max_length=2048):
                    raise RegistryIntegrityError(
                        f"collection {uri_field} must be a safe absolute HTTP(S) URI"
                    )
            fingerprint = payload["content_fingerprint"]
            if fingerprint is not None and (
                not isinstance(fingerprint, str)
                or not fingerprint.startswith("sha256:")
                or len(fingerprint) != 71
            ):
                raise RegistryIntegrityError("collection fingerprint must be a SHA-256 fingerprint")
            status = payload["response_status"]
            if status is not None and (
                isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599
            ):
                raise RegistryIntegrityError("collection response status is invalid")
            content_type = payload["content_type"]
            if content_type is not None and (
                not isinstance(content_type, str) or len(content_type) > 160
            ):
                raise RegistryIntegrityError("collection content type is invalid")
            if payload["http_method"] != "GET":
                raise RegistryIntegrityError("collection observations currently support GET only")
            if payload["permission_reason"] != "approved":
                raise RegistryIntegrityError("collection observation lacks an approved permission decision")
            _aware_datetime(payload["observed_at"], "observed_at")

        with self._transaction() as connection:
            for payload in payloads:
                source = connection.execute(
                    "SELECT status, payload_json FROM sources WHERE id = ?",
                    (payload["source_id"],),
                ).fetchone()
                endpoint = connection.execute(
                    """SELECT status, endpoint_uri FROM source_endpoints
                    WHERE id = ? AND source_id = ?""",
                    (payload["endpoint_id"], payload["source_id"]),
                ).fetchone()
                grant = connection.execute(
                    """SELECT revision, payload_json FROM permission_grants
                    WHERE id = ? AND source_id = ?""",
                    (payload["grant_id"], payload["source_id"]),
                ).fetchone()
                if source is None or source["status"] != "active":
                    raise RegistryIntegrityError("collection source is missing or inactive")
                if endpoint is None or endpoint["status"] != "active":
                    raise RegistryIntegrityError("collection endpoint is missing or inactive")
                allowed_domains = json.loads(source["payload_json"]).get("domains", [])
                if not valid_domain_allowlist(allowed_domains):
                    raise RegistryIntegrityError("collection source domain allowlist is invalid")
                endpoint_host = urlsplit(endpoint["endpoint_uri"]).hostname or ""
                if not _host_in_domains(endpoint_host, allowed_domains):
                    raise RegistryIntegrityError("collection endpoint is outside the registered source domains")
                for uri_field in ("locator", "final_locator"):
                    uri_value = payload[uri_field]
                    if uri_value is None:
                        continue
                    uri_host = urlsplit(uri_value).hostname or ""
                    if not _host_in_domains(uri_host, allowed_domains):
                        raise RegistryIntegrityError(
                            f"collection {uri_field} is outside the registered source domains"
                        )
                if grant is None:
                    raise RegistryIntegrityError("collection grant is missing or belongs to another source")
                if grant["revision"] != payload["grant_revision"]:
                    raise RegistryIntegrityError("permission grant changed during collection")
                grant_payload = json.loads(grant["payload_json"])
                if grant_payload["status"] != "approved":
                    raise RegistryIntegrityError("permission grant is no longer approved")

                evidence_ids = grant_payload.get("evidence_ids", [])
                evidence_statuses: dict[str, str] = {}
                for evidence_id in evidence_ids:
                    evidence = connection.execute(
                        """SELECT review_status FROM permission_evidence
                        WHERE id = ? AND source_id = ?""",
                        (evidence_id, payload["source_id"]),
                    ).fetchone()
                    if evidence is not None:
                        evidence_statuses[evidence_id] = evidence["review_status"]
                if len(evidence_statuses) != len(set(evidence_ids)) or any(
                    status != "verified" for status in evidence_statuses.values()
                ):
                    raise RegistryIntegrityError("collection permission evidence is no longer verified")

                decision = evaluate_permission_record(
                    grant_payload,
                    payload["operation"],
                    source_id=payload["source_id"],
                    endpoint_id=payload["endpoint_id"],
                    now=datetime.now(timezone.utc),
                )
                if not decision.allowed or payload["permission_reason"] != decision.reason:
                    raise RegistryIntegrityError("permission is not valid at observation storage time")

                metadata_json = _json_text(payload["metadata"])
                connection.execute(
                    """INSERT INTO collection_observations
                    (observation_id, source_id, endpoint_id, grant_id, grant_revision,
                     operation, connector_name, connector_version, observed_at, locator,
                     final_locator, content_fingerprint, response_status, content_type,
                     metadata_json, permission_reason, http_method)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["id"], payload["source_id"], payload["endpoint_id"],
                        payload["grant_id"], payload["grant_revision"], payload["operation"],
                        payload["connector_name"], payload["connector_version"],
                        payload["observed_at"], payload["locator"], payload["final_locator"],
                        payload["content_fingerprint"], payload["response_status"],
                        payload["content_type"], metadata_json, payload["permission_reason"],
                        payload["http_method"],
                    ),
                )
                self._append_audit(
                    connection,
                    event_type="collection.observation_recorded",
                    entity_type="collection_observation",
                    entity_id=payload["id"],
                    payload={
                        "source_id": payload["source_id"],
                        "endpoint_id": payload["endpoint_id"],
                        "grant_id": payload["grant_id"],
                        "grant_revision": payload["grant_revision"],
                        "operation": payload["operation"],
                        "http_method": payload["http_method"],
                        "locator": payload["locator"],
                        "content_fingerprint": payload["content_fingerprint"],
                    },
                    policy_version=POLICY_VERSION,
                )

    def get_collection_observation(self, observation_id: str) -> dict[str, Any] | None:
        """Return one stored collection observation with its original metadata."""
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM collection_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["observation_id"],
            "source_id": row["source_id"],
            "endpoint_id": row["endpoint_id"],
            "grant_id": row["grant_id"],
            "grant_revision": row["grant_revision"],
            "operation": row["operation"],
            "connector_name": row["connector_name"],
            "connector_version": row["connector_version"],
            "observed_at": row["observed_at"],
            "locator": row["locator"],
            "final_locator": row["final_locator"],
            "content_fingerprint": row["content_fingerprint"],
            "response_status": row["response_status"],
            "content_type": row["content_type"],
            "metadata": json.loads(row["metadata_json"]),
            "permission_reason": row["permission_reason"],
            "http_method": row["http_method"],
        }

    @staticmethod
    def _ingestion_decision_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "decision_id": row["decision_id"],
            "observation_id": row["observation_id"],
            "decision_revision": row["decision_revision"],
            "source_id": row["source_id"],
            "article_id": row["article_id"],
            "outcome": row["outcome"],
            "duplicate_of_article_id": row["duplicate_of_article_id"],
            "fingerprint": row["fingerprint"],
            "canonical_uri": row["canonical_uri"],
            "reason": row["reason"],
            "decided_at": row["decided_at"],
        }

    def get_ingestion_decision(self, observation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
        return self._ingestion_decision_payload(row) if row else None

    def ingestion_decision_history(self, observation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT * FROM ingestion_decisions
                WHERE observation_id = ? ORDER BY decision_revision""",
                (observation_id,),
            ).fetchall()
        return [self._ingestion_decision_payload(row) for row in rows]

    def _check_ingestion_permission(
        self, connection: sqlite3.Connection, observation: sqlite3.Row
    ) -> None:
        if observation["operation"] != "metadata" or observation["http_method"] != "GET":
            raise RegistryIntegrityError("only GET metadata observations can be ingested")
        if observation["permission_reason"] != "approved":
            raise RegistryIntegrityError("observation lacks an approved permission decision")
        source = connection.execute(
            "SELECT status FROM sources WHERE id = ?", (observation["source_id"],)
        ).fetchone()
        endpoint = connection.execute(
            """SELECT status, payload_json FROM source_endpoints
            WHERE id = ? AND source_id = ?""",
            (observation["endpoint_id"], observation["source_id"]),
        ).fetchone()
        grant = connection.execute(
            """SELECT revision, payload_json FROM permission_grants
            WHERE id = ? AND source_id = ?""",
            (observation["grant_id"], observation["source_id"]),
        ).fetchone()
        if source is None or source["status"] != "active":
            raise RegistryIntegrityError("ingestion source is missing or inactive")
        if endpoint is None or endpoint["status"] != "active":
            raise RegistryIntegrityError("ingestion endpoint is missing or inactive")
        if grant is None or grant["revision"] != observation["grant_revision"]:
            raise RegistryIntegrityError("permission grant changed since the observation")
        source_row = connection.execute(
            "SELECT payload_json FROM sources WHERE id = ?", (observation["source_id"],)
        ).fetchone()
        source_domains = json.loads(source_row["payload_json"]).get("domains", [])
        if not valid_domain_allowlist(source_domains):
            raise RegistryIntegrityError("ingestion source domain allowlist is invalid")
        for locator in (observation["locator"], observation["final_locator"]):
            if locator is None:
                continue
            if not valid_http_uri(locator, max_length=2048):
                raise RegistryIntegrityError("ingestion observation locator is malformed")
            host = urlsplit(locator).hostname or ""
            if not _host_in_domains(host, source_domains):
                raise RegistryIntegrityError("ingestion observation locator is outside source scope")
        grant_payload = json.loads(grant["payload_json"])
        if grant_payload.get("status") != "approved":
            raise RegistryIntegrityError("permission grant is no longer approved")
        evidence_ids = grant_payload.get("evidence_ids", [])
        evidence_statuses: dict[str, str] = {}
        for evidence_id in evidence_ids:
            evidence = connection.execute(
                """SELECT review_status FROM permission_evidence
                WHERE id = ? AND source_id = ?""",
                (evidence_id, observation["source_id"]),
            ).fetchone()
            if evidence is not None:
                evidence_statuses[evidence_id] = evidence["review_status"]
        if len(evidence_statuses) != len(set(evidence_ids)) or any(
            status != "verified" for status in evidence_statuses.values()
        ):
            raise RegistryIntegrityError("ingestion permission evidence is no longer verified")
        metadata_operation = grant_payload.get("operations", {}).get("metadata", {})
        operation_evidence = metadata_operation.get("evidence_ids", [])
        if metadata_operation.get("allowed") is not True or any(
            evidence_statuses.get(item) != "verified" for item in operation_evidence
        ):
            raise RegistryIntegrityError("metadata permission evidence is no longer verified")
        decision = evaluate_permission_record(
            grant_payload,
            "metadata",
            source_id=observation["source_id"],
            endpoint_id=observation["endpoint_id"],
            now=datetime.now(timezone.utc),
        )
        if not decision.allowed:
            raise RegistryIntegrityError("metadata permission is no longer valid")

    def ingest_article_observation(
        self,
        observation_id: str,
        article_payload: dict[str, Any],
        *,
        duplicate_fingerprint: str | None,
    ) -> dict[str, Any]:
        """Atomically create/reuse an article and record an explainable identity decision."""
        validate_payload("article", article_payload)
        if duplicate_fingerprint is not None and (
            not isinstance(duplicate_fingerprint, str)
            or not duplicate_fingerprint.startswith("sha256:")
            or len(duplicate_fingerprint) != 71
            or any(char not in "0123456789abcdef" for char in duplicate_fingerprint[7:])
        ):
            raise RegistryIntegrityError("ingestion duplicate fingerprint must be SHA-256")
        with self._transaction() as connection:
            existing_decision = connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
            if existing_decision is not None:
                return self._ingestion_decision_payload(existing_decision)

            observation = connection.execute(
                "SELECT * FROM collection_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
            if observation is None:
                raise RegistryIntegrityError("collection observation does not exist")
            if (
                article_payload.get("source_id") != observation["source_id"]
                or article_payload.get("permission_id") != observation["grant_id"]
            ):
                raise RegistryIntegrityError("article source/permission does not match its observation")
            from .ingestion import language_is_supported, normalize_observation
            expected_payload, expected_fingerprint = normalize_observation(
                self.get_collection_observation(observation_id),
                article_payload.get("source_language", ""),
            )
            if article_payload != expected_payload or duplicate_fingerprint != expected_fingerprint:
                raise RegistryIntegrityError(
                    "article payload and duplicate fingerprint do not match normalized source observation"
                )
            self._check_ingestion_permission(connection, observation)
            endpoint_payload = json.loads(connection.execute(
                "SELECT payload_json FROM source_endpoints WHERE id = ?",
                (observation["endpoint_id"],),
            ).fetchone()["payload_json"])
            declared_languages = endpoint_payload.get("supported_languages", [])
            article_language = article_payload.get("source_language", "")
            try:
                language_supported = language_is_supported(article_language, declared_languages)
            except ValueError as exc:
                raise RegistryIntegrityError("endpoint language declaration is invalid") from exc
            if not language_supported:
                raise RegistryIntegrityError("article language is outside the endpoint language declaration")

            existing_article = connection.execute(
                """SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = 'article'
                  AND json_extract(payload_json, '$.source_id') = ?
                  AND json_extract(payload_json, '$.canonical_uri') = ?
                ORDER BY created_at, id LIMIT 1""",
                (article_payload["source_id"], article_payload["canonical_uri"]),
            ).fetchone()

            if existing_article is not None:
                article_id = existing_article["id"]
                outcome = "reused"
                existing_payload = json.loads(existing_article["payload_json"])
                incoming_time = _aware_datetime(article_payload["updated_at"], "updated_at")
                existing_time = _aware_datetime(
                    existing_payload.get("updated_at", existing_payload["discovered_at"]),
                    "updated_at",
                )
                if incoming_time > existing_time:
                    refreshed = dict(existing_payload)
                    for field in (
                        "title", "source_language", "updated_at", "provenance",
                        "byline", "published_at", "summary_metadata", "content_fingerprint",
                    ):
                        if field in article_payload:
                            refreshed[field] = article_payload[field]
                    validate_payload("article", refreshed)
                    next_revision = existing_article["revision"] + 1
                    refreshed_json = _json_text(refreshed)
                    connection.execute(
                        """UPDATE domain_records
                        SET revision = ?, updated_at = ?, payload_json = ?
                        WHERE record_type = 'article' AND id = ? AND revision = ?""",
                        (
                            next_revision, article_payload["updated_at"], refreshed_json,
                            article_id, existing_article["revision"],
                        ),
                    )
                    connection.execute(
                        """INSERT INTO domain_record_revisions
                        (record_type, record_id, revision, created_at, actor, reason, payload_json)
                        VALUES ('article', ?, ?, ?, 'ingestion', 'metadata refreshed from newer observation', ?)""",
                        (article_id, next_revision, _utc_now(), refreshed_json),
                    )
                    self._append_audit(
                        connection,
                        event_type="article.revised",
                        entity_type="article",
                        entity_id=article_id,
                        payload={
                            "revision": next_revision,
                            "actor": "ingestion",
                            "reason": "metadata refreshed from newer observation",
                            "observation_id": observation_id,
                        },
                    )
                    reason = "same_source_article_reused_and_metadata_refreshed"
                else:
                    reason = "same_source_canonical_uri_already_exists_newer_or_equal_metadata_retained"
            else:
                self._validate_domain_references(connection, "article", article_payload)
                article_json = _json_text(article_payload)
                created_at = article_payload["discovered_at"]
                connection.execute(
                    """INSERT INTO domain_records
                    (record_type, id, revision, created_at, updated_at, payload_json)
                    VALUES ('article', ?, 1, ?, ?, ?)""",
                    (article_payload["id"], created_at, _utc_now(), article_json),
                )
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('article', ?, 1, ?, 'ingestion', 'article ingested from observation', ?)""",
                    (article_payload["id"], _utc_now(), article_json),
                )
                article_id = article_payload["id"]
                outcome = "created"
                reason = "new_source_article_identity"

            duplicate_of = None
            if outcome == "created" and duplicate_fingerprint is not None:
                candidate = connection.execute(
                    """SELECT d.article_id FROM ingestion_current c
                    JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                    JOIN domain_records a ON a.record_type = 'article' AND a.id = d.article_id
                    WHERE d.fingerprint = ? AND d.source_id != ?
                      AND d.outcome IN ('created', 'reused', 'distinct')
                      AND json_extract(a.payload_json, '$.record_status') = 'active'
                      AND NOT EXISTS (
                          SELECT 1 FROM ingestion_current c2
                          JOIN ingestion_decisions d2 ON d2.decision_id = c2.decision_id
                          WHERE d2.article_id = d.article_id
                            AND (
                                d2.decided_at > d.decided_at
                                OR (d2.decided_at = d.decided_at AND d2.decision_id > d.decision_id)
                            )
                      )
                    ORDER BY d.decided_at, d.decision_id LIMIT 1""",
                    (duplicate_fingerprint, article_payload["source_id"]),
                ).fetchone()
                if candidate is not None and candidate["article_id"] != article_id:
                    outcome = "possible_duplicate"
                    duplicate_of = candidate["article_id"]
                    reason = "matching_normalized_title_language_and_publication_time"

            decision_id = f"ingestion-decision:{uuid.uuid4().hex}"
            decided_at = _utc_now()
            connection.execute(
                """INSERT INTO ingestion_decisions
                (decision_id, observation_id, decision_revision, source_id, article_id,
                 outcome, duplicate_of_article_id, fingerprint, canonical_uri, reason, decided_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, observation_id, article_payload["source_id"], article_id,
                    outcome, duplicate_of, duplicate_fingerprint,
                    article_payload["canonical_uri"], reason, decided_at,
                ),
            )
            connection.execute(
                "INSERT INTO ingestion_current(observation_id, decision_id) VALUES (?, ?)",
                (observation_id, decision_id),
            )
            self._append_audit(
                connection,
                event_type="ingestion.decided",
                entity_type="ingestion_decision",
                entity_id=decision_id,
                payload={
                    "observation_id": observation_id,
                    "article_id": article_id,
                    "outcome": outcome,
                    "duplicate_of_article_id": duplicate_of,
                    "fingerprint": duplicate_fingerprint,
                    "reason": reason,
                    "decision_revision": 1,
                },
            )
            row = connection.execute(
                "SELECT * FROM ingestion_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._ingestion_decision_payload(row)

    def resolve_ingestion_duplicate(
        self,
        observation_id: str,
        *,
        outcome: str,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Append a human resolution; never merges or deletes either article."""
        if not isinstance(outcome, str) or outcome not in {"confirmed_duplicate", "distinct"}:
            raise ValueError("outcome must be confirmed_duplicate or distinct")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 800:
            raise ValueError("a bounded resolution reason is required")
        if len(actor) > 200 or len(actor) + 2 + len(reason) > 1000:
            raise ValueError("actor and reason exceed the 1000-character audit limit")
        with self._transaction() as connection:
            current = connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE c.observation_id = ?""",
                (observation_id,),
            ).fetchone()
            if current is None:
                raise RegistryIntegrityError("ingestion decision does not exist")
            if current["decision_revision"] != expected_revision:
                raise RegistryIntegrityError("ingestion decision revision conflict")
            if current["outcome"] not in {"possible_duplicate", "confirmed_duplicate", "distinct"}:
                raise RegistryIntegrityError("ingestion decision is not a duplicate candidate")
            duplicate_of = current["duplicate_of_article_id"]
            if duplicate_of is None:
                prior_candidate = connection.execute(
                    """SELECT duplicate_of_article_id FROM ingestion_decisions
                    WHERE observation_id = ? AND duplicate_of_article_id IS NOT NULL
                    ORDER BY decision_revision DESC LIMIT 1""",
                    (observation_id,),
                ).fetchone()
                duplicate_of = prior_candidate["duplicate_of_article_id"] if prior_candidate else None
            if outcome == "confirmed_duplicate" and duplicate_of is None:
                raise RegistryIntegrityError("no duplicate candidate is available to confirm")
            decision_id = f"ingestion-decision:{uuid.uuid4().hex}"
            decided_at = _utc_now()
            next_revision = expected_revision + 1
            connection.execute(
                """INSERT INTO ingestion_decisions
                (decision_id, observation_id, decision_revision, source_id, article_id,
                 outcome, duplicate_of_article_id, fingerprint, canonical_uri, reason, decided_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id, observation_id, next_revision, current["source_id"],
                    current["article_id"], outcome,
                    duplicate_of if outcome == "confirmed_duplicate" else None,
                    current["fingerprint"], current["canonical_uri"],
                    f"{actor}: {reason}", decided_at,
                ),
            )
            connection.execute(
                """UPDATE ingestion_current SET decision_id = ?
                WHERE observation_id = ? AND decision_id = ?""",
                (decision_id, observation_id, current["decision_id"]),
            )
            self._append_audit(
                connection,
                event_type="ingestion.duplicate_resolved",
                entity_type="ingestion_decision",
                entity_id=decision_id,
                payload={
                    "observation_id": observation_id,
                    "article_id": current["article_id"],
                    "previous_decision_id": current["decision_id"],
                    "outcome": outcome,
                    "duplicate_of_article_id": duplicate_of if outcome == "confirmed_duplicate" else None,
                    "actor": actor,
                    "reason": reason,
                    "decision_revision": next_revision,
                },
            )
            row = connection.execute(
                "SELECT * FROM ingestion_decisions WHERE decision_id = ?",
                (decision_id,),
            ).fetchone()
            return self._ingestion_decision_payload(row)

    def collection_observations(
        self, *, source_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM collection_observations"
        params: tuple[Any, ...] = ()
        if source_id is not None:
            query += " WHERE source_id = ?"
            params = (source_id,)
        query += " ORDER BY observed_at, observation_id"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [
            {
                "id": row["observation_id"],
                "source_id": row["source_id"],
                "endpoint_id": row["endpoint_id"],
                "grant_id": row["grant_id"],
                "grant_revision": row["grant_revision"],
                "operation": row["operation"],
                "connector_name": row["connector_name"],
                "connector_version": row["connector_version"],
                "observed_at": row["observed_at"],
                "locator": row["locator"],
                "final_locator": row["final_locator"],
                "content_fingerprint": row["content_fingerprint"],
                "response_status": row["response_status"],
                "content_type": row["content_type"],
                "metadata": json.loads(row["metadata_json"]),
                "permission_reason": row["permission_reason"],
                "http_method": row["http_method"],
            }
            for row in rows
        ]

    def audit_events(self, *, entity_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: tuple[Any, ...] = ()
        if entity_id is not None:
            query += " WHERE entity_id = ?"
            params = (entity_id,)
        query += " ORDER BY sequence"
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "entity_type": row["entity_type"],
                "entity_id": row["entity_id"],
                "occurred_at": row["occurred_at"],
                "policy_version": row["policy_version"],
                "payload": json.loads(row["payload_json"]),
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
            }
            for row in rows
        ]

    def audit_checkpoint(self) -> dict[str, Any]:
        """Return the current chain tip for storage outside this database.

        Persist this sequence/hash pair in an independently controlled system
        if later verification must detect deletion or truncation of the chain tail.
        A checkpoint kept only in this database provides no external protection.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return {"sequence": 0, "event_hash": None}
        return {"sequence": row["sequence"], "event_hash": row["event_hash"]}

    def verify_audit_chain(self) -> bool:
        """Verify links from the first retained event; this cannot detect tail truncation."""
        events = self.audit_events()
        previous_hash = None
        for event in events:
            expected = sha256_fingerprint(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "entity_type": event["entity_type"],
                    "entity_id": event["entity_id"],
                    "occurred_at": event["occurred_at"],
                    "policy_version": event["policy_version"],
                    "payload": event["payload"],
                    "previous_hash": previous_hash,
                }
            )
            if event["previous_hash"] != previous_hash or event["event_hash"] != expected:
                return False
            previous_hash = event["event_hash"]
        return True

    _DOMAIN_MODELS = {
        "article": ArticleRecord,
        "story": StoryRecord,
        "story_decision": StoryDecisionRecord,
        "claim": ClaimRecord,
        "evidence": EvidenceRecord,
        "edition": EditionRecord,
    }

    def _validate_domain_references(
        self, connection: sqlite3.Connection, record_type: str, payload: dict[str, Any]
    ) -> None:
        def require_record(kind: str, record_id: str, label: str) -> dict[str, Any]:
            row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = ? AND id = ?",
                (kind, record_id),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"{label} references an unknown {kind}: {record_id}")
            return json.loads(row["payload_json"])

        if record_type == "article":
            source = connection.execute(
                "SELECT 1 FROM sources WHERE id = ?", (payload["source_id"],)
            ).fetchone()
            if source is None:
                raise RegistryIntegrityError("article references an unknown source")
            permission_id = payload.get("permission_id")
            if permission_id:
                grant = connection.execute(
                    "SELECT source_id FROM permission_grants WHERE id = ?", (permission_id,)
                ).fetchone()
                if grant is None or grant["source_id"] != payload["source_id"]:
                    raise RegistryIntegrityError(
                        "article permission reference is missing or belongs to another source"
                    )
        elif record_type == "story":
            article_ids = set(payload.get("article_ids", []))
            for article_id in article_ids:
                require_record("article", article_id, "story")
            existing_claims = connection.execute(
                """SELECT payload_json FROM domain_records
                WHERE record_type = 'claim'
                  AND json_extract(payload_json, '$.story_id') = ?""",
                (payload["id"],),
            ).fetchall()
            for row in existing_claims:
                claim_payload = json.loads(row["payload_json"])
                if any(
                    item.get("article_id") is not None
                    and item["article_id"] not in article_ids
                    for item in claim_payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        "story revision would orphan existing claim provenance"
                    )
        elif record_type == "claim":
            story_payload = require_record("story", payload["story_id"], "claim")
            story_article_ids = set(story_payload.get("article_ids", []))
            for provenance in payload["provenance"]:
                source_id = provenance["source_id"]
                source = connection.execute(
                    "SELECT 1 FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
                if source is None:
                    raise RegistryIntegrityError("claim provenance references an unknown source")
                article_id = provenance.get("article_id")
                observation_id = provenance.get("observation_id")
                if article_id:
                    article = require_record("article", article_id, "claim provenance")
                    if article["source_id"] != source_id:
                        raise RegistryIntegrityError(
                            "claim provenance article and source do not match"
                        )
                    if article_id not in story_article_ids:
                        raise RegistryIntegrityError(
                            "claim provenance article is not linked to its story"
                        )
                if observation_id:
                    observation = connection.execute(
                        "SELECT source_id FROM collection_observations WHERE observation_id = ?",
                        (observation_id,),
                    ).fetchone()
                    if observation is None or observation["source_id"] != source_id:
                        raise RegistryIntegrityError(
                            "claim provenance observation is missing or belongs to another source"
                        )
                if article_id and observation_id:
                    linked = connection.execute(
                        """SELECT 1 FROM ingestion_current c
                        JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                        WHERE c.observation_id = ? AND d.article_id = ?""",
                        (observation_id, article_id),
                    ).fetchone()
                    if linked is None:
                        raise RegistryIntegrityError(
                            "claim provenance article and observation are not linked by ingestion"
                        )
            existing_evidence = connection.execute(
                """SELECT id, payload_json FROM domain_records
                WHERE record_type = 'evidence'
                  AND json_extract(payload_json, '$.claim_id') = ?""",
                (payload["id"],),
            ).fetchall()
            for row in existing_evidence:
                evidence_payload = json.loads(row["payload_json"])
                evidence_source = evidence_payload["source_id"]
                evidence_article = evidence_payload.get("article_id")
                evidence_observation = evidence_payload.get("observation_id")
                if not any(
                    item.get("source_id") == evidence_source
                    and (evidence_article is None or item.get("article_id") == evidence_article)
                    and (evidence_observation is None or item.get("observation_id") == evidence_observation)
                    for item in payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        "claim revision would orphan existing evidence provenance"
                    )
            linked_evidence = []
            for evidence_id in payload.get("evidence_ids", []):
                evidence = require_record("evidence", evidence_id, "claim evidence")
                if evidence["claim_id"] != payload["id"]:
                    raise RegistryIntegrityError(
                        "claim evidence reference belongs to another claim"
                    )
                linked_evidence.append(evidence)
            assessment = payload["assessment"]
            relationships = {item["relationship"] for item in linked_evidence}
            if assessment != "unassessed":
                if not linked_evidence:
                    raise RegistryIntegrityError(
                        "an assessed claim must reference at least one evidence record"
                    )
                expected_evidence_ids = {row["id"] for row in existing_evidence}
                if set(payload.get("evidence_ids", [])) != expected_evidence_ids:
                    raise RegistryIntegrityError(
                        "reviewed claim must reference every evidence record linked to it"
                    )
                reviewed_at = payload.get("reviewed_at")
                review_note = payload.get("review_note")
                if not isinstance(reviewed_at, str) or not isinstance(review_note, str) or not review_note.strip():
                    raise RegistryIntegrityError(
                        "reviewed claim assessment requires a review timestamp and a non-empty review note (review_note)"
                    )
                if _aware_datetime(reviewed_at, "reviewed_at") < _aware_datetime(payload["created_at"], "created_at"):
                    raise RegistryIntegrityError("claim review cannot predate claim creation")
            if assessment == "supported" and (
                "supports" not in relationships or "contradicts" in relationships
            ):
                raise RegistryIntegrityError(
                    "supported claim requires supporting evidence and no unresolved contradiction"
                )
            if assessment == "partially_supported" and not (
                "supports" in relationships and ({"qualifies", "unclear"} & relationships)
            ):
                raise RegistryIntegrityError(
                    "partially supported claim requires support and qualifying/unclear evidence"
                )
            if assessment == "disputed" and not (
                "supports" in relationships and "contradicts" in relationships
            ):
                raise RegistryIntegrityError(
                    "disputed claim requires both supporting and contradicting evidence"
                )
            if assessment == "contradicted" and (
                "contradicts" not in relationships or "supports" in relationships
            ):
                raise RegistryIntegrityError(
                    "contradicted claim requires contradicting evidence without supporting evidence"
                )
            if assessment == "retracted" and not (
                isinstance(payload.get("review_note"), str) and payload["review_note"].strip()
            ):
                raise RegistryIntegrityError("retracted claim requires a non-empty review note")
        elif record_type == "evidence":
            claim = require_record("claim", payload["claim_id"], "evidence")
            if claim.get("assessment") != "unassessed":
                raise RegistryIntegrityError(
                    "claim must be reset to unassessed before adding new evidence"
                )
            source_id = payload["source_id"]
            source = connection.execute(
                "SELECT payload_json FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            if source is None:
                raise RegistryIntegrityError("evidence references an unknown source")
            source_domains = json.loads(source["payload_json"]).get("domains", [])
            if not valid_domain_allowlist(source_domains):
                raise RegistryIntegrityError("evidence source domain allowlist is invalid")
            article_id = payload.get("article_id")
            observation_id = payload.get("observation_id")
            if article_id:
                article = require_record("article", article_id, "evidence")
                if article["source_id"] != source_id:
                    raise RegistryIntegrityError(
                        "evidence article and source do not match"
                    )
            if observation_id:
                observation = connection.execute(
                    "SELECT source_id FROM collection_observations WHERE observation_id = ?",
                    (observation_id,),
                ).fetchone()
                if observation is None or observation["source_id"] != source_id:
                    raise RegistryIntegrityError(
                        "evidence observation is missing or belongs to another source"
                    )
            if article_id and observation_id:
                linked = connection.execute(
                    """SELECT 1 FROM ingestion_current c
                    JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                    WHERE c.observation_id = ? AND d.article_id = ?""",
                    (observation_id, article_id),
                ).fetchone()
                if linked is None:
                    raise RegistryIntegrityError(
                        "evidence article and observation are not linked by ingestion"
                    )
            provenance_matches = any(
                item.get("source_id") == source_id
                and (article_id is None or item.get("article_id") == article_id)
                and (observation_id is None or item.get("observation_id") == observation_id)
                for item in claim.get("provenance", [])
            )
            if not provenance_matches:
                raise RegistryIntegrityError(
                    "evidence source/article/observation is not present in claim provenance"
                )
            locator_uri = payload["locator"]["uri"]
            try:
                parsed_locator = urlsplit(locator_uri)
            except ValueError as exc:
                raise RegistryIntegrityError("evidence locator is malformed") from exc
            if parsed_locator.scheme.lower() not in {"http", "https"} or not parsed_locator.hostname:
                raise RegistryIntegrityError("evidence locator must be an absolute HTTP(S) URL")
            if parsed_locator.username is not None or parsed_locator.password is not None:
                raise RegistryIntegrityError("evidence locator cannot contain credentials")
            safe_locator = urlunsplit(parsed_locator._replace(fragment=""))
            if not valid_http_uri(safe_locator, max_length=2048):
                raise RegistryIntegrityError("evidence locator is malformed")
            if not _host_in_domains(parsed_locator.hostname, source_domains):
                raise RegistryIntegrityError("evidence locator is outside its registered source domains")
        elif record_type == "story_decision":
            story_ids = set(payload["story_ids"])
            if payload["canonical_story_id"] not in story_ids:
                raise RegistryIntegrityError(
                    "story decision canonical story must be one of the affected stories"
                )
            story_payloads = {
                story_id: require_record("story", story_id, "story decision")
                for story_id in story_ids
            }

            expected_story_revisions = payload["expected_story_revisions"]
            expected_story_ids = [item["story_id"] for item in expected_story_revisions]
            if len(expected_story_ids) != len(set(expected_story_ids)) or set(expected_story_ids) != story_ids:
                raise RegistryIntegrityError(
                    "story decision must capture exactly one expected revision for every affected story"
                )
            for item in expected_story_revisions:
                row = connection.execute(
                    "SELECT revision FROM domain_records WHERE record_type = 'story' AND id = ?",
                    (item["story_id"],),
                ).fetchone()
                if row is None or row["revision"] != item["expected_revision"]:
                    raise RegistryIntegrityError(
                        f"story decision expected revision is stale for story: {item['story_id']}"
                    )

            available_articles = {
                article_id
                for story_payload in story_payloads.values()
                for article_id in story_payload.get("article_ids", [])
            }
            assignments = payload["article_assignments"]
            assigned_articles = [item["article_id"] for item in assignments]
            article_destinations = {
                item["article_id"]: item["assigned_story_id"] for item in assignments
            }
            assigned_stories = {item["assigned_story_id"] for item in assignments}
            if len(assigned_articles) != len(set(assigned_articles)):
                raise RegistryIntegrityError("story decision cannot assign an article more than once")
            for assignment in assignments:
                article_id = assignment["article_id"]
                assigned_story_id = assignment["assigned_story_id"]
                require_record("article", article_id, "story decision")
                if article_id not in available_articles:
                    raise RegistryIntegrityError(
                        "story decision article must belong to at least one affected story"
                    )
                if assigned_story_id not in story_ids:
                    raise RegistryIntegrityError(
                        "story decision article assignment target must be in the decision scope"
                    )
            if payload["operation"] == "merge":
                if assigned_stories != {payload["canonical_story_id"]}:
                    raise RegistryIntegrityError(
                        "merge decision must assign every article to the canonical story"
                    )
                if set(assigned_articles) != available_articles:
                    raise RegistryIntegrityError(
                        "merge decision must account for every article in the affected stories"
                    )
            elif set(assigned_articles) != available_articles:
                raise RegistryIntegrityError(
                    "split decision must account for every article in the affected stories"
                )
            elif len(assigned_stories) < 2:
                raise RegistryIntegrityError(
                    "split decision must allocate articles to at least two resulting stories"
                )
            elif any(
                not any(item["assigned_story_id"] == story_id for item in assignments)
                for story_id in story_ids
            ):
                raise RegistryIntegrityError(
                    "split decision must leave at least one article in every resulting story"
                )

            claim_rows = connection.execute(
                """SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = 'claim'
                  AND json_extract(payload_json, '$.story_id') IN (
                      SELECT value FROM json_each(?)
                  )""",
                (_json_text(sorted(story_ids)),),
            ).fetchall()
            current_claims = {
                row["id"]: (row["revision"], json.loads(row["payload_json"]))
                for row in claim_rows
            }
            claim_assignments = payload["claim_assignments"]
            assigned_claim_ids = [item["claim_id"] for item in claim_assignments]
            if len(assigned_claim_ids) != len(set(assigned_claim_ids)):
                raise RegistryIntegrityError("story decision cannot assign a claim more than once")
            if set(assigned_claim_ids) != set(current_claims):
                raise RegistryIntegrityError(
                    "story decision must account for every claim in the affected stories"
                )
            for item in claim_assignments:
                claim_id = item["claim_id"]
                destination = item["assigned_story_id"]
                current_revision, claim_payload = current_claims[claim_id]
                if destination not in story_ids:
                    raise RegistryIntegrityError(
                        "story decision claim assignment target must be in the decision scope"
                    )
                if current_revision != item["expected_revision"]:
                    raise RegistryIntegrityError(
                        f"story decision expected revision is stale for claim: {claim_id}"
                    )
                provenance_destinations = {
                    article_destinations[entry["article_id"]]
                    for entry in claim_payload.get("provenance", [])
                    if entry.get("article_id") in article_destinations
                }
                if any(
                    entry.get("article_id") is not None
                    and entry["article_id"] not in article_destinations
                    for entry in claim_payload.get("provenance", [])
                ):
                    raise RegistryIntegrityError(
                        f"story decision is missing an article assignment used by claim: {claim_id}"
                    )
                if provenance_destinations and provenance_destinations != {destination}:
                    raise RegistryIntegrityError(
                        f"claim provenance does not fit its assigned resulting story: {claim_id}"
                    )
                if payload["operation"] == "merge" and destination != payload["canonical_story_id"]:
                    raise RegistryIntegrityError(
                        "merge decision must assign every claim to the canonical story"
                    )
        elif record_type == "edition":
            for story_id in payload["story_ids"]:
                require_record("story", story_id, "edition")

    @staticmethod
    def _domain_record_timestamp(record_type: str, payload: dict[str, Any]) -> str:
        field = {
            "article": "discovered_at",
            "story": "created_at",
            "claim": "created_at",
            "evidence": "assessed_at",
            "story_decision": "decided_at",
            "edition": "created_at",
        }[record_type]
        _aware_datetime(payload[field], field)
        return payload[field]

    def add_domain_record(self, record_type: str, payload: dict[str, Any]):
        """Persist a validated domain record."""
        if record_type == "story_decision":
            validate_payload(record_type, payload)
            record = StoryDecisionRecord.from_payload(payload)
            created_at = self._domain_record_timestamp(record_type, payload)
            with self._transaction() as connection:
                self._validate_domain_references(connection, record_type, payload)
                connection.execute(
                    """INSERT INTO story_decisions
                    (id, operation, canonical_story_id, story_ids_json, article_ids_json,
                     decided_by, rationale, decided_at, payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["id"],
                        payload["operation"],
                        payload["canonical_story_id"],
                        _json_text(payload["story_ids"]),
                        _json_text([item["article_id"] for item in payload["article_assignments"]]),
                        payload["decided_by"],
                        payload["rationale"],
                        created_at,
                        record.payload_json,
                    ),
                )
                self._append_audit(
                    connection,
                    event_type="story_decision.created",
                    entity_type="story_decision",
                    entity_id=record.id,
                    payload={"revision": 1, "record": payload},
                )
            return record
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        if record_type == "edition" and payload.get("status") == "published":
            raise RegistryIntegrityError(
                "published editions require the controlled release workflow; generic insertion is blocked"
            )
        validate_payload(record_type, payload)
        record = model.from_payload(payload)
        created_at = self._domain_record_timestamp(record_type, payload)
        updated_at = _utc_now()
        with self._transaction() as connection:
            self._validate_domain_references(connection, record_type, payload)
            connection.execute(
                """INSERT INTO domain_records
                (record_type, id, revision, created_at, updated_at, payload_json)
                VALUES (?, ?, 1, ?, ?, ?)""",
                (record_type, record.id, created_at, updated_at, record.payload_json),
            )
            connection.execute(
                """INSERT INTO domain_record_revisions
                (record_type, record_id, revision, created_at, actor, reason, payload_json)
                VALUES (?, ?, 1, ?, ?, ?, ?)""",
                (
                    record_type,
                    record.id,
                    _utc_now(),
                    "system",
                    "record created",
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type=f"{record_type}.created",
                entity_type=record_type,
                entity_id=record.id,
                payload={"revision": 1, "record": payload},
            )
        return record

    def story_reporting_independence(self, story_id: str) -> dict[str, Any]:
        """Summarize publisher count and duplicate-review state without claiming proof of independence."""
        story = self.get_domain_record("story", story_id)
        if story is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        story_payload = story.to_payload()
        article_ids = list(story_payload.get("article_ids", []))
        article_payloads: dict[str, dict[str, Any]] = {}
        for article_id in article_ids:
            article = self.get_domain_record("article", article_id)
            if article is None:
                raise RegistryIntegrityError(
                    f"story references a missing article: {article_id}"
                )
            article_payloads[article_id] = article.to_payload()

        with self._lock:
            rows = self._connection.execute(
                """SELECT d.* FROM ingestion_current c
                JOIN ingestion_decisions d ON d.decision_id = c.decision_id
                WHERE d.article_id IN (
                    SELECT id FROM domain_records WHERE record_type = 'article'
                )
                ORDER BY d.decided_at, d.decision_id"""
            ).fetchall()

        relevant = set(article_ids)
        latest_review: dict[str, sqlite3.Row] = {}
        for row in rows:
            if row["article_id"] not in relevant:
                continue
            if row["outcome"] not in {
                "possible_duplicate", "confirmed_duplicate", "distinct"
            }:
                continue
            previous = latest_review.get(row["article_id"])
            if previous is None or (
                row["decided_at"], row["decision_id"]
            ) > (
                previous["decided_at"], previous["decision_id"]
            ):
                latest_review[row["article_id"]] = row

        statuses: list[dict[str, Any]] = []
        unresolved: list[dict[str, str]] = []
        confirmed_pairs: list[dict[str, str]] = []
        reviewed_distinct = 0
        no_candidate = 0
        external_confirmed = 0

        parent = {article_id: article_id for article_id in article_ids}

        def find(item: str) -> str:
            while parent[item] != item:
                parent[item] = parent[parent[item]]
                item = parent[item]
            return item

        def union(left: str, right: str) -> None:
            root_left, root_right = find(left), find(right)
            if root_left != root_right:
                parent[root_right] = root_left

        for article_id in article_ids:
            payload = article_payloads[article_id]
            decision = latest_review.get(article_id)
            if decision is None:
                status = "no_candidate"
                duplicate_of = None
                no_candidate += 1
            else:
                status = decision["outcome"]
                duplicate_of = decision["duplicate_of_article_id"]
                if status == "possible_duplicate":
                    unresolved.append({
                        "article_id": article_id,
                        "duplicate_of_article_id": duplicate_of or "",
                    })
                elif status == "distinct":
                    reviewed_distinct += 1
                elif status == "confirmed_duplicate":
                    confirmed_pairs.append({
                        "article_id": article_id,
                        "duplicate_of_article_id": duplicate_of or "",
                    })
                    if duplicate_of in parent:
                        union(article_id, duplicate_of)
                    else:
                        external_confirmed += 1
            statuses.append({
                "article_id": article_id,
                "source_id": payload["source_id"],
                "duplicate_status": status,
                "duplicate_of_article_id": duplicate_of,
            })

        grouped: dict[str, list[str]] = {}
        for article_id in article_ids:
            root = find(article_id)
            grouped.setdefault(root, []).append(article_id)
        confirmed_groups = [
            sorted(group) for group in grouped.values() if len(group) > 1
        ]

        return {
            "story_id": story_id,
            "article_count": len(article_ids),
            "publisher_count": len({item["source_id"] for item in statuses}),
            "articles": statuses,
            "unresolved_candidates": unresolved,
            "confirmed_duplicate_pairs": confirmed_pairs,
            "confirmed_duplicate_groups": confirmed_groups,
            "unresolved_candidate_count": len(unresolved),
            "confirmed_duplicate_count": len(confirmed_pairs),
            "external_confirmed_duplicate_count": external_confirmed,
            "reviewed_distinct_count": reviewed_distinct,
            "no_candidate_count": no_candidate,
            "independence_claim": "not_established_by_publisher_count_or_no_candidate",
        }

    def story_source_independence_evidence_report(self, story_id: str) -> dict[str, Any]:
        """Expose article-identity review separately from evidence of independent reporting."""
        independence = self.story_reporting_independence(story_id)
        story = self.get_domain_record("story", story_id)
        if story is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        article_map = {}
        for article_id in story.to_payload().get("article_ids", []):
            article = self.get_domain_record("article", article_id)
            if article is None:
                raise RegistryIntegrityError(f"story references a missing article: {article_id}")
            article_map[article_id] = article.to_payload()

        articles = []
        for item in independence["articles"]:
            article = article_map[item["article_id"]]
            duplicate_status = item["duplicate_status"]
            if duplicate_status == "confirmed_duplicate":
                duplicate_review = "confirmed_duplicate"
            elif duplicate_status == "distinct":
                duplicate_review = "reviewed_distinct_article_only"
            elif duplicate_status == "possible_duplicate":
                duplicate_review = "unresolved_duplicate_candidate"
            else:
                duplicate_review = "not_reviewed"

            articles.append({
                "article_id": item["article_id"],
                "source_id": item["source_id"],
                "title": article.get("title"),
                "canonical_uri": article.get("canonical_uri"),
                "duplicate_review_status": duplicate_review,
                "duplicate_of_article_id": item["duplicate_of_article_id"],
                "independent_reporting_status": "not_established",
                "independence_evidence": {
                    "original_reporting_provenance": "not_assessed",
                    "syndication_or_republication": "not_assessed",
                    "shared_source_or_common_origin": "not_assessed",
                    "direct_interview_or_primary_document": "not_assessed",
                },
            })

        return {
            "story_id": story_id,
            "article_count": len(articles),
            "publisher_count": independence["publisher_count"],
            "articles": articles,
            "confirmed_duplicate_groups": independence["confirmed_duplicate_groups"],
            "unresolved_duplicate_candidates": independence["unresolved_candidates"],
            "independent_reporting_count": None,
            "independence_status": "not_established",
            "interpretation": (
                "A distinct-article review addresses whether two article records are duplicates; "
                "it does not establish independent reporting. Publisher identity, article count, "
                "different wording, and absence of a duplicate candidate are insufficient evidence."
            ),
            "evidence_required_before_independence_claim": [
                "reviewed original-reporting provenance for each article",
                "syndication and republication checks",
                "shared-source and common-origin checks",
                "where relevant, direct interviews or primary-document evidence",
            ],
            "production_ready": False,
            "authorization_granted": False,
        }

    def _preview_story_decision_application(
        self, connection: sqlite3.Connection, decision_id: str
    ) -> dict[str, Any]:
        """Build the preview using a caller-owned transaction."""
        decision_row = connection.execute(
            "SELECT payload_json FROM story_decisions WHERE id = ?",
            (decision_id,),
        ).fetchone()
        if decision_row is None:
            raise RegistryIntegrityError(f"unknown story decision: {decision_id}")
        decision = json.loads(decision_row["payload_json"])
        story_ids = sorted(set(decision["story_ids"]))
        application_row = connection.execute(
            """SELECT application_id, applied_at, actor, rationale,
                      decision_fingerprint, impact_fingerprint, result_json
            FROM story_decision_applications WHERE decision_id = ?""",
            (decision_id,),
        ).fetchone()
        application = (
            {
                "application_id": application_row["application_id"],
                "applied_at": application_row["applied_at"],
                "actor": application_row["actor"],
                "rationale": application_row["rationale"],
                "decision_fingerprint": application_row["decision_fingerprint"],
                "impact_fingerprint": application_row["impact_fingerprint"],
                "result": json.loads(application_row["result_json"]),
            }
            if application_row is not None else None
        )

        blockers: list[str] = []
        if application is not None:
            blockers.append("story decision has already been applied")
        try:
            self._validate_domain_references(connection, "story_decision", decision)
        except RegistryIntegrityError as exc:
            blockers.append(str(exc))

        def load_records(record_type: str, ids: list[str]) -> list[dict[str, Any]]:
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"""SELECT id, revision, payload_json FROM domain_records
                WHERE record_type = ? AND id IN ({placeholders}) ORDER BY id""",
                (record_type, *ids),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "revision": row["revision"],
                    "payload": json.loads(row["payload_json"]),
                }
                for row in rows
            ]

        stories = load_records("story", story_ids)
        article_ids = sorted({
            item["article_id"] for item in decision["article_assignments"]
        })
        articles = load_records("article", article_ids)

        claim_rows = connection.execute(
            """SELECT id, revision, payload_json FROM domain_records
            WHERE record_type = 'claim'
              AND json_extract(payload_json, '$.story_id') IN (
                  SELECT value FROM json_each(?)
              )
            ORDER BY id""",
            (_json_text(story_ids),),
        ).fetchall()
        claims = [
            {
                "id": row["id"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in claim_rows
        ]
        claim_ids = [item["id"] for item in claims]
        evidence = load_records("evidence", sorted(
            row["id"]
            for row in connection.execute(
                """SELECT id FROM domain_records
                WHERE record_type = 'evidence'
                  AND json_extract(payload_json, '$.claim_id') IN (
                      SELECT value FROM json_each(?)
                  )""",
                (_json_text(claim_ids),),
            ).fetchall()
        ))

        edition_rows = connection.execute(
            """SELECT DISTINCT d.id, d.revision, d.payload_json
            FROM domain_records d, json_each(d.payload_json, '$.story_ids') story_ref
            WHERE d.record_type = 'edition'
              AND story_ref.value IN (
                  SELECT value FROM json_each(?)
              )
            ORDER BY d.id""",
            (_json_text(story_ids),),
        ).fetchall()
        editions = [
            {
                "id": row["id"],
                "revision": row["revision"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in edition_rows
        ]

        if len(stories) != len(story_ids):
            blockers.append("one or more affected stories are missing")
        if len(articles) != len(article_ids):
            blockers.append("one or more assigned articles are missing")

        story_summaries = [
            {
                "story_id": item["id"],
                "revision": item["revision"],
                "status": item["payload"]["status"],
                "canonical_title": item["payload"]["canonical_title"],
                "article_ids": sorted(item["payload"].get("article_ids", [])),
            }
            for item in stories
        ]
        article_summaries = [
            {
                "article_id": item["id"],
                "source_id": item["payload"]["source_id"],
                "title": item["payload"]["title"],
                "assigned_story_id": next(
                    assignment["assigned_story_id"]
                    for assignment in decision["article_assignments"]
                    if assignment["article_id"] == item["id"]
                ),
            }
            for item in articles
        ]
        claim_destination = {
            item["claim_id"]: item["assigned_story_id"]
            for item in decision["claim_assignments"]
        }
        claim_summaries = [
            {
                "claim_id": item["id"],
                "revision": item["revision"],
                "current_story_id": item["payload"]["story_id"],
                "assigned_story_id": claim_destination.get(item["id"]),
                "assessment": item["payload"]["assessment"],
                "evidence_ids": sorted(item["payload"].get("evidence_ids", [])),
            }
            for item in claims
        ]
        evidence_summaries = [
            {
                "evidence_id": item["id"],
                "claim_id": item["payload"]["claim_id"],
                "source_id": item["payload"]["source_id"],
                "relationship": item["payload"]["relationship"],
            }
            for item in evidence
        ]
        edition_summaries = [
            {
                "edition_id": item["id"],
                "status": item["payload"]["status"],
                "content_hash": item["payload"]["content_hash"],
                "affected_story_ids": sorted(
                    set(item["payload"]["story_ids"]) & set(story_ids)
                ),
            }
            for item in editions
        ]

        fingerprint_payload = {
            "decision": decision,
            "stories": stories,
            "articles": articles,
            "claims": claims,
            "evidence": evidence,
            "editions": editions,
            "application": application,
            "blockers": sorted(set(blockers)),
        }
        return {
            "decision_id": decision_id,
            "operation": decision["operation"],
            "applicable": not blockers,
            "already_applied": application is not None,
            "application": application,
            "blockers": sorted(set(blockers)),
            "impact_fingerprint": sha256_fingerprint(fingerprint_payload),
            "stories": story_summaries,
            "articles": article_summaries,
            "claims": claim_summaries,
            "evidence": evidence_summaries,
            "editions": edition_summaries,
        }


    def preview_story_decision_application(self, decision_id: str) -> dict[str, Any]:
        """Return a deterministic, read-only impact preview for a story decision."""
        with self._transaction("DEFERRED") as connection:
            return self._preview_story_decision_application(connection, decision_id)

    def apply_story_decision(
        self,
        decision_id: str,
        *,
        expected_impact_fingerprint: str,
        actor: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Atomically apply a reviewed story decision using its exact preview fingerprint."""
        if not isinstance(actor, str) or not actor.strip() or len(actor.strip()) > 200:
            raise ValueError("actor must contain 1 to 200 non-whitespace characters")
        if not isinstance(rationale, str) or not rationale.strip() or len(rationale.strip()) > 4000:
            raise ValueError("rationale must contain 1 to 4000 non-whitespace characters")
        if not isinstance(expected_impact_fingerprint, str):
            raise ValueError("expected_impact_fingerprint is required")

        with self._transaction("IMMEDIATE") as connection:
            existing = connection.execute(
                """SELECT application_id, applied_at, actor, rationale, result_json
                FROM story_decision_applications WHERE decision_id = ?""",
                (decision_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "decision_id": decision_id,
                    "application_id": existing["application_id"],
                    "applied_at": existing["applied_at"],
                    "actor": existing["actor"],
                    "rationale": existing["rationale"],
                    "already_applied": True,
                    "result": json.loads(existing["result_json"]),
                }

            preview = self._preview_story_decision_application(connection, decision_id)
            if not preview["applicable"]:
                raise RegistryIntegrityError(
                    "story decision cannot be applied: " + "; ".join(preview["blockers"])
                )
            if preview["impact_fingerprint"] != expected_impact_fingerprint:
                raise RegistryIntegrityError("story decision preview is stale; create a new preview")

            decision_row = connection.execute(
                "SELECT payload_json FROM story_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
            if decision_row is None:
                raise RegistryIntegrityError(f"unknown story decision: {decision_id}")
            decision = json.loads(decision_row["payload_json"])
            decision_fingerprint = sha256_fingerprint(decision)
            story_ids = sorted(decision["story_ids"])
            article_destinations = {
                item["article_id"]: item["assigned_story_id"]
                for item in decision["article_assignments"]
            }
            claim_destinations = {
                item["claim_id"]: item["assigned_story_id"]
                for item in decision["claim_assignments"]
            }
            now = _utc_now()
            story_changes: list[dict[str, Any]] = []
            claim_changes: list[dict[str, Any]] = []
            changed_story_payloads: dict[str, dict[str, Any]] = {}
            changed_claim_payloads: dict[str, dict[str, Any]] = {}

            for story_id in story_ids:
                row = connection.execute(
                    "SELECT revision, payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                    (story_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError(f"affected story disappeared: {story_id}")
                old_revision = row["revision"]
                payload = json.loads(row["payload_json"])
                new_articles = sorted(
                    article_id for article_id, destination in article_destinations.items()
                    if destination == story_id
                )
                if decision["operation"] == "merge" and story_id != decision["canonical_story_id"]:
                    payload["status"] = "archived"
                    payload["article_ids"] = []
                else:
                    payload["article_ids"] = new_articles
                if "revision" in payload:
                    payload["revision"] = old_revision + 1
                if "updated_at" in payload:
                    payload["updated_at"] = now
                validate_payload("story", payload)
                next_revision = old_revision + 1
                encoded = _json_text(payload)
                updated = connection.execute(
                    """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                    WHERE record_type = 'story' AND id = ? AND revision = ?""",
                    (next_revision, now, encoded, story_id, old_revision),
                )
                if updated.rowcount != 1:
                    raise RegistryIntegrityError(f"story revision conflict: {story_id}")
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('story', ?, ?, ?, ?, ?, ?)""",
                    (story_id, next_revision, now, actor.strip(), rationale.strip(), encoded),
                )
                changed_story_payloads[story_id] = payload
                story_changes.append({
                    "story_id": story_id,
                    "from_revision": old_revision,
                    "to_revision": next_revision,
                    "status": payload["status"],
                    "article_ids": payload["article_ids"],
                })

            for claim_id in sorted(claim_destinations):
                row = connection.execute(
                    "SELECT revision, payload_json FROM domain_records WHERE record_type = 'claim' AND id = ?",
                    (claim_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError(f"assigned claim disappeared: {claim_id}")
                old_revision = row["revision"]
                payload = json.loads(row["payload_json"])
                payload["story_id"] = claim_destinations[claim_id]
                validate_payload("claim", payload)
                next_revision = old_revision + 1
                encoded = _json_text(payload)
                updated = connection.execute(
                    """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                    WHERE record_type = 'claim' AND id = ? AND revision = ?""",
                    (next_revision, now, encoded, claim_id, old_revision),
                )
                if updated.rowcount != 1:
                    raise RegistryIntegrityError(f"claim revision conflict: {claim_id}")
                connection.execute(
                    """INSERT INTO domain_record_revisions
                    (record_type, record_id, revision, created_at, actor, reason, payload_json)
                    VALUES ('claim', ?, ?, ?, ?, ?, ?)""",
                    (claim_id, next_revision, now, actor.strip(), rationale.strip(), encoded),
                )
                changed_claim_payloads[claim_id] = payload
                claim_changes.append({
                    "claim_id": claim_id,
                    "from_revision": old_revision,
                    "to_revision": next_revision,
                    "story_id": payload["story_id"],
                })

            # Validate the completed graph only after both sides of every
            # story/article and claim/provenance move have been updated.
            for story_id, payload in changed_story_payloads.items():
                self._validate_domain_references(connection, "story", payload)
            for claim_id, payload in changed_claim_payloads.items():
                self._validate_domain_references(connection, "claim", payload)
                evidence_rows = connection.execute(
                    "SELECT id, payload_json FROM domain_records WHERE record_type = 'evidence' AND json_extract(payload_json, '$.claim_id') = ?",
                    (claim_id,),
                ).fetchall()
                for evidence_row in evidence_rows:
                    self._validate_domain_references(
                        connection, "evidence", json.loads(evidence_row["payload_json"])
                    )

            result = {
                "decision_id": decision_id,
                "operation": decision["operation"],
                "canonical_story_id": decision["canonical_story_id"],
                "stories": story_changes,
                "claims": claim_changes,
                "preserved_evidence_ids": sorted(item["evidence_id"] for item in preview["evidence"]),
                "preserved_editions": [
                    {"edition_id": item["edition_id"], "content_hash": item["content_hash"]}
                    for item in preview["editions"]
                ],
            }
            application_id = f"story-application:{uuid.uuid4().hex}"
            impact_fingerprint = preview["impact_fingerprint"]
            connection.execute(
                """INSERT INTO story_decision_applications
                (application_id, decision_id, applied_at, actor, rationale,
                 decision_fingerprint, impact_fingerprint, result_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (application_id, decision_id, now, actor.strip(), rationale.strip(),
                 decision_fingerprint, impact_fingerprint, _json_text(result)),
            )
            self._append_audit(
                connection,
                event_type="story_decision.applied",
                entity_type="story_decision",
                entity_id=decision_id,
                payload={
                    "application_id": application_id,
                    "actor": actor.strip(),
                    "rationale": rationale.strip(),
                    "decision_fingerprint": decision_fingerprint,
                    "impact_fingerprint": impact_fingerprint,
                    "result": result,
                },
            )
            return {
                "decision_id": decision_id,
                "application_id": application_id,
                "applied_at": now,
                "actor": actor.strip(),
                "rationale": rationale.strip(),
                "already_applied": False,
                "result": result,
            }


    def add_timeline_event(self, payload: dict[str, Any]) -> TimelineEventRecord:
        """Append a claim-backed event assertion; corrections supersede, never rewrite."""
        validate_payload("timeline-event", payload)
        created_at = _aware_datetime(payload["created_at"], "created_at")
        review_status = payload.get("review_status", "unreviewed")
        review_fields = ("reviewed_at", "reviewed_by", "review_note")
        if review_status in {"approved", "rejected"}:
            if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in review_fields):
                raise RegistryIntegrityError("reviewed timeline event requires reviewer, timestamp, and note")
            if _aware_datetime(payload["reviewed_at"], "reviewed_at") < created_at:
                raise RegistryIntegrityError("timeline event review cannot predate its creation")
        elif any(field in payload for field in review_fields):
            raise RegistryIntegrityError("unreviewed timeline event cannot contain review metadata")
        occurred = payload["occurred"]
        precision = occurred["precision"]
        start = occurred.get("start")
        end = occurred.get("end")
        sort_key = None
        if precision in {"instant", "range"}:
            if not isinstance(start, str):
                raise RegistryIntegrityError("timeline event precision requires a start timestamp")
            parsed_start = _aware_datetime(start, "occurred.start")
            sort_key = parsed_start.isoformat()
            if precision == "range":
                if not isinstance(end, str):
                    raise RegistryIntegrityError("timeline event range requires an end timestamp")
                parsed_end = _aware_datetime(end, "occurred.end")
                if parsed_end < parsed_start:
                    raise RegistryIntegrityError("timeline event end cannot precede its start")
            elif end is not None:
                raise RegistryIntegrityError("instant timeline event cannot include an end")
        elif precision == "date":
            if not isinstance(start, str):
                raise RegistryIntegrityError("date precision requires an ISO date start")
            try:
                from datetime import date
                sort_key = date.fromisoformat(start).isoformat()
            except ValueError as exc:
                raise RegistryIntegrityError("date precision requires an ISO calendar date") from exc
            if end is not None:
                raise RegistryIntegrityError("date precision cannot include an end")
        elif precision == "approximate":
            if not isinstance(start, str) or not isinstance(occurred.get("note"), str) or not occurred["note"].strip():
                raise RegistryIntegrityError("approximate event timing requires a date and an uncertainty note")
            try:
                from datetime import date
                sort_key = date.fromisoformat(start).isoformat()
            except ValueError as exc:
                raise RegistryIntegrityError("approximate event timing requires an ISO calendar date") from exc
            if end is not None:
                raise RegistryIntegrityError("approximate event timing cannot include an end")
        elif precision == "unknown":
            if start is not None or end is not None:
                raise RegistryIntegrityError("unknown event timing cannot include a start or end")
        timezone_name = occurred.get("timezone")
        if timezone_name:
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(timezone_name)
            except (KeyError, ValueError) as exc:
                raise RegistryIntegrityError("timeline event timezone must be a valid IANA timezone") from exc

        record = TimelineEventRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("timeline event references an unknown story")
            story_payload = json.loads(story_row["payload_json"])
            if story_payload.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a timeline event to an archived story")
            claim_ids = payload["claim_ids"]
            placeholders = ",".join("?" for _ in claim_ids)
            claim_rows = connection.execute(
                f"""SELECT id, payload_json FROM domain_records
                WHERE record_type = 'claim' AND id IN ({placeholders})""",
                tuple(claim_ids),
            ).fetchall()
            if len(claim_rows) != len(claim_ids):
                raise RegistryIntegrityError("timeline event references an unknown claim")
            if any(json.loads(row["payload_json"])["story_id"] != record.story_id for row in claim_rows):
                raise RegistryIntegrityError("timeline event claims must belong to its story")
            supersedes_id = payload.get("supersedes_event_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id FROM timeline_events WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("timeline correction must supersede an event in the same story")
            connection.execute(
                """INSERT INTO timeline_events
                (id, story_id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, sort_key, precision, supersedes_id,
                 payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="timeline_event.created",
                entity_type="timeline_event",
                entity_id=record.id,
                payload={"record": payload, "supersedes_event_id": supersedes_id},
            )
        return record

    def get_timeline_event(self, event_id: str) -> TimelineEventRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM timeline_events WHERE id = ?", (event_id,)
            ).fetchone()
        return TimelineEventRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def timeline_event_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all event assertions and correction links, including superseded records."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, occurred_sort_key, precision, supersedes_event_id, created_at, payload_json
                FROM timeline_events WHERE story_id = ?
                ORDER BY CASE WHEN occurred_sort_key IS NULL THEN 1 ELSE 0 END,
                         occurred_sort_key, created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_event_id"] for row in rows if row["supersedes_event_id"]}
        return [
            {
                "event_id": row["id"],
                "precision": row["precision"],
                "occurred_sort_key": row["occurred_sort_key"],
                "created_at": row["created_at"],
                "supersedes_event_id": row["supersedes_event_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_timeline(self, story_id: str) -> list[dict[str, Any]]:
        """Return the current event assertions in chronological order, retaining uncertainty."""
        return [
            item for item in self.timeline_event_history(story_id)
            if not item["is_superseded"]
        ]

    def story_decision_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return immutable merge/split decisions involving a story, oldest first."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT payload_json FROM story_decisions
                ORDER BY decided_at, id"""
            ).fetchall()
        return [
            payload
            for row in rows
            if story_id in (payload := json.loads(row["payload_json"]))["story_ids"]
        ]

    def add_perspective(self, payload: dict[str, Any]) -> PerspectiveRecord:
        """Append an attributed perspective; corrections supersede, never rewrite."""
        validate_payload("perspective", payload)
        _aware_datetime(payload["created_at"], "created_at")
        review_status = payload.get("review_status", "unreviewed")
        review_fields = ("reviewed_at", "reviewed_by", "review_note")
        if review_status in {"approved", "rejected"}:
            if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in review_fields):
                raise RegistryIntegrityError("reviewed perspective requires reviewer, timestamp, and note")
            _aware_datetime(payload["reviewed_at"], "reviewed_at")
        elif any(field in payload for field in review_fields):
            raise RegistryIntegrityError("unreviewed perspective cannot contain review metadata")
        record = PerspectiveRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("perspective references an unknown story")
            story = json.loads(story_row["payload_json"])
            if story.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a perspective to an archived story")
            source_id = payload.get("source_id")
            article_id = payload.get("article_id")
            if source_id:
                source = connection.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
                if source is None:
                    raise RegistryIntegrityError("perspective references an unknown source")
            if article_id:
                article_row = connection.execute(
                    "SELECT payload_json FROM domain_records WHERE record_type = 'article' AND id = ?",
                    (article_id,),
                ).fetchone()
                if article_row is None:
                    raise RegistryIntegrityError("perspective references an unknown article")
                article = json.loads(article_row["payload_json"])
                if article.get("source_id") != source_id or article_id not in story.get("article_ids", []):
                    raise RegistryIntegrityError("perspective article must belong to its source and story")
            if payload["kind"] == "publisher_framing" and (not source_id or not article_id):
                raise RegistryIntegrityError("publisher framing requires its source and article")
            claim_ids = payload["claim_ids"]
            if claim_ids:
                placeholders = ",".join("?" for _ in claim_ids)
                rows = connection.execute(
                    f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                    tuple(claim_ids),
                ).fetchall()
                if len(rows) != len(claim_ids) or any(
                    json.loads(row["payload_json"])["story_id"] != record.story_id for row in rows
                ):
                    raise RegistryIntegrityError("perspective claims must belong to its story")
            supersedes_id = payload.get("supersedes_perspective_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id, created_at FROM perspectives WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("perspective correction must supersede an entry in the same story")
                if _aware_datetime(payload["created_at"], "created_at") < _aware_datetime(prior["created_at"], "prior.created_at"):
                    raise RegistryIntegrityError("perspective correction cannot predate the assertion it supersedes")
            connection.execute(
                """INSERT INTO perspectives
                (id, story_id, source_id, article_id, supersedes_perspective_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, source_id, article_id, supersedes_id,
                 payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="perspective.created",
                entity_type="perspective",
                entity_id=record.id,
                payload={"record": payload, "supersedes_perspective_id": supersedes_id},
            )
        return record

    def get_perspective(self, perspective_id: str) -> PerspectiveRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM perspectives WHERE id = ?", (perspective_id,)
            ).fetchone()
        return PerspectiveRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def perspective_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all attributed framings and corrections, including superseded entries."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, source_id, article_id, supersedes_perspective_id, created_at, payload_json
                FROM perspectives WHERE story_id = ? ORDER BY created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_perspective_id"] for row in rows if row["supersedes_perspective_id"]}
        return [
            {
                "perspective_id": row["id"],
                "source_id": row["source_id"],
                "article_id": row["article_id"],
                "created_at": row["created_at"],
                "supersedes_perspective_id": row["supersedes_perspective_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_perspectives(self, story_id: str) -> list[dict[str, Any]]:
        """Return current attributed perspectives without rewriting prior assertions."""
        return [
            item for item in self.perspective_history(story_id)
            if not item["is_superseded"]
        ]

    def story_perspective_attribution_report(self, story_id: str) -> dict[str, Any]:
        """Return a read-only, source-attributed view of current perspectives and reviewed claim conflicts."""
        perspectives = self.story_perspectives(story_id)
        relations = [
            item for item in self.claim_relation_history(story_id)
            if not item["is_superseded"]
        ]
        reporting = self.story_reporting_independence(story_id)
        by_kind: dict[str, int] = {}
        by_language: dict[str, int] = {}
        by_source: dict[str, list[str]] = {}
        entries: list[dict[str, Any]] = []
        unreviewed: list[str] = []
        unattributed: list[str] = []
        claim_coverage: set[str] = set()
        for item in perspectives:
            record = item["record"]
            kind = record["kind"]
            language = record["language"]
            by_kind[kind] = by_kind.get(kind, 0) + 1
            by_language[language] = by_language.get(language, 0) + 1
            source_id = item["source_id"]
            article_id = item["article_id"]
            if source_id:
                by_source.setdefault(source_id, []).append(item["perspective_id"])
            else:
                unattributed.append(item["perspective_id"])
            if record.get("review_status") != "approved":
                unreviewed.append(item["perspective_id"])
            claim_coverage.update(record.get("claim_ids", []))
            entries.append({
                "perspective_id": item["perspective_id"],
                "kind": kind,
                "attribution": record["attribution"],
                "source_id": source_id,
                "article_id": article_id,
                "language": language,
                "text": record["text"],
                "claim_ids": sorted(record.get("claim_ids", [])),
                "review_status": record.get("review_status", "unreviewed"),
                "reviewed_by": record.get("reviewed_by"),
                "reviewed_at": record.get("reviewed_at"),
            })
        contradictions = [
            {
                "relation_id": item["relation_id"],
                "claim_id": item["claim_id"],
                "related_claim_id": item["related_claim_id"],
                "review_status": item["record"]["review_status"],
                "reviewed_by": item["record"].get("reviewed_by"),
                "reviewed_at": item["record"].get("reviewed_at"),
                "resolution_note": item["record"].get("review_note"),
            }
            for item in relations if item["relationship"] == "contradicts"
        ]
        return {
            "story_id": story_id,
            "perspective_count": len(entries),
            "reviewed_count": sum(1 for item in entries if item["review_status"] == "approved"),
            "unreviewed_perspective_ids": sorted(unreviewed),
            "unattributed_perspective_ids": sorted(unattributed),
            "by_kind": dict(sorted(by_kind.items())),
            "by_language": dict(sorted(by_language.items())),
            "source_attributions": [
                {"source_id": source_id, "perspective_ids": sorted(ids)}
                for source_id, ids in sorted(by_source.items())
            ],
            "perspectives": entries,
            "claim_ids_with_perspectives": sorted(claim_coverage),
            "contradictions": contradictions,
            "reporting_independence": reporting,
            "independence_caveat": (
                "Distinct publisher IDs and distinct articles do not establish independent reporting; "
                "syndication, shared sourcing, and common-origin material require separate evidence."
            ),
            "production_ready": False,
            "authorization_granted": False,
        }

    def add_claim_relation(self, payload: dict[str, Any]) -> ClaimRelationRecord:
        """Append an explicit relationship between two claims in one story."""
        validate_payload("claim-relation", payload)
        payload = dict(payload)
        _aware_datetime(payload["created_at"], "created_at")
        if payload["claim_id"] == payload["related_claim_id"]:
            raise RegistryIntegrityError("claim relation must connect two different claims")
        if payload["relationship"] == "contradicts" and payload["related_claim_id"] < payload["claim_id"]:
            payload["claim_id"], payload["related_claim_id"] = payload["related_claim_id"], payload["claim_id"]
        review_status = payload["review_status"]
        review_fields = ("reviewed_at", "reviewed_by", "review_note")
        if review_status in {"accepted", "rejected"}:
            if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in review_fields):
                raise RegistryIntegrityError("reviewed claim relation requires reviewer, timestamp, and note")
            _aware_datetime(payload["reviewed_at"], "reviewed_at")
        elif any(field in payload for field in review_fields):
            raise RegistryIntegrityError("unreviewed claim relation cannot contain review metadata")
        record = ClaimRelationRecord.from_payload(payload)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("claim relation references an unknown story")
            story = json.loads(story_row["payload_json"])
            if story.get("status") == "archived":
                raise RegistryIntegrityError("cannot append a claim relation to an archived story")
            ids = (payload["claim_id"], payload["related_claim_id"])
            placeholders = ",".join("?" for _ in ids)
            rows = connection.execute(
                f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                ids,
            ).fetchall()
            if len(rows) != 2 or any(json.loads(row["payload_json"])["story_id"] != record.story_id for row in rows):
                raise RegistryIntegrityError("claim relation endpoints must belong to its story")
            supersedes_id = payload.get("supersedes_relation_id")
            if supersedes_id:
                prior = connection.execute(
                    "SELECT story_id, created_at FROM claim_relations WHERE id = ?", (supersedes_id,)
                ).fetchone()
                if prior is None or prior["story_id"] != record.story_id:
                    raise RegistryIntegrityError("claim relation correction must supersede a relation in the same story")
                if _aware_datetime(payload["created_at"], "created_at") < _aware_datetime(prior["created_at"], "prior.created_at"):
                    raise RegistryIntegrityError("claim relation correction cannot predate the relation it supersedes")
            connection.execute(
                """INSERT INTO claim_relations
                (id, story_id, claim_id, related_claim_id, relationship, supersedes_relation_id, created_at, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, record.claim_id, record.related_claim_id,
                 record.relationship, supersedes_id, payload["created_at"], record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="claim_relation.created",
                entity_type="claim_relation",
                entity_id=record.id,
                payload={"record": payload, "supersedes_relation_id": supersedes_id},
            )
        return record

    def get_claim_relation(self, relation_id: str) -> ClaimRelationRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM claim_relations WHERE id = ?", (relation_id,)
            ).fetchone()
        return ClaimRelationRecord.from_payload(json.loads(row["payload_json"])) if row else None

    def claim_relation_history(self, story_id: str) -> list[dict[str, Any]]:
        """Return all explicit claim relationships, including superseded assertions."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT id, claim_id, related_claim_id, relationship,
                          supersedes_relation_id, created_at, payload_json
                FROM claim_relations WHERE story_id = ?
                ORDER BY julianday(created_at), created_at, id""",
                (story_id,),
            ).fetchall()
        superseded = {row["supersedes_relation_id"] for row in rows if row["supersedes_relation_id"]}
        return [
            {
                "relation_id": row["id"],
                "claim_id": row["claim_id"],
                "related_claim_id": row["related_claim_id"],
                "relationship": row["relationship"],
                "created_at": row["created_at"],
                "supersedes_relation_id": row["supersedes_relation_id"],
                "is_superseded": row["id"] in superseded,
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_claim_relations(self, story_id: str) -> list[dict[str, Any]]:
        """Return current reviewed and unreviewed relationships without resolving disputes."""
        return [
            item for item in self.claim_relation_history(story_id)
            if not item["is_superseded"]
        ]

    @staticmethod
    def _story_narrative_assembly_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        """Assemble reviewed, language-separated narrative segments with claim/evidence traces."""
        claims = {item["id"]: item["record"] for item in snapshot["claims"]}
        evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
        for item in snapshot["evidence"]:
            evidence_by_claim.setdefault(item["record"]["claim_id"], []).append(item["record"])

        by_language: dict[str, dict[str, Any]] = {
            "bn": {"language": "bn", "objects": [], "segments": [], "blockers": []},
            "en": {"language": "en", "objects": [], "segments": [], "blockers": []},
        }
        for item in snapshot["learning_objects"]:
            record = item["record"]
            language = record["language"]
            if language not in by_language:
                continue
            segments = record.get("content", {}).get("segments", [])
            if not segments:
                continue
            language_report = by_language[language]
            language_review = record.get("language_review")
            language_review_pass = _language_review_passes(language_review, language)
            object_entry = {
                "learning_object_id": item["id"],
                "revision": item["revision"],
                "kind": record["kind"],
                "status": record["status"],
                "language_review": language_review,
                "language_review_pass": language_review_pass,
                "segment_count": len(segments),
            }
            language_report["objects"].append(object_entry)
            if record["status"] != "approved":
                language_report["blockers"].append({
                    "learning_object_id": item["id"],
                    "reason": "learning object is not approved",
                })
            if language_review is None:
                language_report["blockers"].append({
                    "learning_object_id": item["id"],
                    "reason": "independent language review evidence is missing",
                })
            elif not language_review_pass:
                language_report["blockers"].append({
                    "learning_object_id": item["id"],
                    "reason": "language review is rejected or one or more required rubric dimensions did not pass",
                    "reviewer_id": language_review.get("reviewer_id"),
                    "rubric_version": language_review.get("rubric_version"),
                })

            for index, segment in enumerate(segments):
                claim_ids = sorted(segment.get("claim_ids", []))
                claim_entries = []
                missing_claim_ids = []
                unsupported_claim_ids = []
                for claim_id in claim_ids:
                    claim = claims.get(claim_id)
                    if claim is None:
                        missing_claim_ids.append(claim_id)
                        continue
                    evidence = sorted(
                        evidence_by_claim.get(claim_id, []),
                        key=lambda entry: entry["id"],
                    )
                    supporting_evidence = [
                        entry for entry in evidence if entry["relationship"] == "supports"
                    ]
                    if claim.get("assessment") != "supported" or not supporting_evidence:
                        unsupported_claim_ids.append(claim_id)
                    claim_entries.append({
                        "claim_id": claim_id,
                        "claim_text": claim["text"],
                        "assessment": claim["assessment"],
                        "provenance": claim.get("provenance", []),
                        "evidence": [
                            {
                                "evidence_id": entry["id"],
                                "source_id": entry["source_id"],
                                "article_id": entry.get("article_id"),
                                "relationship": entry["relationship"],
                                "locator": entry["locator"],
                                "assessment_method": entry.get("assessment_method"),
                            }
                            for entry in evidence
                        ],
                    })

                role = segment["role"]
                if role == "fact":
                    support_state = (
                        "supported"
                        if claim_ids and not missing_claim_ids and not unsupported_claim_ids
                        else "unverified_or_disputed"
                    )
                elif role == "attributed":
                    support_state = "attributed_position"
                else:
                    support_state = "editorial_analysis"
                trace = {
                    "learning_object_id": item["id"],
                    "learning_object_revision": item["revision"],
                    "segment_index": index,
                    "language": language,
                    "text": segment["text"],
                    "role": role,
                    "attribution": segment.get("attribution"),
                    "support_state": support_state,
                    "claim_ids": claim_ids,
                    "claims": claim_entries,
                    "missing_claim_ids": missing_claim_ids,
                    "unsupported_claim_ids": unsupported_claim_ids,
                    "traceable": bool(claim_ids) and not missing_claim_ids,
                }
                language_report["segments"].append(trace)
                if missing_claim_ids:
                    language_report["blockers"].append({
                        "learning_object_id": item["id"],
                        "segment_index": index,
                        "reason": "segment references missing claims",
                        "claim_ids": missing_claim_ids,
                    })
                if role == "fact" and support_state != "supported":
                    language_report["blockers"].append({
                        "learning_object_id": item["id"],
                        "segment_index": index,
                        "reason": "factual sentence lacks fully supported claims and supporting evidence",
                        "claim_ids": unsupported_claim_ids or claim_ids,
                    })

        perspectives_by_language: dict[str, list[dict[str, Any]]] = {"bn": [], "en": []}
        for item in snapshot["perspectives"]:
            record = item["record"]
            language = record["language"]
            if language in perspectives_by_language:
                perspectives_by_language[language].append({
                    "perspective_id": item["perspective_id"],
                    "kind": record["kind"],
                    "attribution": record["attribution"],
                    "source_id": item["source_id"],
                    "article_id": item["article_id"],
                    "text": record["text"],
                    "claim_ids": sorted(record.get("claim_ids", [])),
                    "review_status": record.get("review_status", "unreviewed"),
                })

        contradictions = []
        for relation in snapshot["claim_relations"]:
            if relation["relationship"] != "contradicts":
                continue
            record = relation["record"]
            left = claims.get(relation["claim_id"], {})
            right = claims.get(relation["related_claim_id"], {})
            contradictions.append({
                "relation_id": relation["relation_id"],
                "claim_id": relation["claim_id"],
                "claim_text": left.get("text"),
                "related_claim_id": relation["related_claim_id"],
                "related_claim_text": right.get("text"),
                "review_status": record.get("review_status", "unreviewed"),
                "reviewed_by": record.get("reviewed_by"),
                "reviewed_at": record.get("reviewed_at"),
                "review_note": record.get("review_note"),
            })
        unresolved_contradictions = [
            item for item in contradictions
            if item["review_status"] != "rejected"
        ]

        languages = {}
        for language, report in by_language.items():
            report["publisher_perspectives"] = [
                item for item in perspectives_by_language[language]
                if item["kind"] == "publisher_framing"
            ]
            report["other_attributed_perspectives"] = [
                item for item in perspectives_by_language[language]
                if item["kind"] != "publisher_framing"
            ]
            report["unresolved_contradiction_ids"] = [
                item["relation_id"] for item in unresolved_contradictions
            ]
            factual_segments = [
                item for item in report["segments"] if item["role"] == "fact"
            ]
            report["summary"] = {
                "learning_object_count": len(report["objects"]),
                "segment_count": len(report["segments"]),
                "factual_segment_count": len(factual_segments),
                "traceable_segment_count": sum(
                    1 for item in report["segments"] if item["traceable"]
                ),
                "unsupported_factual_segment_count": sum(
                    1 for item in factual_segments
                    if item["support_state"] != "supported"
                ),
                "language_review_pass_count": sum(
                    1 for item in report["objects"] if item["language_review_pass"]
                ),
                "blocker_count": len(report["blockers"]),
            }
            report["assembly_status"] = "blocked" if report["blockers"] else "review_required"
            report["production_ready"] = False
            report["authorization_granted"] = False
            languages[language] = report

        return {
            "story_id": snapshot["story_id"],
            "languages": languages,
            "contradictions": contradictions,
            "unresolved_contradictions": unresolved_contradictions,
            "reporting_independence": snapshot["reporting_independence"],
            "independence_caveat": (
                "Publisher count and article count are not proof of independent reporting. "
                "Syndication, shared sourcing, and common-origin material must be investigated separately."
            ),
            "assembly_policy": {
                "facts_require_supported_claims_and_supporting_evidence": True,
                "attributed_positions_remain_separate": True,
                "editorial_analysis_remains_labelled": True,
                "bangla_and_english_are_assessed_independently": True,
                "unresolved_contradictions_are_preserved": True,
            },
            "production_ready": False,
            "authorization_granted": False,
        }

    def story_narrative_assembly_report(self, story_id: str) -> dict[str, Any]:
        """Return a deterministic, non-authorizing narrative assembly dossier."""
        snapshot = self.story_editorial_snapshot(story_id)
        report = snapshot["narrative_assembly_report"]
        report["snapshot_fingerprint"] = snapshot["fingerprint"]
        return report

    def story_editorial_snapshot(self, story_id: str) -> dict[str, Any]:
        """Build a deterministic, read-only editorial dossier from one SQLite snapshot."""
        with self._transaction("DEFERRED") as connection:
            story_row = connection.execute(
                """SELECT revision, updated_at, payload_json FROM domain_records
                WHERE record_type = 'story' AND id = ?""",
                (story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError(f"unknown story: {story_id}")
            story_payload = json.loads(story_row["payload_json"])
            article_ids = sorted(story_payload.get("article_ids", []))
            articles = []
            if article_ids:
                placeholders = ",".join("?" for _ in article_ids)
                rows = connection.execute(
                    f"""SELECT id, revision, updated_at, payload_json FROM domain_records
                    WHERE record_type = 'article' AND id IN ({placeholders}) ORDER BY id""",
                    tuple(article_ids),
                ).fetchall()
                if len(rows) != len(article_ids):
                    raise RegistryIntegrityError("story editorial snapshot found a missing article")
                articles = [
                    {"id": row["id"], "revision": row["revision"],
                     "updated_at": row["updated_at"], "record": json.loads(row["payload_json"])}
                    for row in rows
                ]
            claim_rows = connection.execute(
                """SELECT id, revision, updated_at, payload_json FROM domain_records
                WHERE record_type = 'claim' AND json_extract(payload_json, '$.story_id') = ?
                ORDER BY id""",
                (story_id,),
            ).fetchall()
            claims = [
                {"id": row["id"], "revision": row["revision"],
                 "updated_at": row["updated_at"], "record": json.loads(row["payload_json"])}
                for row in claim_rows
            ]
            claim_ids = [row["id"] for row in claim_rows]
            evidence = []
            if claim_ids:
                placeholders = ",".join("?" for _ in claim_ids)
                evidence_rows = connection.execute(
                    f"""SELECT id, revision, updated_at, payload_json FROM domain_records
                    WHERE record_type = 'evidence'
                      AND json_extract(payload_json, '$.claim_id') IN ({placeholders})
                    ORDER BY id""",
                    tuple(claim_ids),
                ).fetchall()
                evidence = [
                    {"id": row["id"], "revision": row["revision"],
                     "updated_at": row["updated_at"], "record": json.loads(row["payload_json"])}
                    for row in evidence_rows
                ]
            timeline_history = self.timeline_event_history(story_id)
            perspective_history = self.perspective_history(story_id)
            claim_relation_history = self.claim_relation_history(story_id)
            perspective_attribution_report = self.story_perspective_attribution_report(story_id)
            decisions = self.story_decision_history(story_id)
            learning_object_rows = connection.execute(
                """SELECT id, revision, updated_at, payload_json FROM learning_objects
                WHERE story_id = ? ORDER BY kind, language, id""",
                (story_id,),
            ).fetchall()
            learning_objects = [
                {"id": row["id"], "revision": row["revision"],
                 "updated_at": row["updated_at"], "record": json.loads(row["payload_json"])}
                for row in learning_object_rows
            ]
            glossary_ids = sorted({
                item["record"].get("content", {}).get("glossary_entry_id")
                for item in learning_objects
                if item["record"].get("content", {}).get("glossary_entry_id")
            })
            glossary_entries = []
            if glossary_ids:
                placeholders = ",".join("?" for _ in glossary_ids)
                glossary_rows = connection.execute(
                    f"""SELECT id, revision, updated_at, payload_json FROM glossary_entries
                    WHERE id IN ({placeholders}) ORDER BY id""",
                    tuple(glossary_ids),
                ).fetchall()
                glossary_entries = [
                    {"id": row["id"], "revision": row["revision"],
                     "updated_at": row["updated_at"], "record": json.loads(row["payload_json"])}
                    for row in glossary_rows
                ]
            reporting_independence = self.story_reporting_independence(story_id)
            source_independence_evidence_report = self.story_source_independence_evidence_report(story_id)
            benchmark_reviews = []
            if article_ids:
                review_rows = connection.execute(
                    "SELECT review_id, revision, payload_json FROM benchmark_reviews ORDER BY review_id"
                ).fetchall()
                story_article_ids = set(article_ids)
                for review_row in review_rows:
                    review_payload = json.loads(review_row["payload_json"])
                    linked_article_ids = {
                        item.get("article_id")
                        for item in review_payload.get("source_materials", [])
                        if isinstance(item, dict)
                    }
                    if not story_article_ids.intersection(linked_article_ids):
                        continue
                    benchmark_reviews.append({
                        "review_id": review_row["review_id"],
                        "revision": review_row["revision"],
                        "record": review_payload,
                        "quality_report": self._benchmark_review_quality_report(
                            review_payload, connection
                        ),
                        "history": self.benchmark_review_history(review_row["review_id"]),
                    })
            acceptance_reviews = []
            benchmark_ids = sorted({item["record"].get("benchmark_id") for item in benchmark_reviews
                                    if item["record"].get("benchmark_id")})
            if benchmark_ids:
                placeholders = ",".join("?" for _ in benchmark_ids)
                acceptance_rows = connection.execute(
                    f"SELECT review_id FROM benchmark_acceptance_reviews WHERE benchmark_id IN ({placeholders}) ORDER BY created_at, review_id",
                    tuple(benchmark_ids),
                ).fetchall()
                acceptance_reviews = [
                    {
                        "review_id": acceptance_row["review_id"],
                        "quality_report": self._benchmark_acceptance_review_quality_report(
                            acceptance_row["review_id"], connection
                        ),
                    }
                    for acceptance_row in acceptance_rows
                ]
            snapshot = {
                "story_id": story_id,
                "story_revision": story_row["revision"],
                "story_updated_at": story_row["updated_at"],
                "story": story_payload,
                "articles": articles,
                "claims": claims,
                "evidence": evidence,
                "timeline_history": timeline_history,
                "timeline": [item for item in timeline_history if not item["is_superseded"]],
                "perspective_history": perspective_history,
                "perspectives": [item for item in perspective_history if not item["is_superseded"]],
                "perspective_attribution_report": perspective_attribution_report,
                "claim_relation_history": claim_relation_history,
                "claim_relations": [item for item in claim_relation_history if not item["is_superseded"]],
                "story_decisions": decisions,
                "learning_objects": learning_objects,
                "glossary_entries": glossary_entries,
                "benchmark_reviews": benchmark_reviews,
                "benchmark_acceptance_reviews": acceptance_reviews,
                "reporting_independence": reporting_independence,
                "source_independence_evidence_report": source_independence_evidence_report,
            }
            snapshot["narrative_assembly_report"] = self._story_narrative_assembly_from_snapshot(snapshot)
            snapshot["fingerprint"] = sha256_fingerprint(snapshot)
            return snapshot

    def add_glossary_entry(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> GlossaryEntryRecord:
        """Create a shared glossary headword with language-specific senses."""
        validate_payload("glossary-entry", payload)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        _aware_datetime(payload["created_at"], "created_at")
        if payload["status"] not in {"draft", "review_required"}:
            raise RegistryIntegrityError("new glossary entry must start in draft or review-required state")
        if any(field in payload for field in ("reviewed_at", "reviewed_by", "review_note")):
            raise RegistryIntegrityError("new glossary entry cannot contain review metadata")
        record = GlossaryEntryRecord.from_payload(payload, revision=1)
        lemma_key = payload["lemma"].strip().casefold()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO glossary_entries
                (id, lemma, lemma_key, term_language, part_of_speech, status, revision,
                 created_at, updated_at, last_actor, last_reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (record.id, record.lemma, lemma_key, record.term_language, record.part_of_speech,
                 record.status, payload["created_at"], payload["created_at"],
                 actor.strip(), reason.strip(), record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="glossary_entry.created",
                entity_type="glossary_entry",
                entity_id=record.id,
                payload={"record": payload, "actor": actor.strip(), "reason": reason.strip(), "revision": 1},
            )
        return record

    def get_glossary_entry(self, glossary_entry_id: str) -> GlossaryEntryRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, payload_json FROM glossary_entries WHERE id = ?",
                (glossary_entry_id,),
            ).fetchone()
        return GlossaryEntryRecord.from_payload(json.loads(row["payload_json"]), row["revision"]) if row else None

    def find_glossary_entry(
        self, lemma: str, *, term_language: str, part_of_speech: str
    ) -> GlossaryEntryRecord | None:
        lemma_key = lemma.strip().casefold()
        with self._lock:
            row = self._connection.execute(
                """SELECT revision, payload_json FROM glossary_entries
                WHERE lemma_key = ? AND term_language = ? AND part_of_speech = ?""",
                (lemma_key, term_language, part_of_speech),
            ).fetchone()
        return GlossaryEntryRecord.from_payload(json.loads(row["payload_json"]), row["revision"]) if row else None

    def revise_glossary_entry(
        self,
        glossary_entry_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> GlossaryEntryRecord:
        """Revise senses or review state without rewriting the headword identity."""
        validate_payload("glossary-entry", payload)
        if payload.get("id") != glossary_entry_id:
            raise RegistryIntegrityError("glossary entry identity cannot change during revision")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        _aware_datetime(payload["created_at"], "created_at")
        if payload["status"] in {"approved", "rejected"}:
            reviewed_at = _aware_datetime(payload["reviewed_at"], "reviewed_at")
            if reviewed_at < _aware_datetime(payload["created_at"], "created_at"):
                raise RegistryIntegrityError("glossary entry review cannot predate its creation")
        record = GlossaryEntryRecord.from_payload(payload, revision=expected_revision + 1)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM glossary_entries WHERE id = ?",
                (glossary_entry_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revise an unknown glossary entry")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("glossary entry revision conflict")
            previous = json.loads(row["payload_json"])
            for field in ("lemma", "term_language", "part_of_speech", "created_at"):
                if payload[field] != previous[field]:
                    raise RegistryIntegrityError("glossary entry identity fields cannot be rewritten")
            now = _utc_now()
            updated = connection.execute(
                """UPDATE glossary_entries
                SET status = ?, revision = ?, updated_at = ?, last_actor = ?, last_reason = ?, payload_json = ?
                WHERE id = ? AND revision = ?""",
                (record.status, expected_revision + 1, now, actor.strip(), reason.strip(),
                 record.payload_json, glossary_entry_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise RegistryIntegrityError("glossary entry revision conflict")
            self._append_audit(
                connection,
                event_type="glossary_entry.revised",
                entity_type="glossary_entry",
                entity_id=glossary_entry_id,
                payload={
                    "revision": expected_revision + 1,
                    "actor": actor.strip(),
                    "reason": reason.strip(),
                    "record": payload,
                },
            )
        return record

    def glossary_entry_history(self, glossary_entry_id: str) -> list[dict[str, Any]]:
        if self.get_glossary_entry(glossary_entry_id) is None:
            raise RegistryIntegrityError(f"unknown glossary entry: {glossary_entry_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM glossary_entry_revisions WHERE glossary_entry_id = ? ORDER BY revision""",
                (glossary_entry_id,),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def _validate_learning_object_answer_evidence(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        if payload.get("kind") not in {
            "comprehension_question", "analysis_question", "mcq_question"
        }:
            return
        content = payload.get("content", {})
        raw_answer_claim_ids = content.get("answer_claim_ids", [])
        raw_object_claim_ids = payload.get("claim_ids", [])
        references = content.get("answer_evidence", [])
        if (
            not isinstance(raw_answer_claim_ids, list)
            or not raw_answer_claim_ids
            or any(not isinstance(item, str) or not item for item in raw_answer_claim_ids)
            or not isinstance(raw_object_claim_ids, list)
            or any(not isinstance(item, str) or not item for item in raw_object_claim_ids)
            or not isinstance(references, list)
            or any(not isinstance(item, dict) for item in references)
        ):
            raise RegistryIntegrityError("question answer evidence references are malformed")
        answer_claim_ids = set(raw_answer_claim_ids)
        object_claim_ids = set(raw_object_claim_ids)
        if not answer_claim_ids or not references:
            raise RegistryIntegrityError("question answer evidence is required")
        if any(
            not isinstance(item.get("claim_id"), str)
            or not item.get("claim_id")
            or not isinstance(item.get("evidence_id"), str)
            or not item.get("evidence_id")
            for item in references
        ):
            raise RegistryIntegrityError("question answer evidence IDs are malformed")
        reference_claim_ids = {item["claim_id"] for item in references}
        reference_pairs = [
            (item["claim_id"], item["evidence_id"]) for item in references
        ]
        if (
            len(raw_answer_claim_ids) != len(answer_claim_ids)
            or reference_claim_ids != answer_claim_ids
            or not answer_claim_ids.issubset(object_claim_ids)
            or len(reference_pairs) != len(set(reference_pairs))
        ):
            raise RegistryIntegrityError(
                "question answer evidence must uniquely cover every answer claim"
            )
        for item in references:
            claim_id = item.get("claim_id")
            evidence_id = item.get("evidence_id")
            expected_revision = item.get("evidence_revision")
            if (
                not isinstance(expected_revision, int)
                or isinstance(expected_revision, bool)
                or expected_revision < 1
            ):
                raise RegistryIntegrityError(
                    "question answer evidence must pin a positive evidence revision"
                )
            claim_row = connection.execute(
                """SELECT payload_json FROM domain_records
                WHERE record_type = 'claim' AND id = ?""",
                (claim_id,),
            ).fetchone()
            evidence_row = connection.execute(
                """SELECT revision, payload_json FROM domain_records
                WHERE record_type = 'evidence' AND id = ?""",
                (evidence_id,),
            ).fetchone()
            if claim_row is None or evidence_row is None:
                raise RegistryIntegrityError(
                    "question answer evidence references an unknown claim or evidence record"
                )
            if evidence_row["revision"] != expected_revision:
                raise RegistryIntegrityError("question answer evidence revision is stale")
            claim = json.loads(claim_row["payload_json"])
            evidence = json.loads(evidence_row["payload_json"])
            if evidence.get("claim_id") != claim_id:
                raise RegistryIntegrityError(
                    "question answer evidence must belong to its referenced answer claim"
                )
            if evidence_id not in claim.get("evidence_ids", []):
                raise RegistryIntegrityError(
                    "question answer evidence is no longer linked from its claim"
                )
            if evidence.get("relationship") != "supports":
                raise RegistryIntegrityError(
                    "question answer evidence must support its referenced answer claim"
                )
            provenance_matches = any(
                item.get("source_id") == evidence.get("source_id")
                and (
                    evidence.get("article_id") is None
                    or item.get("article_id") == evidence.get("article_id")
                )
                and (
                    evidence.get("observation_id") is None
                    or item.get("observation_id") == evidence.get("observation_id")
                )
                for item in claim.get("provenance", [])
            )
            if not provenance_matches or not evidence.get("locator", {}).get("uri"):
                raise RegistryIntegrityError(
                    "question answer evidence locator must match current claim provenance"
                )

    def _validate_learning_object_source_evidence_reference(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        if payload.get("kind") != "vocabulary":
            return
        content = payload["content"]
        evidence_id = content.get("source_evidence_id")
        expected_revision = content.get("source_evidence_revision")
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            raise RegistryIntegrityError("vocabulary source evidence ID is required")
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 1:
            raise RegistryIntegrityError("vocabulary source evidence revision is required")
        evidence_row = connection.execute(
            """SELECT revision, payload_json FROM domain_records
            WHERE record_type = 'evidence' AND id = ?""",
            (evidence_id,),
        ).fetchone()
        if evidence_row is None:
            raise RegistryIntegrityError(
                "vocabulary learning object references an unknown source evidence record"
            )
        if evidence_row["revision"] != expected_revision:
            raise RegistryIntegrityError("vocabulary source evidence revision is stale")
        evidence = json.loads(evidence_row["payload_json"])
        claim_ids = payload.get("claim_ids", [])
        if evidence.get("claim_id") not in claim_ids:
            raise RegistryIntegrityError(
                "vocabulary source evidence must belong to one of the learning object's claims"
            )
        claim_row = connection.execute(
            """SELECT payload_json FROM domain_records
            WHERE record_type = 'claim' AND id = ?""",
            (evidence["claim_id"],),
        ).fetchone()
        if claim_row is None:
            raise RegistryIntegrityError("vocabulary source evidence claim is missing")
        claim = json.loads(claim_row["payload_json"])
        if evidence_id not in claim.get("evidence_ids", []):
            raise RegistryIntegrityError(
                "vocabulary source evidence is no longer linked from its claim"
            )
        provenance_matches = any(
            item.get("source_id") == evidence.get("source_id")
            and (
                evidence.get("article_id") is None
                or item.get("article_id") == evidence.get("article_id")
            )
            and (
                evidence.get("observation_id") is None
                or item.get("observation_id") == evidence.get("observation_id")
            )
            for item in claim.get("provenance", [])
        )
        if not provenance_matches or not evidence.get("locator"):
            raise RegistryIntegrityError(
                "vocabulary source evidence locator must match the claim's current provenance"
            )

    def _validate_learning_object_glossary_reference(
        self, connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        if payload.get("kind") != "vocabulary":
            return
        content = payload["content"]
        row = connection.execute(
            "SELECT status, payload_json FROM glossary_entries WHERE id = ?",
            (content["glossary_entry_id"],),
        ).fetchone()
        if row is None:
            raise RegistryIntegrityError("vocabulary learning object references an unknown glossary entry")
        glossary = json.loads(row["payload_json"])
        if any((
            glossary["lemma"] != content["term"],
            glossary["term_language"] != content["term_language"],
            glossary["part_of_speech"] != content["part_of_speech"],
        )):
            raise RegistryIntegrityError("vocabulary fields must match the referenced glossary entry")
        if not any(
            sense["language"] == payload["language"]
            and sense["definition"] == content["meaning"]
            for sense in glossary["senses"]
        ):
            raise RegistryIntegrityError("vocabulary meaning must match a glossary sense in the learning object's language")
        if content.get("pronunciation") and glossary.get("pronunciation") and content["pronunciation"] != glossary["pronunciation"]:
            raise RegistryIntegrityError("vocabulary pronunciation must match the referenced glossary entry")
        if payload["status"] == "approved" and row["status"] != "approved":
            raise RegistryIntegrityError("approved vocabulary requires an approved glossary entry")

    def add_learning_object(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> LearningObjectRecord:
        """Create a language-specific learning object in a non-approved state."""
        validate_payload("learning-object", payload)
        _validate_learning_object_language_review(payload)
        _validate_learning_object_mcq_review(payload)
        _validate_learning_object_traceability(payload)
        _validate_learning_object_answer_links(payload)
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        _aware_datetime(payload["created_at"], "created_at")
        if payload["status"] not in {"draft", "review_required"}:
            raise RegistryIntegrityError("new learning object must start in draft or review-required state")
        if any(field in payload for field in ("reviewed_at", "reviewed_by", "review_note")):
            raise RegistryIntegrityError("new learning object cannot contain review metadata")
        record = LearningObjectRecord.from_payload(payload, revision=1)
        with self._transaction() as connection:
            story_row = connection.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'story' AND id = ?",
                (record.story_id,),
            ).fetchone()
            if story_row is None:
                raise RegistryIntegrityError("learning object references an unknown story")
            story = json.loads(story_row["payload_json"])
            if story.get("status") == "archived":
                raise RegistryIntegrityError("cannot create a learning object for an archived story")
            if record.variant_group_id:
                variants = connection.execute(
                    """SELECT story_id, kind, language FROM learning_objects
                    WHERE variant_group_id = ?""",
                    (record.variant_group_id,),
                ).fetchall()
                if any(item["story_id"] != record.story_id or item["kind"] != record.kind for item in variants):
                    raise RegistryIntegrityError("learning object variants must share the same story and kind")
                if any(item["language"] == record.language for item in variants):
                    raise RegistryIntegrityError("variant group already has an object in this language")
            claim_ids = payload["claim_ids"]
            if claim_ids:
                placeholders = ",".join("?" for _ in claim_ids)
                rows = connection.execute(
                    f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                    tuple(claim_ids),
                ).fetchall()
                if len(rows) != len(claim_ids) or any(
                    json.loads(row["payload_json"])["story_id"] != record.story_id for row in rows
                ):
                    raise RegistryIntegrityError("learning object claims must belong to its story")
            self._validate_learning_object_source_evidence_reference(connection, payload)
            self._validate_learning_object_glossary_reference(connection, payload)
            self._validate_learning_object_answer_evidence(connection, payload)
            connection.execute(
                """INSERT INTO learning_objects
                (id, story_id, kind, language, variant_group_id, status, revision, created_at, updated_at,
                 last_actor, last_reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (record.id, record.story_id, record.kind, record.language, record.variant_group_id, record.status,
                 payload["created_at"], payload["created_at"], actor.strip(), reason.strip(), record.payload_json),
            )
            self._append_audit(
                connection,
                event_type="learning_object.created",
                entity_type="learning_object",
                entity_id=record.id,
                payload={"record": payload, "actor": actor.strip(), "reason": reason.strip(), "revision": 1},
            )
        return record

    def get_learning_object(self, learning_object_id: str) -> LearningObjectRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, payload_json FROM learning_objects WHERE id = ?",
                (learning_object_id,),
            ).fetchone()
        return LearningObjectRecord.from_payload(json.loads(row["payload_json"]), row["revision"]) if row else None

    def revise_learning_object(
        self,
        learning_object_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> LearningObjectRecord:
        """Revise an editorial object with optimistic concurrency and immutable history."""
        validate_payload("learning-object", payload)
        _validate_learning_object_language_review(payload)
        _validate_learning_object_mcq_review(payload)
        _validate_learning_object_traceability(payload)
        _validate_learning_object_answer_links(payload)
        if payload.get("id") != learning_object_id:
            raise RegistryIntegrityError("learning object identity cannot change during revision")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        record = LearningObjectRecord.from_payload(payload, revision=expected_revision + 1)
        _aware_datetime(payload["created_at"], "created_at")
        if record.status in {"approved", "rejected"}:
            _aware_datetime(payload["reviewed_at"], "reviewed_at")
            if _aware_datetime(payload["reviewed_at"], "reviewed_at") < _aware_datetime(payload["created_at"], "created_at"):
                raise RegistryIntegrityError("learning object review cannot predate its creation")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM learning_objects WHERE id = ?",
                (learning_object_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revise an unknown learning object")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("learning object revision conflict")
            previous = json.loads(row["payload_json"])
            for field in ("story_id", "kind", "language", "variant_group_id", "created_at"):
                if payload.get(field) != previous.get(field):
                    raise RegistryIntegrityError("learning object identity fields cannot be rewritten")
            claim_ids = payload["claim_ids"]
            if claim_ids:
                placeholders = ",".join("?" for _ in claim_ids)
                claims = connection.execute(
                    f"SELECT id, payload_json FROM domain_records WHERE record_type = 'claim' AND id IN ({placeholders})",
                    tuple(claim_ids),
                ).fetchall()
                if len(claims) != len(claim_ids) or any(
                    json.loads(item["payload_json"])["story_id"] != record.story_id for item in claims
                ):
                    raise RegistryIntegrityError("learning object claims must belong to its story")
            self._validate_learning_object_source_evidence_reference(connection, payload)
            self._validate_learning_object_glossary_reference(connection, payload)
            self._validate_learning_object_answer_evidence(connection, payload)
            now = _utc_now()
            updated = connection.execute(
                """UPDATE learning_objects
                SET status = ?, revision = ?, updated_at = ?, last_actor = ?, last_reason = ?, payload_json = ?
                WHERE id = ? AND revision = ?""",
                (record.status, expected_revision + 1, now, actor.strip(), reason.strip(),
                 record.payload_json, learning_object_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise RegistryIntegrityError("learning object revision conflict")
            self._append_audit(
                connection,
                event_type="learning_object.revised",
                entity_type="learning_object",
                entity_id=learning_object_id,
                payload={
                    "revision": expected_revision + 1,
                    "actor": actor.strip(),
                    "reason": reason.strip(),
                    "record": payload,
                },
            )
        return record

    def learning_object_history(self, learning_object_id: str) -> list[dict[str, Any]]:
        """Return the immutable editorial revision history for one learning object."""
        if self.get_learning_object(learning_object_id) is None:
            raise RegistryIntegrityError(f"unknown learning object: {learning_object_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM learning_object_revisions
                WHERE learning_object_id = ? ORDER BY revision""",
                (learning_object_id,),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def story_learning_objects(self, story_id: str) -> list[LearningObjectRecord]:
        """Return current language-specific learning objects for one story."""
        if self.get_domain_record("story", story_id) is None:
            raise RegistryIntegrityError(f"unknown story: {story_id}")
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, payload_json FROM learning_objects
                WHERE story_id = ? ORDER BY kind, language, id""",
                (story_id,),
            ).fetchall()
        return [
            LearningObjectRecord.from_payload(json.loads(row["payload_json"]), row["revision"])
            for row in rows
        ]

    def learning_object_quality_report(self, learning_object_id: str) -> dict[str, Any]:
        """Evaluate deterministic editorial completeness without judging truth automatically."""
        with self._transaction("DEFERRED") as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM learning_objects WHERE id = ?",
                (learning_object_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"unknown learning object: {learning_object_id}")
            payload = json.loads(row["payload_json"])
            blockers: list[dict[str, str]] = []
            warnings: list[dict[str, str]] = []
            checks: list[dict[str, Any]] = []

            def check(name: str, passed: bool, detail: str, *, blocking: bool = True) -> None:
                checks.append({"name": name, "passed": passed, "detail": detail})
                if not passed:
                    target = blockers if blocking else warnings
                    target.append({"check": name, "detail": detail})

            check(
                "editorial_review",
                payload["status"] == "approved",
                "Learning object must be approved before it can pass the registry quality gate.",
            )
            language_review_valid = _language_review_passes(
                payload.get("language_review"), payload.get("language")
            )
            check(
                "language_review",
                language_review_valid,
                "A complete language-specific rubric review must pass before a learning object can pass the registry quality gate.",
            )
            mcq_review_valid = _mcq_review_passes(
                payload.get("mcq_review"), payload.get("language")
            )
            check(
                "mcq_pedagogical_review",
                payload.get("kind") != "mcq_question"
                or (payload.get("status") != "approved" or mcq_review_valid),
                "An approved MCQ requires a complete all-pass pedagogical review rubric.",
            )
            claim_ids = payload.get("claim_ids", [])
            placeholders = ",".join("?" for _ in claim_ids)
            claim_rows = connection.execute(
                f"""SELECT id, payload_json FROM domain_records
                WHERE record_type = 'claim' AND id IN ({placeholders})""" if claim_ids else
                "SELECT id, payload_json FROM domain_records WHERE 0",
                tuple(claim_ids),
            ).fetchall()
            claims = {item["id"]: json.loads(item["payload_json"]) for item in claim_rows}
            missing_claims = sorted(set(claim_ids) - set(claims))
            check(
                "claim_references",
                not missing_claims,
                "All linked claims must still exist." if not missing_claims else
                "Missing claims: " + ", ".join(missing_claims),
            )
            claim_evidence: dict[str, list[str]] = {}
            claim_assessments: dict[str, str] = {}
            for claim_id, claim in claims.items():
                provenance = claim.get("provenance", [])
                evidence_ids = claim.get("evidence_ids", [])
                if evidence_ids:
                    ev_placeholders = ",".join("?" for _ in evidence_ids)
                    found = connection.execute(
                        f"""SELECT id FROM domain_records
                        WHERE record_type = 'evidence' AND id IN ({ev_placeholders})""",
                        tuple(evidence_ids),
                    ).fetchall()
                    found_ids = {item["id"] for item in found}
                    if found_ids != set(evidence_ids):
                        blockers.append({
                            "check": "claim_evidence",
                            "detail": f"Claim {claim_id} has missing evidence records.",
                        })
                claim_evidence[claim_id] = evidence_ids
                claim_assessments[claim_id] = claim.get("assessment", "unassessed")
                if not provenance:
                    blockers.append({
                        "check": "claim_provenance",
                        "detail": f"Claim {claim_id} has no source provenance.",
                    })
                if claim.get("assessment") in {"supported", "partially_supported"} and not evidence_ids:
                    blockers.append({
                        "check": "claim_evidence",
                        "detail": f"Claim {claim_id} is assessed but has no linked evidence.",
                    })
            check(
                "claim_provenance",
                all(claim.get("provenance") for claim in claims.values()) and not missing_claims,
                "Every linked claim has source provenance." if claims and not missing_claims and
                all(claim.get("provenance") for claim in claims.values()) else
                "One or more linked claims lack source provenance.",
            )

            factual_claims: set[str] = set()
            if payload["kind"] in {"headline_summary", "brief_summary", "detailed_summary", "revision_card"}:
                for segment in payload["content"].get("segments", []):
                    if segment["role"] == "fact":
                        factual_claims.update(segment["claim_ids"])
                        for claim_id in segment["claim_ids"]:
                            assessment = claim_assessments.get(claim_id, "unassessed")
                            if assessment not in {"supported", "partially_supported"}:
                                blockers.append({
                                    "check": "factual_segment_assessment",
                                    "detail": f"Factual segment claim {claim_id} is assessed as {assessment}, not supported.",
                                })
                            elif not claim_evidence.get(claim_id):
                                blockers.append({
                                    "check": "factual_segment_evidence",
                                    "detail": f"Factual segment claim {claim_id} has no linked evidence.",
                                })
                    elif segment["role"] in {"attributed", "analysis"}:
                        for claim_id in segment["claim_ids"]:
                            assessment = claim_assessments.get(claim_id, "unassessed")
                            if assessment in {"disputed", "contradicted", "retracted", "unassessed"}:
                                warnings.append({
                                    "check": "qualified_segment",
                                    "detail": f"{segment['role'].title()} segment cites claim {claim_id} with assessment {assessment}; preserve that qualification in the wording.",
                                })
            check(
                "factual_segments",
                all(
                    claim_assessments.get(claim_id) in {"supported", "partially_supported"}
                    and bool(claim_evidence.get(claim_id))
                    for claim_id in factual_claims
                ),
                "Every factual summary segment cites assessed claims with linked evidence.",
            )

            if payload["kind"] in {"comprehension_question", "analysis_question", "mcq_question"}:
                answer_claims = payload["content"].get("answer_claim_ids", [])
                answer_supported = bool(answer_claims) and all(
                    claim_assessments.get(claim_id) in {"supported", "partially_supported"}
                    and bool(claim_evidence.get(claim_id))
                    for claim_id in answer_claims
                )
                check(
                    "answer_support",
                    answer_supported,
                    "Every answer claim must be supported or partially supported and have linked evidence.",
                )
                if payload["kind"] == "mcq_question":
                    options = payload["content"].get("options", [])
                    correct_option_id = payload["content"].get("correct_option_id")
                    distractor_ids = {item["id"] for item in options} - {correct_option_id}
                    rationale_ids = [
                        item.get("option_id")
                        for item in payload["content"].get("distractor_rationales", [])
                    ]
                    rationale_coverage = (
                        len(rationale_ids) == len(set(rationale_ids))
                        and set(rationale_ids) == distractor_ids
                    )
                    check(
                        "mcq_distractor_rationale_coverage",
                        rationale_coverage,
                        "Every incorrect option must have exactly one rationale; the correct option must not have a distractor rationale.",
                    )
                    rationale_claim_ids = {
                        claim_id
                        for item in payload["content"].get("distractor_rationales", [])
                        for claim_id in item.get("basis_claim_ids", [])
                    }
                    rationale_claims_supported = bool(rationale_claim_ids) and all(
                        claim_assessments.get(claim_id) in {"supported", "partially_supported"}
                        and bool(claim_evidence.get(claim_id))
                        for claim_id in rationale_claim_ids
                    )
                    check(
                        "mcq_distractor_rationale_source_support",
                        rationale_claims_supported,
                        "Every distractor rationale must link to story claims assessed as supported or partially supported with evidence.",
                    )
                    warnings.append({
                        "check": "mcq_human_distractor_review",
                        "detail": (
                            "Human review is still required to judge whether distractors are plausible, "
                            "unambiguously incorrect under the question wording, misconception-based, "
                            "parallel in form, and free of accidental clues. Rationale presence does not "
                            "prove distractor quality."
                        ),
                    })

            if payload["kind"] in {"comprehension_question", "analysis_question", "mcq_question"}:
                try:
                    self._validate_learning_object_answer_evidence(connection, payload)
                    answer_evidence_valid = True
                    answer_evidence_detail = (
                        "Every answer claim has a current supporting evidence locator."
                    )
                except RegistryIntegrityError as exc:
                    answer_evidence_valid = False
                    answer_evidence_detail = str(exc)
                check("answer_evidence", answer_evidence_valid, answer_evidence_detail)

            if payload["kind"] == "vocabulary":
                try:
                    self._validate_learning_object_source_evidence_reference(connection, payload)
                    source_evidence_valid = True
                    source_evidence_detail = (
                        "The vocabulary source evidence exists, remains linked to an object claim, "
                        "and matches current claim provenance."
                    )
                except RegistryIntegrityError as exc:
                    source_evidence_valid = False
                    source_evidence_detail = str(exc)
                check("vocabulary_source_evidence", source_evidence_valid, source_evidence_detail)

                glossary_id = payload["content"]["glossary_entry_id"]
                glossary_row = connection.execute(
                    "SELECT status, payload_json FROM glossary_entries WHERE id = ?",
                    (glossary_id,),
                ).fetchone()
                glossary_valid = False
                if glossary_row:
                    glossary = json.loads(glossary_row["payload_json"])
                    glossary_valid = (
                        glossary_row["status"] == "approved"
                        and glossary["lemma"] == payload["content"]["term"]
                        and glossary["term_language"] == payload["content"]["term_language"]
                        and glossary["part_of_speech"] == payload["content"]["part_of_speech"]
                        and any(
                            sense["language"] == payload["language"]
                            and sense["definition"] == payload["content"]["meaning"]
                            for sense in glossary["senses"]
                        )
                    )
                check(
                    "controlled_glossary",
                    glossary_valid,
                    "Vocabulary must match an approved glossary entry and a sense in the learning object's language.",
                )

            return {
                "learning_object_id": learning_object_id,
                "revision": row["revision"],
                "status": payload["status"],
                "meets_registry_quality_gates": not blockers,
                "blockers": blockers,
                "warnings": warnings,
                "checks": checks,
            }

    def bilingual_editorial_quality_report(self, story_id: str) -> dict[str, Any]:
        """Compare Bangla and English editorial coverage without averaging away language failures."""
        snapshot = self.story_editorial_snapshot(story_id)
        objects_by_language: dict[str, list[dict[str, Any]]] = {"bn": [], "en": []}
        variant_groups: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for item in snapshot["learning_objects"]:
            language = item["record"].get("language")
            if language not in objects_by_language:
                continue
            record = item["record"]
            variant_group_id = record.get("variant_group_id")
            if isinstance(variant_group_id, str) and variant_group_id:
                variant_groups.setdefault((record["kind"], variant_group_id), {})[language] = record
            quality = self.learning_object_quality_report(item["id"])
            if quality["revision"] != item["revision"]:
                raise RegistryIntegrityError(
                    "learning object changed while bilingual quality report was being assembled"
                )
            objects_by_language[language].append({
                "learning_object_id": item["id"],
                "revision": item["revision"],
                "kind": item["record"]["kind"],
                "variant_group_id": item["record"].get("variant_group_id"),
                "claim_ids": sorted(item["record"].get("claim_ids", [])),
                "status": quality["status"],
                "meets_registry_quality_gates": quality["meets_registry_quality_gates"],
                "blockers": quality["blockers"],
                "warnings": quality["warnings"],
                "language_review": item["record"].get("language_review"),
            })

        kinds_by_language = {
            language: {item["kind"] for item in items}
            for language, items in objects_by_language.items()
        }
        all_kinds = sorted(kinds_by_language["bn"] | kinds_by_language["en"])
        coverage = [
            {
                "kind": kind,
                "bangla_present": kind in kinds_by_language["bn"],
                "english_present": kind in kinds_by_language["en"],
                "paired": kind in kinds_by_language["bn"] and kind in kinds_by_language["en"],
            }
            for kind in all_kinds
        ]
        language_reports = {}
        for language, items in objects_by_language.items():
            language_reports[language] = {
                "artifact_count": len(items),
                "approved_count": sum(item["status"] == "approved" for item in items),
                "quality_gate_pass_count": sum(item["meets_registry_quality_gates"] for item in items),
                "blocked_count": sum(not item["meets_registry_quality_gates"] for item in items),
                "by_kind": {
                    kind: sum(item["kind"] == kind for item in items)
                    for kind in sorted({item["kind"] for item in items})
                },
                "artifacts": items,
            }

        variant_claim_coverage = []
        for (kind, variant_group_id), members in sorted(variant_groups.items()):
            bangla = members.get("bn")
            english = members.get("en")
            bangla_claims = set(bangla.get("claim_ids", [])) if bangla else set()
            english_claims = set(english.get("claim_ids", [])) if english else set()
            both_present = bangla is not None and english is not None
            variant_claim_coverage.append({
                "kind": kind,
                "variant_group_id": variant_group_id,
                "bangla_learning_object_id": bangla.get("id") if bangla else None,
                "english_learning_object_id": english.get("id") if english else None,
                "bangla_claim_ids": sorted(bangla_claims),
                "english_claim_ids": sorted(english_claims),
                "bangla_only_claim_ids": sorted(bangla_claims - english_claims),
                "english_only_claim_ids": sorted(english_claims - bangla_claims),
                "claim_sets_match": both_present and bangla_claims == english_claims,
                "status": (
                    "matched" if both_present and bangla_claims == english_claims
                    else "claim_set_mismatch" if both_present
                    else "missing_language_counterpart"
                ),
            })

        confirmed_snapshot = self.story_editorial_snapshot(story_id)
        if confirmed_snapshot["fingerprint"] != snapshot["fingerprint"]:
            raise RegistryIntegrityError(
                "story editorial snapshot changed while bilingual quality report was being assembled"
            )
        return {
            "story_id": story_id,
            "snapshot_fingerprint": snapshot["fingerprint"],
            "languages": language_reports,
            "kind_coverage": coverage,
            "missing_language_counterparts": [
                {"kind": item["kind"], "missing_language": "en" if item["bangla_present"] else "bn"}
                for item in coverage if not item["paired"]
            ],
            "variant_claim_coverage": variant_claim_coverage,
            "overall_quality_score": None,
            "interpretation": (
                "Bangla and English are reported independently. Counts describe registered artifacts "
                "and deterministic registry checks, not linguistic quality or factual truth. "
                "A language with no artifacts is unassessed, not a zero-quality result. "
                "No cross-language average or automatic publication approval is produced."
            ),
            "production_ready": False,
            "authorization_granted": False,
        }


    def story_summary_tier_report(self, story_id: str) -> dict[str, Any]:
        """Report the completeness of the three independently reviewed summary tiers."""
        snapshot = self.story_editorial_snapshot(story_id)
        required_tiers = ("headline_summary", "brief_summary", "detailed_summary")
        by_language: dict[str, dict[str, list[dict[str, Any]]]] = {
            language: {tier: [] for tier in required_tiers}
            for language in ("bn", "en")
        }
        for item in snapshot["learning_objects"]:
            record = item["record"]
            language = record.get("language")
            tier = record.get("kind")
            if language not in by_language or tier not in required_tiers:
                continue
            quality = self.learning_object_quality_report(item["id"])
            if quality["revision"] != item["revision"]:
                raise RegistryIntegrityError(
                    "learning object changed while summary-tier report was being assembled"
                )
            by_language[language][tier].append({
                "learning_object_id": item["id"],
                "revision": item["revision"],
                "status": quality["status"],
                "meets_registry_quality_gates": quality["meets_registry_quality_gates"],
                "blockers": quality["blockers"],
                "warnings": quality["warnings"],
                "language_review": record.get("language_review"),
            })

        languages: dict[str, Any] = {}
        for language, tiers in by_language.items():
            tier_reports = {}
            missing_tiers = []
            duplicate_tiers = []
            unapproved_tiers = []
            quality_blocked_tiers = []
            for tier in required_tiers:
                artifacts = tiers[tier]
                if not artifacts:
                    missing_tiers.append(tier)
                if len(artifacts) > 1:
                    duplicate_tiers.append(tier)
                if len(artifacts) != 1 or artifacts[0]["status"] != "approved":
                    unapproved_tiers.append(tier)
                review = artifacts[0]["language_review"] if len(artifacts) == 1 else None
                review_passed = (
                    artifacts[0]["status"] == "approved"
                    and _language_review_passes(review, language)
                ) if len(artifacts) == 1 else False
                if (
                    len(artifacts) != 1
                    or not artifacts[0]["meets_registry_quality_gates"]
                    or not review_passed
                ):
                    quality_blocked_tiers.append(tier)
                tier_reports[tier] = {
                    "artifact_count": len(artifacts),
                    "exactly_one": len(artifacts) == 1,
                    "approved": len(artifacts) == 1 and artifacts[0]["status"] == "approved",
                    "quality_gate_pass": (
                        len(artifacts) == 1
                        and artifacts[0]["meets_registry_quality_gates"]
                    ),
                    "language_review_pass": (
                        len(artifacts) == 1
                        and artifacts[0]["status"] == "approved"
                        and _language_review_passes(artifacts[0]["language_review"], language)
                    ),
                    "artifacts": artifacts,
                }
            languages[language] = {
                "tiers": tier_reports,
                "missing_tiers": missing_tiers,
                "duplicate_tiers": duplicate_tiers,
                "unapproved_or_ambiguous_tiers": unapproved_tiers,
                "quality_blocked_or_ambiguous_tiers": quality_blocked_tiers,
                "complete_reviewed_set": not (
                    missing_tiers or duplicate_tiers or unapproved_tiers or quality_blocked_tiers
                ),
            }

        confirmed_snapshot = self.story_editorial_snapshot(story_id)
        if confirmed_snapshot["fingerprint"] != snapshot["fingerprint"]:
            raise RegistryIntegrityError(
                "story editorial snapshot changed while summary-tier report was being assembled"
            )
        return {
            "story_id": story_id,
            "snapshot_fingerprint": snapshot["fingerprint"],
            "required_tiers": list(required_tiers),
            "languages": languages,
            "interpretation": (
                "This report checks tier coverage, uniqueness, independent approval, and existing "
                "deterministic registry quality gates. It does not assess prose quality, summary "
                "length, completeness against the full source record, or factual truth. Bangla and "
                "English are evaluated independently; an absent language is unassessed."
            ),
            "production_ready": False,
            "authorization_granted": False,
        }

    def add_pipeline_run(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Register the initial queued/running state of one immutable pipeline execution."""
        validate_payload("pipeline-run", payload)
        if payload.get("status") not in {"queued", "running"}:
            raise RegistryIntegrityError("new pipeline run must start queued or running")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("pipeline run actor and reason are required")
        _aware_datetime(payload["started_at"], "started_at")
        payload_json = _json_text(payload)
        with self._transaction() as connection:
            retry_of = payload.get("retry_of")
            if retry_of is not None:
                if retry_of == payload["id"]:
                    raise RegistryIntegrityError("pipeline run cannot retry itself")
                parent_row = connection.execute(
                    "SELECT payload_json FROM pipeline_runs WHERE id = ?", (retry_of,)
                ).fetchone()
                if parent_row is None:
                    raise RegistryIntegrityError("retry parent pipeline run does not exist")
                parent = json.loads(parent_row["payload_json"])
                if parent["status"] != "failed":
                    raise RegistryIntegrityError("retry parent pipeline run must be failed")
                if payload.get("attempt") != parent.get("attempt", 1) + 1:
                    raise RegistryIntegrityError("retry attempt must immediately follow its parent")
                inherited = ("software_version", "config_hash", "input_hashes", "initiated_by")
                if any(payload.get(field) != parent.get(field) for field in inherited):
                    raise RegistryIntegrityError("retry must preserve its parent's immutable inputs")
            connection.execute(
                """INSERT INTO pipeline_runs (id, status, revision, started_at, payload_json)
                   VALUES (?, ?, 1, ?, ?)""",
                (payload["id"], payload["status"], payload["started_at"], payload_json),
            )
            connection.execute(
                """INSERT INTO pipeline_run_revisions
                   (pipeline_run_id, revision, created_at, actor, reason, payload_json)
                   VALUES (?, 1, ?, ?, ?, ?)""",
                (payload["id"], _utc_now(), actor.strip(), reason.strip(), payload_json),
            )
            self._append_audit(
                connection, event_type="pipeline_run.created",
                entity_type="pipeline_run", entity_id=payload["id"],
                payload={"revision": 1, "record": payload, "actor": actor.strip(), "reason": reason.strip()},
            )
        return {"revision": 1, "record": json.loads(payload_json)}

    def get_pipeline_run(self, pipeline_run_id: str) -> dict[str, Any] | None:
        """Return the current pipeline-run revision and its validated record."""
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, payload_json FROM pipeline_runs WHERE id = ?",
                (pipeline_run_id,),
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row["payload_json"])
        validate_payload("pipeline-run", record)
        return {"revision": row["revision"], "record": record}

    def transition_pipeline_run(
        self, pipeline_run_id: str, payload: dict[str, Any], *,
        expected_revision: int, actor: str, reason: str
    ) -> dict[str, Any]:
        """Append a legal lifecycle transition; run identity and inputs never change."""
        validate_payload("pipeline-run", payload)
        if payload.get("id") != pipeline_run_id:
            raise RegistryIntegrityError("pipeline run identity cannot change")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("pipeline run actor and reason are required")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM pipeline_runs WHERE id = ?",
                (pipeline_run_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("unknown pipeline run")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("pipeline run revision conflict")
            previous = json.loads(row["payload_json"])
            transitions = {
                "queued": {"running", "cancelled", "rejected"},
                "running": {"succeeded", "failed", "cancelled"},
            }
            if payload.get("status") not in transitions.get(previous["status"], set()):
                raise RegistryIntegrityError("invalid pipeline run status transition")
            immutable = ("id", "started_at", "software_version", "config_hash", "input_hashes", "initiated_by", "attempt", "retry_of")
            if any(payload.get(field) != previous.get(field) for field in immutable):
                raise RegistryIntegrityError("pipeline run identity and inputs are immutable")
            _aware_datetime(payload["started_at"], "started_at")
            if "finished_at" in payload and _aware_datetime(payload["finished_at"], "finished_at") < _aware_datetime(previous["started_at"], "started_at"):
                raise RegistryIntegrityError("pipeline run cannot finish before it starts")
            revision = expected_revision + 1
            payload_json = _json_text(payload)
            updated = connection.execute(
                """UPDATE pipeline_runs SET status = ?, revision = ?, payload_json = ?
                   WHERE id = ? AND revision = ?""",
                (payload["status"], revision, payload_json, pipeline_run_id, expected_revision),
            )
            if updated.rowcount != 1:
                raise RegistryIntegrityError("pipeline run revision conflict")
            connection.execute(
                """INSERT INTO pipeline_run_revisions
                   (pipeline_run_id, revision, created_at, actor, reason, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (pipeline_run_id, revision, _utc_now(), actor.strip(), reason.strip(), payload_json),
            )
            self._append_audit(
                connection, event_type="pipeline_run.transitioned",
                entity_type="pipeline_run", entity_id=pipeline_run_id,
                payload={"from": previous["status"], "to": payload["status"], "revision": revision,
                         "actor": actor.strip(), "reason": reason.strip(), "record": payload},
            )
        return {"revision": revision, "record": json.loads(payload_json)}

    def pipeline_run_history(self, pipeline_run_id: str) -> list[dict[str, Any]]:
        """Return append-only pipeline execution history."""
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                   FROM pipeline_run_revisions WHERE pipeline_run_id = ? ORDER BY revision""",
                (pipeline_run_id,),
            ).fetchall()
        return [
            {"revision": row["revision"], "created_at": row["created_at"],
             "actor": row["actor"], "reason": row["reason"], "record": json.loads(row["payload_json"])}
            for row in rows
        ]


    def list_pipeline_runs(
        self, *, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List recent pipeline runs without exposing unbounded history or payload detail."""
        allowed_statuses = {"queued", "running", "succeeded", "failed", "cancelled", "rejected"}
        if status is not None and (not isinstance(status, str) or status not in allowed_statuses):
            raise ValueError("unsupported pipeline run status filter")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be an integer between 1 and 5000")
        query = "SELECT revision, payload_json FROM pipeline_runs"
        params: tuple[Any, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY started_at DESC, id DESC LIMIT ?"
        params = (*params, limit)
        with self._lock:
            rows = self._connection.execute(query, params).fetchall()
        results = []
        for row in rows:
            record = json.loads(row["payload_json"])
            validate_payload("pipeline-run", record)
            results.append({"revision": row["revision"], "record": record})
        return results

    def store_verified_edition_artifact(
        self, verified: Any, *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Reverify and persist one immutable staging artifact/manifest pair."""
        from .edition_artifacts import (
            VerifiedRegistryEdition,
            verify_registry_backed_edition_artifact,
        )

        if not isinstance(verified, VerifiedRegistryEdition):
            raise RegistryIntegrityError("only a registry-verified staging artifact can be stored")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        try:
            payload = json.loads(verified.artifact.payload_bytes)
            manifest = json.loads(verified.artifact.manifest_bytes)
        except (TypeError, ValueError) as exc:
            raise RegistryIntegrityError("verified artifact bytes are not valid JSON") from exc

        # Hold this registry instance's re-entrant lock across re-verification
        # and insertion so another thread cannot revise the records in between.
        with self._lock:
            evaluated_at = datetime.now(timezone.utc)
            fresh = verify_registry_backed_edition_artifact(
                self, payload, manifest, now=evaluated_at
            )
            if fresh.artifact.payload_sha256 != verified.artifact.payload_sha256:
                raise RegistryIntegrityError("staging artifact changed before persistence")
            created_at = _utc_now()
            receipt = {
                "status": "verified_for_staging_only",
                "publication_authorized": False,
                "pipeline_run_id": fresh.pipeline_run.pipeline_run_id,
                "pipeline_run_revision": fresh.pipeline_run.revision,
                "story_ids": list(fresh.story_ids),
                "learning_object_ids": list(fresh.learning_object_ids),
                "source_permission_checks": fresh.source_permission_checks,
                "verified_at": evaluated_at.isoformat(),
                "reverify_required_before_release": True,
            }
            with self._transaction() as connection:
                try:
                    connection.execute(
                        """INSERT INTO edition_artifacts
                        (manifest_id, edition_id, edition_revision, payload_sha256,
                         payload_json, manifest_json, verification_json, created_at, actor, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            manifest["manifest_id"], manifest["edition_id"],
                            manifest["edition_revision"], fresh.artifact.payload_sha256,
                            fresh.artifact.payload_bytes.decode("utf-8"),
                            fresh.artifact.manifest_bytes.decode("utf-8"),
                            _json_text(receipt), created_at, actor.strip(), reason.strip(),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RegistryIntegrityError(
                        "edition artifact already exists or violates immutable artifact constraints"
                    ) from exc
                self._append_audit(
                    connection,
                    event_type="edition_artifact.stored",
                    entity_type="edition_artifact",
                    entity_id=manifest["manifest_id"],
                    payload={
                        "edition_id": manifest["edition_id"],
                        "edition_revision": manifest["edition_revision"],
                        "payload_sha256": fresh.artifact.payload_sha256,
                        "actor": actor.strip(),
                        "reason": reason.strip(),
                        "publication_authorized": False,
                    },
                )
        return self.get_edition_artifact(manifest["manifest_id"]) or {}

    def get_edition_artifact(self, manifest_id: str) -> dict[str, Any] | None:
        """Retrieve an immutable artifact after rechecking its stored byte fingerprints.

        Retrieval verifies stored integrity only. Current rights, reviewers, and
        release eligibility must be re-evaluated before any later release decision.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM edition_artifacts WHERE manifest_id = ?", (manifest_id,)
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        manifest = json.loads(row["manifest_json"])
        receipt = json.loads(row["verification_json"])
        from .edition_artifacts import verify_edition_artifact
        verified = verify_edition_artifact(payload, manifest)
        if (
            verified.payload_sha256 != row["payload_sha256"]
            or row["edition_id"] != manifest["edition_id"]
            or row["edition_revision"] != manifest["edition_revision"]
            or receipt.get("publication_authorized") is not False
        ):
            raise RegistryIntegrityError("stored edition artifact integrity or receipt binding failed")
        return {
            "manifest_id": row["manifest_id"],
            "edition_id": row["edition_id"],
            "edition_revision": row["edition_revision"],
            "payload_sha256": row["payload_sha256"],
            "payload": payload,
            "manifest": manifest,
            "verification": receipt,
            "created_at": row["created_at"],
            "actor": row["actor"],
            "reason": row["reason"],
            "integrity_verified": True,
            "publication_authorized": False,
            "reverify_required_before_release": True,
        }


    def record_edition_decision(
        self, manifest_id: str, *, identity: Any, decision: str, reason: str
    ) -> dict[str, Any]:
        """Record one provider-attested human approval/rejection of an exact staged artifact.

        This records an editorial release-review decision only. It never authorizes
        publication, changes edition status, or grants downstream write permission.
        """
        from datetime import timedelta
        from .benchmark_access import AuthenticatedIdentity
        from .edition_artifacts import verify_registry_backed_edition_artifact

        if not isinstance(identity, AuthenticatedIdentity):
            raise RegistryIntegrityError("edition decision requires a trusted authenticated identity")
        if decision not in {"approve", "reject"}:
            raise RegistryIntegrityError("edition decision must be approve or reject")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("decision reason is required")
        now = datetime.now(timezone.utc)
        if (
            identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None
            or identity.authenticated_at > now or identity.expires_at <= now
            or now - identity.authenticated_at > timedelta(minutes=15)
        ):
            raise RegistryIntegrityError("edition decision requires a valid session and authentication within 15 minutes")
        if not all(isinstance(value, str) and value.strip() for value in (
            identity.principal_id, identity.issuer, identity.audience, identity.subject,
            identity.session_id, identity.authentication_context, identity.provider_key_id,
            identity.provider_adapter,
        )) or not identity.authentication_methods:
            raise RegistryIntegrityError("edition decision identity attestation is incomplete")

        with self._lock:
            principal = self.get_benchmark_principal(identity.principal_id)
            if (
                principal is None or not principal["active"]
                or principal.get("principal_kind") != "human"
                or "edition_approver" not in principal["roles"]
            ):
                raise RegistryIntegrityError("edition decision requires an active human edition approver")
            artifact = self.get_edition_artifact(manifest_id)
            if artifact is None:
                raise RegistryIntegrityError("unknown stored edition artifact")
            verified = verify_registry_backed_edition_artifact(
                self, artifact["payload"], artifact["manifest"], now=now
            )
            if verified.artifact.payload_sha256 != artifact["payload_sha256"]:
                raise RegistryIntegrityError("stored edition artifact changed during decision verification")

            # An approver must be independent of every language reviewer on the candidate.
            for object_id in verified.learning_object_ids:
                record = self.get_learning_object(object_id)
                if record is None:
                    raise RegistryIntegrityError("edition learning object disappeared during decision verification")
                object_payload = record.to_payload()
                review = object_payload.get("language_review", {})
                reviewer_id = review.get("reviewer_id") or object_payload.get("reviewed_by")
                if not isinstance(reviewer_id, str) or not reviewer_id.strip():
                    raise RegistryIntegrityError("edition approval requires recorded language-reviewer identity for every selected object")
                if reviewer_id == identity.principal_id:
                    raise RegistryIntegrityError("edition approver must be independent of the candidate's language reviewers")

            decided_at = now.isoformat()
            import hashlib
            payload = {
                "schema_version": "1.0.0",
                "decision_id": f"edition-decision:{uuid.uuid4().hex}",
                "manifest_id": manifest_id,
                "edition_id": artifact["edition_id"],
                "edition_revision": artifact["edition_revision"],
                "payload_sha256": artifact["payload_sha256"],
                "decision": decision,
                "scope": "staging_candidate_review",
                "principal_id": identity.principal_id,
                "role": "edition_approver",
                "decided_at": decided_at,
                "reason": reason.strip(),
                "identity_attestation": {
                    "issuer": identity.issuer,
                    "audience": identity.audience,
                    "subject": identity.subject,
                    "authenticated_at": identity.authenticated_at.isoformat(),
                    "expires_at": identity.expires_at.isoformat(),
                    "authentication_methods": list(identity.authentication_methods),
                    "authentication_context": identity.authentication_context,
                    "provider_key_id": identity.provider_key_id,
                    "provider_adapter": identity.provider_adapter,
                    "session_fingerprint": "sha256:" + hashlib.sha256(identity.session_id.encode("utf-8")).hexdigest(),
                },
                "publication_authorized": False,
            }
            validate_payload("edition-decision", payload)
            with self._transaction() as connection:
                self._require_benchmark_role(connection, identity.principal_id, "edition_approver")
                try:
                    connection.execute(
                        """INSERT INTO edition_decisions
                        (decision_id, manifest_id, edition_id, edition_revision,
                         payload_sha256, decision, principal_id, decided_at, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            payload["decision_id"], manifest_id, payload["edition_id"],
                            payload["edition_revision"], payload["payload_sha256"],
                            decision, identity.principal_id, decided_at, _json_text(payload),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RegistryIntegrityError(
                        "this immutable artifact already has a final edition decision"
                    ) from exc
                self._append_audit(
                    connection, event_type="edition_decision.recorded",
                    entity_type="edition_artifact", entity_id=manifest_id,
                    payload=payload,
                )
        return payload


    def authorize_edition_release(
        self, manifest_id: str, *, identity: Any, reason: str
    ) -> dict[str, Any]:
        """Authorize an exact approved staging artifact for a separate promotion gate.

        This is a second-person authorization only. It never writes a live destination,
        changes an edition status, or sets publication_authorized.
        """
        from datetime import timedelta
        from .benchmark_access import AuthenticatedIdentity
        from .edition_artifacts import verify_registry_backed_edition_artifact

        if not isinstance(identity, AuthenticatedIdentity):
            raise RegistryIntegrityError("release authorization requires a trusted authenticated identity")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("release authorization reason is required")
        now = datetime.now(timezone.utc)
        if (
            identity.authenticated_at.tzinfo is None or identity.expires_at.tzinfo is None
            or identity.authenticated_at > now or identity.expires_at <= now
            or now - identity.authenticated_at > timedelta(minutes=15)
        ):
            raise RegistryIntegrityError("release authorization requires a valid session and authentication within 15 minutes")
        if not all(isinstance(value, str) and value.strip() for value in (
            identity.principal_id, identity.issuer, identity.audience, identity.subject,
            identity.session_id, identity.authentication_context, identity.provider_key_id,
            identity.provider_adapter,
        )) or not identity.authentication_methods:
            raise RegistryIntegrityError("release authorizer identity attestation is incomplete")

        with self._lock:
            principal = self.get_benchmark_principal(identity.principal_id)
            if (
                principal is None or not principal["active"]
                or principal.get("principal_kind") != "human"
                or "edition_release_authorizer" not in principal["roles"]
            ):
                raise RegistryIntegrityError("release authorization requires an active human edition_release_authorizer")
            approval = self.get_edition_decision(manifest_id)
            if approval is None or approval.get("decision") != "approve":
                raise RegistryIntegrityError("release authorization requires an approved staging decision")
            if approval.get("principal_id") == identity.principal_id:
                raise RegistryIntegrityError("release authorizer must be different from the staging approver")
            approver = self.get_benchmark_principal(approval.get("principal_id", ""))
            if (
                approver is None or not approver["active"]
                or approver.get("principal_kind") != "human"
                or "edition_approver" not in approver["roles"]
            ):
                raise RegistryIntegrityError("the staging approver must remain an active human edition approver")
            artifact = self.get_edition_artifact(manifest_id)
            if artifact is None:
                raise RegistryIntegrityError("unknown stored edition artifact")
            if (
                approval.get("edition_id") != artifact["edition_id"]
                or approval.get("edition_revision") != artifact["edition_revision"]
                or approval.get("payload_sha256") != artifact["payload_sha256"]
            ):
                raise RegistryIntegrityError("staging approval does not bind the exact stored artifact")
            verified = verify_registry_backed_edition_artifact(
                self, artifact["payload"], artifact["manifest"], now=now
            )
            if verified.artifact.payload_sha256 != artifact["payload_sha256"]:
                raise RegistryIntegrityError("stored edition artifact changed during release verification")

            for object_id in verified.learning_object_ids:
                record = self.get_learning_object(object_id)
                if record is None:
                    raise RegistryIntegrityError("edition learning object disappeared during release verification")
                object_payload = record.to_payload()
                review = object_payload.get("language_review", {})
                reviewer_id = review.get("reviewer_id") or object_payload.get("reviewed_by")
                if not isinstance(reviewer_id, str) or not reviewer_id.strip():
                    raise RegistryIntegrityError("release authorization requires recorded reviewer identity for every selected object")
                if reviewer_id == identity.principal_id:
                    raise RegistryIntegrityError("release authorizer must be independent of the candidate's language reviewers")

            authorized_at = now.isoformat()
            import hashlib
            payload = {
                "schema_version": "1.0.0",
                "authorization_id": f"edition-release-authorization:{uuid.uuid4().hex}",
                "manifest_id": manifest_id,
                "edition_id": artifact["edition_id"],
                "edition_revision": artifact["edition_revision"],
                "payload_sha256": artifact["payload_sha256"],
                "approval_decision_id": approval["decision_id"],
                "approver_principal_id": approval["principal_id"],
                "authorizer_principal_id": identity.principal_id,
                "authorized_at": authorized_at,
                "scope": "controlled_release_gate",
                "reason": reason.strip(),
                "identity_attestation": {
                    "issuer": identity.issuer,
                    "audience": identity.audience,
                    "subject": identity.subject,
                    "authenticated_at": identity.authenticated_at.isoformat(),
                    "expires_at": identity.expires_at.isoformat(),
                    "authentication_methods": list(identity.authentication_methods),
                    "authentication_context": identity.authentication_context,
                    "provider_key_id": identity.provider_key_id,
                    "provider_adapter": identity.provider_adapter,
                    "session_fingerprint": "sha256:" + hashlib.sha256(identity.session_id.encode("utf-8")).hexdigest(),
                },
                "release_authorized": True,
                "publication_authorized": False,
            }
            validate_payload("edition-release-authorization", payload)
            with self._transaction() as connection:
                self._require_benchmark_role(connection, identity.principal_id, "edition_release_authorizer")
                try:
                    connection.execute(
                        """INSERT INTO edition_release_authorizations
                        (authorization_id, manifest_id, edition_id, edition_revision,
                         payload_sha256, approval_decision_id, approver_principal_id,
                         authorizer_principal_id, authorized_at, payload_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            payload["authorization_id"], manifest_id, payload["edition_id"],
                            payload["edition_revision"], payload["payload_sha256"],
                            payload["approval_decision_id"], payload["approver_principal_id"],
                            identity.principal_id, authorized_at, _json_text(payload),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RegistryIntegrityError(
                        "this artifact already has a release authorization or failed its database guard"
                    ) from exc
                self._append_audit(
                    connection, event_type="edition_release.authorized",
                    entity_type="edition_artifact", entity_id=manifest_id,
                    payload=payload,
                )
        return payload

    def get_edition_release_authorization(self, manifest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM edition_release_authorizations WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def get_edition_decision(self, manifest_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM edition_decisions WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def list_edition_decisions(self, edition_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload_json FROM edition_decisions WHERE edition_id = ? ORDER BY decided_at, decision_id",
                (edition_id,),
            ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def get_domain_record(self, record_type: str, record_id: str):
        if record_type == "story_decision":
            with self._lock:
                row = self._connection.execute(
                    "SELECT payload_json FROM story_decisions WHERE id = ?",
                    (record_id,),
                ).fetchone()
            return StoryDecisionRecord.from_payload(json.loads(row["payload_json"])) if row else None
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        with self._lock:
            row = self._connection.execute(
                """SELECT payload_json FROM domain_records
                WHERE record_type = ? AND id = ?""",
                (record_type, record_id),
            ).fetchone()
        return model.from_payload(json.loads(row["payload_json"])) if row else None

    def revise_domain_record(
        self,
        record_type: str,
        record_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ):
        """Append a domain-record revision using optimistic concurrency control."""
        if record_type == "story_decision":
            raise RegistryIntegrityError("story decision records are immutable")
        model = self._DOMAIN_MODELS.get(record_type)
        if model is None:
            raise ValueError(f"unsupported domain record type: {record_type}")
        if payload.get("id") != record_id:
            raise RegistryIntegrityError("record identity cannot change during revision")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        payload = dict(payload)
        updated_at = _utc_now()
        if record_type == "story":
            if "revision" in payload:
                payload["revision"] = expected_revision + 1
            if "updated_at" in payload:
                payload["updated_at"] = updated_at
        validate_payload(record_type, payload)
        record = model.from_payload(payload)
        with self._transaction() as connection:
            row = connection.execute(
                """SELECT revision, payload_json FROM domain_records
                WHERE record_type = ? AND id = ?""",
                (record_type, record_id),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revise an unknown domain record")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("domain record revision conflict")
            if record_type == "edition":
                raise RegistryIntegrityError("edition snapshots are immutable")
            previous_payload = json.loads(row["payload_json"])
            immutable_time_field = {
                "article": "discovered_at",
                "story": "created_at",
                "claim": "created_at",
                "evidence": "assessed_at",
            }[record_type]
            if payload[immutable_time_field] != previous_payload[immutable_time_field]:
                raise RegistryIntegrityError("record creation/observation time cannot be rewritten")
            immutable_identity_fields = {
                "article": ("source_id", "canonical_uri"),
                "evidence": ("source_id", "claim_id", "article_id", "observation_id", "locator"),
            }.get(record_type, ())
            if any(payload.get(field) != previous_payload.get(field) for field in immutable_identity_fields):
                raise RegistryIntegrityError("domain record identity links cannot be rewritten")
            self._validate_domain_references(connection, record_type, payload)
            next_revision = expected_revision + 1
            updated = connection.execute(
                """UPDATE domain_records SET revision = ?, updated_at = ?, payload_json = ?
                WHERE record_type = ? AND id = ? AND revision = ?""",
                (
                    next_revision,
                    updated_at,
                    record.payload_json,
                    record_type,
                    record_id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise RegistryIntegrityError("domain record revision conflict")
            connection.execute(
                """INSERT INTO domain_record_revisions
                (record_type, record_id, revision, created_at, actor, reason, payload_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    record_type,
                    record_id,
                    next_revision,
                    _utc_now(),
                    actor,
                    reason,
                    record.payload_json,
                ),
            )
            self._append_audit(
                connection,
                event_type=f"{record_type}.revised",
                entity_type=record_type,
                entity_id=record_id,
                payload={
                    "revision": next_revision,
                    "actor": actor,
                    "reason": reason,
                    "record": payload,
                },
            )
        return record

    def domain_record_history(self, record_type: str, record_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM domain_record_revisions
                WHERE record_type = ? AND record_id = ? ORDER BY revision""",
                (record_type, record_id),
            ).fetchall()
        return [
            {
                "revision": row["revision"],
                "created_at": row["created_at"],
                "actor": row["actor"],
                "reason": row["reason"],
                "record": json.loads(row["payload_json"]),
            }
            for row in rows
        ]



    def register_benchmark_principal(
        self, principal_id: str, *, display_name: str, roles: list[str],
        provisioned_by: str, reason: str, languages: list[str] | None = None,
        principal_kind: str = "unverified"
    ) -> dict[str, Any]:
        """Provision a registry principal; caller must be a trusted operator."""
        allowed = {"benchmark_curator", "benchmark_reviewer", "benchmark_adjudicator", "benchmark_security_admin", "benchmark_methodologist", "benchmark_acceptance_reviewer", "edition_approver", "edition_release_authorizer", "edition_staging_operator"}
        supported_languages = {"bn", "en"}
        if principal_kind not in {"human", "synthetic", "automation", "unverified"}:
            raise ValueError("principal_kind must be human, synthetic, automation, or unverified")
        languages = [] if languages is None else languages
        if not isinstance(languages, list) or len(languages) != len(set(languages)) or not set(languages) <= supported_languages:
            raise ValueError("languages must be a unique subset of bn and en")
        if languages and "benchmark_reviewer" not in roles:
            raise ValueError("language qualifications may only be assigned to benchmark reviewers")
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("principal_id is required")
        if not isinstance(display_name, str) or not display_name.strip():
            raise ValueError("display_name is required")
        if not isinstance(roles, list) or not roles or len(roles) != len(set(roles)) or not set(roles) <= allowed:
            raise ValueError("roles must be a non-empty unique subset of the benchmark roles")
        if not isinstance(provisioned_by, str) or not provisioned_by.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("provisioning actor and reason are required")
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """INSERT INTO benchmark_principals
                (principal_id, display_name, roles_json, active, created_at, created_by, reason, languages_json, principal_kind)
                VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (principal_id, display_name, _json_text(sorted(roles)), now, provisioned_by, reason, _json_text(sorted(languages)), principal_kind),
            )
            self._append_audit(
                connection, event_type="benchmark_principal.provisioned",
                entity_type="benchmark_principal", entity_id=principal_id,
                payload={"roles": sorted(roles), "principal_kind": principal_kind, "provisioned_by": provisioned_by, "reason": reason},
            )
        return self.get_benchmark_principal(principal_id) or {}

    def get_benchmark_principal(self, principal_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_principals WHERE principal_id = ?", (principal_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "principal_id": row["principal_id"], "display_name": row["display_name"],
            "roles": json.loads(row["roles_json"]), "active": bool(row["active"]),
            "languages": json.loads(row["languages_json"]), "principal_kind": row["principal_kind"],
            "created_at": row["created_at"], "created_by": row["created_by"], "reason": row["reason"],
        }

    def revoke_benchmark_principal(self, principal_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and reason are required")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT active FROM benchmark_principals WHERE principal_id = ?", (principal_id,)
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revoke an unknown benchmark principal")
            if row["active"]:
                connection.execute(
                    "UPDATE benchmark_principals SET active = 0 WHERE principal_id = ?", (principal_id,)
                )
                self._append_audit(
                    connection, event_type="benchmark_principal.revoked",
                    entity_type="benchmark_principal", entity_id=principal_id,
                    payload={"actor": actor, "reason": reason},
                )
        return self.get_benchmark_principal(principal_id) or {}

    def reinstate_benchmark_principal(self, principal_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        """Restore a revoked principal; application access must authorize a security administrator."""
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("actor and reason are required")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT active FROM benchmark_principals WHERE principal_id = ?", (principal_id,)
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot reinstate an unknown benchmark principal")
            if row["active"]:
                raise RegistryIntegrityError("benchmark principal is already active")
            connection.execute(
                "UPDATE benchmark_principals SET active = 1 WHERE principal_id = ?", (principal_id,)
            )
            self._append_audit(
                connection, event_type="benchmark_principal.reinstated",
                entity_type="benchmark_principal", entity_id=principal_id,
                payload={"actor": actor, "reason": reason},
            )
        return self.get_benchmark_principal(principal_id) or {}

    @staticmethod
    def _require_benchmark_role(
        connection: sqlite3.Connection, principal_id: str, role: str
    ) -> None:
        row = connection.execute(
            "SELECT roles_json, active FROM benchmark_principals WHERE principal_id = ?",
            (principal_id,),
        ).fetchone()
        if row is None or not row["active"]:
            raise RegistryIntegrityError("benchmark action requires an active registered principal")
        if role not in json.loads(row["roles_json"]):
            raise RegistryIntegrityError(f"benchmark principal lacks required role: {role}")

    def register_benchmark_artifact(
        self, artifact_id: str, *, language: str, content: Any,
        actor: str, reason: str
    ) -> dict[str, Any]:
        """Register canonical benchmark content and calculate its immutable fingerprint."""
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ValueError("artifact_id is required")
        if language not in {"bn", "en"}:
            raise ValueError("benchmark language must be bn or en")
        if not isinstance(content, (dict, list)):
            raise ValueError("benchmark content must be a JSON object or array")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        try:
            validate_payload("editorial-benchmark-content", content)
            content_json = _json_text(content)
            canonical_content = json.loads(content_json)
            fingerprint = sha256_fingerprint(canonical_content)
        except (TypeError, ValueError) as exc:
            raise RegistryIntegrityError("benchmark content must satisfy the canonical editorial benchmark contract") from exc
        item_ids = [item["item_id"] for item in canonical_content["items"]]
        if len(item_ids) != len(set(item_ids)):
            raise RegistryIntegrityError("benchmark item IDs must be unique within an artifact")
        created_at = _utc_now()
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_curator")
            connection.execute(
                """INSERT INTO benchmark_artifacts
                (artifact_id, language, canonicalization, content_fingerprint,
                 content_json, created_at, actor, reason)
                VALUES (?, ?, 'canonical-json-v1', ?, ?, ?, ?, ?)""",
                (artifact_id, language, fingerprint, content_json, created_at, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_artifact.registered",
                entity_type="benchmark_artifact", entity_id=artifact_id,
                payload={"language": language, "content_fingerprint": fingerprint,
                         "canonicalization": "canonical-json-v1", "reason": reason},
            )
        return self.get_benchmark_artifact(artifact_id) or {}

    def get_benchmark_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        if row is None:
            return None
        content = json.loads(row["content_json"])
        recomputed = sha256_fingerprint(content)
        return {
            "artifact_id": row["artifact_id"],
            "language": row["language"],
            "canonicalization": row["canonicalization"],
            "content_fingerprint": row["content_fingerprint"],
            "content": content,
            "created_at": row["created_at"],
            "actor": row["actor"],
            "reason": row["reason"],
            "fingerprint_verified": recomputed == row["content_fingerprint"],
        }

    def _validate_benchmark_artifact_binding(
        self, review: dict[str, Any], *, connection: sqlite3.Connection | None = None
    ) -> None:
        conn = connection or self._connection
        row = conn.execute(
            "SELECT language, canonicalization, content_fingerprint, content_json "
            "FROM benchmark_artifacts WHERE artifact_id = ?",
            (review["benchmark_id"],),
        ).fetchone()
        if row is None:
            raise RegistryIntegrityError("benchmark review must reference a registered benchmark artifact")
        artifact_content = json.loads(row["content_json"])
        actual_fingerprint = sha256_fingerprint(artifact_content)
        if actual_fingerprint != row["content_fingerprint"]:
            raise RegistryIntegrityError("registered benchmark artifact fingerprint verification failed")
        if row["canonicalization"] != "canonical-json-v1":
            raise RegistryIntegrityError("unsupported benchmark artifact canonicalization")
        if row["language"] != review["language"]:
            raise RegistryIntegrityError("benchmark artifact language does not match the review language")
        if row["content_fingerprint"] != review["benchmark_fingerprint"]:
            raise RegistryIntegrityError("benchmark review fingerprint does not match registered artifact content")

    def create_benchmark_review_assignment(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Create an immutable-artifact assignment for a language-qualified reviewer."""
        try:
            validate_payload("editorial-benchmark-assignment", payload)
        except (TypeError, ValueError) as exc:
            raise RegistryIntegrityError("assignment does not satisfy its versioned JSON contract") from exc
        report = validate_benchmark_assignment(payload)
        if not report["valid"] or payload.get("status") != "assigned":
            raise RegistryIntegrityError("assignment contract invalid; new assignments must start as assigned")
        if not actor.strip() or not reason.strip() or actor != payload["assigned_by"]:
            raise RegistryIntegrityError("assignment actor must match the declared assigner and include a reason")
        now = _utc_now()
        assigned_at = _aware_datetime(payload["assigned_at"], "assigned_at")
        due_at = _aware_datetime(payload["due_at"], "due_at")
        if assigned_at > _aware_datetime(now, "now"):
            raise RegistryIntegrityError("assignment cannot be created in the future")
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_curator")
            artifact = connection.execute(
                "SELECT language, content_fingerprint FROM benchmark_artifacts WHERE artifact_id = ?",
                (payload["artifact_id"],),
            ).fetchone()
            if artifact is None:
                raise RegistryIntegrityError("assignment must reference a registered benchmark artifact")
            if artifact["language"] != payload["language"] or artifact["content_fingerprint"] != payload["artifact_fingerprint"]:
                raise RegistryIntegrityError("assignment must bind to the exact artifact fingerprint and language")
            final_adjudication = connection.execute(
                "SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = ?",
                (payload["artifact_id"],),
            ).fetchone()
            if final_adjudication is not None:
                raise RegistryIntegrityError("cannot assign reviews after final artifact adjudication")
            reviewer = connection.execute(
                "SELECT roles_json, languages_json, active FROM benchmark_principals WHERE principal_id = ?",
                (payload["reviewer_id"],),
            ).fetchone()
            if reviewer is None or not reviewer["active"]:
                raise RegistryIntegrityError("assigned reviewer must be active")
            if "benchmark_reviewer" not in json.loads(reviewer["roles_json"]):
                raise RegistryIntegrityError("assigned principal lacks benchmark_reviewer role")
            if payload["language"] not in json.loads(reviewer["languages_json"]):
                raise RegistryIntegrityError("assigned reviewer is not qualified for the benchmark language")
            if payload["reviewer_id"] == actor:
                raise RegistryIntegrityError("curators cannot assign benchmark reviews to themselves")
            payload_json = _json_text(payload)
            connection.execute(
                """INSERT INTO benchmark_review_assignments
                (assignment_id, artifact_id, artifact_fingerprint, language, reviewer_id,
                 assigned_by, assigned_at, due_at, status, revision, payload_json,
                 updated_at, last_actor, last_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'assigned', 1, ?, ?, ?, ?)""",
                (payload["assignment_id"], payload["artifact_id"], payload["artifact_fingerprint"],
                 payload["language"], payload["reviewer_id"], actor, assigned_at.isoformat(),
                 due_at.isoformat(), payload_json, now, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_review_assignment.created",
                entity_type="benchmark_review_assignment", entity_id=payload["assignment_id"],
                payload={"artifact_id": payload["artifact_id"], "artifact_fingerprint": payload["artifact_fingerprint"],
                         "language": payload["language"], "reviewer_id": payload["reviewer_id"],
                         "due_at": payload["due_at"], "actor": actor, "reason": reason},
            )
        return self.get_benchmark_review_assignment(payload["assignment_id"]) or {}

    def get_benchmark_review_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT revision, assigned_at AS created_at, updated_at, last_actor, last_reason, payload_json "
            "FROM benchmark_review_assignments WHERE assignment_id = ?", (assignment_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            **json.loads(row["payload_json"]), "revision": row["revision"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "last_actor": row["last_actor"], "last_reason": row["last_reason"],
        }

    def list_benchmark_review_assignments(
        self, *, reviewer_id: str | None = None, status: str | None = None,
        language: str | None = None
    ) -> list[dict[str, Any]]:
        """List assignment queue entries using explicit optional filters."""
        if status is not None and status not in {"assigned", "accepted", "submitted", "cancelled", "expired"}:
            raise ValueError("unsupported assignment status filter")
        if language is not None and language not in {"bn", "en"}:
            raise ValueError("unsupported assignment language filter")
        clauses = []
        values: list[Any] = []
        if reviewer_id is not None:
            clauses.append("reviewer_id = ?")
            values.append(reviewer_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        if language is not None:
            clauses.append("language = ?")
            values.append(language)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT revision, assigned_at AS created_at, updated_at, last_actor, last_reason, payload_json "
            "FROM benchmark_review_assignments" + where + " ORDER BY due_at, assignment_id",
            values,
        ).fetchall()
        return [
            {**json.loads(row["payload_json"]), "revision": row["revision"],
             "created_at": row["created_at"], "updated_at": row["updated_at"],
             "last_actor": row["last_actor"], "last_reason": row["last_reason"]}
            for row in rows
        ]

    def benchmark_review_assignment_history(self, assignment_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """SELECT revision, created_at, actor, reason, payload_json
               FROM benchmark_review_assignment_revisions
               WHERE assignment_id = ? ORDER BY revision""", (assignment_id,)
        ).fetchall()
        return [
            {"revision": row["revision"], "created_at": row["created_at"],
             "actor": row["actor"], "reason": row["reason"],
             "assignment": json.loads(row["payload_json"])}
            for row in rows
        ]

    def transition_benchmark_review_assignment(
        self, assignment_id: str, *, event: str, actor: str, at: str,
        reason: str, submitted_review_id: str | None = None
    ) -> dict[str, Any]:
        """Apply one authorized transition and append its immutable revision."""
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("transition actor and reason are required")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM benchmark_review_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"unknown benchmark review assignment: {assignment_id}")
            current = json.loads(row["payload_json"])
            reviewer_id = current["reviewer_id"]
            assigner_id = current["assigned_by"]
            language = current["language"]

            if event in {"accept", "submit"}:
                if actor != reviewer_id:
                    raise RegistryIntegrityError("only the assigned reviewer may accept or submit")
                self._require_benchmark_role(connection, actor, "benchmark_reviewer")
                principal = connection.execute(
                    "SELECT languages_json FROM benchmark_principals WHERE principal_id = ?",
                    (actor,),
                ).fetchone()
                if principal is None or language not in json.loads(principal["languages_json"]):
                    raise RegistryIntegrityError("reviewer is not currently qualified for the assignment language")
            elif event == "expire":
                if actor != assigner_id:
                    raise RegistryIntegrityError("only the assigning curator may expire an assignment")
                self._require_benchmark_role(connection, actor, "benchmark_curator")
            elif event == "cancel":
                if actor == assigner_id:
                    self._require_benchmark_role(connection, actor, "benchmark_curator")
                elif actor == reviewer_id:
                    self._require_benchmark_role(connection, actor, "benchmark_reviewer")
                    principal = connection.execute(
                        "SELECT languages_json FROM benchmark_principals WHERE principal_id = ?",
                        (actor,),
                    ).fetchone()
                    if principal is None or language not in json.loads(principal["languages_json"]):
                        raise RegistryIntegrityError("reviewer is not currently qualified for the assignment language")
                else:
                    raise RegistryIntegrityError("only the assigned reviewer or assigning curator may cancel")
            else:
                raise RegistryIntegrityError("unsupported assignment transition")

            try:
                updated = transition_benchmark_assignment(
                    current, event=event, actor=actor, at=at, reason=reason,
                    submitted_review_id=submitted_review_id,
                )
            except (TypeError, ValueError) as exc:
                raise RegistryIntegrityError(f"invalid benchmark assignment transition: {exc}") from exc
            try:
                validate_payload("editorial-benchmark-assignment", updated)
            except (TypeError, ValueError) as exc:
                raise RegistryIntegrityError("assignment transition violates the versioned JSON contract") from exc

            if event == "submit":
                review_row = connection.execute(
                    """SELECT benchmark_id, benchmark_fingerprint, language, payload_json
                       FROM benchmark_reviews WHERE review_id = ?""",
                    (submitted_review_id,),
                ).fetchone()
                if review_row is None:
                    raise RegistryIntegrityError("submitted assignment must reference a stored human review")
                review_payload = json.loads(review_row["payload_json"])
                if (
                    review_row["benchmark_id"] != current["artifact_id"]
                    or review_row["benchmark_fingerprint"] != current["artifact_fingerprint"]
                    or review_row["language"] != current["language"]
                    or review_payload["reviewer"]["reviewer_id"] != reviewer_id
                ):
                    raise RegistryIntegrityError("submitted review must match the assigned reviewer, language, and exact artifact")
                final = connection.execute(
                    "SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = ?",
                    (current["artifact_id"],),
                ).fetchone()
                if final is not None:
                    raise RegistryIntegrityError("cannot submit a review after final artifact adjudication")

            now = _utc_now()
            next_revision = row["revision"] + 1
            payload_json = _json_text(updated)
            connection.execute(
                """INSERT INTO benchmark_review_assignment_revisions
                   (assignment_id, revision, created_at, actor, reason, payload_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (assignment_id, next_revision, now, actor, reason, payload_json),
            )
            connection.execute(
                """UPDATE benchmark_review_assignments
                   SET status = ?, revision = ?, payload_json = ?, updated_at = ?,
                       last_actor = ?, last_reason = ?
                   WHERE assignment_id = ? AND revision = ?""",
                (updated["status"], next_revision, payload_json, now, actor, reason,
                 assignment_id, row["revision"]),
            )
            self._append_audit(
                connection, event_type=f"benchmark_review_assignment.{event}",
                entity_type="benchmark_review_assignment", entity_id=assignment_id,
                payload={"revision": next_revision, "status": updated["status"],
                         "actor": actor, "reason": reason,
                         "submitted_review_id": submitted_review_id},
            )
        return self.get_benchmark_review_assignment(assignment_id) or {}

    def add_benchmark_review(
        self, review: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Store a validated review and its immutable initial revision."""
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        validate_payload("editorial-benchmark-review", review)
        self._validate_benchmark_artifact_binding(review)
        report = validate_human_benchmark_review(review)
        if not report["valid"]:
            raise RegistryIntegrityError("benchmark review metadata is invalid")
        self._validate_benchmark_review_sources(review)
        if review["status"] == "approved" and not report["approval_eligible"]:
            raise RegistryIntegrityError("benchmark review is not eligible for approval")
        now = _utc_now()
        payload_json = _json_text(review)
        with self._transaction() as connection:
            reviewer_id = review["reviewer"]["reviewer_id"]
            if actor != reviewer_id:
                raise RegistryIntegrityError("review actor must match the declared reviewer principal")
            self._require_benchmark_role(connection, actor, "benchmark_reviewer")
            connection.execute(
                """INSERT INTO benchmark_reviews
                (review_id, benchmark_id, benchmark_fingerprint, language, status,
                 revision, created_at, updated_at, last_actor, last_reason, payload_json)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (review["review_id"], review["benchmark_id"], review["benchmark_fingerprint"],
                 review["language"], review["status"], now, now, actor, reason, payload_json),
            )
            self._append_audit(
                connection, event_type="benchmark_review.created",
                entity_type="benchmark_review", entity_id=review["review_id"],
                payload={"revision": 1, "actor": actor, "reason": reason, "record": review},
            )
        return {**review, "revision": 1, "created_at": now, "updated_at": now}

    def _validate_benchmark_review_sources(
        self, review: dict[str, Any], *, connection: sqlite3.Connection | None = None
    ) -> None:
        """Resolve every declared source reference against registered records."""
        conn = connection or self._connection
        for source in review.get("source_materials", []):
            article_row = conn.execute(
                "SELECT payload_json FROM domain_records WHERE record_type = 'article' AND id = ?",
                (source["article_id"],),
            ).fetchone()
            publisher_row = conn.execute(
                "SELECT id FROM sources WHERE id = ?", (source["source_id"],)
            ).fetchone()
            permission_row = conn.execute(
                "SELECT source_id, review_status FROM permission_evidence WHERE id = ?",
                (source["approved_use_reference"],),
            ).fetchone()
            grant_row = conn.execute(
                "SELECT payload_json FROM permission_grants WHERE id = ?",
                (source["permission_grant_id"],),
            ).fetchone()
            if article_row is None or publisher_row is None or permission_row is None or grant_row is None:
                raise RegistryIntegrityError("benchmark source material must reference a registered article, publisher, verified evidence, and permission grant")
            article = json.loads(article_row["payload_json"])
            grant = json.loads(grant_row["payload_json"])
            if article.get("source_id") != source["source_id"] or permission_row["source_id"] != source["source_id"] or grant.get("source_id") != source["source_id"]:
                raise RegistryIntegrityError("benchmark article, publisher, evidence, and permission grant must belong to the same publisher")
            if permission_row["review_status"] != "verified":
                raise RegistryIntegrityError("benchmark source permission evidence must be verified")
            if source["endpoint_id"] not in grant.get("endpoint_ids", []):
                raise RegistryIntegrityError("benchmark permission grant does not cover the declared endpoint")
            operation = source["permission_operation"]
            operation_record = grant.get("operations", {}).get(operation, {})
            if (
                grant.get("status") != "approved"
                or grant.get("effective_from") is None
                or grant.get("effective_until") is None
                or operation_record.get("allowed") is not True
                or operation_record.get("status") != "approved"
                or source["approved_use_reference"] not in grant.get("evidence_ids", [])
                or source["approved_use_reference"] not in operation_record.get("evidence_ids", [])
            ):
                raise RegistryIntegrityError("benchmark permission grant does not explicitly authorize the required operation with this evidence")
            decision = evaluate_permission_record(
                grant, operation, source_id=source["source_id"],
                endpoint_id=source["endpoint_id"],
            )
            if not decision.allowed:
                raise RegistryIntegrityError(f"benchmark permission grant is not currently usable: {decision.reason}")
            if article.get("canonical_uri") != source["canonical_uri"]:
                raise RegistryIntegrityError("benchmark source URI must match the registered article")
            if article.get("content_fingerprint") and article["content_fingerprint"] != source["content_fingerprint"]:
                raise RegistryIntegrityError("benchmark source fingerprint must match the registered article")

    def revise_benchmark_review(
        self, review_id: str, review: dict[str, Any], *,
        expected_revision: int, actor: str, reason: str
    ) -> dict[str, Any]:
        """Append a new review decision revision with optimistic concurrency."""
        if review.get("review_id") != review_id:
            raise RegistryIntegrityError("benchmark review identity cannot change")
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        validate_payload("editorial-benchmark-review", review)
        self._validate_benchmark_artifact_binding(review)
        report = validate_human_benchmark_review(review)
        if not report["valid"]:
            raise RegistryIntegrityError("benchmark review metadata is invalid")
        if review["status"] == "approved" and not report["approval_eligible"]:
            raise RegistryIntegrityError("benchmark review is not eligible for approval")
        now = _utc_now()
        payload_json = _json_text(review)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT revision, created_at FROM benchmark_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError("cannot revise an unknown benchmark review")
            if row["revision"] != expected_revision:
                raise RegistryIntegrityError("benchmark review revision conflict")
            finalized = connection.execute(
                "SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = ? LIMIT 1",
                (review["benchmark_id"],),
            ).fetchone()
            if finalized is not None:
                raise RegistryIntegrityError("a review bound to a finally adjudicated artifact cannot be revised")
            reviewer_id = review["reviewer"]["reviewer_id"]
            if actor != reviewer_id:
                raise RegistryIntegrityError("review actor must match the declared reviewer principal")
            self._require_benchmark_role(connection, actor, "benchmark_reviewer")
            self._validate_benchmark_review_sources(review, connection=connection)
            next_revision = expected_revision + 1
            connection.execute(
                """UPDATE benchmark_reviews SET benchmark_id = ?, benchmark_fingerprint = ?,
                language = ?, status = ?, revision = ?, updated_at = ?, last_actor = ?,
                last_reason = ?, payload_json = ?
                WHERE review_id = ? AND revision = ?""",
                (review["benchmark_id"], review["benchmark_fingerprint"], review["language"],
                 review["status"], next_revision, now, actor, reason, payload_json, review_id, expected_revision),
            )
            self._append_audit(
                connection, event_type="benchmark_review.revised",
                entity_type="benchmark_review", entity_id=review_id,
                payload={"revision": next_revision, "actor": actor, "reason": reason, "record": review},
            )
        return {**review, "revision": next_revision, "created_at": row["created_at"], "updated_at": now}


    def add_benchmark_adjudication(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Persist an immutable, independently adjudicated benchmark disagreement."""
        try:
            validation = validate_benchmark_adjudication(payload)
        except (TypeError, ValueError) as exc:
            raise RegistryIntegrityError("adjudication contract or independence validation failed") from exc
        if not validation["eligible_for_adjudication"]:
            raise RegistryIntegrityError("adjudication contract or independence checks failed")
        if not actor.strip() or not reason.strip():
            raise ValueError("actor and reason are required")
        adjudicator_id = payload["adjudicator"]["principal_id"]
        if actor != adjudicator_id:
            raise RegistryIntegrityError("adjudication actor must match the declared adjudicator principal")
        resolved_at = _aware_datetime(payload["resolved_at"], "resolved_at")
        created_at = _utc_now()
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_adjudicator")
            artifact = connection.execute(
                "SELECT content_fingerprint FROM benchmark_artifacts WHERE artifact_id = ?",
                (payload["benchmark_id"],),
            ).fetchone()
            if artifact is None or artifact["content_fingerprint"] != payload["benchmark_fingerprint"]:
                raise RegistryIntegrityError("adjudication must bind to the exact registered benchmark artifact")
            actual_reviewers = []
            referenced_reviews = {}
            for review_id in payload["review_ids"]:
                row = connection.execute(
                    "SELECT benchmark_id, benchmark_fingerprint, status, payload_json FROM benchmark_reviews WHERE review_id = ?",
                    (review_id,),
                ).fetchone()
                if row is None:
                    raise RegistryIntegrityError("adjudication references an unknown benchmark review")
                if row["benchmark_id"] != payload["benchmark_id"] or row["benchmark_fingerprint"] != payload["benchmark_fingerprint"]:
                    raise RegistryIntegrityError("adjudication reviews must concern the same exact benchmark artifact")
                actual_reviewers.append(json.loads(row["payload_json"])["reviewer"]["reviewer_id"])
                referenced_reviews[review_id] = row["status"]
            if payload["outcome"] == "uphold_existing":
                upheld = payload["upheld_review_ids"]
                if any(referenced_reviews.get(review_id) != "approved" for review_id in upheld):
                    raise RegistryIntegrityError("upheld reviews must be referenced approved review records")
            if sorted(actual_reviewers) != sorted(payload["reviewer_ids"]):
                raise RegistryIntegrityError("adjudication reviewer IDs must match the referenced stored reviews")
            if adjudicator_id in actual_reviewers:
                raise RegistryIntegrityError("adjudicator must be independent of all referenced reviewers")
            connection.execute(
                """INSERT INTO benchmark_adjudications
                (adjudication_id, benchmark_id, benchmark_fingerprint, adjudicator_id,
                 resolved_at, payload_json, created_at, actor, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["adjudication_id"], payload["benchmark_id"], payload["benchmark_fingerprint"],
                 adjudicator_id, resolved_at.isoformat(), _json_text(payload), created_at, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_adjudication.recorded",
                entity_type="benchmark_adjudication", entity_id=payload["adjudication_id"],
                payload={"benchmark_id": payload["benchmark_id"], "review_ids": payload["review_ids"],
                         "outcome": payload["outcome"], "actor": actor, "reason": reason},
            )
        return self.get_benchmark_adjudication(payload["adjudication_id"]) or {}

    def get_benchmark_adjudication(self, adjudication_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_adjudications WHERE adjudication_id = ?", (adjudication_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            **json.loads(row["payload_json"]), "created_at": row["created_at"],
            "actor": row["actor"], "reason": row["reason"],
        }

    def get_benchmark_review(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT revision, created_at, updated_at, payload_json FROM benchmark_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row["payload_json"]), "revision": row["revision"],
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def benchmark_review_history(self, review_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """SELECT revision, created_at, actor, reason, payload_json
                FROM benchmark_review_revisions WHERE review_id = ? ORDER BY revision""",
                (review_id,),
            ).fetchall()
        return [{"revision": row["revision"], "created_at": row["created_at"],
                 "actor": row["actor"], "reason": row["reason"],
                 "record": json.loads(row["payload_json"])} for row in rows]

    def _benchmark_acceptance_review_quality_report(
        self, review_id: str, connection: sqlite3.Connection
    ) -> dict[str, Any]:
        """Recheck exact study evidence and current source-review permissions for scoped reference."""
        row = connection.execute(
            "SELECT * FROM benchmark_acceptance_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        if row is None:
            raise RegistryIntegrityError(f"unknown benchmark acceptance review: {review_id}")
        payload = json.loads(row["payload_json"])
        checks: list[dict[str, Any]] = []
        blockers: list[str] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": bool(passed), "detail": detail})
            if not passed:
                blockers.append(detail)

        fingerprint_ok = sha256_fingerprint(payload) == row["acceptance_fingerprint"]
        check("acceptance_fingerprint", fingerprint_ok,
              "Acceptance decision fingerprint is verified." if fingerprint_ok else
              "Acceptance decision fingerprint does not match its immutable payload.")
        protocol_row = connection.execute(
            "SELECT * FROM benchmark_study_protocols WHERE protocol_record_id = ?",
            (row["protocol_record_id"],),
        ).fetchone()
        execution_row = connection.execute(
            "SELECT * FROM benchmark_study_executions WHERE execution_id = ?",
            (row["execution_id"],),
        ).fetchone()
        protocol = json.loads(protocol_row["payload_json"]) if protocol_row else None
        execution = json.loads(execution_row["payload_json"]) if execution_row else None
        protocol_ok = bool(
            protocol_row and protocol
            and sha256_fingerprint(protocol) == protocol_row["protocol_fingerprint"] == row["protocol_fingerprint"]
            and protocol_row["study_id"] == row["study_id"]
            and protocol_row["study_version"] == row["study_version"]
        )
        execution_ok = bool(
            execution_row and execution
            and sha256_fingerprint(execution) == execution_row["execution_fingerprint"] == row["execution_fingerprint"]
            and execution_row["protocol_record_id"] == row["protocol_record_id"]
        )
        check("registered_protocol", protocol_ok,
              "Exact registered protocol version and fingerprint are verified." if protocol_ok else
              "Registered protocol is missing, changed, or does not match the acceptance decision.")
        check("registered_execution", execution_ok,
              "Exact registered execution and protocol linkage are verified." if execution_ok else
              "Registered execution is missing, changed, or not bound to the accepted protocol.")
        artifact_row = connection.execute(
            "SELECT language, content_fingerprint, content_json FROM benchmark_artifacts WHERE artifact_id = ?",
            (row["benchmark_id"],),
        ).fetchone()
        artifact_ok = bool(
            artifact_row
            and sha256_fingerprint(json.loads(artifact_row["content_json"])) == artifact_row["content_fingerprint"]
            and artifact_row["content_fingerprint"] == row["benchmark_fingerprint"]
            and execution_ok and execution_row["benchmark_id"] == row["benchmark_id"]
            and execution_row["benchmark_fingerprint"] == row["benchmark_fingerprint"]
        )
        check("benchmark_artifact", artifact_ok,
              "Registered benchmark artifact fingerprint is verified." if artifact_ok else
              "Benchmark artifact is missing, changed, or no longer matches the study execution.")
        try:
            if not (protocol_ok and execution_ok and artifact_ok):
                raise RegistryIntegrityError("exact registered study evidence is unavailable")
            from .benchmark_acceptance_review import validate_benchmark_acceptance_review
            acceptance_validation = validate_benchmark_acceptance_review(
                payload, protocol=protocol, execution=execution
            )
            contract_ok = bool(acceptance_validation["valid"])
        except (RegistryIntegrityError, KeyError, TypeError, ValueError):
            contract_ok = False
        check("acceptance_contract", contract_ok,
              "Independent acceptance contract remains valid." if contract_ok else
              "Acceptance decision no longer satisfies its registered study contract.")
        domains = payload.get("domains", {})
        all_domains_pass = (
            set(domains) == {"statistical_validity", "editorial_validity", "operational_security"}
            and all(isinstance(value, dict) and value.get("status") == "pass" for value in domains.values())
        )
        decision_accepted = row["decision"] in {"accepted_for_limited_use", "accepted_for_proposed_use"}
        check("accepted_decision", decision_accepted and all_domains_pass,
              "All three independent review domains passed for a scoped accepted decision."
              if decision_accepted and all_domains_pass else
              "Decision is not accepted for scoped use or all three review domains do not pass.")

        source_rows = connection.execute(
            "SELECT revision, payload_json FROM benchmark_reviews WHERE benchmark_id = ? ORDER BY review_id",
            (row["benchmark_id"],),
        ).fetchall()
        eligible_reviewers: set[str] = set()
        source_review_checks = []
        for source_row in source_rows:
            source_review = json.loads(source_row["payload_json"])
            reviewer_id = source_review.get("reviewer", {}).get("reviewer_id")
            principal_row = connection.execute(
                "SELECT active, roles_json, languages_json, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                (reviewer_id,),
            ).fetchone() if reviewer_id else None
            principal_ok = bool(
                principal_row and principal_row["active"]
                and "benchmark_reviewer" in json.loads(principal_row["roles_json"])
                and principal_row["principal_kind"] == "human"
                and source_review.get("language") in json.loads(principal_row["languages_json"])
            )
            exact_binding = bool(
                source_review.get("benchmark_id") == row["benchmark_id"]
                and source_review.get("benchmark_fingerprint") == row["benchmark_fingerprint"]
                and artifact_row is not None
                and source_review.get("language") == artifact_row["language"]
                and source_review.get("reviewer", {}).get("language") == artifact_row["language"]
            )
            permission_report = self._benchmark_review_quality_report(source_review, connection)
            attestation = connection.execute(
                """SELECT principal_id, issuer, audience, subject, provider_key_id, provider_adapter,
                expires_at, attested_at FROM benchmark_review_identity_attestations
                WHERE review_id = ? AND review_revision = ?""",
                (source_review.get("review_id"), source_row["revision"]),
            ).fetchone()
            attested = bool(
                attestation and attestation["principal_id"] == reviewer_id
                and all(attestation[key] for key in (
                    "issuer", "audience", "subject", "provider_key_id", "provider_adapter"
                ))
                and _aware_datetime(attestation["attested_at"], "attested_at")
                < _aware_datetime(attestation["expires_at"], "expires_at")
            )
            eligible = bool(
                source_review.get("status") == "approved" and principal_ok and exact_binding
                and permission_report["eligible_for_current_use"] and attested
            )
            if eligible:
                eligible_reviewers.add(reviewer_id)
            source_review_checks.append({
                "review_id": source_review.get("review_id"),
                "reviewer_id": reviewer_id,
                "current_permission_eligible": permission_report["eligible_for_current_use"],
                "active_human_reviewer": principal_ok,
                "exact_artifact_binding": exact_binding,
                "identity_attested_for_current_revision": attested,
                "eligible_current_source_review": eligible,
            })
        source_review_coverage = len(eligible_reviewers) >= 2
        check("current_human_source_reviews", source_review_coverage,
              "At least two distinct active, attested human reviews remain eligible under current source permissions."
              if source_review_coverage else
              "At least two distinct active, attested human reviews with current source permissions are required.")
        scoped = bool(
            fingerprint_ok and protocol_ok and execution_ok and artifact_ok and contract_ok
            and decision_accepted and all_domains_pass and source_review_coverage
        )
        return {
            "review_id": row["review_id"], "study_id": row["study_id"],
            "study_version": row["study_version"], "execution_id": row["execution_id"],
            "benchmark_id": row["benchmark_id"], "decision": row["decision"],
            "intended_use": payload.get("intended_use"),
            "scope_limitations": payload.get("scope_limitations", []),
            "eligible_for_scoped_reference": scoped,
            "checks": checks, "blockers": blockers,
            "source_reviews": source_review_checks,
            "production_ready": False, "authorization_granted": False,
            "interpretation": (
                "This report rechecks evidence linkage and current source permissions for editorial context. "
                "It is not production authorization and does not establish factual correctness."
            ),
        }

    def benchmark_acceptance_review_quality_report(self, review_id: str) -> dict[str, Any]:
        """Return a read-only, current-use report for one acceptance decision."""
        with self._transaction("DEFERRED") as connection:
            return self._benchmark_acceptance_review_quality_report(review_id, connection)

    def _benchmark_review_quality_report(
        self, review: dict[str, Any], connection: sqlite3.Connection
    ) -> dict[str, Any]:
        """Evaluate review metadata and whether declared source permissions remain usable."""
        metadata = validate_human_benchmark_review(review)
        artifact_ok = True
        artifact_detail = "Registered benchmark artifact fingerprint and language are verified."
        try:
            self._validate_benchmark_artifact_binding(review, connection=connection)
        except (RegistryIntegrityError, KeyError, TypeError, ValueError) as exc:
            artifact_ok = False
            artifact_detail = f"Benchmark artifact check failed: {exc}"
        checks: list[dict[str, Any]] = []
        blockers: list[dict[str, str]] = []

        def check(name: str, passed: bool, detail: str) -> None:
            checks.append({"name": name, "passed": passed, "detail": detail})
            if not passed:
                blockers.append({"check": name, "detail": detail})

        approved = review.get("status") == "approved"
        check(
            "approved_review",
            approved,
            "Review must be approved before it can qualify as a current benchmark reference.",
        )
        check(
            "review_metadata",
            metadata["valid"] and metadata["approval_eligible"] if approved else metadata["valid"],
            "Review metadata satisfies the human-review contract."
            if metadata["valid"] else "Review metadata has validation failures.",
        )
        check("benchmark_artifact", artifact_ok, artifact_detail)
        permission_ok = True
        permission_detail = "Not required for a non-approved review."
        if approved:
            try:
                self._validate_benchmark_review_sources(review, connection=connection)
                permission_detail = (
                    "Every source still has a verified evidence record and a currently usable "
                    "grant for its declared endpoint and operation."
                )
            except (RegistryIntegrityError, KeyError, TypeError, ValueError) as exc:
                permission_ok = False
                permission_detail = f"Current source permission check failed: {exc}"
        check("current_source_permission", permission_ok, permission_detail)

        adjudication_rows = connection.execute(
            """SELECT adjudication_id, payload_json FROM benchmark_adjudications
            WHERE benchmark_id = ? ORDER BY resolved_at, created_at, adjudication_id""",
            (review.get("benchmark_id"),),
        ).fetchall()
        adjudication_ok = len(adjudication_rows) <= 1
        adjudication_detail = "No adjudication currently applies to this benchmark."
        adjudication_summary = None
        if len(adjudication_rows) > 1:
            adjudication_detail = "Multiple final adjudications exist for one immutable artifact; eligibility is blocked pending data review."
            adjudication_summary = {
                "conflict": True,
                "adjudication_ids": [row["adjudication_id"] for row in adjudication_rows],
            }
        elif adjudication_rows:
            decision = json.loads(adjudication_rows[0]["payload_json"])
            adjudication_summary = {
                "adjudication_id": decision["adjudication_id"],
                "outcome": decision["outcome"],
                "resolved_at": decision["resolved_at"],
                "upheld_review_ids": decision.get("upheld_review_ids", []),
            }
            if decision["outcome"] == "uphold_existing":
                adjudication_ok = (
                    review.get("review_id") in decision.get("upheld_review_ids", [])
                    and approved
                )
                adjudication_detail = (
                    "This exact approved review was explicitly upheld."
                    if adjudication_ok else
                    "The adjudicator upheld only the explicitly listed approved reviews; this review was not selected."
                )
            elif decision["outcome"] == "return_for_revision":
                adjudication_ok = False
                adjudication_detail = "The adjudication returned this immutable artifact for revision; it remains blocked. Register a new artifact and obtain fresh reviews."
            elif decision["outcome"] == "reject_benchmark_item":
                adjudication_ok = False
                adjudication_detail = "The adjudication rejected this benchmark item; it is not eligible for current use."
            elif decision["outcome"] == "split_accepted_answers":
                adjudication_ok = False
                adjudication_detail = "The adjudication requires distinct accepted-answer items; register a new artifact and obtain fresh reviews."
        check("adjudication_policy", adjudication_ok, adjudication_detail)
        return {
            "review_id": review.get("review_id"),
            "benchmark_id": review.get("benchmark_id"),
            "status": review.get("status"),
            "adjudication": adjudication_summary,
            "eligible_for_current_use": approved and metadata["approval_eligible"] and artifact_ok and permission_ok and adjudication_ok,
            "blockers": blockers,
            "checks": checks,
            "warnings": [
                "Registry roles control declared principal IDs but do not authenticate the person operating the account.",
                "This report does not assess factual correctness or general editorial quality.",
            ],
        }

    def record_benchmark_review_identity_attestation(
        self, attestation: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Persist provider-verified identity metadata for one exact review revision."""
        required = {
            "attestation_id", "review_id", "review_revision", "principal_id", "issuer",
            "audience", "subject", "authenticated_at", "expires_at",
            "authentication_methods", "authentication_context", "provider_key_id",
            "provider_adapter", "session_fingerprint", "attested_at",
        }
        if not isinstance(attestation, dict) or set(attestation) != required:
            raise RegistryIntegrityError("identity attestation fields do not match the contract")
        validate_payload("editorial-benchmark-identity-attestation", attestation)
        for field in ("attestation_id", "review_id", "principal_id", "issuer", "audience",
                      "subject", "authentication_context", "provider_key_id", "provider_adapter"):
            if not isinstance(attestation[field], str) or not attestation[field].strip():
                raise RegistryIntegrityError(f"identity attestation {field} is required")
        if not isinstance(attestation["review_revision"], int) or attestation["review_revision"] < 1:
            raise RegistryIntegrityError("identity attestation review_revision must be positive")
        methods = attestation["authentication_methods"]
        if not isinstance(methods, list) or not methods or any(not isinstance(x, str) or not x.strip() for x in methods):
            raise RegistryIntegrityError("identity attestation requires non-empty authentication methods")
        if len(set(methods)) != len(methods):
            raise RegistryIntegrityError("identity attestation authentication methods must be unique")
        if not isinstance(attestation["session_fingerprint"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", attestation["session_fingerprint"]):
            raise RegistryIntegrityError("identity attestation session fingerprint is invalid")
        authenticated_at = _aware_datetime(attestation["authenticated_at"], "authenticated_at")
        expires_at = _aware_datetime(attestation["expires_at"], "expires_at")
        attested_at = _aware_datetime(attestation["attested_at"], "attested_at")
        if expires_at <= authenticated_at or attested_at < authenticated_at or attested_at >= expires_at:
            raise RegistryIntegrityError("identity attestation timestamps are inconsistent")
        if (attested_at - authenticated_at).total_seconds() > 900:
            raise RegistryIntegrityError("identity attestation must be captured within 15 minutes of authentication")
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise RegistryIntegrityError("identity attestation actor and reason are required")
        payload_json = _json_text(attestation)
        with self._transaction() as connection:
            review_row = connection.execute(
                "SELECT revision, payload_json FROM benchmark_reviews WHERE review_id = ?",
                (attestation["review_id"],),
            ).fetchone()
            if review_row is None or review_row["revision"] != attestation["review_revision"]:
                raise RegistryIntegrityError("identity attestation must bind to the current stored review revision")
            review = json.loads(review_row["payload_json"])
            if actor != attestation["principal_id"] or review["reviewer"]["reviewer_id"] != actor:
                raise RegistryIntegrityError("identity attestation actor must match the stored reviewer")
            principal = connection.execute(
                "SELECT active, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                (actor,),
            ).fetchone()
            if principal is None or not principal["active"] or principal["principal_kind"] != "human":
                raise RegistryIntegrityError("identity attestation requires an active human-classified principal")
            connection.execute(
                """INSERT INTO benchmark_review_identity_attestations
                (attestation_id, review_id, review_revision, principal_id, issuer, audience,
                 subject, authenticated_at, expires_at, authentication_methods_json,
                 authentication_context, provider_key_id, provider_adapter, session_fingerprint,
                 attested_at, payload_json, actor, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (attestation["attestation_id"], attestation["review_id"], attestation["review_revision"],
                 actor, attestation["issuer"], attestation["audience"], attestation["subject"],
                 attestation["authenticated_at"], attestation["expires_at"], _json_text(methods),
                 attestation["authentication_context"], attestation["provider_key_id"],
                 attestation["provider_adapter"], attestation["session_fingerprint"],
                 attestation["attested_at"], payload_json, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_review.identity_attested",
                entity_type="benchmark_review", entity_id=attestation["review_id"],
                payload={"attestation_id": attestation["attestation_id"],
                         "review_revision": attestation["review_revision"],
                         "principal_id": actor, "issuer": attestation["issuer"],
                         "provider_adapter": attestation["provider_adapter"],
                         "provider_key_id": attestation["provider_key_id"],
                         "attestation_fingerprint": sha256_fingerprint(attestation),
                         "reason": reason},
            )
        return dict(attestation)


    def add_benchmark_score(
        self, score: dict[str, Any], *, assignment_id: str, actor: str,
        identity: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        """Persist one immutable score for an accepted, exact-item assignment."""
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise RegistryIntegrityError("score actor and reason are required")
        try:
            report = validate_benchmark_scoring(score)
        except (TypeError, ValueError, KeyError) as exc:
            raise RegistryIntegrityError("score sheet violates the versioned scoring contract") from exc
        if not report["valid"]:
            raise RegistryIntegrityError("score sheet has invalid criterion IDs or weights")
        evaluated_at = _aware_datetime(score["evaluated_at"], "evaluated_at")
        required_identity = ("issuer", "subject", "provider_key_id", "provider_adapter", "authenticated_at")
        if not isinstance(identity, dict) or any(
            not isinstance(identity.get(key), str) or not identity[key].strip()
            for key in required_identity
        ):
            raise RegistryIntegrityError("score requires trusted provider identity evidence")
        authenticated_at = _aware_datetime(identity["authenticated_at"], "authenticated_at")
        if evaluated_at < authenticated_at:
            raise RegistryIntegrityError("score timestamp cannot precede evaluator authentication")
        with self._transaction() as connection:
            assignment_row = connection.execute(
                "SELECT payload_json FROM benchmark_review_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if assignment_row is None:
                raise RegistryIntegrityError("score must reference an existing reviewer assignment")
            assignment = json.loads(assignment_row["payload_json"])
            if assignment["status"] != "accepted":
                raise RegistryIntegrityError("only an accepted reviewer assignment may be scored")
            if assignment["reviewer_id"] != actor or score["evaluator_id"] != actor:
                raise RegistryIntegrityError("score evaluator must be the reviewer assigned to this artifact")
            if (
                score["benchmark_id"] != assignment["artifact_id"]
                or score["benchmark_fingerprint"] != assignment["artifact_fingerprint"]
                or score["language"] != assignment["language"]
            ):
                raise RegistryIntegrityError("score must bind to the assignment's exact artifact, fingerprint, and language")
            artifact_row = connection.execute(
                "SELECT language, content_fingerprint, content_json FROM benchmark_artifacts WHERE artifact_id = ?",
                (assignment["artifact_id"],),
            ).fetchone()
            if artifact_row is None:
                raise RegistryIntegrityError("score artifact is not registered")
            artifact_content = json.loads(artifact_row["content_json"])
            if sha256_fingerprint(artifact_content) != artifact_row["content_fingerprint"]:
                raise RegistryIntegrityError("registered artifact fingerprint verification failed")
            if (
                artifact_row["content_fingerprint"] != assignment["artifact_fingerprint"]
                or artifact_row["language"] != assignment["language"]
            ):
                raise RegistryIntegrityError("assignment no longer matches its immutable artifact")
            item = next(
                (candidate for candidate in artifact_content.get("items", [])
                 if candidate.get("item_id") == score["item_id"]),
                None,
            )
            if item is None or item.get("task_type") != score["task_type"]:
                raise RegistryIntegrityError("score item ID and task type must match the registered artifact")
            principal = connection.execute(
                "SELECT active, roles_json, languages_json, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                (actor,),
            ).fetchone()
            if (
                principal is None or not principal["active"]
                or "benchmark_reviewer" not in json.loads(principal["roles_json"])
                or assignment["language"] not in json.loads(principal["languages_json"])
                or principal["principal_kind"] != "human"
            ):
                raise RegistryIntegrityError("scoring requires an active, qualified, human-classified reviewer")
            final = connection.execute(
                "SELECT 1 FROM benchmark_adjudications WHERE benchmark_id = ?",
                (assignment["artifact_id"],),
            ).fetchone()
            if final is not None:
                raise RegistryIntegrityError("cannot add a score after final artifact adjudication")
            score_id = f"score:{assignment_id}:{score['item_id']}"
            payload_json = _json_text(score)
            score_fingerprint = sha256_fingerprint(score)
            created_at = _utc_now()
            connection.execute(
                """INSERT INTO benchmark_scores
                (score_id, assignment_id, artifact_id, artifact_fingerprint, item_id,
                 task_type, language, evaluator_id, evaluated_at, weighted_score,
                 eligible_for_comparison, score_fingerprint, payload_json, issuer,
                 subject, provider_key_id, provider_adapter, authenticated_at,
                 created_at, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (score_id, assignment_id, assignment["artifact_id"],
                 assignment["artifact_fingerprint"], score["item_id"], score["task_type"],
                 score["language"], actor, score["evaluated_at"], report["weighted_score"],
                 int(report["eligible_for_comparison"]), score_fingerprint, payload_json,
                 identity["issuer"], identity["subject"], identity["provider_key_id"],
                 identity["provider_adapter"], identity["authenticated_at"], created_at, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_score.recorded",
                entity_type="benchmark_score", entity_id=score_id,
                payload={"assignment_id": assignment_id, "artifact_id": assignment["artifact_id"],
                         "artifact_fingerprint": assignment["artifact_fingerprint"],
                         "item_id": score["item_id"], "evaluator_id": actor,
                         "score_fingerprint": score_fingerprint,
                         "eligible_for_comparison": report["eligible_for_comparison"],
                         "reason": reason},
            )
        return {
            "score_id": score_id, "assignment_id": assignment_id,
            "artifact_id": assignment["artifact_id"], "item_id": score["item_id"],
            "evaluator_id": actor, "weighted_score": report["weighted_score"],
            "eligible_for_comparison": report["eligible_for_comparison"],
            "score_fingerprint": score_fingerprint, "created_at": created_at,
        }


    def register_benchmark_study_protocol(
        self, payload: dict[str, Any], *, actor: str, reason: str
    ) -> dict[str, Any]:
        """Persist an immutable, curator-approved version of a study protocol."""
        try:
            report = validate_benchmark_study_protocol(payload)
        except (TypeError, ValueError, BenchmarkStudyError) as exc:
            raise RegistryIntegrityError("study protocol does not satisfy its versioned contract") from exc
        if not isinstance(actor, str) or not actor.strip():
            raise ValueError("actor is required")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        fingerprint = report["protocol_fingerprint"]
        protocol_record_id = f"benchmark-study-protocol:{payload['study_id']}:{payload['study_version']}"
        created_at = _utc_now()
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_curator")
            artifact = connection.execute(
                "SELECT language, content_fingerprint, content_json FROM benchmark_artifacts WHERE artifact_id = ?",
                (payload["benchmark_id"],),
            ).fetchone()
            if artifact is None:
                raise RegistryIntegrityError("study protocol must reference a registered benchmark artifact")
            if sha256_fingerprint(json.loads(artifact["content_json"])) != artifact["content_fingerprint"]:
                raise RegistryIntegrityError("registered benchmark artifact fingerprint verification failed")
            if payload["benchmark_fingerprint"] != artifact["content_fingerprint"]:
                raise RegistryIntegrityError("study protocol benchmark fingerprint is stale or mismatched")
            item_ids = {item["item_id"] for item in json.loads(artifact["content_json"])["items"]}
            declared = payload["partition_membership"]
            partition_ids = set(
                declared["calibration_item_ids"]
                + declared["blind_evaluation_item_ids"]
                + declared["adjudication_item_ids"]
            )
            if not partition_ids.issubset(item_ids):
                raise RegistryIntegrityError("study protocol contains item IDs outside the registered benchmark artifact")
            if any(row["language"] != artifact["language"] for row in payload["language_task_strata"]):
                raise RegistryIntegrityError("study protocol language strata do not match the benchmark artifact language")
            for assignment in payload["blind_assignment_plan"]:
                if assignment["item_id"] not in item_ids:
                    raise RegistryIntegrityError("blind assignment references an item outside the registered benchmark artifact")
                artifact_item = next(item for item in json.loads(artifact["content_json"])["items"] if item["item_id"] == assignment["item_id"])
                if artifact_item["task_type"] != assignment["task_type"]:
                    raise RegistryIntegrityError("blind assignment task type does not match the registered benchmark item")
            connection.execute(
                """INSERT INTO benchmark_study_protocols
                (protocol_record_id, study_id, study_version, benchmark_id,
                 benchmark_fingerprint, rubric_version, protocol_fingerprint,
                 status, payload_json, created_at, actor, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'protocol_only', ?, ?, ?, ?)""",
                (protocol_record_id, payload["study_id"], payload["study_version"],
                 payload["benchmark_id"], payload["benchmark_fingerprint"],
                 payload["rubric_version"], fingerprint, _json_text(payload),
                 created_at, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_study_protocol.registered",
                entity_type="benchmark_study_protocol", entity_id=protocol_record_id,
                payload={"study_id": payload["study_id"], "study_version": payload["study_version"],
                         "benchmark_id": payload["benchmark_id"],
                         "benchmark_fingerprint": payload["benchmark_fingerprint"],
                         "protocol_fingerprint": fingerprint, "reason": reason},
            )
        return self.get_benchmark_study_protocol(payload["study_id"], payload["study_version"]) or {}

    def get_benchmark_study_protocol(
        self, study_id: str, study_version: int | None = None
    ) -> dict[str, Any] | None:
        if study_version is None:
            row = self._connection.execute(
                "SELECT * FROM benchmark_study_protocols WHERE study_id = ? ORDER BY study_version DESC LIMIT 1",
                (study_id,),
            ).fetchone()
        else:
            row = self._connection.execute(
                "SELECT * FROM benchmark_study_protocols WHERE study_id = ? AND study_version = ?",
                (study_id, study_version),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        verified = sha256_fingerprint(payload) == row["protocol_fingerprint"]
        return {
            "protocol_record_id": row["protocol_record_id"], "study_id": row["study_id"],
            "study_version": row["study_version"], "benchmark_id": row["benchmark_id"],
            "benchmark_fingerprint": row["benchmark_fingerprint"],
            "rubric_version": row["rubric_version"],
            "protocol_fingerprint": row["protocol_fingerprint"], "status": row["status"],
            "protocol": payload, "created_at": row["created_at"], "actor": row["actor"],
            "reason": row["reason"], "fingerprint_verified": verified,
        }

    def benchmark_study_protocol_history(self, study_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT study_version FROM benchmark_study_protocols WHERE study_id = ? ORDER BY study_version",
            (study_id,),
        ).fetchall()
        return [
            self.get_benchmark_study_protocol(study_id, row["study_version"])
            for row in rows
        ]

    def list_benchmark_study_protocols(
        self, *, benchmark_id: str | None = None
    ) -> list[dict[str, Any]]:
        if benchmark_id is None:
            rows = self._connection.execute(
                "SELECT study_id, study_version FROM benchmark_study_protocols ORDER BY study_id, study_version"
            ).fetchall()
        else:
            rows = self._connection.execute(
                "SELECT study_id, study_version FROM benchmark_study_protocols WHERE benchmark_id = ? ORDER BY study_id, study_version",
                (benchmark_id,),
            ).fetchall()
        return [
            self.get_benchmark_study_protocol(row["study_id"], row["study_version"])
            for row in rows
        ]


    def register_benchmark_study_execution(
        self, payload: dict[str, Any], *, actor: str,
        identity: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        """Persist immutable execution evidence with an independently reviewed analysis."""
        try:
            report = validate_benchmark_study_execution(payload)
        except (TypeError, ValueError, BenchmarkStudyExecutionError) as exc:
            raise RegistryIntegrityError("study execution record does not satisfy its versioned contract") from exc
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise RegistryIntegrityError("study execution actor and reason are required")
        identity_fields = ("issuer", "subject", "provider_key_id", "provider_adapter", "authenticated_at")
        if not isinstance(identity, dict) or any(not isinstance(identity.get(k), str) or not identity[k].strip() for k in identity_fields):
            raise RegistryIntegrityError("study execution requires trusted methodologist identity evidence")
        authenticated_at = _aware_datetime(identity["authenticated_at"], "authenticated_at")
        started_at = _aware_datetime(payload["started_at"], "started_at")
        completed_at = _aware_datetime(payload["completed_at"], "completed_at")
        now = datetime.now(timezone.utc)
        if completed_at > now:
            raise RegistryIntegrityError("study execution cannot be registered before its declared completion")
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_methodologist")
            methodologist = connection.execute(
                "SELECT active, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                (actor,),
            ).fetchone()
            if methodologist is None or not methodologist["active"] or methodologist["principal_kind"] != "human":
                raise RegistryIntegrityError("study analysis requires an active human-classified methodologist")
            protocol_row = connection.execute(
                "SELECT * FROM benchmark_study_protocols WHERE study_id = ? AND study_version = ?",
                (payload["study_id"], payload["study_version"]),
            ).fetchone()
            if protocol_row is None:
                raise RegistryIntegrityError("execution must reference a registered study protocol version")
            protocol = json.loads(protocol_row["payload_json"])
            if sha256_fingerprint(protocol) != protocol_row["protocol_fingerprint"]:
                raise RegistryIntegrityError("registered study protocol fingerprint verification failed")
            if payload["protocol_fingerprint"] != protocol_row["protocol_fingerprint"]:
                raise RegistryIntegrityError("execution protocol fingerprint is stale or mismatched")
            for field in ("benchmark_id", "benchmark_fingerprint", "rubric_version"):
                if payload[field] != protocol_row[field]:
                    raise RegistryIntegrityError(f"execution {field} does not match the registered protocol")
            assigned = {
                (item["item_id"], reviewer_id)
                for item in protocol["blind_assignment_plan"]
                for reviewer_id in item["reviewer_ids"]
            }
            scored_pairs, missing_pairs, score_ids = set(), set(), set()
            for item in payload["score_records"]:
                pair = (item["item_id"], item["evaluator_id"])
                if pair not in assigned:
                    raise RegistryIntegrityError("execution score is outside the protocol's blind reviewer assignments")
                if pair in scored_pairs or item["score_id"] in score_ids:
                    raise RegistryIntegrityError("execution score evidence contains duplicate pairs or score IDs")
                row = connection.execute("SELECT * FROM benchmark_scores WHERE score_id = ?", (item["score_id"],)).fetchone()
                if row is None:
                    raise RegistryIntegrityError("execution references a score not present in the durable score ledger")
                if (row["item_id"], row["evaluator_id"]) != pair:
                    raise RegistryIntegrityError("execution score ID does not match its declared item and evaluator")
                if row["artifact_id"] != payload["benchmark_id"] or row["artifact_fingerprint"] != payload["benchmark_fingerprint"]:
                    raise RegistryIntegrityError("execution score is bound to a different benchmark artifact")
                if not row["eligible_for_comparison"]:
                    raise RegistryIntegrityError("ineligible critical-failure scores must be recorded as missing/excluded pairs")
                evaluated_at = _aware_datetime(row["evaluated_at"], "score.evaluated_at")
                if evaluated_at < started_at or evaluated_at > completed_at:
                    raise RegistryIntegrityError("score timestamp falls outside the declared study execution window")
                scored_pairs.add(pair)
                score_ids.add(item["score_id"])
            for item in payload["missing_records"]:
                pair = (item["item_id"], item["evaluator_id"])
                if pair not in assigned:
                    raise RegistryIntegrityError("missing pair is outside the protocol's blind reviewer assignments")
                if pair in scored_pairs or pair in missing_pairs:
                    raise RegistryIntegrityError("execution missing evidence contains duplicate or already-scored pairs")
                if item.get("score_id"):
                    row = connection.execute("SELECT * FROM benchmark_scores WHERE score_id = ?", (item["score_id"],)).fetchone()
                    if row is None or (row["item_id"], row["evaluator_id"]) != pair:
                        raise RegistryIntegrityError("excluded score reference does not match its declared item/evaluator pair")
                    if row["artifact_id"] != payload["benchmark_id"] or row["artifact_fingerprint"] != payload["benchmark_fingerprint"]:
                        raise RegistryIntegrityError("excluded score is bound to a different benchmark artifact")
                    if row["eligible_for_comparison"]:
                        raise RegistryIntegrityError("only ineligible scores may be represented as excluded score evidence")
                    excluded_at = _aware_datetime(row["evaluated_at"], "excluded_score.evaluated_at")
                    if excluded_at < started_at or excluded_at > completed_at:
                        raise RegistryIntegrityError("excluded score timestamp falls outside the declared study execution window")
                    if item["score_id"] in score_ids:
                        raise RegistryIntegrityError("execution cannot reference the same score twice")
                    score_ids.add(item["score_id"])
                else:
                    existing = connection.execute(
                        "SELECT score_id FROM benchmark_scores WHERE artifact_id = ? AND item_id = ? AND evaluator_id = ?",
                        (payload["benchmark_id"], item["item_id"], item["evaluator_id"]),
                    ).fetchone()
                    if existing is not None:
                        raise RegistryIntegrityError("an existing score must be explicitly referenced when classified as missing/excluded")
                missing_pairs.add(pair)
            if scored_pairs | missing_pairs != assigned:
                raise RegistryIntegrityError("execution must account for every planned blind item/evaluator pair")
            if len({item["item_id"] for item in protocol["blind_assignment_plan"]}) != payload["analysis"]["sampled_item_count"]:
                raise RegistryIntegrityError("analysis sampled-item count must match the protocol blind sample")
            if actor in {reviewer_id for _, reviewer_id in assigned}:
                raise RegistryIntegrityError("methodologist must be independent of all blind evaluators")
            if authenticated_at > now or now - authenticated_at > timedelta(minutes=15):
                raise RegistryIntegrityError("methodologist submission requires recent authentication within 15 minutes")
            if _aware_datetime(payload["analysis"]["reviewed_at"], "analysis.reviewed_at") > now:
                raise RegistryIntegrityError("methodological review timestamp cannot be in the future")
            created_at = _utc_now()
            fingerprint = report["execution_fingerprint"]
            connection.execute(
                """INSERT INTO benchmark_study_executions
                (execution_id, protocol_record_id, study_id, study_version,
                 protocol_fingerprint, benchmark_id, benchmark_fingerprint,
                 rubric_version, execution_fingerprint, status, methodologist_id,
                 issuer, subject, provider_key_id, provider_adapter, authenticated_at,
                 payload_json, created_at, actor, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'analysis_reviewed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["execution_id"], protocol_row["protocol_record_id"], payload["study_id"],
                 payload["study_version"], payload["protocol_fingerprint"], payload["benchmark_id"],
                 payload["benchmark_fingerprint"], payload["rubric_version"], fingerprint, actor,
                 identity["issuer"], identity["subject"], identity["provider_key_id"],
                 identity["provider_adapter"], identity["authenticated_at"], _json_text(payload),
                 created_at, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_study_execution.registered",
                entity_type="benchmark_study_execution", entity_id=payload["execution_id"],
                payload={"study_id": payload["study_id"], "study_version": payload["study_version"],
                         "execution_fingerprint": fingerprint, "methodologist_id": actor,
                         "score_count": len(payload["score_records"]),
                         "missing_count": len(payload["missing_records"]), "reason": reason},
            )
        return self.get_benchmark_study_execution(payload["execution_id"]) or {}

    def get_benchmark_study_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_study_executions WHERE execution_id = ?", (execution_id,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "execution_id": row["execution_id"], "study_id": row["study_id"],
            "study_version": row["study_version"], "protocol_fingerprint": row["protocol_fingerprint"],
            "benchmark_id": row["benchmark_id"], "benchmark_fingerprint": row["benchmark_fingerprint"],
            "rubric_version": row["rubric_version"], "execution_fingerprint": row["execution_fingerprint"],
            "status": row["status"], "methodologist_id": row["methodologist_id"],
            "issuer": row["issuer"], "subject": row["subject"], "provider_key_id": row["provider_key_id"],
            "provider_adapter": row["provider_adapter"], "authenticated_at": row["authenticated_at"],
            "execution": payload, "created_at": row["created_at"], "actor": row["actor"],
            "reason": row["reason"], "fingerprint_verified": sha256_fingerprint(payload) == row["execution_fingerprint"],
        }

    def list_benchmark_study_executions(self, study_id: str) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            "SELECT execution_id FROM benchmark_study_executions WHERE study_id = ? ORDER BY created_at, execution_id",
            (study_id,),
        ).fetchall()
        return [self.get_benchmark_study_execution(row["execution_id"]) for row in rows]


    def register_benchmark_acceptance_review(
        self, payload: dict[str, Any], *, actor: str,
        identity: dict[str, Any], reason: str
    ) -> dict[str, Any]:
        """Persist one immutable independent acceptance decision for an exact execution."""
        if not isinstance(actor, str) or not actor.strip() or not isinstance(reason, str) or not reason.strip():
            raise RegistryIntegrityError("acceptance reviewer actor and reason are required")
        identity_fields = ("issuer", "subject", "provider_key_id", "provider_adapter", "authenticated_at")
        if not isinstance(identity, dict) or any(
            not isinstance(identity.get(key), str) or not identity[key].strip()
            for key in identity_fields
        ):
            raise RegistryIntegrityError("acceptance review requires trusted provider identity evidence")
        authenticated_at = _aware_datetime(identity["authenticated_at"], "authenticated_at")
        now = datetime.now(timezone.utc)
        if authenticated_at > now or now - authenticated_at > timedelta(minutes=15):
            raise RegistryIntegrityError("acceptance review requires recent authentication within 15 minutes")
        with self._transaction() as connection:
            self._require_benchmark_role(connection, actor, "benchmark_acceptance_reviewer")
            reviewer = connection.execute(
                "SELECT active, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                (actor,),
            ).fetchone()
            if reviewer is None or not reviewer["active"] or reviewer["principal_kind"] != "human":
                raise RegistryIntegrityError("acceptance review requires an active human-classified reviewer")
            protocol_row = connection.execute(
                "SELECT * FROM benchmark_study_protocols WHERE study_id = ? AND study_version = ?",
                (payload.get("study_id"), payload.get("study_version")),
            ).fetchone()
            execution_row = connection.execute(
                "SELECT * FROM benchmark_study_executions WHERE execution_id = ?",
                (payload.get("execution_id"),),
            ).fetchone()
            if protocol_row is None or execution_row is None:
                raise RegistryIntegrityError("acceptance review must reference registered protocol and execution records")
            protocol = json.loads(protocol_row["payload_json"])
            execution = json.loads(execution_row["payload_json"])
            if sha256_fingerprint(protocol) != protocol_row["protocol_fingerprint"]:
                raise RegistryIntegrityError("registered study protocol fingerprint verification failed")
            if sha256_fingerprint(execution) != execution_row["execution_fingerprint"]:
                raise RegistryIntegrityError("registered study execution fingerprint verification failed")
            if execution_row["protocol_record_id"] != protocol_row["protocol_record_id"]:
                raise RegistryIntegrityError("execution is not bound to the referenced registered protocol version")
            if actor != payload["reviewer_id"]:
                raise RegistryIntegrityError("acceptance reviewer must match the authenticated principal")
            if actor in {row["reviewer_id"] for row in protocol["reviewer_plan"]}:
                raise RegistryIntegrityError("blind evaluator cannot act as independent acceptance reviewer")
            if actor == execution_row["methodologist_id"]:
                raise RegistryIntegrityError("methodologist cannot act as independent acceptance reviewer")
            reviewed_at = _aware_datetime(payload["reviewed_at"], "reviewed_at")
            execution_completed_at = _aware_datetime(execution["completed_at"], "execution.completed_at")
            if reviewed_at > now:
                raise RegistryIntegrityError("acceptance review timestamp cannot be in the future")
            if reviewed_at < execution_completed_at:
                raise RegistryIntegrityError("acceptance review cannot precede completed study execution")
            try:
                validate_benchmark_acceptance_review(payload, protocol=protocol, execution=execution)
            except (TypeError, ValueError, BenchmarkAcceptanceReviewError) as exc:
                raise RegistryIntegrityError(
                    f"acceptance review does not satisfy the exact registered study evidence: {exc}"
                ) from exc
            artifact = connection.execute(
                "SELECT content_fingerprint, content_json FROM benchmark_artifacts WHERE artifact_id = ?",
                (execution_row["benchmark_id"],),
            ).fetchone()
            if artifact is None or sha256_fingerprint(json.loads(artifact["content_json"])) != artifact["content_fingerprint"]:
                raise RegistryIntegrityError("acceptance review benchmark artifact fingerprint verification failed")
            if artifact["content_fingerprint"] != execution_row["benchmark_fingerprint"]:
                raise RegistryIntegrityError("acceptance review benchmark fingerprint is stale")
            created_at = _utc_now()
            connection.execute(
                """INSERT INTO benchmark_acceptance_reviews
                (review_id, execution_id, protocol_record_id, study_id, study_version,
                 protocol_fingerprint, execution_fingerprint, acceptance_fingerprint, benchmark_id,
                 benchmark_fingerprint, rubric_version, reviewer_id, reviewer_role,
                 decision, issuer, subject, provider_key_id, provider_adapter,
                 authenticated_at, payload_json, created_at, actor, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["review_id"], payload["execution_id"], protocol_row["protocol_record_id"],
                 payload["study_id"], payload["study_version"], payload["protocol_fingerprint"],
                 payload["execution_fingerprint"], sha256_fingerprint(payload), payload["benchmark_id"],
                 payload["benchmark_fingerprint"], payload["rubric_version"], actor,
                 payload["reviewer_role"], payload["decision"], identity["issuer"], identity["subject"],
                 identity["provider_key_id"], identity["provider_adapter"], identity["authenticated_at"],
                 _json_text(payload), created_at, actor, reason),
            )
            self._append_audit(
                connection, event_type="benchmark_acceptance_review.registered",
                entity_type="benchmark_acceptance_review", entity_id=payload["review_id"],
                payload={"execution_id": payload["execution_id"], "study_id": payload["study_id"],
                         "decision": payload["decision"], "reviewer_id": actor,
                         "protocol_fingerprint": payload["protocol_fingerprint"],
                         "execution_fingerprint": payload["execution_fingerprint"], "reason": reason},
            )
        return self.get_benchmark_acceptance_review(payload["review_id"]) or {}

    def get_benchmark_acceptance_review(self, review_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_acceptance_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        return {
            "review_id": row["review_id"], "execution_id": row["execution_id"],
            "protocol_record_id": row["protocol_record_id"], "study_id": row["study_id"],
            "study_version": row["study_version"], "protocol_fingerprint": row["protocol_fingerprint"],
            "execution_fingerprint": row["execution_fingerprint"], "acceptance_fingerprint": row["acceptance_fingerprint"], "benchmark_id": row["benchmark_id"],
            "benchmark_fingerprint": row["benchmark_fingerprint"], "rubric_version": row["rubric_version"],
            "reviewer_id": row["reviewer_id"], "decision": row["decision"],
            "issuer": row["issuer"], "subject": row["subject"], "provider_key_id": row["provider_key_id"],
            "provider_adapter": row["provider_adapter"], "authenticated_at": row["authenticated_at"],
            "review": payload, "created_at": row["created_at"], "actor": row["actor"],
            "reason": row["reason"], "fingerprint_verified": (
                sha256_fingerprint(payload) == row["acceptance_fingerprint"]
            ),
            "production_ready": False, "authorization_granted": False,
        }

    def list_benchmark_acceptance_reviews(
        self, *, study_id: str | None = None, execution_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, values = [], []
        if study_id is not None:
            clauses.append("study_id = ?")
            values.append(study_id)
        if execution_id is not None:
            clauses.append("execution_id = ?")
            values.append(execution_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT review_id FROM benchmark_acceptance_reviews" + where + " ORDER BY created_at, review_id",
            values,
        ).fetchall()
        return [self.get_benchmark_acceptance_review(row["review_id"]) for row in rows]

    def get_benchmark_score(self, score_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT * FROM benchmark_scores WHERE score_id = ?", (score_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "score_id": row["score_id"], "assignment_id": row["assignment_id"],
            "artifact_id": row["artifact_id"], "artifact_fingerprint": row["artifact_fingerprint"],
            "item_id": row["item_id"], "task_type": row["task_type"],
            "language": row["language"], "evaluator_id": row["evaluator_id"],
            "evaluated_at": row["evaluated_at"], "weighted_score": row["weighted_score"],
            "eligible_for_comparison": bool(row["eligible_for_comparison"]),
            "score_fingerprint": row["score_fingerprint"],
            "score": json.loads(row["payload_json"]), "issuer": row["issuer"],
            "subject": row["subject"], "provider_key_id": row["provider_key_id"],
            "provider_adapter": row["provider_adapter"], "authenticated_at": row["authenticated_at"],
            "created_at": row["created_at"], "reason": row["reason"],
        }

    def list_benchmark_scores(
        self, *, artifact_id: str | None = None, item_id: str | None = None,
        evaluator_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, values = [], []
        for column, value in (("artifact_id", artifact_id), ("item_id", item_id),
                              ("evaluator_id", evaluator_id)):
            if value is not None:
                clauses.append(f"{column} = ?")
                values.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._connection.execute(
            "SELECT score_id FROM benchmark_scores" + where + " ORDER BY created_at, score_id",
            values,
        ).fetchall()
        return [self.get_benchmark_score(row["score_id"]) for row in rows]


    def benchmark_evaluator_agreement_report(
        self, artifact_id: str, item_id: str
    ) -> dict[str, Any]:
        """Return descriptive agreement for durable score sheets on one exact item."""
        artifact = self.get_benchmark_artifact(artifact_id)
        if artifact is None:
            raise RegistryIntegrityError(f"unknown benchmark artifact: {artifact_id}")
        scores = self.list_benchmark_scores(artifact_id=artifact_id, item_id=item_id)
        if not scores:
            raise RegistryIntegrityError("no persisted score sheets exist for this benchmark item")
        for stored in scores:
            if stored["artifact_fingerprint"] != artifact["content_fingerprint"]:
                raise RegistryIntegrityError("stored score does not match the registered artifact fingerprint")
            principal = self.get_benchmark_principal(stored["evaluator_id"])
            if principal is None or principal.get("principal_kind") != "human":
                raise RegistryIntegrityError("agreement reports require human-classified evaluator principals")
            if not stored["issuer"] or not stored["subject"] or not stored["provider_key_id"]:
                raise RegistryIntegrityError("agreement report requires provider-attested evaluator provenance")
        try:
            return benchmark_evaluator_agreement([row["score"] for row in scores])
        except BenchmarkAgreementError as exc:
            raise RegistryIntegrityError(str(exc)) from exc

    def benchmark_readiness_report(self, artifact_id: str) -> dict[str, Any]:
        """Report evidence readiness without treating declared principal types as identity proof."""
        with self._transaction("DEFERRED") as connection:
            artifact_row = connection.execute(
                "SELECT language, content_fingerprint, content_json FROM benchmark_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()
            if artifact_row is None:
                raise RegistryIntegrityError(f"unknown benchmark artifact: {artifact_id}")
            artifact = {
                "artifact_id": artifact_id,
                "language": artifact_row["language"],
                "content_fingerprint": artifact_row["content_fingerprint"],
                "fingerprint_verified": sha256_fingerprint(json.loads(artifact_row["content_json"])) == artifact_row["content_fingerprint"],
            }
            rows = connection.execute(
                "SELECT revision, payload_json FROM benchmark_reviews WHERE benchmark_id = ? ORDER BY review_id",
                (artifact_id,),
            ).fetchall()
            reviews = [(json.loads(row["payload_json"]), row["revision"]) for row in rows]
            principals = {}
            for review, _review_revision in reviews:
                reviewer_id = review.get("reviewer", {}).get("reviewer_id")
                if reviewer_id and reviewer_id not in principals:
                    row = connection.execute(
                        "SELECT active, roles_json, languages_json, principal_kind FROM benchmark_principals WHERE principal_id = ?",
                        (reviewer_id,),
                    ).fetchone()
                    principals[reviewer_id] = None if row is None else {
                        "active": bool(row["active"]), "roles": json.loads(row["roles_json"]),
                        "languages": json.loads(row["languages_json"]), "principal_kind": row["principal_kind"],
                    }
            eligible = []
            review_checks = []
            for review, review_revision in reviews:
                reviewer_id = review.get("reviewer", {}).get("reviewer_id")
                principal = principals.get(reviewer_id)
                quality = self._benchmark_review_quality_report(review, connection)
                principal_ok = bool(
                    principal and principal["active"]
                    and "benchmark_reviewer" in principal["roles"]
                    and artifact["language"] in principal["languages"]
                )
                binding_ok = (
                    review.get("benchmark_fingerprint") == artifact["content_fingerprint"]
                    and review.get("language") == artifact["language"]
                )
                attestation_row = connection.execute(
                    "SELECT principal_id, issuer, audience, subject, provider_key_id, provider_adapter, authenticated_at, expires_at, attested_at FROM benchmark_review_identity_attestations WHERE review_id = ? AND review_revision = ?",
                    (review.get("review_id"), review_revision),
                ).fetchone()
                identity_attested = bool(
                    attestation_row
                    and attestation_row["principal_id"] == reviewer_id
                    and attestation_row["subject"]
                    and attestation_row["issuer"]
                    and attestation_row["audience"]
                    and attestation_row["provider_key_id"]
                    and attestation_row["provider_adapter"]
                    and _aware_datetime(attestation_row["attested_at"], "attested_at") < _aware_datetime(attestation_row["expires_at"], "expires_at")
                )
                current = bool(
                    review.get("status") == "approved"
                    and quality["eligible_for_current_use"]
                    and principal_ok and binding_ok
                )
                if current:
                    eligible.append((review, principal, identity_attested))
                review_checks.append({
                    "review_id": review.get("review_id"), "reviewer_id": reviewer_id,
                    "status": review.get("status"), "principal_kind": principal["principal_kind"] if principal else "missing",
                    "active_qualified_principal": principal_ok, "exact_artifact_binding": binding_ok,
                    "current_review_policy_eligible": quality["eligible_for_current_use"],
                    "identity_provider_attested": identity_attested,
                    "eligible_approved_review": current,
                })
            distinct_reviewers = {review["reviewer"]["reviewer_id"] for review, _, _ in eligible}
            kinds = {principal["principal_kind"] for _, principal, _ in eligible}
            attested_reviewers = {review["reviewer"]["reviewer_id"] for review, principal, attested in eligible if principal["principal_kind"] == "human" and attested}
            adjudication_rows = connection.execute(
                "SELECT adjudication_id, payload_json FROM benchmark_adjudications WHERE benchmark_id = ?",
                (artifact_id,),
            ).fetchall()
            blockers = []
            if not artifact["fingerprint_verified"]:
                blockers.append("Artifact fingerprint verification failed.")
            if len(distinct_reviewers) < 2:
                blockers.append("At least two distinct active, language-qualified reviewers with currently eligible approved reviews are required.")
            if any(kind != "human" for kind in kinds):
                blockers.append("Eligible reviews include synthetic, automated, or unverified principals.")
            if len(attested_reviewers) < 2:
                blockers.append("At least two eligible human reviews require immutable identity-provider attestations bound to their exact review revisions.")
            if len(adjudication_rows) == 0:
                blockers.append("No independent final adjudication is recorded.")
            if len(adjudication_rows) > 1:
                blockers.append("Conflicting final adjudications exist.")
            # Provider attestations are only as trustworthy as the configured production adapter and its key-validation policy.
            if not reviews and not eligible:
                status = "human_review_pending"
            elif eligible and kinds <= {"synthetic", "automation"}:
                status = "synthetic_only"
            elif len(distinct_reviewers) >= 2 and len(attested_reviewers) >= 2 and kinds == {"human"} and not adjudication_rows:
                status = "ready_for_adjudication"
            else:
                status = "human_review_pending"
            return {
                "artifact": artifact, "status": status, "eligible_approved_review_count": len(eligible),
                "distinct_eligible_reviewer_count": len(distinct_reviewers),
                "reviewer_classifications": sorted(kinds), "reviews": review_checks,
                "adjudication_count": len(adjudication_rows), "blockers": blockers,
                "production_ready": False,
                "warnings": [
                    "principal_kind is an administrative classification; identity attestation depends on the configured provider adapter verifying issuer, audience, signature/key, session revocation, and subject-to-principal mapping.",
                    "This report does not compare review content to determine whether reviewers disagree.",
                    "Only the existing review, permission, and adjudication policy gates can authorize current use; this report grants no approval.",
                ],
            }

    def benchmark_review_quality_report(self, review_id: str) -> dict[str, Any]:
        """Return a read-only report of review eligibility and current permission state."""
        with self._transaction("DEFERRED") as connection:
            row = connection.execute(
                "SELECT revision, payload_json FROM benchmark_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if row is None:
                raise RegistryIntegrityError(f"unknown benchmark review: {review_id}")
            report = self._benchmark_review_quality_report(
                json.loads(row["payload_json"]), connection
            )
            report["revision"] = row["revision"]
            return report

    def schema_version(self) -> int:
        with self._lock:
            return int(self._connection.execute("PRAGMA user_version").fetchone()[0])
