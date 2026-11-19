from __future__ import annotations

import sqlite3

import pytest

from sngso_engine.collection import CollectionObservation
from sngso_engine.ingestion import (
    IngestionError,
    canonicalize_article_url,
    ingest_observation,
    normalize_language_tag,
    normalize_observation,
    normalize_timestamp,
)
from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


STAMP = "2026-11-20T01:00:00Z"
PUBLISHED = "2026-11-19T18:30:00+06:00"


def source_record(name: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "id": f"source:{name}",
        "publisher_name": f"Publisher {name}",
        "publisher_type": "newspaper",
        "canonical_homepage": f"https://{name}.example.org/",
        "domains": [f"{name}.example.org"],
        "country_code": "BD",
        "languages": ["bn", "en"],
        "status": "active",
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def endpoint_record(name: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "id": f"endpoint:{name}-feed",
        "source_id": f"source:{name}",
        "endpoint_uri": f"https://{name}.example.org/feed.xml",
        "endpoint_type": "rss",
        "status": "active",
        "supported_languages": ["bn"],
        "created_at": STAMP,
        "updated_at": STAMP,
    }


def evidence_record(name: str) -> dict:
    return {
        "schema_version": "1.0.0",
        "id": f"permission-evidence:{name}",
        "source_id": f"source:{name}",
        "evidence_type": "written_grant",
        "evidence_uri": f"https://{name}.example.org/permission",
        "captured_at": STAMP,
        "content_fingerprint": "sha256:" + "a" * 64,
        "scope_description": "Fixture approval for metadata collection.",
        "review_status": "verified",
        "reviewed_at": STAMP,
        "reviewed_by": "fixture-reviewer",
    }


def grant_record(name: str) -> dict:
    evidence_id = f"permission-evidence:{name}"
    operations = {}
    for operation in (
        "discovery", "metadata", "excerpt_storage", "full_text_storage",
        "translation", "image_storage", "republication",
    ):
        allowed = operation == "metadata"
        operations[operation] = {
            "allowed": allowed,
            "status": "approved" if allowed else "denied",
            "conditions": [],
            **({"evidence_ids": [evidence_id]} if allowed else {}),
        }
    return {
        "schema_version": "1.0.0",
        "id": f"permission:{name}-metadata",
        "source_id": f"source:{name}",
        "endpoint_ids": [f"endpoint:{name}-feed"],
        "status": "approved",
        "operations": operations,
        "evidence_ids": [evidence_id],
        "basis": "Reviewed fixture permission for metadata only.",
        "effective_from": "2026-11-19T00:00:00Z",
        "effective_until": "2026-12-19T00:00:00Z",
        "created_at": STAMP,
        "reviewed_at": STAMP,
        "reviewed_by": "fixture-reviewer",
    }


def add_publisher(registry: SQLiteRegistry, name: str) -> None:
    registry.add_source(source_record(name))
    registry.add_endpoint(endpoint_record(name))
    registry.add_permission_evidence(evidence_record(name))
    registry.add_permission_grant(grant_record(name))


def add_observation(
    registry: SQLiteRegistry,
    name: str,
    observation_id: str,
    *,
    path: str = "/news/one?utm_source=feed&id=7",
    title: str = "নতুন অর্থনৈতিক প্রতিবেদন প্রকাশ",
    published_at: str | None = PUBLISHED,
    observed_at: str = STAMP,
) -> None:
    metadata = {"title": title}
    if published_at is not None:
        metadata["published_at"] = published_at
    registry.append_many((CollectionObservation(
        id=observation_id,
        source_id=f"source:{name}",
        endpoint_id=f"endpoint:{name}-feed",
        grant_id=f"permission:{name}-metadata",
        grant_revision=1,
        operation="metadata",
        connector_name="fixture-rss",
        connector_version="1.0.0",
        observed_at=observed_at,
        locator=f"https://{name}.example.org{path}",
        final_locator=None,
        content_fingerprint=None,
        response_status=200,
        content_type="application/rss+xml",
        metadata=metadata,
        permission_reason="approved",
        http_method="GET",
    ),))


def test_article_url_canonicalization_removes_only_known_tracking_parameters():
    assert canonicalize_article_url(
        "HTTPS://Publisher-A.Example.org:443/news/one?utm_source=x&id=7&fbclid=abc&tag=a&tag=b"
    ) == "https://publisher-a.example.org/news/one?id=7&tag=a&tag=b"


def test_article_url_canonicalization_removes_dot_segments():
    assert canonicalize_article_url(
        "https://publisher-a.example.org/news/section/../one"
    ) == "https://publisher-a.example.org/news/one"


def test_encoded_unreserved_dot_segments_are_normalized():
    assert canonicalize_article_url(
        "https://publisher-a.example.org/news/%2e%2e/world"
    ) == "https://publisher-a.example.org/world"


def test_article_url_canonicalization_preserves_unknown_query_and_path_case():
    assert canonicalize_article_url(
        "https://publisher-a.example.org/News/One?ref=homepage"
    ) == "https://publisher-a.example.org/News/One?ref=homepage"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "https://user:pass@publisher-a.example.org/news",
    "https://publisher-a.example.org/news#section",
    "https://publisher-a.example.org/news%",
    "https://publisher-a.example.org:0443/news",
    "https://publisher-a.example.org/news?x=1#",
    "https://127.0.0.1/news",
    "https://publisher-a.example.org/" + "?x=1&" * 130,
])
def test_article_url_canonicalization_rejects_unsafe_or_ambiguous_urls(url):
    with pytest.raises(IngestionError):
        canonicalize_article_url(url)


def test_timestamp_and_language_normalization_are_explicit():
    assert normalize_timestamp("2026-11-20T00:30:00+06:00") == "2026-11-19T18:30:00Z"
    assert normalize_language_tag("BN-bd") == "bn-BD"
    with pytest.raises(IngestionError, match="timezone"):
        normalize_timestamp("2026-11-20T00:30:00")


def test_normalization_keeps_observation_values_unchanged():
    observation = {
        "operation": "metadata",
        "source_id": "source:publisher-a",
        "grant_id": "permission:publisher-a-metadata",
        "id": "observation:fixture",
        "connector_name": "fixture-rss",
        "connector_version": "1.0.0",
        "observed_at": STAMP,
        "locator": "https://publisher-a.example.org/news/one?utm_source=feed",
        "final_locator": None,
        "content_fingerprint": None,
        "metadata": {
            "title": "  নতুন   অর্থনৈতিক প্রতিবেদন প্রকাশ  ",
            "published_at": PUBLISHED,
        },
    }
    payload, fingerprint = normalize_observation(observation, "BN-bd")
    assert payload["title"] == "নতুন অর্থনৈতিক প্রতিবেদন প্রকাশ"
    assert payload["source_language"] == "bn-BD"
    assert payload["published_at"] == "2026-11-19T12:30:00Z"
    assert payload["canonical_uri"] == "https://publisher-a.example.org/news/one"
    assert fingerprint.startswith("sha256:")
    assert observation["locator"].endswith("?utm_source=feed")
    assert observation["metadata"]["title"].startswith("  ")


def test_repeated_observation_ingestion_is_idempotent_and_preserves_originals():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        original = registry.get_collection_observation("observation:first")

        audit_count = len(registry.audit_events())
        first = ingest_observation(
            registry, "observation:first", source_language="bn-BD"
        )
        second = ingest_observation(
            registry, "observation:first", source_language="BN-bd"
        )

        assert first == second
        assert len(registry.audit_events()) == audit_count + 1
        assert first["outcome"] == "created"
        assert first["decision_revision"] == 1
        assert registry.get_collection_observation("observation:first") == original
        assert len(registry.ingestion_decision_history("observation:first")) == 1
        article = registry.get_domain_record("article", first["article_id"])
        assert article is not None
        assert article.source_language == "bn-BD"
        assert registry.verify_audit_chain()


def test_newer_observation_refreshes_article_metadata_with_revision_history():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        add_observation(
            registry, "publisher-a", "observation:newer",
            title="নতুন সংশোধিত শিরোনাম",
            observed_at="2026-11-20T02:00:00Z",
        )
        first = ingest_observation(registry, "observation:first", source_language="bn")
        second = ingest_observation(registry, "observation:newer", source_language="bn")
        article = registry.get_domain_record("article", first["article_id"])
        assert second["outcome"] == "reused"
        assert second["article_id"] == first["article_id"]
        assert article.to_payload()["title"] == "নতুন সংশোধিত শিরোনাম"
        assert article.to_payload()["provenance"]["observation_id"] == "observation:newer"
        revisions = registry._connection.execute(
            """SELECT revision FROM domain_record_revisions
            WHERE record_type = 'article' AND record_id = ?
            ORDER BY revision""",
            (first["article_id"],),
        ).fetchall()
        assert [row["revision"] for row in revisions] == [1, 2]


def test_older_observation_does_not_overwrite_newer_article_metadata():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(
            registry, "publisher-a", "observation:newer",
            title="সর্বশেষ শিরোনাম",
            observed_at="2026-11-20T02:00:00Z",
        )
        add_observation(
            registry, "publisher-a", "observation:older",
            title="পুরোনো শিরোনাম",
            observed_at="2026-11-20T01:00:00Z",
        )
        first = ingest_observation(registry, "observation:newer", source_language="bn")
        second = ingest_observation(registry, "observation:older", source_language="bn")
        article = registry.get_domain_record("article", first["article_id"])
        assert second["outcome"] == "reused"
        assert article.to_payload()["title"] == "সর্বশেষ শিরোনাম"
        assert article.to_payload()["provenance"]["observation_id"] == "observation:newer"


def test_tracking_parameter_variation_reuses_same_source_article():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        add_observation(
            registry, "publisher-a", "observation:second",
            path="/news/one?id=7&utm_campaign=daily",
        )
        first = ingest_observation(registry, "observation:first", source_language="bn")
        second = ingest_observation(registry, "observation:second", source_language="bn")

        assert first["outcome"] == "created"
        assert second["outcome"] == "reused"
        assert second["article_id"] == first["article_id"]
        assert len(registry.ingestion_decision_history("observation:second")) == 1


def test_cross_publisher_match_is_a_candidate_not_an_automatic_merge():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_publisher(registry, "publisher-b")
        add_observation(registry, "publisher-a", "observation:a")
        add_observation(
            registry, "publisher-b", "observation:b", path="/world/report"
        )
        first = ingest_observation(registry, "observation:a", source_language="bn")
        second = ingest_observation(registry, "observation:b", source_language="bn")

        assert second["outcome"] == "possible_duplicate"
        assert second["duplicate_of_article_id"] == first["article_id"]
        assert second["article_id"] != first["article_id"]
        assert registry.get_domain_record("article", second["article_id"]) is not None


def test_duplicate_decision_can_be_reversed_without_deleting_articles():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_publisher(registry, "publisher-b")
        add_observation(registry, "publisher-a", "observation:a")
        add_observation(registry, "publisher-b", "observation:b", path="/world/report")
        first = ingest_observation(registry, "observation:a", source_language="bn")
        candidate = ingest_observation(registry, "observation:b", source_language="bn")

        confirmed = registry.resolve_ingestion_duplicate(
            "observation:b", outcome="confirmed_duplicate", expected_revision=1,
            actor="editor", reason="Publisher B republishes the same wire report.",
        )
        distinct = registry.resolve_ingestion_duplicate(
            "observation:b", outcome="distinct", expected_revision=2,
            actor="editor", reason="Review found materially independent reporting.",
        )
        reconfirmed = registry.resolve_ingestion_duplicate(
            "observation:b", outcome="confirmed_duplicate", expected_revision=3,
            actor="senior-editor", reason="A second review confirmed the syndication.",
        )

        assert candidate["outcome"] == "possible_duplicate"
        assert confirmed["duplicate_of_article_id"] == first["article_id"]
        assert distinct["duplicate_of_article_id"] is None
        assert reconfirmed["duplicate_of_article_id"] == first["article_id"]
        assert [item["decision_revision"] for item in registry.ingestion_decision_history("observation:b")] == [1, 2, 3, 4]
        assert registry.get_domain_record("article", first["article_id"]) is not None
        assert registry.get_domain_record("article", candidate["article_id"]) is not None
        assert registry.verify_audit_chain()


def test_stale_duplicate_resolution_is_rejected():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_publisher(registry, "publisher-b")
        add_observation(registry, "publisher-a", "observation:a")
        add_observation(registry, "publisher-b", "observation:b", path="/world/report")
        ingest_observation(registry, "observation:a", source_language="bn")
        ingest_observation(registry, "observation:b", source_language="bn")
        with pytest.raises(RegistryIntegrityError, match="revision conflict"):
            registry.resolve_ingestion_duplicate(
                "observation:b", outcome="distinct", expected_revision=9,
                actor="editor", reason="Stale review.",
            )


def test_article_canonical_identity_cannot_be_rewritten():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        decision = ingest_observation(registry, "observation:first", source_language="bn")
        article = registry.get_domain_record("article", decision["article_id"])
        payload = article.to_payload()
        payload["canonical_uri"] = "https://publisher-a.example.org/changed"
        with pytest.raises(RegistryIntegrityError, match="identity links cannot be rewritten"):
            registry.revise_domain_record(
                "article", article.id, payload, expected_revision=1,
                actor="editor", reason="Attempted identity mutation.",
            )


def test_revoked_or_revised_permission_blocks_new_ingestion():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        registry.transition_permission_grant(
            "permission:publisher-a-metadata", "revoked",
            actor="rights-reviewer", reason="Publisher withdrew permission.",
        )
        with pytest.raises(RegistryIntegrityError, match="grant changed"):
            ingest_observation(registry, "observation:first", source_language="bn")
        assert registry.get_ingestion_decision("observation:first") is None
        assert registry.get_domain_record(
            "article",
            normalize_observation(
                registry.get_collection_observation("observation:first"), "bn"
            )[0]["id"],
        ) is None


def test_ingestion_rejects_language_not_declared_by_endpoint():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        with pytest.raises(IngestionError, match="not supported"):
            ingest_observation(registry, "observation:first", source_language="en")
        assert registry.get_ingestion_decision("observation:first") is None


def test_duplicate_candidate_requires_publication_timestamp():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_publisher(registry, "publisher-b")
        add_observation(registry, "publisher-a", "observation:a", published_at=None)
        add_observation(registry, "publisher-b", "observation:b", path="/world/report", published_at=None)
        first = ingest_observation(registry, "observation:a", source_language="bn")
        second = ingest_observation(registry, "observation:b", source_language="bn")
        assert first["outcome"] == "created"
        assert second["outcome"] == "created"
        assert second["fingerprint"] is None


def test_registry_rejects_tampered_article_provenance():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        observation = registry.get_collection_observation("observation:first")
        payload, fingerprint = normalize_observation(observation, "bn")
        payload["provenance"]["observation_id"] = "observation:other"
        with pytest.raises(RegistryIntegrityError, match="do not match normalized source observation"):
            registry.ingest_article_observation(
                "observation:first", payload, duplicate_fingerprint=fingerprint
            )
        assert registry.get_ingestion_decision("observation:first") is None


def test_registry_rejects_article_identity_not_bound_to_observation():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        observation = registry.get_collection_observation("observation:first")
        payload, fingerprint = normalize_observation(observation, "bn")
        payload["canonical_uri"] = "https://publisher-a.example.org/another-page"
        with pytest.raises(RegistryIntegrityError, match="do not match normalized source observation"):
            registry.ingest_article_observation(
                "observation:first", payload, duplicate_fingerprint=fingerprint
            )
        assert registry.get_ingestion_decision("observation:first") is None
        assert registry.get_domain_record("article", payload["id"]) is None


def test_invalid_optional_metadata_types_are_rejected_cleanly():
    observation = {
        "operation": "metadata",
        "source_id": "source:publisher-a",
        "grant_id": "permission:publisher-a-metadata",
        "observed_at": STAMP,
        "locator": "https://publisher-a.example.org/news/one",
        "metadata": {"title": "A title", "byline": 123},
    }
    with pytest.raises(IngestionError, match="byline must be text"):
        normalize_observation(observation, "bn")


def test_discovery_observation_cannot_be_ingested():
    observation = {
        "operation": "discovery",
        "source_id": "source:publisher-a",
        "grant_id": "permission:publisher-a-metadata",
        "observed_at": STAMP,
        "locator": "https://publisher-a.example.org/news/one",
        "metadata": {},
    }
    with pytest.raises(IngestionError, match="metadata"):
        normalize_observation(observation, "bn")


def test_ingestion_transaction_rolls_back_article_and_decision_on_audit_failure(monkeypatch):
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")

        def fail_audit(*args, **kwargs):
            raise RuntimeError("simulated audit write failure")

        monkeypatch.setattr(registry, "_append_audit", fail_audit)
        observation = registry.get_collection_observation("observation:first")
        payload, fingerprint = normalize_observation(observation, "bn")
        with pytest.raises(RuntimeError, match="audit write failure"):
            registry.ingest_article_observation(
                "observation:first", payload, duplicate_fingerprint=fingerprint
            )
        assert registry.get_ingestion_decision("observation:first") is None
        assert registry.ingestion_decision_history("observation:first") == []
        assert registry.get_domain_record("article", payload["id"]) is None
        assert registry._connection.in_transaction is False


def test_two_registry_instances_ingest_one_observation_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    database = tmp_path / "ingestion-race.sqlite"
    first = SQLiteRegistry(database)
    try:
        add_publisher(first, "publisher-a")
        add_observation(first, "publisher-a", "observation:first")
        second = SQLiteRegistry(database)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda registry: ingest_observation(
                        registry, "observation:first", source_language="bn"
                    ),
                    (first, second),
                ))
            assert results[0] == results[1]
            assert len(first.ingestion_decision_history("observation:first")) == 1
            assert first.verify_audit_chain()
        finally:
            second.close()
    finally:
        first.close()


def test_ingestion_decision_history_is_append_only():
    with SQLiteRegistry() as registry:
        add_publisher(registry, "publisher-a")
        add_observation(registry, "publisher-a", "observation:first")
        decision = ingest_observation(registry, "observation:first", source_language="bn")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "UPDATE ingestion_decisions SET reason = 'tampered' WHERE decision_id = ?",
                (decision["decision_id"],),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            registry._connection.execute(
                "DELETE FROM ingestion_decisions WHERE decision_id = ?",
                (decision["decision_id"],),
            )
