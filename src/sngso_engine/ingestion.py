"""Deterministic, provenance-preserving ingestion of permitted metadata observations."""

from __future__ import annotations

from datetime import datetime, timezone
import posixpath
import re
import unicodedata
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from .canonical import sha256_fingerprint
from .domains import canonical_dns_name
from .registry import RegistryIntegrityError, SQLiteRegistry


_TRACKING_KEYS = frozenset({
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "vero_id", "vero_conv", "oly_anon_id", "oly_enc_id",
})
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_BAD_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_PERCENT_ESCAPE = re.compile(r"%[0-9a-fA-F]{2}")


class IngestionError(ValueError):
    """Raised when an observation cannot safely become an article record."""


def normalize_text(value: str, *, max_length: int, field: str) -> str:
    """Normalize Unicode and whitespace while retaining the original observation."""
    if not isinstance(value, str):
        raise IngestionError(f"{field} must be text")
    normalized = unicodedata.normalize("NFC", " ".join(value.split()))
    if not normalized:
        raise IngestionError(f"{field} cannot be empty")
    if len(normalized) > max_length:
        raise IngestionError(f"{field} exceeds {max_length} characters")
    return normalized


def normalize_language_tag(value: str) -> str:
    """Normalize the casing of a conservative BCP-47 language tag."""
    if not isinstance(value, str) or not _LANGUAGE_TAG.fullmatch(value):
        raise IngestionError("source_language must be a valid language tag")
    parts = value.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def normalize_timestamp(value: str, *, field: str = "timestamp") -> str:
    """Require an explicit timezone and return a stable UTC ISO-8601 value."""
    if not isinstance(value, str):
        raise IngestionError(f"{field} must be a timestamp string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IngestionError(f"{field} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IngestionError(f"{field} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    timespec = "microseconds" if utc.microsecond else "seconds"
    return utc.isoformat(timespec=timespec).replace("+00:00", "Z")


def canonicalize_article_url(value: str) -> str:
    """Canonicalize safe article URLs without guessing away content identifiers.

    Only known tracking parameters are removed. Unknown query parameters and
    path case are preserved because they may identify distinct publisher pages.
    """
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 2048
        or any(ord(char) <= 32 or ord(char) == 127 or char == chr(92) for char in value)
        or "#" in value
        or _BAD_PERCENT.search(value)
    ):
        raise IngestionError("article URL is malformed or exceeds the allowed length")
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise IngestionError("article URL must use HTTP or HTTPS")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise IngestionError("article URL must have a host and no credentials")
        host = canonical_dns_name(parsed.hostname)
        if host is None:
            raise IngestionError("article URL host is not a valid DNS name")
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise IngestionError("article URL port is invalid")
    except ValueError as exc:
        if isinstance(exc, IngestionError):
            raise
        raise IngestionError("article URL is malformed") from exc

    scheme = parsed.scheme.lower()
    netloc = host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"

    path = parsed.path or "/"
    if _BAD_PERCENT.search(path):
        raise IngestionError("article URL path contains an invalid percent escape")
    path = quote(path, safe="/:@!$&'()*+,;=-._~%")
    path = _PERCENT_ESCAPE.sub(lambda match: match.group(0).upper(), path)

    try:
        query_items = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=128)
    except ValueError as exc:
        raise IngestionError("article URL has too many query parameters") from exc
    kept = [
        (key, item)
        for key, item in query_items
        if not key.casefold().startswith("utm_") and key.casefold() not in _TRACKING_KEYS
    ]
    # Stable key sorting preserves the order of repeated values for one key.
    kept = sorted(enumerate(kept), key=lambda pair: (pair[1][0], pair[0]))
    query = urlencode([item for _, item in kept], doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _title_identity(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold()
    chars = [
        " " if unicodedata.category(char)[0] in {"P", "S"} else char
        for char in normalized
    ]
    return " ".join("".join(chars).split())


def normalize_observation(observation: dict, source_language: str) -> tuple[dict, str | None]:
    """Create a validated article payload and a conservative duplicate fingerprint."""
    if not isinstance(observation, dict):
        raise IngestionError("observation must be an object")
    if observation.get("operation") != "metadata":
        raise IngestionError("only approved metadata observations can be ingested")
    metadata = observation.get("metadata")
    if not isinstance(metadata, dict):
        raise IngestionError("observation metadata must be an object")

    title = normalize_text(metadata.get("title"), max_length=1000, field="title")
    byline = metadata.get("byline")
    summary = metadata.get("summary_metadata")
    if byline is not None:
        byline = normalize_text(byline, max_length=500, field="byline")
    if summary is not None:
        summary = normalize_text(summary, max_length=1000, field="summary_metadata")

    canonical_uri = canonicalize_article_url(
        observation.get("final_locator") or observation.get("locator")
    )
    discovered_at = normalize_timestamp(observation.get("observed_at"), field="observed_at")
    language = normalize_language_tag(source_language)
    published_at = metadata.get("published_at")
    if published_at is not None:
        published_at = normalize_timestamp(published_at, field="published_at")

    source_id = observation.get("source_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise IngestionError("observation source_id is required")
    article_id = "article:" + sha256_fingerprint(
        {"source_id": source_id, "canonical_uri": canonical_uri}
    ).split(":", 1)[1]
    payload = {
        "schema_version": "1.0.0",
        "id": article_id,
        "source_id": source_id,
        "canonical_uri": canonical_uri,
        "title": title,
        "source_language": language,
        "discovered_at": discovered_at,
        "record_status": "active",
        "permission_id": observation.get("grant_id"),
    }
    if byline is not None:
        payload["byline"] = byline
    if summary is not None:
        payload["summary_metadata"] = summary
    if published_at is not None:
        payload["published_at"] = published_at
    content_fingerprint = observation.get("content_fingerprint")
    if content_fingerprint is not None:
        payload["content_fingerprint"] = content_fingerprint

    duplicate_fingerprint = None
    if published_at is not None:
        duplicate_fingerprint = sha256_fingerprint({
            "title": _title_identity(title),
            "source_language": language.casefold(),
            "published_at": published_at,
        })
    return payload, duplicate_fingerprint


def ingest_observation(
    registry: SQLiteRegistry,
    observation_id: str,
    *,
    source_language: str,
) -> dict:
    """Ingest one stored metadata observation; the registry makes it atomic/idempotent."""
    observation = registry.get_collection_observation(observation_id)
    if observation is None:
        raise IngestionError("collection observation does not exist")
    endpoint = registry.get_endpoint(observation["endpoint_id"])
    if endpoint is None:
        raise IngestionError("observation endpoint does not exist")
    normalized_language = normalize_language_tag(source_language)
    supported = endpoint.to_payload().get("supported_languages", [])
    try:
        supported_primary = {normalize_language_tag(item).split("-", 1)[0] for item in supported}
    except IngestionError as exc:
        raise IngestionError("endpoint language declaration is invalid") from exc
    if normalized_language.split("-", 1)[0] not in supported_primary:
        raise IngestionError("source_language is not supported by the registered endpoint")
    article_payload, duplicate_fingerprint = normalize_observation(observation, normalized_language)
    return registry.ingest_article_observation(
        observation_id,
        article_payload,
        duplicate_fingerprint=duplicate_fingerprint,
    )
