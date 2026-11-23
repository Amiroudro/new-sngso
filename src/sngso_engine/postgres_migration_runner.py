"""Ordered, checksum-verified PostgreSQL schema migration runner."""
from __future__ import annotations

import hashlib
from importlib.resources import files
from typing import Any

from .postgres_transaction import run_atomic


_LOCK_KEY = 0x534E47534F504731  # Stable namespace key: "SNGSOPG1".
_MIGRATIONS = (
    (1, "0001_audit_chain.sql"),
    (2, "0002_audit_head_guards.sql"),
    (3, "0003_source_permission_domain_registry.sql"),
    (4, "0004_collection_ingestion.sql"),
    (5, "0005_story_timeline.sql"),
    (6, "0006_perspectives_claim_relations.sql"),
    (7, "0007_learning_glossary_revisions.sql"),
    (8, "0008_benchmark_review_core.sql"),
    (9, "0009_benchmark_assignments_identity_scores.sql"),
    (10, "0010_benchmark_study_evidence.sql"),
    (11, "0011_learning_specialized_evidence.sql"),
    (12, "0012_pipeline_runs.sql"),
    (13, "0013_pipeline_stage_checkpoints.sql"),
    (14, "0014_pipeline_worker_leases.sql"),
    (15, "0015_edition_artifacts.sql"),
    (16, "0016_edition_decisions.sql"),
    (17, "0017_edition_snapshot_guards.sql"),
    (18, "0018_edition_release_authorizations.sql"),
)


class PostgresMigrationError(RuntimeError):
    """Raised when the PostgreSQL schema ledger diverges from packaged migrations."""


def _migration_bytes(name: str) -> bytes:
    return files("sngso_engine.postgres_migrations").joinpath(name).read_bytes()


def apply_postgres_migrations(connection: Any) -> tuple[int, ...]:
    """Apply all packaged migrations atomically and return newly applied versions.

    The transaction-scoped advisory lock serializes competing migrators. The
    migration ledger detects modified historical SQL and databases newer than
    this application. A migration's DDL and ledger row commit together.
    """
    def migrate(cursor: Any) -> tuple[int, ...]:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        cursor.execute(
            """CREATE TABLE IF NOT EXISTS postgres_schema_migrations (
                version integer PRIMARY KEY CHECK (version > 0),
                name text NOT NULL UNIQUE,
                checksum text NOT NULL CHECK (checksum ~ '^sha256:[0-9a-f]{64}$'),
                applied_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        cursor.execute(
            "SELECT version, name, checksum FROM postgres_schema_migrations ORDER BY version"
        )
        existing = {row[0]: (row[1], row[2]) for row in cursor.fetchall()}
        known = {version for version, _ in _MIGRATIONS}
        unknown = sorted(set(existing) - known)
        if unknown:
            raise PostgresMigrationError(
                f"database contains unsupported PostgreSQL migration versions: {unknown}"
            )

        recorded_versions = sorted(existing)
        expected_prefix = [version for version, _ in _MIGRATIONS[:len(recorded_versions)]]
        if recorded_versions != expected_prefix:
            raise PostgresMigrationError(
                "PostgreSQL migration ledger is not a contiguous prefix of packaged migrations"
            )

        applied: list[int] = []
        for version, name in _MIGRATIONS:
            raw = _migration_bytes(name)
            checksum = "sha256:" + hashlib.sha256(raw).hexdigest()
            recorded = existing.get(version)
            if recorded is not None:
                if recorded != (name, checksum):
                    raise PostgresMigrationError(
                        f"PostgreSQL migration {version} name/checksum differs from packaged SQL"
                    )
                continue

            cursor.execute(raw.decode("utf-8"), prepare=False)
            cursor.execute(
                """INSERT INTO postgres_schema_migrations (version, name, checksum)
                   VALUES (%s, %s, %s)""",
                (version, name, checksum),
            )
            applied.append(version)
        return tuple(applied)

    return run_atomic(connection, migrate)
