"""Immutable typed views of validated registry records."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .canonical import canonical_json_bytes


@dataclass(frozen=True, slots=True)
class _JsonRecord:
    id: str
    payload_json: str

    def to_payload(self) -> dict[str, Any]:
        import json

        return json.loads(self.payload_json)


@dataclass(frozen=True, slots=True)
class SourceRecord(_JsonRecord):
    """Validated publisher identity, not a permission grant."""

    publisher_name: str
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            publisher_name=payload["publisher_name"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class SourceEndpointRecord(_JsonRecord):
    """A single feed, API route, sitemap, or page endpoint."""

    source_id: str
    endpoint_uri: str
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "SourceEndpointRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            endpoint_uri=payload["endpoint_uri"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class PermissionEvidenceRecord(_JsonRecord):
    """Captured permission evidence and its review state."""

    source_id: str
    review_status: str
    content_fingerprint: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PermissionEvidenceRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            review_status=payload["review_status"],
            content_fingerprint=payload["content_fingerprint"],
        )


@dataclass(frozen=True, slots=True)
class PermissionGrantRecord(_JsonRecord):
    """A time-bounded grant scoped to exact endpoint and operation IDs."""

    source_id: str
    status: str
    effective_from: str
    effective_until: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PermissionGrantRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            status=payload["status"],
            effective_from=payload["effective_from"],
            effective_until=payload["effective_until"],
        )


@dataclass(frozen=True, slots=True)
class ArticleRecord(_JsonRecord):
    """Normalized bibliographic record linked to a registered publisher."""

    source_id: str
    canonical_uri: str
    title: str
    source_language: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ArticleRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            source_id=payload["source_id"],
            canonical_uri=payload["canonical_uri"],
            title=payload["title"],
            source_language=payload["source_language"],
        )


@dataclass(frozen=True, slots=True)
class StoryRecord(_JsonRecord):
    """Evolving story identity with explicit article membership."""

    canonical_title: str
    status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StoryRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            canonical_title=payload["canonical_title"],
            status=payload["status"],
        )


@dataclass(frozen=True, slots=True)
class ClaimRecord(_JsonRecord):
    """Atomic attributable proposition linked to a story."""

    story_id: str
    text: str
    assessment: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ClaimRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            story_id=payload["story_id"],
            text=payload["text"],
            assessment=payload["assessment"],
        )


@dataclass(frozen=True, slots=True)
class EvidenceRecord(_JsonRecord):
    """Factual evidence relationship for a claim, distinct from rights evidence."""

    claim_id: str
    source_id: str
    relationship: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EvidenceRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            claim_id=payload["claim_id"],
            source_id=payload["source_id"],
            relationship=payload["relationship"],
        )


@dataclass(frozen=True, slots=True)
class EditionRecord(_JsonRecord):
    """Immutable publication snapshot metadata."""

    edition_key: str
    status: str
    content_hash: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EditionRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            edition_key=payload["edition_key"],
            status=payload["status"],
            content_hash=payload["content_hash"],
        )



@dataclass(frozen=True, slots=True)
class StoryDecisionRecord(_JsonRecord):
    """Append-only human decision linking or separating story identities."""

    operation: str
    canonical_story_id: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "StoryDecisionRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            operation=payload["operation"],
            canonical_story_id=payload["canonical_story_id"],
        )


@dataclass(frozen=True, slots=True)
class TimelineEventRecord(_JsonRecord):
    """Append-only, claim-backed event assertion within a story timeline."""

    story_id: str
    event_type: str
    precision: str
    review_status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "TimelineEventRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            story_id=payload["story_id"],
            event_type=payload["event_type"],
            precision=payload["occurred"]["precision"],
            review_status=payload.get("review_status", "unreviewed"),
        )



@dataclass(frozen=True, slots=True)
class PerspectiveRecord(_JsonRecord):
    """Attributed publisher framing or stakeholder viewpoint, not a fact verdict."""

    story_id: str
    kind: str
    attribution: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PerspectiveRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            story_id=payload["story_id"],
            kind=payload["kind"],
            attribution=payload["attribution"],
        )


@dataclass(frozen=True, slots=True)
class ClaimRelationRecord(_JsonRecord):
    """Curated relationship between two claims, not an adjudication of either claim."""

    story_id: str
    claim_id: str
    related_claim_id: str
    relationship: str
    review_status: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ClaimRelationRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            story_id=payload["story_id"],
            claim_id=payload["claim_id"],
            related_claim_id=payload["related_claim_id"],
            relationship=payload["relationship"],
            review_status=payload["review_status"],
        )


@dataclass(frozen=True, slots=True)
class LearningObjectRecord(_JsonRecord):
    """One language-specific, claim-linked editorial or learning artifact."""

    story_id: str
    kind: str
    language: str
    status: str
    variant_group_id: str | None = None
    revision: int = 1

    @classmethod
    def from_payload(cls, payload: dict[str, Any], revision: int = 1) -> "LearningObjectRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            story_id=payload["story_id"],
            kind=payload["kind"],
            language=payload["language"],
            status=payload["status"],
            variant_group_id=payload.get("variant_group_id"),
            revision=revision,
        )


@dataclass(frozen=True, slots=True)
class GlossaryEntryRecord(_JsonRecord):
    """Canonical headword with language-specific senses and exam vocabulary metadata."""

    lemma: str
    term_language: str
    part_of_speech: str
    status: str
    revision: int = 1

    @classmethod
    def from_payload(cls, payload: dict[str, Any], revision: int = 1) -> "GlossaryEntryRecord":
        return cls(
            id=payload["id"],
            payload_json=canonical_json_bytes(payload).decode("utf-8"),
            lemma=payload["lemma"],
            term_language=payload["term_language"],
            part_of_speech=payload["part_of_speech"],
            status=payload["status"],
            revision=revision,
        )
