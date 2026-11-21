"""Canonical, staging-only edition artifact integrity checks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .canonical import canonical_json_bytes, sha256_fingerprint
from .contracts import ContractValidationError, validate_payload


class EditionArtifactIntegrityError(ValueError):
    """Raised when an edition payload and its provenance manifest disagree."""


@dataclass(frozen=True, slots=True)
class VerifiedEditionArtifact:
    """Immutable byte representation of a verified artifact pair."""

    payload_bytes: bytes
    manifest_bytes: bytes
    payload_sha256: str
    edition_id: str
    edition_revision: int



@dataclass(frozen=True, slots=True)
class VerifiedPipelineRun:
    """A successful registry run bound to one exact artifact fingerprint."""

    pipeline_run_id: str
    revision: int
    finished_at: str
    output_fingerprint: str


def verify_pipeline_run_for_artifact(
    registry: Any, manifest: dict[str, Any], payload_sha256: str
) -> VerifiedPipelineRun:
    """Require a persisted successful run whose output includes this artifact hash."""
    if not isinstance(manifest, dict):
        raise EditionArtifactIntegrityError("edition manifest must be an object")
    try:
        validate_payload("edition-artifact-manifest", manifest)
    except (ContractValidationError, FileNotFoundError, ValueError) as exc:
        raise EditionArtifactIntegrityError(f"invalid edition manifest: {exc}") from exc
    if not isinstance(payload_sha256, str) or payload_sha256 != manifest["payload_sha256"]:
        raise EditionArtifactIntegrityError("artifact fingerprint does not match manifest")
    stored = registry.get_pipeline_run(manifest["pipeline_run_id"])
    if stored is None:
        raise EditionArtifactIntegrityError("manifest references an unknown pipeline run")
    record = stored["record"]
    if record.get("status") != "succeeded":
        raise EditionArtifactIntegrityError("pipeline run has not succeeded")
    if payload_sha256 not in record.get("output_hashes", []):
        raise EditionArtifactIntegrityError("pipeline run output does not bind this artifact fingerprint")
    try:
        finished = datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00"))
        created = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise EditionArtifactIntegrityError("pipeline run or manifest timestamp is invalid") from exc
    if finished.tzinfo is None or created.tzinfo is None:
        raise EditionArtifactIntegrityError("pipeline run and manifest timestamps must be timezone-aware")
    if finished.astimezone(timezone.utc) > created.astimezone(timezone.utc):
        raise EditionArtifactIntegrityError("edition manifest predates pipeline completion")
    return VerifiedPipelineRun(
        pipeline_run_id=manifest["pipeline_run_id"],
        revision=stored["revision"],
        finished_at=record["finished_at"],
        output_fingerprint=payload_sha256,
    )


def verify_edition_artifact(
    payload: dict[str, Any], manifest: dict[str, Any]
) -> VerifiedEditionArtifact:
    """Validate manifest structure, canonical payload hash, and edition identity.

    This function performs no registry reads, filesystem writes, publication,
    or permission authorization. It verifies only the supplied in-memory pair.
    """
    if not isinstance(payload, dict):
        raise EditionArtifactIntegrityError("edition payload must be an object")
    if not isinstance(manifest, dict):
        raise EditionArtifactIntegrityError("edition manifest must be an object")
    try:
        validate_payload("edition-artifact-manifest", manifest)
    except (ContractValidationError, FileNotFoundError, ValueError) as exc:
        raise EditionArtifactIntegrityError(f"invalid edition manifest: {exc}") from exc

    required_payload_fields = ("edition_id", "edition_revision", "edition_key")
    missing = [field for field in required_payload_fields if field not in payload]
    if missing:
        raise EditionArtifactIntegrityError(
            "edition payload is missing identity fields: " + ", ".join(missing)
        )

    if isinstance(payload["edition_revision"], bool) or not isinstance(
        payload["edition_revision"], int
    ):
        raise EditionArtifactIntegrityError("edition_revision must be an integer")
    if payload["edition_id"] != manifest["edition_id"]:
        raise EditionArtifactIntegrityError("payload and manifest edition IDs differ")
    if payload["edition_revision"] != manifest["edition_revision"]:
        raise EditionArtifactIntegrityError("payload and manifest revisions differ")
    if payload["edition_key"] != manifest["edition_key"]:
        raise EditionArtifactIntegrityError("payload and manifest edition keys differ")

    payload_bytes = canonical_json_bytes(payload)
    payload_hash = sha256_fingerprint(payload)
    if payload_hash != manifest["payload_sha256"]:
        raise EditionArtifactIntegrityError("canonical payload fingerprint does not match manifest")

    return VerifiedEditionArtifact(
        payload_bytes=payload_bytes,
        manifest_bytes=canonical_json_bytes(manifest),
        payload_sha256=payload_hash,
        edition_id=manifest["edition_id"],
        edition_revision=manifest["edition_revision"],
    )



@dataclass(frozen=True, slots=True)
class VerifiedRegistryEdition:
    """Read-only registry evidence for one complete bilingual staging artifact."""

    artifact: VerifiedEditionArtifact
    pipeline_run: VerifiedPipelineRun
    story_ids: tuple[str, ...]
    learning_object_ids: tuple[str, ...]
    source_permission_checks: int


def verify_registry_backed_edition_artifact(
    registry: Any,
    payload: dict[str, Any],
    manifest: dict[str, Any],
    *,
    now: datetime | None = None,
) -> VerifiedRegistryEdition:
    """Verify artifact, edition/story/object revisions, bilingual review, and rights.

    This is a read-only staging gate. It does not create an approval decision,
    write artifacts, or grant publication authority. It intentionally requires
    current registry revisions to match the manifest; historic snapshots that
    cannot be reconstructed exactly are rejected rather than guessed.
    """
    from .flutter_compat import _endpoint_covers_article
    from .language_variants import BilingualVariantError, verify_independent_bilingual_variants

    try:
        artifact = verify_edition_artifact(payload, manifest)
        pipeline = verify_pipeline_run_for_artifact(
            registry, manifest, artifact.payload_sha256
        )
        evaluation_time = now or datetime.now(timezone.utc)
        if not isinstance(evaluation_time, datetime) or evaluation_time.tzinfo is None:
            raise EditionArtifactIntegrityError("permission evaluation time must be timezone-aware")

        edition_record = registry.get_domain_record("edition", manifest["edition_id"])
        edition_history = registry.domain_record_history("edition", manifest["edition_id"])
        if edition_record is None or not edition_history:
            raise EditionArtifactIntegrityError("manifest edition is not registered with revision history")
        edition_payload = edition_record.to_payload()
        current_edition_revision = edition_history[-1]["revision"]
        if current_edition_revision != manifest["edition_revision"]:
            raise EditionArtifactIntegrityError("registered edition revision is stale")
        if edition_payload.get("status") != "approved":
            raise EditionArtifactIntegrityError("registered edition is not approved for staging verification")
        if edition_payload.get("edition_key") != manifest["edition_key"]:
            raise EditionArtifactIntegrityError("registered edition key does not match manifest")
        if edition_payload.get("content_hash") != artifact.payload_sha256:
            raise EditionArtifactIntegrityError("registered edition content hash does not bind the canonical artifact")
        if edition_payload.get("pipeline_run_id") != manifest["pipeline_run_id"]:
            raise EditionArtifactIntegrityError("registered edition and manifest pipeline runs differ")

        story_bindings = manifest["story_bindings"]
        bound_story_ids = [item["story_id"] for item in story_bindings]
        if len(bound_story_ids) != len(set(bound_story_ids)):
            raise EditionArtifactIntegrityError("manifest story bindings contain duplicate stories")
        registered_story_ids = edition_payload.get("story_ids")
        if (
            not isinstance(registered_story_ids, list)
            or len(registered_story_ids) != len(set(registered_story_ids))
            or set(registered_story_ids) != set(bound_story_ids)
        ):
            raise EditionArtifactIntegrityError("manifest must bind every registered edition story exactly once")

        required_sources: dict[str, set[str]] = {}
        seen_learning_object_ids: set[str] = set()
        for binding in story_bindings:
            story_id = binding["story_id"]
            story_history = registry.domain_record_history("story", story_id)
            if not story_history or story_history[-1]["revision"] != binding["story_revision"]:
                raise EditionArtifactIntegrityError(f"story revision is missing or stale: {story_id}")
            snapshot = registry.story_editorial_snapshot(story_id)
            if (
                snapshot.get("story_id") != story_id
                or snapshot.get("story_revision") != binding["story_revision"]
                or snapshot.get("fingerprint") != binding["story_snapshot_sha256"]
            ):
                raise EditionArtifactIntegrityError(f"story editorial snapshot fingerprint mismatch: {story_id}")
            story_payload = snapshot.get("story")
            if not isinstance(story_payload, dict) or story_payload.get("status") in {"archived", "contested"}:
                raise EditionArtifactIntegrityError(f"story is not eligible for staging: {story_id}")

            objects_by_kind: dict[str, dict[str, str]] = {}
            for object_binding in binding["learning_objects"]:
                object_id = object_binding["learning_object_id"]
                if object_id in seen_learning_object_ids:
                    raise EditionArtifactIntegrityError(f"learning object is bound more than once: {object_id}")
                seen_learning_object_ids.add(object_id)
                history = registry.learning_object_history(object_id)
                if not history or history[-1]["revision"] != object_binding["learning_object_revision"]:
                    raise EditionArtifactIntegrityError(f"learning-object revision is missing or stale: {object_id}")
                record = registry.get_learning_object(object_id)
                if record is None or record.revision != object_binding["learning_object_revision"]:
                    raise EditionArtifactIntegrityError(f"learning object is missing or stale: {object_id}")
                object_payload = record.to_payload()
                if sha256_fingerprint(object_payload) != object_binding["learning_object_sha256"]:
                    raise EditionArtifactIntegrityError(f"learning-object fingerprint mismatch: {object_id}")
                if (
                    object_payload.get("story_id") != story_id
                    or object_payload.get("kind") != object_binding["kind"]
                    or object_payload.get("language") != object_binding["language"]
                    or object_payload.get("status") != "approved"
                ):
                    raise EditionArtifactIntegrityError(f"learning-object binding does not match approved record: {object_id}")
                language_bindings = objects_by_kind.setdefault(object_binding["kind"], {})
                if object_binding["language"] in language_bindings:
                    raise EditionArtifactIntegrityError(
                        f"story {story_id} has duplicate {object_binding['language']} variants for {object_binding['kind']}"
                    )
                language_bindings[object_binding["language"]] = object_id
            if not objects_by_kind:
                raise EditionArtifactIntegrityError(f"story has no bound learning objects: {story_id}")
            for kind, language_ids in objects_by_kind.items():
                if set(language_ids) != {"bn", "en"}:
                    raise EditionArtifactIntegrityError(f"story {story_id} lacks a Bangla/English pair for {kind}")
                try:
                    verify_independent_bilingual_variants(
                        registry, language_ids["bn"], language_ids["en"]
                    )
                except BilingualVariantError as exc:
                    raise EditionArtifactIntegrityError(
                        f"bilingual review gate failed for {story_id}/{kind}: {exc}"
                    ) from exc

            active_articles = [
                item.get("record", {}) for item in snapshot.get("articles", [])
                if isinstance(item, dict)
                and isinstance(item.get("record"), dict)
                and item["record"].get("record_status") == "active"
            ]
            if not active_articles:
                raise EditionArtifactIntegrityError(f"story has no active source articles: {story_id}")
            for article in active_articles:
                source_id = article.get("source_id")
                article_url = article.get("canonical_uri")
                if not isinstance(source_id, str) or not source_id.strip():
                    raise EditionArtifactIntegrityError(f"article has no registered source: {story_id}")
                if not isinstance(article_url, str) or not article_url.strip():
                    raise EditionArtifactIntegrityError(f"article has no canonical URL: {story_id}")
                source = registry.get_source(source_id)
                if source is None:
                    raise EditionArtifactIntegrityError(f"article source is not registered: {source_id}")
                source_payload = source.to_payload() if hasattr(source, "to_payload") else source
                if source_payload.get("status") != "active":
                    raise EditionArtifactIntegrityError(f"article source is not active: {source_id}")
                required_sources.setdefault(source_id, set()).add(article_url)

        permission_bindings = manifest["source_permissions"]
        permission_keys: set[tuple[str, str]] = set()
        endpoints_by_source: dict[str, list[tuple[str, str]]] = {}
        for permission in permission_bindings:
            source_id = permission["source_id"]
            endpoint_id = permission["endpoint_id"]
            grant_id = permission["grant_id"]
            key = (source_id, endpoint_id)
            if key in permission_keys:
                raise EditionArtifactIntegrityError("duplicate source/endpoint permission binding")
            permission_keys.add(key)
            if permission["operation"] != "republication":
                raise EditionArtifactIntegrityError("edition staging requires republication permission")
            endpoint = registry.get_endpoint(endpoint_id)
            grant = registry.get_permission_grant(grant_id)
            if endpoint is None or grant is None:
                raise EditionArtifactIntegrityError("permission binding references an unknown endpoint or grant")
            endpoint_payload = endpoint.to_payload() if hasattr(endpoint, "to_payload") else endpoint
            grant_payload = grant.to_payload() if hasattr(grant, "to_payload") else grant
            if (
                endpoint_payload.get("source_id") != source_id
                or grant_payload.get("source_id") != source_id
            ):
                raise EditionArtifactIntegrityError("permission grant and endpoint must match the exact source")
            endpoint_url = endpoint_payload.get("endpoint_uri")
            if not isinstance(endpoint_url, str):
                raise EditionArtifactIntegrityError("registered endpoint URL is missing")
            decision = registry.evaluate_grant(
                grant_id, "republication", endpoint_id=endpoint_id, now=evaluation_time
            )
            if not decision.allowed or decision.permission_id != grant_id:
                raise EditionArtifactIntegrityError(
                    f"current republication permission denied for {source_id}: {decision.reason}"
                )
            endpoints_by_source.setdefault(source_id, []).append((endpoint_id, endpoint_url))

        used_permission_keys: set[tuple[str, str]] = set()
        for source_id, article_urls in required_sources.items():
            for article_url in article_urls:
                covering = [
                    endpoint_id for endpoint_id, endpoint_url in endpoints_by_source.get(source_id, [])
                    if _endpoint_covers_article(endpoint_url, article_url)
                ]
                if not covering:
                    raise EditionArtifactIntegrityError(
                        f"no current republication permission endpoint covers an article for {source_id}"
                    )
                used_permission_keys.update((source_id, endpoint_id) for endpoint_id in covering)
        if permission_keys != used_permission_keys:
            raise EditionArtifactIntegrityError(
                "manifest source permissions must exactly cover article endpoints without unused grants"
            )

        return VerifiedRegistryEdition(
            artifact=artifact,
            pipeline_run=pipeline,
            story_ids=tuple(sorted(bound_story_ids)),
            learning_object_ids=tuple(sorted(
                item["learning_object_id"]
                for binding in story_bindings for item in binding["learning_objects"]
            )),
            source_permission_checks=len(permission_bindings),
        )
    except EditionArtifactIntegrityError:
        raise
    except Exception as exc:
        raise EditionArtifactIntegrityError(f"registry-backed edition verification failed: {exc}") from exc
