"""Build the explicit Android client read model from validated engine records.

This adapter is deliberately pure: it performs no network or persistence I/O.
Callers must supply the editorially selected primary article and localized
story titles explicitly. Missing metadata, approval, or permission fails closed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from sngso_engine.canonical import sha256_fingerprint
from sngso_engine.contracts import validate_payload
from sngso_engine.permissions import evaluate_permission


class ClientEditionExportError(ValueError):
    """Raised when domain records cannot safely form a client edition."""


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ClientEditionExportError(f"{label} must be a non-empty string")
    return value


def _published_at(value: Any, label: str) -> str:
    text = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ClientEditionExportError(f"{label} must be an RFC 3339 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ClientEditionExportError(f"{label} must include a timezone")
    return text


def build_client_edition(
    *,
    edition: dict[str, Any],
    stories: Mapping[str, dict[str, Any]],
    articles: Mapping[str, dict[str, Any]],
    source_metadata: Mapping[str, dict[str, Any]],
    source_permissions: Mapping[str, dict[str, Any]],
    learning_objects: list[dict[str, Any]],
    primary_article_by_story: Mapping[str, str],
    titles_by_story: Mapping[str, Mapping[str, str]],
    permission_evaluated_at: datetime,
) -> dict[str, Any]:
    """Return a deterministic, hash-sealed Android projection.

    The primary article and localized title for every story/language are
    explicit editorial inputs. The adapter never picks the first article,
    translates a title, or infers permission from a publisher's presence.
    """
    validate_payload("edition", edition)
    if edition["status"] != "published":
        raise ClientEditionExportError("only published editions can be exported")

    published_at = _published_at(edition.get("published_at"), "edition.published_at")
    if permission_evaluated_at.tzinfo is None or permission_evaluated_at.utcoffset() is None:
        raise ClientEditionExportError("permission_evaluated_at must include a timezone")
    language_variants = edition["language_variants"]
    languages = [item["language"] for item in language_variants]
    if len(languages) != len(set(languages)):
        raise ClientEditionExportError("edition contains duplicate language variants")
    if not languages or any(item["status"] != "approved" for item in language_variants):
        raise ClientEditionExportError("every exported language variant must be approved")

    ordered_story_ids = edition["story_ids"]
    if len(ordered_story_ids) != len(set(ordered_story_ids)):
        raise ClientEditionExportError("edition contains duplicate story IDs")
    if set(primary_article_by_story) != set(ordered_story_ids):
        raise ClientEditionExportError(
            "an explicit primary article must be selected for every edition story"
        )
    if set(titles_by_story) != set(ordered_story_ids):
        raise ClientEditionExportError(
            "localized titles must be supplied for every edition story"
        )

    summaries_by_story_language: dict[tuple[str, str], dict[str, str]] = {}
    for item in learning_objects:
        validate_payload("learning-object", item)
        if item["status"] != "approved":
            continue
        if item["kind"] not in {"brief_summary", "detailed_summary"}:
            continue
        key = (item["story_id"], item["language"])
        group = summaries_by_story_language.setdefault(key, {})
        if item["kind"] in group:
            raise ClientEditionExportError(
                f"duplicate approved {item['kind']} for {item['story_id']} ({item['language']})"
            )
        content_text = item.get("content", {}).get("text")
        group[item["kind"]] = _required_text(
            content_text, f"{item['kind']} content.text"
        )

    projected_stories: list[dict[str, Any]] = []
    for story_id in ordered_story_ids:
        story = stories.get(story_id)
        if story is None:
            raise ClientEditionExportError(f"missing story record: {story_id}")
        validate_payload("story", story)
        if story["id"] != story_id:
            raise ClientEditionExportError(f"story map key does not match {story_id}")

        article_id = primary_article_by_story[story_id]
        if article_id not in story["article_ids"]:
            raise ClientEditionExportError(
                f"selected primary article {article_id} is not linked to {story_id}"
            )
        article = articles.get(article_id)
        if article is None:
            raise ClientEditionExportError(f"missing primary article: {article_id}")
        validate_payload("article", article)
        if article["id"] != article_id:
            raise ClientEditionExportError(f"article map key does not match {article_id}")
        if article["source_id"] not in source_metadata:
            raise ClientEditionExportError(
                f"missing explicit source metadata: {article['source_id']}"
            )
        if article["record_status"] != "active":
            raise ClientEditionExportError(f"primary article is not active: {article_id}")
        article_published_at = _published_at(
            article.get("published_at"), f"{article_id}.published_at"
        )

        permission = source_permissions.get(article["source_id"])
        if permission is None:
            raise ClientEditionExportError(
                f"no current source permission supplied for {article['source_id']}"
            )
        validate_payload("source-permission", permission)
        if permission["source_id"] != article["source_id"]:
            raise ClientEditionExportError("source permission belongs to another source")
        decision = evaluate_permission(
            permission, "metadata", now=permission_evaluated_at
        )
        if not decision.allowed:
            raise ClientEditionExportError(
                f"metadata permission denied for {article['source_id']}: {decision.reason}"
            )

        source = source_metadata[article["source_id"]]
        source_name = _required_text(source.get("name"), "source name")
        if source.get("id") != article["source_id"]:
            raise ClientEditionExportError("source metadata ID does not match article")
        source_url = _required_text(article.get("canonical_uri"), "article canonical URI")
        if not source_url.startswith(("https://", "http://")):
            raise ClientEditionExportError("primary source URL must use HTTP or HTTPS")

        localized_titles = titles_by_story[story_id]
        if set(localized_titles) != set(languages):
            raise ClientEditionExportError(
                f"localized title languages do not match edition languages for {story_id}"
            )
        title = {
            language: _required_text(
                localized_titles[language], f"title for {story_id}/{language}"
            )
            for language in languages
        }

        summaries = []
        for language in languages:
            approved = summaries_by_story_language.get((story_id, language), {})
            if set(approved) != {"brief_summary", "detailed_summary"}:
                raise ClientEditionExportError(
                    f"missing approved brief and detailed summaries for {story_id}/{language}"
                )
            summaries.append(
                {
                    "language": language,
                    "status": "approved",
                    "brief": approved["brief_summary"],
                    "detailed": approved["detailed_summary"],
                }
            )

        projected_stories.append(
            {
                "id": story_id,
                "title": title,
                "topic_tags": story.get("topic_tags", []),
                "published_at": article_published_at,
                "source": {
                    "id": article["source_id"],
                    "name": source_name,
                    "url": source_url,
                    "published_at": article_published_at,
                },
                "summaries": summaries,
            }
        )

    projection_edition: dict[str, Any] = {
        "id": edition["id"],
        "edition_key": edition["edition_key"],
        "status": "published",
        "published_at": published_at,
        "languages": languages,
    }
    if edition.get("supersedes_edition_id"):
        projection_edition["supersedes_edition_id"] = edition["supersedes_edition_id"]
        if not edition.get("correction_note"):
            raise ClientEditionExportError(
                "a superseding edition requires an explicit correction_note"
            )
    if edition.get("correction_note"):
        projection_edition["correction_note"] = edition["correction_note"]

    result: dict[str, Any] = {
        "contract_version": "1.0.0",
        "content_hash": "",
        "edition": projection_edition,
        "stories": projected_stories,
    }
    result["content_hash"] = sha256_fingerprint(
        {key: value for key, value in result.items() if key != "content_hash"}
    )
    validate_payload("client-edition-v1", result)
    return result
