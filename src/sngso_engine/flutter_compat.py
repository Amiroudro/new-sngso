"""Fail-closed verification for a staging-only Flutter edition candidate.

This module validates a caller-prepared legacy Edition payload against current
registry records. It does not serialize, write, publish, or authorize an edition.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from .canonical import sha256_fingerprint


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


def build_flutter_staging_candidate(
    registry: Any,
    edition_id: str,
    field_bindings: dict[str, dict[str, str]],
    source_permissions: list[dict[str, str]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build and verify an English story-only Flutter staging candidate in memory.

    field_bindings maps each registered story ID to the explicit approved
    headline_summary and brief_summary learning-object IDs. No file or
    publication feed is written.
    """
    _required_text(edition_id, "edition_id")
    edition_record = registry.get_domain_record("edition", edition_id)
    if edition_record is None:
        raise FlutterCompatibilityError(f"registered edition not found: {edition_id}")
    edition = edition_record.to_payload()
    history = registry.domain_record_history("edition", edition_id)
    edition_revision = history[-1]["revision"] if history else None
    if edition_revision is None or edition.get("status") != "approved":
        raise FlutterCompatibilityError("candidate requires a current approved registered edition")
    story_ids = edition.get("story_ids")
    if not isinstance(story_ids, list) or not story_ids or set(field_bindings) != set(story_ids):
        raise FlutterCompatibilityError("field bindings must cover every approved edition story exactly")

    edition_date = edition.get("edition_key")
    try:
        day_name = date.fromisoformat(edition_date).strftime("%A")
    except (TypeError, ValueError) as exc:
        raise FlutterCompatibilityError("registered edition key must be an ISO date") from exc

    client_stories = []
    story_bindings = []
    all_source_refs: dict[str, dict[str, Any]] = {}
    for story_id in sorted(story_ids):
        selected = field_bindings.get(story_id)
        if not isinstance(selected, dict) or set(selected) != {"headline", "summary"}:
            raise FlutterCompatibilityError(f"story {story_id} requires explicit headline and summary object IDs")
        snapshot = registry.story_editorial_snapshot(story_id)
        if snapshot.get("story_id") != story_id:
            raise FlutterCompatibilityError("registry story snapshot identity mismatch")
        sources = []
        for article in snapshot.get("articles", []):
            article_payload = article.get("record", {}) if isinstance(article, dict) else {}
            if article_payload.get("record_status") != "active":
                continue
            source_id = article_payload.get("source_id")
            source_record = registry.get_source(source_id)
            if source_record is None:
                raise FlutterCompatibilityError(f"article source is not registered: {source_id}")
            source_payload = source_record.to_payload() if hasattr(source_record, "to_payload") else source_record
            if source_payload.get("status") != "active":
                raise FlutterCompatibilityError(f"article source is not active: {source_id}")
            published_at = article_payload.get("published_at")
            source_ref = {
                "id": source_id,
                "publication": source_payload.get("publisher_name"),
                "headline": article_payload.get("title"),
                "date": published_at[:10] if isinstance(published_at, str) else "",
                "url": article_payload.get("canonical_uri"),
            }
            if not all(isinstance(value, str) and value.strip() for value in source_ref.values()):
                raise FlutterCompatibilityError(f"article source metadata is incomplete: {source_id}")
            sources.append(source_ref)
            all_source_refs[sha256_fingerprint(source_ref)] = source_ref
        if not sources:
            raise FlutterCompatibilityError(f"story {story_id} has no active registered source articles")

        selected_objects = {}
        object_bindings = {}
        for field, expected_kind in (("headline", "headline_summary"), ("summary", "brief_summary")):
            object_id = _required_text(selected[field], f"{story_id}.{field} learning object ID")
            record = registry.get_learning_object(object_id)
            if record is None:
                raise FlutterCompatibilityError(f"selected learning object is missing: {object_id}")
            payload = record.to_payload()
            if payload.get("story_id") != story_id or payload.get("kind") != expected_kind:
                raise FlutterCompatibilityError(f"selected object is not the required {expected_kind}: {object_id}")
            text_value = _object_text(payload, "content.text")
            selected_objects[field] = text_value
            object_bindings[field] = {
                "learning_object_id": object_id,
                "revision": record.revision,
                "sha256": sha256_fingerprint(payload),
            }
        client_stories.append({
            "id": story_id,
            "headline": selected_objects["headline"],
            "category": "",
            "summary": selected_objects["summary"],
            "sources": sources,
        })
        story_bindings.append({
            "story_id": story_id,
            "story_revision": snapshot["story_revision"],
            "snapshot_sha256": snapshot["fingerprint"],
            "fields": object_bindings,
        })

    client_edition = {
        "edition_date": edition_date,
        "edition_day": day_name,
        "coverage": "",
        "status": "staging",
        "counts": {
            "stories": len(client_stories), "words": 0, "revision_words": 0,
            "phrases": 0, "mcqs": 0, "sources": len(all_source_refs),
        },
        "stories": client_stories,
        "words": [],
        "revision_words": [],
        "phrases": [],
        "mcqs": [],
        "sources": sorted(all_source_refs.values(), key=lambda item: (item["id"], item["url"])),
    }
    manifest = {
        "schema_version": "1.0.0",
        "target_contract": "flutter-edition-v1",
        "edition_id": edition_id,
        "edition_revision": edition_revision,
        "edition_date": edition_date,
        "payload_sha256": sha256_fingerprint(client_edition),
        "story_bindings": story_bindings,
        "source_permissions": source_permissions,
    }
    verification = verify_flutter_staging_candidate(
        registry, client_edition, manifest, now=now
    )
    return {
        "client_edition": client_edition,
        "provenance_manifest": manifest,
        "verification": verification,
    }


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
          "story_revision": 1,
          "snapshot_sha256": "sha256:...",
          "fields": {
            "headline": {"learning_object_id": "...", "revision": 1, "sha256": "sha256:..."},
            "summary": {"learning_object_id": "...", "revision": 1, "sha256": "sha256:..."}
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
    supported_edition_fields = {
        "edition_date", "edition_day", "coverage", "status", "counts",
        "stories", "words", "revision_words", "phrases", "mcqs", "sources",
    }
    if set(client_edition) - supported_edition_fields:
        raise FlutterCompatibilityError("client edition contains unsupported fields")
    if client_edition.get("status") not in {"draft", "staging"}:
        raise FlutterCompatibilityError("candidate must remain draft or staging")
    if provenance_manifest.get("schema_version") != "1.0.0":
        raise FlutterCompatibilityError("unsupported provenance manifest schema")
    if provenance_manifest.get("target_contract") != "flutter-edition-v1":
        raise FlutterCompatibilityError("unsupported Flutter client contract")
    edition_id = _required_text(provenance_manifest.get("edition_id"), "manifest edition_id")
    edition_revision = provenance_manifest.get("edition_revision")
    if not isinstance(edition_revision, int) or edition_revision < 1:
        raise FlutterCompatibilityError("manifest edition_revision must be a positive integer")
    edition_record = registry.get_domain_record("edition", edition_id)
    if edition_record is None:
        raise FlutterCompatibilityError(f"registered edition not found: {edition_id}")
    edition_payload = edition_record.to_payload()
    history = registry.domain_record_history("edition", edition_id)
    current_revision = history[-1]["revision"] if history else None
    if current_revision != edition_revision:
        raise FlutterCompatibilityError("manifest edition revision is stale")
    if edition_payload.get("status") != "approved":
        raise FlutterCompatibilityError("only an approved registered edition can be staged")
    if provenance_manifest.get("edition_date") != edition_payload.get("edition_key"):
        raise FlutterCompatibilityError("manifest date does not match the registered edition")
    approved_languages = {
        item.get("language") for item in edition_payload.get("language_variants", [])
        if isinstance(item, dict) and item.get("status") == "approved"
    }
    if "en" not in approved_languages:
        raise FlutterCompatibilityError("registered edition lacks an approved English variant")
    if provenance_manifest.get("payload_sha256") != sha256_fingerprint(client_edition):
        raise FlutterCompatibilityError("client payload fingerprint does not match the manifest")

    edition_date = _required_text(client_edition.get("edition_date"), "edition_date")
    if provenance_manifest.get("edition_date") != edition_date:
        raise FlutterCompatibilityError("client edition date does not match the manifest")
    try:
        expected_day = date.fromisoformat(edition_date).strftime("%A")
    except ValueError as exc:
        raise FlutterCompatibilityError("edition_date must be an ISO calendar date") from exc
    if client_edition.get("edition_day") != expected_day:
        raise FlutterCompatibilityError("edition day does not match edition_date")
    if client_edition.get("coverage") not in (None, ""):
        raise FlutterCompatibilityError("coverage text is unsupported until it has a reviewed source binding")
    if not isinstance(client_edition.get("stories"), list) or not client_edition["stories"]:
        raise FlutterCompatibilityError("staging candidate must contain at least one story")
    if set(edition_payload.get("story_ids", [])) != {
        story.get("id") for story in client_edition["stories"] if isinstance(story, dict)
    }:
        raise FlutterCompatibilityError("client story set must match the approved edition exactly")
    for collection in ("words", "revision_words", "phrases", "mcqs"):
        if client_edition.get(collection, []) not in ([], None):
            raise FlutterCompatibilityError(f"unsupported client collection must be empty: {collection}")
    counts = client_edition.get("counts", {})
    if not isinstance(counts, dict):
        raise FlutterCompatibilityError("counts must be an object")
    for count_key, collection in (
        ("stories", "stories"), ("words", "words"), ("revision_words", "revision_words"),
        ("phrases", "phrases"), ("mcqs", "mcqs"), ("sources", "sources"),
    ):
        if count_key in counts and counts[count_key] != len(client_edition.get(collection, [])):
            raise FlutterCompatibilityError(f"{count_key} count does not match its client array")

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
        if set(story) != {"id", "headline", "summary", "category", "sources"}:
            raise FlutterCompatibilityError("client story contains unsupported or unbound fields")
        if set(story) != {"id", "headline", "summary", "category", "sources"}:
            raise FlutterCompatibilityError("client story contains unsupported or unbound fields")
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
    permission_bindings: list[dict[str, Any]] = []
    permission_keys: set[tuple[str, str]] = set()
    for permission in permissions:
        if not isinstance(permission, dict):
            raise FlutterCompatibilityError("each source permission binding must be an object")
        if set(permission) != {"source_id", "endpoint_id", "grant_id", "operation"}:
            raise FlutterCompatibilityError("source permission binding contains unsupported fields")
        source_id = _required_text(permission.get("source_id"), "permission source_id")
        endpoint_id = _required_text(permission.get("endpoint_id"), "permission endpoint_id")
        key = (source_id, endpoint_id)
        if key in permission_keys:
            raise FlutterCompatibilityError("source permission bindings must be unique by source and endpoint")
        permission_keys.add(key)
        permission_bindings.append(permission)

    used_sources: set[str] = set()
    used_article_urls: dict[str, set[str]] = {}
    verified_source_refs: dict[str, list[dict[str, Any]]] = {}
    verified_learning_objects: set[str] = set()
    verified_story_ids: list[str] = []
    for story_id, client_story in client_stories.items():
        snapshot = registry.story_editorial_snapshot(story_id)
        if snapshot.get("story_id") != story_id:
            raise FlutterCompatibilityError("registry story snapshot identity mismatch")
        story_payload = snapshot.get("story", {})
        if story_payload.get("status") in {"archived", "contested"}:
            raise FlutterCompatibilityError("archived or contested stories cannot enter this client contract")
        articles_by_source: dict[str, list[dict[str, Any]]] = {}
        for article in snapshot.get("articles", []):
            if not isinstance(article, dict) or not isinstance(article.get("record"), dict):
                continue
            article_record = article["record"]
            if article_record.get("record_status") != "active":
                continue
            articles_by_source.setdefault(article_record.get("source_id"), []).append(article_record)
        if not articles_by_source:
            raise FlutterCompatibilityError(f"story {story_id} has no active registered source articles")
        source_ids = []
        story_source_refs: set[str] = set()
        for source_ref in client_story["sources"]:
            if not isinstance(source_ref, dict):
                raise FlutterCompatibilityError("client source references must be objects")
            if set(source_ref) != {"id", "publication", "headline", "date", "url"}:
                raise FlutterCompatibilityError("client source reference contains unsupported fields")
            source_fingerprint = sha256_fingerprint(source_ref)
            if source_fingerprint in story_source_refs:
                raise FlutterCompatibilityError("duplicate source reference in client story")
            story_source_refs.add(source_fingerprint)
            source_id = _required_text(source_ref.get("id"), "client source id")
            articles = articles_by_source.get(source_id, [])
            if not articles:
                raise FlutterCompatibilityError(
                    f"client source {source_id} is not linked to an active article in story {story_id}"
                )
            source_record = registry.get_source(source_id)
            if source_record is None:
                raise FlutterCompatibilityError(f"client source is not registered: {source_id}")
            source_payload = source_record.to_payload() if hasattr(source_record, "to_payload") else source_record
            if source_payload.get("status") != "active":
                raise FlutterCompatibilityError(f"client source is not active: {source_id}")
            if source_ref.get("publication") != source_payload.get("publisher_name"):
                raise FlutterCompatibilityError(f"client publisher name mismatch for {source_id}")
            matching_article = next((article for article in articles
                if article.get("title") == source_ref.get("headline")
                and article.get("canonical_uri") == source_ref.get("url")), None)
            if matching_article is None:
                raise FlutterCompatibilityError(
                    f"client source headline or URL does not match a registered article for {source_id}"
                )
            published_at = matching_article.get("published_at")
            source_date = source_ref.get("date")
            if published_at:
                if not isinstance(source_date, str) or source_date[:10] != published_at[:10]:
                    raise FlutterCompatibilityError(f"client publication date mismatch for {source_id}")
            elif source_date not in (None, ""):
                raise FlutterCompatibilityError(f"client publication date has no registered basis for {source_id}")
            source_ids.append(source_id)
            used_sources.add(source_id)
            used_article_urls.setdefault(source_id, set()).add(matching_article["canonical_uri"])
            verified_source_refs.setdefault(source_id, []).append(source_ref)
        if not source_ids:
            raise FlutterCompatibilityError(f"story {story_id} must display at least one source")
        if client_story["category"] and client_story["category"] not in story_payload.get("topic_tags", []):
            raise FlutterCompatibilityError(f"client category is not a registered story topic tag: {story_id}")
        if client_story["category"] and client_story["category"] not in story_payload.get("topic_tags", []):
            raise FlutterCompatibilityError(f"client category is not a registered story topic tag: {story_id}")
        binding = bindings_by_story[story_id]
        if set(binding) != {"story_id", "story_revision", "snapshot_sha256", "fields"}:
            raise FlutterCompatibilityError("story binding contains unsupported fields")
        if binding.get("story_revision") != snapshot.get("story_revision"):
            raise FlutterCompatibilityError(f"story revision is stale: {story_id}")
        if binding.get("snapshot_sha256") != snapshot.get("fingerprint"):
            raise FlutterCompatibilityError(f"story snapshot fingerprint is stale: {story_id}")
        fields = binding.get("fields")
        if not isinstance(fields, dict) or set(fields) != {"headline", "summary"}:
            raise FlutterCompatibilityError(
                f"story {story_id} must bind headline and summary to approved learning objects"
            )
        for field, expected_kind in (("headline", "headline_summary"), ("summary", "brief_summary")):
            item_binding = fields[field]
            if not isinstance(item_binding, dict) or set(item_binding) != {
                "learning_object_id", "revision", "sha256"
            }:
                raise FlutterCompatibilityError(f"{story_id}.{field} binding must contain object ID, revision, and fingerprint")
            object_id = _required_text(item_binding.get("learning_object_id"), "learning object id")
            revision = item_binding.get("revision")
            if not isinstance(revision, int) or revision < 1:
                raise FlutterCompatibilityError("learning-object revision must be a positive integer")
            record = registry.get_learning_object(object_id)
            if record is None or record.revision != revision:
                raise FlutterCompatibilityError(f"learning object is missing or stale: {object_id}")
            payload = record.to_payload()
            if item_binding.get("sha256") != sha256_fingerprint(payload):
                raise FlutterCompatibilityError(f"learning object fingerprint is stale: {object_id}")
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
                or payload.get("language") not in principal.get("languages", [])
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

    edition_sources = client_edition.get("sources", [])
    if not isinstance(edition_sources, list):
        raise FlutterCompatibilityError("edition sources must be a list")
    if not isinstance(edition_sources, list):
        raise FlutterCompatibilityError("edition sources must be a list")
    edition_ref_fingerprints = set()
    if not isinstance(edition_sources, list):
        raise FlutterCompatibilityError("edition sources must be a list")
    for source_ref in edition_sources:
        if not isinstance(source_ref, dict):
            raise FlutterCompatibilityError("edition source references must be objects")
        source_id = source_ref.get("id")
        if source_id not in used_sources or source_ref not in verified_source_refs.get(source_id, []):
            raise FlutterCompatibilityError("edition source is not bound to a verified story source")
        edition_ref_fingerprints.add(sha256_fingerprint(source_ref))
    story_ref_fingerprints = {
        sha256_fingerprint(source_ref)
        for refs in verified_source_refs.values()
        for source_ref in refs
    }
    if edition_ref_fingerprints != story_ref_fingerprints:
        raise FlutterCompatibilityError("edition sources must exactly match the verified story source set")
    permission_sources = {item["source_id"] for item in permission_bindings}
    if used_sources != permission_sources:
        raise FlutterCompatibilityError(
            "source permission bindings must exactly cover the client source references"
        )
    endpoint_urls: dict[str, list[str]] = {}
    for permission in permission_bindings:
        source_id = permission["source_id"]
        endpoint_id = permission["endpoint_id"]
        grant_id = _required_text(permission.get("grant_id"), "permission grant_id")
        if permission.get("operation") != "republication":
            raise FlutterCompatibilityError("Flutter export requires republication permission")
        endpoint_record = registry.get_endpoint(endpoint_id)
        grant_record = registry.get_permission_grant(grant_id)
        if endpoint_record is None or grant_record is None:
            raise FlutterCompatibilityError("source endpoint or republication grant is not registered")
        endpoint_payload = endpoint_record.to_payload() if hasattr(endpoint_record, "to_payload") else endpoint_record
        grant_payload = grant_record.to_payload() if hasattr(grant_record, "to_payload") else grant_record
        if endpoint_payload.get("source_id") != source_id or grant_payload.get("source_id") != source_id:
            raise FlutterCompatibilityError("republication grant and endpoint must match the exact source")
        endpoint_url = endpoint_payload.get("endpoint_uri")
        if not isinstance(endpoint_url, str):
            raise FlutterCompatibilityError("registered endpoint URI is missing")
        endpoint_urls.setdefault(source_id, []).append(endpoint_url)
        decision = registry.evaluate_grant(
            grant_id, "republication", endpoint_id=endpoint_id, now=evaluation_time
        )
        if not decision.allowed or decision.permission_id != grant_id:
            raise FlutterCompatibilityError(
                f"current republication permission denied for {source_id}: {decision.reason}"
            )
    for source_id, article_urls in used_article_urls.items():
        for article_url in article_urls:
            article_parts = urlsplit(article_url)
            covered = False
            for endpoint_url in endpoint_urls.get(source_id, []):
                endpoint_parts = urlsplit(endpoint_url)
                base_path = endpoint_parts.path.rstrip("/")
                path_matches = (
                    article_parts.path == base_path
                    or article_parts.path.startswith(base_path + "/")
                    or base_path == ""
                )
                if (
                    article_parts.scheme == endpoint_parts.scheme
                    and article_parts.netloc.casefold() == endpoint_parts.netloc.casefold()
                    and path_matches
                ):
                    covered = True
                    break
            if not covered:
                raise FlutterCompatibilityError(
                    f"no permitted endpoint covers article URL for {source_id}"
                )

    return {
        "status": "verified_for_staging_only",
        "publication_authorized": False,
        "edition_id": edition_id,
        "edition_revision": edition_revision,
        "edition_date": edition_date,
        "client_payload_sha256": sha256_fingerprint(client_edition),
        "story_ids": sorted(verified_story_ids),
        "learning_object_ids": sorted(verified_learning_objects),
        "source_ids": sorted(used_sources),
        "permission_checks": len(used_sources),
    }
