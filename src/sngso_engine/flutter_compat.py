"""Fail-closed verification for a staging-only Flutter edition candidate.

This module validates a caller-prepared legacy Edition payload against current
registry records. It does not serialize, write, publish, or authorize an edition.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .canonical import sha256_fingerprint
from .registry import RegistryIntegrityError


class FlutterCompatibilityError(ValueError):
    """Raised when a staging candidate does not match the current registry."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FlutterCompatibilityError(f"{label} must be a non-empty string")
    return value


def _object_text(payload: dict[str, Any], path: str) -> str:
    value: Any = payload
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise FlutterCompatibilityError(f"learning object is missing bound field {path}")
        value = value[key]
    return _required_text(value, f"learning object field {path}")


def verify_flutter_staging_candidate(
    registry: Any,
    client_edition: dict[str, Any],
    provenance_manifest: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a prepared legacy-edition payload for staging, never publication.

    Manifest shape:
      {
        "schema_version": "1.0.0",
        "target_contract": "flutter-edition-v1",
        "edition_id": "...",
        "payload_sha256": "...",
        "story_bindings": [{
          "story_id": "...",
          "fields": {
            "headline": {"learning_object_id": "...", "revision": 1},
            "summary": {"learning_object_id": "...", "revision": 1}
          }
        }],
        "source_permissions": [{
          "source_id": "...", "endpoint_id": "...", "grant_id": "..."
        }]
      }

    Every story's headline and summary must exactly match approved, English,
    quality-gated learning-object text. Every referenced source must have a
    current registry grant for republication. Other client content collections
    are rejected until their mappings are explicitly implemented.
    """
    evaluation_time = now or datetime.now(timezone.utc)
    if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
        raise FlutterCompatibilityError("verification time must include a timezone")

    if not isinstance(client_edition, dict) or not isinstance(provenance_manifest, dict):
        raise FlutterCompatibilityError("edition and provenance manifest must be JSON objects")
    if client_edition.get("status") not in {"draft", "staging"}:
        raise FlutterCompatibilityError("candidate must remain draft or staging")
    if provenance_manifest.get("schema_version") != "1.0.0":
        raise FlutterCompatibilityError("unsupported provenance manifest schema")
    if provenance_manifest.get("target_contract") != "flutter-edition-v1":
        raise FlutterCompatibilityError("unsupported Flutter client contract")
    if provenance_manifest.get("edition_id") is None:
        raise FlutterCompatibilityError("manifest edition_id is required")
    if provenance_manifest.get("payload_sha256") != sha256_fingerprint(client_edition):
        raise FlutterCompatibilityError("client payload fingerprint does not match the manifest")

    edition_date = _required_text(client_edition.get("edition_date"), "edition_date")
    if not isinstance(client_edition.get("stories"), list) or not client_edition["stories"]:
        raise FlutterCompatibilityError("staging candidate must contain at least one story")
    for collection in ("words", "revision_words", "phrases", "mcqs"):
        if client_edition.get(collection, []) not in ([], None):
            raise FlutterCompatibilityError(f"unsupported client collection must be empty: {collection}")
    counts = client_edition.get("counts", {})
    if not isinstance(counts, dict):
        raise FlutterCompatibilityError("counts must be an object")
    if "stories" in counts and counts["stories"] != len(client_edition["stories"]):
        raise FlutterCompatibilityError("story count does not match the stories array")

    story_bindings = provenance_manifest.get("story_bindings")
    if not isinstance(story_bindings, list) or not story_bindings:
        raise FlutterCompatibilityError("manifest story_bindings must be a non-empty list")
    client_stories: dict[str, dict[str, Any]] = {}
    for story in client_edition["stories"]:
        if not isinstance(story, dict):
            raise FlutterCompatibilityError("every client story must be an object")
        story_id = _required_text(story.get("id"), "client story id")
        if story_id in client_stories:
            raise FlutterCompatibilityError("client story IDs must be unique")
        for field in ("headline", "summary", "category"):
            if not isinstance(story.get(field), str):
                raise FlutterCompatibilityError(f"client story {field} must be a string")
        if not isinstance(story.get("sources"), list):
            raise FlutterCompatibilityError("client story sources must be a list")
        client_stories[story_id] = story

    bindings_by_story: dict[str, dict[str, Any]] = {}
    for binding in story_bindings:
        if not isinstance(binding, dict):
            raise FlutterCompatibilityError("each story binding must be an object")
        story_id = _required_text(binding.get("story_id"), "bound story id")
        if story_id in bindings_by_story:
            raise FlutterCompatibilityError("manifest story bindings must be unique")
        bindings_by_story[story_id] = binding
    if set(bindings_by_story) != set(client_stories):
        raise FlutterCompatibilityError("manifest and client story IDs must match exactly")

    permissions = provenance_manifest.get("source_permissions")
    if not isinstance(permissions, list):
        raise FlutterCompatibilityError("manifest source_permissions must be a list")
    permission_by_source: dict[str, dict[str, Any]] = {}
    for permission in permissions:
        if not isinstance(permission, dict):
            raise FlutterCompatibilityError("each source permission binding must be an object")
        source_id = _required_text(permission.get("source_id"), "permission source_id")
        if source_id in permission_by_source:
            raise FlutterCompatibilityError("source permission bindings must be unique by source")
        permission_by_source[source_id] = permission

    used_sources: set[str] = set()
    verified_learning_objects: set[str] = set()
    verified_story_ids: list[str] = []
    for story_id, client_story in client_stories.items():
        snapshot = registry.story_editorial_snapshot(story_id)
        if snapshot.get("story_id") != story_id:
            raise FlutterCompatibilityError("registry story snapshot identity mismatch")
        if snapshot.get("story", {}).get("status") != "active":
            raise FlutterCompatibilityError("only active stories can enter a staging candidate")
        article_sources = {
            article["record"].get("source_id")
            for article in snapshot.get("articles", [])
            if isinstance(article, dict) and isinstance(article.get("record"), dict)
        }
        if not article_sources:
            raise FlutterCompatibilityError(f"story {story_id} has no registered source articles")
        source_ids = []
        for source_ref in client_story["sources"]:
            if not isinstance(source_ref, dict):
                raise FlutterCompatibilityError("client source references must be objects")
            source_id = _required_text(source_ref.get("id"), "client source id")
            if source_id not in article_sources:
                raise FlutterCompatibilityError(
                    f"client source {source_id} is not linked to story {story_id}"
                )
            source_ids.append(source_id)
            used_sources.add(source_id)
        if not source_ids:
            raise FlutterCompatibilityError(f"story {story_id} must display at least one source")
        binding = bindings_by_story[story_id]
        fields = binding.get("fields")
        if not isinstance(fields, dict) or set(fields) != {"headline", "summary"}:
            raise FlutterCompatibilityError(
                f"story {story_id} must bind headline and summary to approved learning objects"
            )
        for field, expected_kind in (("headline", "headline_summary"), ("summary", "brief_summary")):
            item_binding = fields[field]
            if not isinstance(item_binding, dict):
                raise FlutterCompatibilityError(f"{story_id}.{field} binding must be an object")
            object_id = _required_text(item_binding.get("learning_object_id"), "learning object id")
            revision = item_binding.get("revision")
            if not isinstance(revision, int) or revision < 1:
                raise FlutterCompatibilityError("learning-object revision must be a positive integer")
            record = registry.get_learning_object(object_id)
            if record is None or record.revision != revision:
                raise FlutterCompatibilityError(f"learning object is missing or stale: {object_id}")
            payload = record.to_payload()
            if (
                payload.get("story_id") != story_id
                or payload.get("kind") != expected_kind
                or payload.get("language") != "en"
                or payload.get("status") != "approved"
            ):
                raise FlutterCompatibilityError(
                    f"learning object {object_id} is not an approved English {expected_kind} for {story_id}"
                )
            quality = registry.learning_object_quality_report(object_id)
            if not quality.get("meets_registry_quality_gates"):
                raise FlutterCompatibilityError(f"learning object quality gate failed: {object_id}")
            review = payload.get("language_review")
            attestation = review.get("identity_attestation") if isinstance(review, dict) else None
            if (
                not isinstance(attestation, dict)
                or attestation.get("principal_id") != review.get("reviewer_id")
            ):
                raise FlutterCompatibilityError(
                    f"learning object lacks provider-attested language review: {object_id}"
                )
            principal = registry.get_benchmark_principal(review["reviewer_id"])
            if (
                principal is None
                or not principal.get("active")
                or principal.get("principal_kind") != "human"
            ):
                raise FlutterCompatibilityError(
                    f"learning-object reviewer is not an active human principal: {object_id}"
                )
            if client_story[field] != _object_text(payload, "content.text"):
                raise FlutterCompatibilityError(
                    f"client story {field} does not match its approved learning object: {story_id}"
                )
            verified_learning_objects.add(object_id)
        verified_story_ids.append(story_id)

    if used_sources != set(permission_by_source):
        raise FlutterCompatibilityError(
            "source permission bindings must exactly cover the client source references"
        )
    for source_id, permission in permission_by_source.items():
        if permission.get("operation") != "republication":
            raise FlutterCompatibilityError("Flutter export requires republication permission")
        endpoint_id = _required_text(permission.get("endpoint_id"), "permission endpoint_id")
        grant_id = _required_text(permission.get("grant_id"), "permission grant_id")
        decision = registry.evaluate_grant(
            grant_id, "republication", endpoint_id=endpoint_id, now=evaluation_time
        )
        if not decision.allowed or decision.permission_id != grant_id:
            raise FlutterCompatibilityError(
                f"current republication permission denied for {source_id}: {decision.reason}"
            )

    return {
        "status": "verified_for_staging_only",
        "publication_authorized": False,
        "edition_id": provenance_manifest["edition_id"],
        "edition_date": edition_date,
        "client_payload_sha256": sha256_fingerprint(client_edition),
        "story_ids": sorted(verified_story_ids),
        "learning_object_ids": sorted(verified_learning_objects),
        "source_ids": sorted(used_sources),
        "permission_checks": len(used_sources),
    }
