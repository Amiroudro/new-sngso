"""Build the transaction-bound PostgreSQL edition export re-verifier.

This composes the existing registry-backed edition verifier with the
PostgreSQL cursor read view. Exact story-snapshot and learning-object quality
ports are mandatory dependencies; without them the verification fails closed.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .contracts import ContractValidationError, validate_payload
from .edition_artifacts import verify_registry_backed_edition_artifact
from .postgres_edition_read import PostgresEditionRegistryReadView


def build_postgres_staging_export_reverifier(
    *,
    story_snapshot_loader: Callable[[Any, str], dict[str, Any]] | None,
    learning_object_quality_loader: Callable[[Any, str], dict[str, Any]] | None,
    export_id_factory: Callable[[], str] | None = None,
) -> Callable[[Any, dict[str, Any], datetime], dict[str, Any]]:
    """Return the callback expected by stage_edition_export().

    The two loaders must reproduce the current SQLite story snapshot and
    learning-object quality report exactly, using only the supplied cursor.
    They must lock all rows read with FOR SHARE (or stronger). The returned
    callback is trusted application code and must never run outside the
    transaction passed by stage_edition_export().
    """
    if not callable(story_snapshot_loader):
        raise ValueError("an exact-parity PostgreSQL story snapshot loader is required")
    if not callable(learning_object_quality_loader):
        raise ValueError("a PostgreSQL learning-object quality loader is required")
    make_export_id = export_id_factory or (lambda: "export:" + uuid.uuid4().hex)
    if not callable(make_export_id):
        raise TypeError("export_id_factory must be callable")

    def reverify(cursor: Any, artifact: dict[str, Any], evaluated_at: datetime) -> dict[str, Any]:
        if not isinstance(evaluated_at, datetime) or evaluated_at.tzinfo is None:
            raise ValueError("edition re-verification time must be timezone-aware")
        manifest_id = artifact.get("manifest_id")
        if not isinstance(manifest_id, str) or not manifest_id.strip():
            raise ValueError("verified artifact must include a manifest ID")

        view = PostgresEditionRegistryReadView(
            cursor,
            story_snapshot_loader=story_snapshot_loader,
            learning_object_quality_loader=learning_object_quality_loader,
        )
        verify_registry_backed_edition_artifact(
            view,
            artifact["payload"],
            artifact["manifest"],
            now=evaluated_at,
        )

        cursor.execute(
            """SELECT payload_json FROM edition_decisions
               WHERE manifest_id = %s
               FOR SHARE""",
            (manifest_id,),
        )
        approval_row = cursor.fetchone()
        if approval_row is None:
            raise ValueError("edition re-verification requires a stored approval decision")
        approval = json.loads(approval_row[0])
        if not isinstance(approval, dict) or approval.get("decision") != "approve":
            raise ValueError("edition re-verification requires an approval decision")

        cursor.execute(
            """SELECT payload_json FROM edition_release_authorizations
               WHERE manifest_id = %s
               FOR SHARE""",
            (manifest_id,),
        )
        authorization_row = cursor.fetchone()
        if authorization_row is None:
            raise ValueError("edition re-verification requires an independent release authorization")
        authorization = json.loads(authorization_row[0])
        if not isinstance(authorization, dict) or authorization.get("release_authorized") is not True:
            raise ValueError("edition re-verification requires a valid release authorization")

        package = {
            "schema_version": "1.0.0",
            "export_id": make_export_id(),
            "target": "staging",
            "created_at": evaluated_at.isoformat(),
            "manifest_id": manifest_id,
            "edition_id": artifact["edition_id"],
            "edition_revision": artifact["edition_revision"],
            "payload_sha256": artifact["payload_sha256"],
            "edition_payload": artifact["payload"],
            "provenance_manifest": artifact["manifest"],
            "staging_decision": approval,
            "release_authorization": authorization,
            "publication_authorized": False,
        }
        try:
            validate_payload("edition-staging-export", package)
        except (ContractValidationError, FileNotFoundError, ValueError) as exc:
            raise ValueError(f"PostgreSQL staging export contract failed: {exc}") from exc
        return package

    return reverify
