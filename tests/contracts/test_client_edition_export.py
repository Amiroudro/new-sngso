from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from sngso_engine.client_edition import ClientEditionExportError, build_client_edition

ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCHEMA = ROOT / "contracts" / "client-edition" / "v1" / "schema.json"
FIXTURE = ROOT / "contracts" / "client-edition" / "v1" / "fixtures" / "published-edition.sample.json"
SCHEMA = json.loads(CLIENT_SCHEMA.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def inputs():
    edition = {
        "schema_version": "1.0.0",
        "id": "edition:2026-11-27-am",
        "edition_key": "2026-11-27-am",
        "created_at": "2026-11-27T07:00:00Z",
        "status": "published",
        "content_hash": "sha256:" + "a" * 64,
        "pipeline_run_id": "run:2026-11-27-am",
        "story_ids": ["story:water"],
        "language_variants": [
            {"language": "bn", "status": "approved", "text": "সকালের সংবাদ"},
            {"language": "en", "status": "approved", "text": "Morning briefing"},
        ],
        "published_at": "2026-11-27T08:00:00Z",
    }
    story = {
        "schema_version": "1.0.0",
        "id": "story:water",
        "canonical_title": "Water supply update",
        "status": "developing",
        "topic_tags": ["bangladesh", "public-services"],
        "created_at": "2026-11-27T06:00:00Z",
        "updated_at": "2026-11-27T07:00:00Z",
        "article_ids": ["article:water-a", "article:water-b"],
    }
    article = {
        "schema_version": "1.0.0",
        "id": "article:water-a",
        "source_id": "source:publisher-a",
        "canonical_uri": "https://example.org/water",
        "title": "Water supply notice",
        "source_language": "en",
        "published_at": "2026-11-27T07:30:00Z",
        "discovered_at": "2026-11-27T07:35:00Z",
        "record_status": "active",
    }
    permission = {
        "schema_version": "1.0.0",
        "id": "permission:publisher-a",
        "source_id": "source:publisher-a",
        "status": "approved",
        "operations": {
            name: {
                "allowed": name == "metadata",
                "status": "approved" if name == "metadata" else "denied",
            }
            for name in (
                "discovery",
                "metadata",
                "excerpt_storage",
                "full_text_storage",
                "translation",
                "image_storage",
                "republication",
            )
        },
        "basis": "Synthetic test permission record",
        "effective_from": "2026-11-01T00:00:00Z",
        "reviewed_at": "2026-11-01T00:00:00Z",
    }
    learning = []
    for language, brief, detailed in (
        ("bn", "সংক্ষিপ্ত সারাংশ", "বিস্তারিত সারাংশ"),
        ("en", "Brief summary", "Detailed summary"),
    ):
        for kind, value in (("brief_summary", brief), ("detailed_summary", detailed)):
            learning.append(
                {
                    "schema_version": "1.0.0",
                    "id": f"learning:{language}-{kind}",
                    "story_id": "story:water",
                    "kind": kind,
                    "language": language,
                    "status": "approved",
                    "content": {"text": value},
                    "claim_ids": [],
                    "created_at": "2026-11-27T07:00:00Z",
                    "reviewed_at": "2026-11-27T07:30:00Z",
                }
            )
    return {
        "edition": edition,
        "stories": {"story:water": story},
        "articles": {"article:water-a": article},
        "source_metadata": {
            "source:publisher-a": {"id": "source:publisher-a", "name": "Publisher A"}
        },
        "source_permissions": {"source:publisher-a": permission},
        "learning_objects": learning,
        "primary_article_by_story": {"story:water": "article:water-a"},
        "permission_evaluated_at": datetime(2026, 11, 27, 8, 5, tzinfo=timezone.utc),
        "titles_by_story": {
            "story:water": {
                "bn": "পানি সরবরাহের হালনাগাদ",
                "en": "Water supply update",
            }
        },
    }


def export(**overrides):
    values = inputs()
    values.update(overrides)
    return build_client_edition(**values)


def test_exports_ordered_published_projection_and_valid_hash():
    result = export()
    assert result["edition"]["status"] == "published"
    assert result["stories"][0]["id"] == "story:water"
    assert result["stories"][0]["source"]["id"] == "source:publisher-a"
    assert result["content_hash"].startswith("sha256:")
    assert not list(VALIDATOR.iter_errors(result))


def test_primary_article_must_be_explicitly_linked_to_story():
    with pytest.raises(ClientEditionExportError, match="not linked"):
        export(primary_article_by_story={"story:water": "article:not-linked"})



def test_exports_explicit_permission_checked_multiple_sources_in_editorial_order():
    values = inputs()
    values["articles"]["article:water-b"] = {
        **copy.deepcopy(values["articles"]["article:water-a"]),
        "id": "article:water-b",
        "source_id": "source:publisher-b",
        "canonical_uri": "https://example.org/water-followup",
        "published_at": "2026-11-27T07:45:00Z",
    }
    values["source_metadata"]["source:publisher-b"] = {
        "id": "source:publisher-b",
        "name": "Publisher B",
    }
    secondary_permission = copy.deepcopy(values["source_permissions"]["source:publisher-a"])
    secondary_permission["id"] = "permission:publisher-b"
    secondary_permission["source_id"] = "source:publisher-b"
    values["source_permissions"]["source:publisher-b"] = secondary_permission
    values["source_article_ids_by_story"] = {
        "story:water": ["article:water-a", "article:water-b"]
    }

    result = build_client_edition(**values)
    story = result["stories"][0]
    assert story["source"]["id"] == "source:publisher-a"
    assert story["additional_sources"] == [{
        "id": "source:publisher-b",
        "name": "Publisher B",
        "url": "https://example.org/water-followup",
        "published_at": "2026-11-27T07:45:00Z",
    }]
    assert not list(VALIDATOR.iter_errors(result))


def test_rejects_secondary_source_without_current_metadata_permission():
    values = inputs()
    values["articles"]["article:water-b"] = {
        **copy.deepcopy(values["articles"]["article:water-a"]),
        "id": "article:water-b",
        "source_id": "source:publisher-b",
        "canonical_uri": "https://example.org/water-followup",
    }
    values["source_metadata"]["source:publisher-b"] = {
        "id": "source:publisher-b",
        "name": "Publisher B",
    }
    secondary_permission = copy.deepcopy(values["source_permissions"]["source:publisher-a"])
    secondary_permission["id"] = "permission:publisher-b"
    secondary_permission["source_id"] = "source:publisher-b"
    secondary_permission["operations"]["metadata"] = {
        "allowed": False,
        "status": "denied",
    }
    values["source_permissions"]["source:publisher-b"] = secondary_permission
    values["source_article_ids_by_story"] = {
        "story:water": ["article:water-a", "article:water-b"]
    }

    with pytest.raises(ClientEditionExportError, match="metadata permission denied"):
        build_client_edition(**values)


def test_rejects_duplicate_source_article_selection():
    with pytest.raises(ClientEditionExportError, match="unique, non-empty"):
        export(source_article_ids_by_story={
            "story:water": ["article:water-a", "article:water-a"]
        })

def test_export_rejects_unpublished_edition():
    values = inputs()
    values["edition"]["status"] = "draft"
    with pytest.raises(ClientEditionExportError, match="only published"):
        build_client_edition(**values)


def test_export_rejects_missing_metadata_permission():
    with pytest.raises(ClientEditionExportError, match="no current source permission"):
        export(source_permissions={})


def test_export_rejects_denied_metadata_permission():
    values = inputs()
    values["source_permissions"]["source:publisher-a"]["operations"]["metadata"] = {
        "allowed": False,
        "status": "denied",
    }
    with pytest.raises(ClientEditionExportError, match="metadata permission denied"):
        build_client_edition(**values)


def test_export_rejects_permission_expired_before_export_time():
    values = inputs()
    values["source_permissions"]["source:publisher-a"]["effective_until"] = (
        "2026-11-27T08:02:00Z"
    )
    with pytest.raises(ClientEditionExportError, match="permission_expired"):
        build_client_edition(**values)


def test_export_requires_timezone_aware_permission_evaluation_time():
    values = inputs()
    values["permission_evaluated_at"] = datetime(2026, 11, 27, 8, 5)
    with pytest.raises(ClientEditionExportError, match="must include a timezone"):
        build_client_edition(**values)


def test_export_rejects_unapproved_language_variant():
    values = inputs()
    values["edition"]["language_variants"][0]["status"] = "review_required"
    with pytest.raises(ClientEditionExportError, match="every exported language"):
        build_client_edition(**values)


def test_export_rejects_missing_approved_summary():
    values = inputs()
    values["learning_objects"] = [
        item
        for item in values["learning_objects"]
        if not (item["language"] == "bn" and item["kind"] == "detailed_summary")
    ]
    with pytest.raises(ClientEditionExportError, match="missing approved brief"):
        build_client_edition(**values)


def test_export_preserves_edition_story_order():
    values = inputs()
    second = copy.deepcopy(values["stories"]["story:water"])
    second.update(
        {
            "id": "story:second",
            "article_ids": ["article:water-a"],
        }
    )
    values["edition"]["story_ids"] = ["story:second", "story:water"]
    values["stories"]["story:second"] = second
    values["primary_article_by_story"] = {
        "story:second": "article:water-a",
        "story:water": "article:water-a",
    }
    values["titles_by_story"]["story:second"] = {
        "bn": "দ্বিতীয় শিরোনাম",
        "en": "Second headline",
    }
    second_learning = []
    for item in values["learning_objects"]:
        duplicate = copy.deepcopy(item)
        duplicate["id"] = duplicate["id"].replace("learning:", "learning:second-")
        duplicate["story_id"] = "story:second"
        second_learning.append(duplicate)
    values["learning_objects"].extend(second_learning)

    result = build_client_edition(**values)
    assert [item["id"] for item in result["stories"]] == ["story:second", "story:water"]


def test_fixture_is_schema_valid_and_hash_is_reproducible():
    from sngso_engine.canonical import sha256_fingerprint

    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert not list(VALIDATOR.iter_errors(fixture))
    without_hash = {key: value for key, value in fixture.items() if key != "content_hash"}
    assert fixture["content_hash"] == sha256_fingerprint(without_hash)


def test_fixture_is_explicitly_synthetic():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["stories"][0]["source"]["url"].startswith("https://example.org/")
    assert "কাল্পনিক" in fixture["stories"][0]["summaries"][0]["brief"]
