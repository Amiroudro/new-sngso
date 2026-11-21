from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from sngso_engine.contracts import ContractValidationError, validate_payload

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts" / "v1"
COMMON = json.loads((CONTRACTS / "common.schema.json").read_text(encoding="utf-8"))
REGISTRY = Registry().with_resource(COMMON["$id"], Resource.from_contents(COMMON))
FORMAT_CHECKER = FormatChecker()


def manifest() -> dict:
    return {
        "schema_version": "1.0.0",
        "manifest_id": "edition-manifest:daily-2026-11-21",
        "edition_id": "edition:daily-2026-11-21",
        "edition_revision": 1,
        "edition_key": "2026-11-21",
        "created_at": "2026-11-21T06:00:00Z",
        "pipeline_run_id": "pipeline-run:daily-2026-11-21",
        "payload_sha256": "sha256:" + "a" * 64,
        "story_bindings": [{
            "story_id": "story:sample-001",
            "story_revision": 3,
            "story_snapshot_sha256": "sha256:" + "b" * 64,
            "learning_objects": [
                {"learning_object_id": "learning:sample-bn", "learning_object_revision": 2, "learning_object_sha256": "sha256:" + "c" * 64, "kind": "headline_summary", "language": "bn"},
                {"learning_object_id": "learning:sample-en", "learning_object_revision": 4, "learning_object_sha256": "sha256:" + "d" * 64, "kind": "headline_summary", "language": "en"}
            ]
        }],
        "source_permissions": [{
            "source_id": "source:publisher-a",
            "endpoint_id": "endpoint:publisher-a-news",
            "grant_id": "grant:publisher-a-republication",
            "operation": "republication"
        }]
    }


def validator() -> Draft202012Validator:
    schema = json.loads((CONTRACTS / "edition-artifact-manifest.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=REGISTRY, format_checker=FORMAT_CHECKER)


def test_edition_artifact_manifest_schema_is_valid_draft_2020_12() -> None:
    assert validator().schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"


def test_valid_edition_artifact_manifest_passes_schema_and_runtime_validation() -> None:
    payload = manifest()
    assert not list(validator().iter_errors(payload))
    validate_payload("edition-artifact-manifest", payload)


@pytest.mark.parametrize("mutation", ["missing_bn", "missing_en", "bad_hash", "bool_revision", "unknown_field", "wrong_operation", "empty_permissions"])
def test_manifest_rejects_incomplete_or_unsafe_provenance(mutation: str) -> None:
    payload = manifest()
    if mutation == "missing_bn":
        payload["story_bindings"][0]["learning_objects"] = [x for x in payload["story_bindings"][0]["learning_objects"] if x["language"] != "bn"]
    elif mutation == "missing_en":
        payload["story_bindings"][0]["learning_objects"] = [x for x in payload["story_bindings"][0]["learning_objects"] if x["language"] != "en"]
    elif mutation == "bad_hash":
        payload["payload_sha256"] = "A" * 64
    elif mutation == "bool_revision":
        payload["edition_revision"] = True
    elif mutation == "unknown_field":
        payload["publication_authorized"] = True
    elif mutation == "wrong_operation":
        payload["source_permissions"][0]["operation"] = "discovery"
    elif mutation == "empty_permissions":
        payload["source_permissions"] = []
    assert list(validator().iter_errors(payload))
    with pytest.raises(ContractValidationError):
        validate_payload("edition-artifact-manifest", payload)


def test_manifest_rejects_duplicate_story_and_learning_object_bindings() -> None:
    payload = manifest()
    payload["story_bindings"].append(dict(payload["story_bindings"][0]))
    assert list(validator().iter_errors(payload))
    payload = manifest()
    payload["story_bindings"][0]["learning_objects"].append(dict(payload["story_bindings"][0]["learning_objects"][0]))
    assert list(validator().iter_errors(payload))


def test_manifest_rejects_unknown_nested_fields() -> None:
    payload = manifest()
    payload["story_bindings"][0]["unreviewed"] = True
    assert list(validator().iter_errors(payload))
    payload = manifest()
    payload["story_bindings"][0]["learning_objects"][0]["reviewed_by"] = "unbound"
    assert list(validator().iter_errors(payload))
