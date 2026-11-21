"""Deterministic bilingual edition assembly from explicitly selected reviewed records.

Assembly is staging-only: it does not approve, persist, or publish an edition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .canonical import sha256_fingerprint
from .contracts import ContractValidationError, validate_payload
from .edition_artifacts import (
    EditionArtifactIntegrityError,
    VerifiedEditionArtifact,
    verify_edition_artifact,
    verify_pipeline_run_for_artifact,
)
from .language_variants import BilingualVariantError, verify_independent_bilingual_variants


class EditionAssemblyError(ValueError):
    """Raised when selected registry records cannot form a safe bilingual edition."""


@dataclass(frozen=True, slots=True)
class AssembledBilingualEdition:
    """Canonical staging payload and manifest assembled from exact registry revisions."""

    artifact: VerifiedEditionArtifact
    story_ids: tuple[str, ...]
    learning_object_ids: tuple[str, ...]
    pipeline_run_id: str

    @property
    def payload_sha256(self) -> str:
        return self.artifact.payload_sha256


def assemble_bilingual_edition_artifact(
    registry: Any,
    edition_id: str,
    learning_object_ids_by_story: dict[str, list[str]],
    source_permissions: list[dict[str, str]],
    *,
    manifest_id: str,
    created_at: str,
) -> AssembledBilingualEdition:
    """Assemble explicitly selected, approved BN/EN pairs into an immutable candidate.

    Every selected object is included verbatim as a canonical payload record.
    The caller must supply exact learning-object IDs for every edition story and
    the exact permission bindings intended for the manifest. No text is generated,
    translated, edited, or silently selected by this function.
    """
    if not isinstance(edition_id, str) or not edition_id.strip():
        raise EditionAssemblyError("edition_id is required")
    if not isinstance(manifest_id, str) or not manifest_id.strip():
        raise EditionAssemblyError("manifest_id is required")
    if not isinstance(created_at, str) or not created_at.strip():
        raise EditionAssemblyError("created_at is required")
    if not isinstance(learning_object_ids_by_story, dict) or not learning_object_ids_by_story:
        raise EditionAssemblyError("explicit learning-object IDs are required for every edition story")
    if not isinstance(source_permissions, list) or not source_permissions:
        raise EditionAssemblyError("explicit republication permission bindings are required")

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise EditionAssemblyError("created_at must be an ISO-8601 timestamp") from exc
    if created.tzinfo is None or created.utcoffset() is None:
        raise EditionAssemblyError("created_at must include a timezone")

    edition_record = registry.get_domain_record("edition", edition_id)
    history = registry.domain_record_history("edition", edition_id)
    if edition_record is None or not history:
        raise EditionAssemblyError("edition must exist with revision history")
    edition = edition_record.to_payload()
    edition_revision = history[-1]["revision"]
    if edition.get("id") != edition_id:
        raise EditionAssemblyError("registered edition identity mismatch")
    if edition.get("status") not in {"draft", "in_review", "approved"}:
        raise EditionAssemblyError("only draft, in-review, or approved editions can be assembled")
    story_ids = edition.get("story_ids")
    if (
        not isinstance(story_ids, list) or not story_ids
        or len(story_ids) != len(set(story_ids))
        or set(learning_object_ids_by_story) != set(story_ids)
    ):
        raise EditionAssemblyError("explicit selections must cover every edition story exactly once")

    pipeline_run_id = edition.get("pipeline_run_id")
    if not isinstance(pipeline_run_id, str) or not pipeline_run_id.strip():
        raise EditionAssemblyError("edition must reference its pipeline run")

    assembled_stories = []
    story_bindings = []
    all_object_ids: list[str] = []
    for story_id in sorted(story_ids):
        selected_ids = learning_object_ids_by_story[story_id]
        if (
            not isinstance(selected_ids, list) or not selected_ids
            or any(not isinstance(item, str) or not item.strip() for item in selected_ids)
            or len(selected_ids) != len(set(selected_ids))
        ):
            raise EditionAssemblyError(f"{story_id} requires unique explicit learning-object IDs")

        snapshot = registry.story_editorial_snapshot(story_id)
        if snapshot.get("story_id") != story_id:
            raise EditionAssemblyError(f"story snapshot identity mismatch: {story_id}")
        snapshot_revision = snapshot.get("story_revision")
        if isinstance(snapshot_revision, bool) or not isinstance(snapshot_revision, int) or snapshot_revision < 1:
            raise EditionAssemblyError(f"story snapshot revision is invalid: {story_id}")
        snapshot_hash = snapshot.get("fingerprint")
        if not isinstance(snapshot_hash, str) or not snapshot_hash.startswith("sha256:"):
            raise EditionAssemblyError(f"story snapshot fingerprint is missing: {story_id}")
        story_payload = snapshot.get("story")
        if not isinstance(story_payload, dict) or story_payload.get("status") in {"archived", "contested"}:
            raise EditionAssemblyError(f"story is not eligible for assembly: {story_id}")

        selected_by_kind: dict[str, dict[str, dict[str, Any]]] = {}
        manifest_objects = []
        payload_objects = []
        for object_id in selected_ids:
            record = registry.get_learning_object(object_id)
            if record is None:
                raise EditionAssemblyError(f"selected learning object is missing: {object_id}")
            object_payload = record.to_payload()
            if object_payload.get("story_id") != story_id:
                raise EditionAssemblyError(f"learning object belongs to a different story: {object_id}")
            if object_payload.get("status") != "approved":
                raise EditionAssemblyError(f"learning object is not approved: {object_id}")
            history_rows = registry.learning_object_history(object_id)
            if not history_rows or history_rows[-1]["revision"] != record.revision:
                raise EditionAssemblyError(f"learning-object revision history mismatch: {object_id}")
            quality = registry.learning_object_quality_report(object_id)
            if not quality.get("meets_registry_quality_gates"):
                raise EditionAssemblyError(f"learning object failed quality gates: {object_id}")
            language = object_payload.get("language")
            kind = object_payload.get("kind")
            if language not in {"bn", "en"} or not isinstance(kind, str):
                raise EditionAssemblyError(f"unsupported language or kind for learning object: {object_id}")
            language_map = selected_by_kind.setdefault(kind, {})
            if language in language_map:
                raise EditionAssemblyError(f"duplicate {language} {kind} object for story {story_id}")
            language_map[language] = {"id": object_id, "record": record, "payload": object_payload}
            fingerprint = sha256_fingerprint(object_payload)
            manifest_objects.append({
                "learning_object_id": object_id,
                "learning_object_revision": record.revision,
                "learning_object_sha256": fingerprint,
                "kind": kind,
                "language": language,
            })
            payload_objects.append({
                "learning_object_id": object_id,
                "revision": record.revision,
                "fingerprint": fingerprint,
                "kind": kind,
                "language": language,
                "variant_group_id": object_payload.get("variant_group_id"),
                "claim_ids": sorted(object_payload.get("claim_ids", [])),
                "content": object_payload["content"],
            })
            all_object_ids.append(object_id)

        for kind, pair in selected_by_kind.items():
            if set(pair) != {"bn", "en"}:
                raise EditionAssemblyError(f"story {story_id} has an incomplete BN/EN pair for {kind}")
            try:
                verify_independent_bilingual_variants(
                    registry, pair["bn"]["id"], pair["en"]["id"]
                )
            except BilingualVariantError as exc:
                raise EditionAssemblyError(f"independent bilingual review failed for {story_id}/{kind}: {exc}") from exc

        articles = []
        for item in snapshot.get("articles", []):
            article = item.get("record", {}) if isinstance(item, dict) else {}
            if article.get("record_status") != "active":
                continue
            source_id = article.get("source_id")
            source_record = registry.get_source(source_id) if isinstance(source_id, str) else None
            if source_record is None:
                raise EditionAssemblyError(f"active article source is not registered for story {story_id}")
            source_payload = source_record.to_payload() if hasattr(source_record, "to_payload") else source_record
            if source_payload.get("status") != "active":
                raise EditionAssemblyError(f"active article source is inactive for story {story_id}")
            required_article_fields = ("id", "canonical_uri", "title", "source_language")
            if any(not isinstance(article.get(field), str) or not article[field].strip() for field in required_article_fields):
                raise EditionAssemblyError(f"active article metadata is incomplete for story {story_id}")
            articles.append({
                "article_id": article["id"],
                "article_revision": item.get("revision"),
                "source_id": source_id,
                "publisher_name": source_payload.get("publisher_name"),
                "title": article["title"],
                "canonical_uri": article["canonical_uri"],
                "source_language": article["source_language"],
                "published_at": article.get("published_at"),
            })
        if not articles:
            raise EditionAssemblyError(f"story has no active source articles: {story_id}")

        assembled_stories.append({
            "story_id": story_id,
            "story_revision": snapshot_revision,
            "snapshot_sha256": snapshot_hash,
            "canonical_title": story_payload.get("canonical_title"),
            "story_status": story_payload.get("status"),
            "learning_objects": sorted(payload_objects, key=lambda item: (item["kind"], item["language"], item["learning_object_id"])),
            "sources": sorted(articles, key=lambda item: (item["source_id"], item["article_id"])),
        })
        story_bindings.append({
            "story_id": story_id,
            "story_revision": snapshot_revision,
            "story_snapshot_sha256": snapshot_hash,
            "learning_objects": sorted(manifest_objects, key=lambda item: (item["kind"], item["language"], item["learning_object_id"])),
        })

    payload = {
        "edition_id": edition_id,
        "edition_revision": edition_revision,
        "edition_key": edition["edition_key"],
        "pipeline_run_id": pipeline_run_id,
        "stories": assembled_stories,
    }
    manifest = {
        "schema_version": "1.0.0",
        "manifest_id": manifest_id,
        "edition_id": edition_id,
        "edition_revision": edition_revision,
        "edition_key": edition["edition_key"],
        "created_at": created_at,
        "pipeline_run_id": pipeline_run_id,
        "payload_sha256": sha256_fingerprint(payload),
        "story_bindings": story_bindings,
        "source_permissions": source_permissions,
    }
    try:
        validate_payload("edition-artifact-manifest", manifest)
        artifact = verify_edition_artifact(payload, manifest)
        verify_pipeline_run_for_artifact(registry, manifest, artifact.payload_sha256)
    except (ContractValidationError, EditionArtifactIntegrityError, FileNotFoundError, ValueError) as exc:
        raise EditionAssemblyError(f"assembled edition failed contract or pipeline binding: {exc}") from exc

    return AssembledBilingualEdition(
        artifact=artifact,
        story_ids=tuple(sorted(story_ids)),
        learning_object_ids=tuple(sorted(all_object_ids)),
        pipeline_run_id=pipeline_run_id,
    )
