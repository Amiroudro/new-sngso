from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "v1"
COMMON = json.loads((CONTRACTS / "common.schema.json").read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(
    COMMON["$id"], Resource.from_contents(COMMON)
)
FORMAT_CHECKER = FormatChecker()


def load_schema(name: str) -> dict:
    return json.loads((CONTRACTS / f"{name}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    ["source-permission", "article", "story", "claim", "evidence", "edition"],
)
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize(
    ("name", "instance"),
    [
        (
            "source-permission",
            {
                "schema_version": "1.0.0",
                "id": "permission:publisher-a-2026",
                "source_id": "source:publisher-a",
                "status": "approved",
                "operations": {
                    operation: {"allowed": operation == "discovery", "status": "approved" if operation == "discovery" else "denied"}
                    for operation in (
                        "discovery", "metadata", "excerpt_storage",
                        "full_text_storage", "translation", "image_storage",
                        "republication",
                    )
                },
                "basis": "Documented permission review",
                "effective_from": "2026-11-19T00:00:00Z",
                "reviewed_at": "2026-11-19T00:00:00Z",
            },
        ),
        (
            "article",
            {
                "schema_version": "1.0.0",
                "id": "article:sample-001",
                "source_id": "source:publisher-a",
                "canonical_uri": "https://example.org/news/1",
                "title": "A sample report",
                "source_language": "en",
                "discovered_at": "2026-11-19T00:00:00Z",
                "record_status": "active",
            },
        ),
        (
            "story",
            {
                "schema_version": "1.0.0",
                "id": "story:sample-001",
                "canonical_title": "A developing public story",
                "status": "developing",
                "created_at": "2026-11-19T00:00:00Z",
                "updated_at": "2026-11-19T00:00:00Z",
                "article_ids": ["article:sample-001"],
            },
        ),
        (
            "claim",
            {
                "schema_version": "1.0.0",
                "id": "claim:sample-001",
                "story_id": "story:sample-001",
                "text": "The authority issued a public notice.",
                "language": "en",
                "assessment": "unassessed",
                "provenance": [{
                    "source_id": "source:publisher-a",
                    "observed_at": "2026-11-19T00:00:00Z",
                    "method": "fixture",
                    "method_version": "1",
                }],
                "created_at": "2026-11-19T00:00:00Z",
            },
        ),
        (
            "evidence",
            {
                "schema_version": "1.0.0",
                "id": "evidence:sample-001",
                "claim_id": "claim:sample-001",
                "source_id": "source:publisher-a",
                "relationship": "supports",
                "locator": {"uri": "https://example.org/news/1"},
                "assessed_at": "2026-11-19T00:00:00Z",
            },
        ),
        (
            "edition",
            {
                "schema_version": "1.0.0",
                "id": "edition:2026-11-19-am",
                "edition_key": "2026-11-19-am",
                "created_at": "2026-11-19T00:00:00Z",
                "status": "draft",
                "content_hash": "sha256:" + "a" * 64,
                "pipeline_run_id": "run:sample-001",
                "story_ids": ["story:sample-001"],
                "language_variants": [
                    {"language": "en", "status": "draft", "text": "Sample briefing"},
                    {"language": "bn", "status": "review_required"},
                ],
            },
        ),
    ],
)
def test_minimal_valid_contract_instances(name: str, instance: dict) -> None:
    schema = load_schema(name)
    validator = Draft202012Validator(
        schema, registry=REGISTRY, format_checker=FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    assert not errors, "\n".join(error.message for error in errors)


def test_permission_contract_rejects_missing_operation() -> None:
    instance = {
        "schema_version": "1.0.0",
        "id": "permission:sample",
        "source_id": "source:sample",
        "status": "pending",
        "operations": {"discovery": {"allowed": False, "status": "pending"}},
        "basis": "Not reviewed",
        "effective_from": "2026-11-19T00:00:00Z",
        "reviewed_at": "2026-11-19T00:00:00Z",
    }
    validator = Draft202012Validator(
        load_schema("source-permission"),
        registry=REGISTRY,
        format_checker=FORMAT_CHECKER,
    )
    assert list(validator.iter_errors(instance))


def test_article_contract_rejects_unknown_fields() -> None:
    instance = {
        "schema_version": "1.0.0",
        "id": "article:sample-002",
        "source_id": "source:sample",
        "canonical_uri": "https://example.org/a",
        "title": "Sample",
        "source_language": "en",
        "discovered_at": "2026-11-19T00:00:00Z",
        "record_status": "active",
        "unreviewed_full_text": "This field must not silently enter the contract",
    }
    validator = Draft202012Validator(
        load_schema("article"),
        registry=REGISTRY,
        format_checker=FORMAT_CHECKER,
    )
    assert list(validator.iter_errors(instance))
