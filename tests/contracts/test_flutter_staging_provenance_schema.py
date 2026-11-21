import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sngso_engine.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "flutter-staging-provenance.schema.json"


def _manifest():
    return {
        "schema_version": "1.0.0",
        "target_contract": "flutter-edition-v1",
        "edition_id": "edition:staging-one",
        "edition_revision": 1,
        "edition_date": "2026-11-20",
        "payload_sha256": "sha256:" + "a" * 64,
        "story_bindings": [{
            "story_id": "story:one",
            "story_revision": 1,
            "snapshot_sha256": "sha256:" + "b" * 64,
            "fields": {
                "headline": {
                    "learning_object_id": "learning:headline",
                    "revision": 1,
                    "sha256": "sha256:" + "c" * 64,
                },
                "summary": {
                    "learning_object_id": "learning:summary",
                    "revision": 2,
                    "sha256": "sha256:" + "d" * 64,
                },
            },
        }],
        "source_permissions": [{
            "source_id": "source:one",
            "endpoint_id": "endpoint:one",
            "grant_id": "grant:republication",
            "operation": "republication",
        }],
    }


def test_flutter_staging_provenance_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_complete_flutter_staging_manifest_passes_versioned_contract():
    validate_payload("flutter-staging-provenance", _manifest())


@pytest.mark.parametrize("mutate", [
    lambda payload: payload.update({"publication_authorized": True}),
    lambda payload: payload["story_bindings"][0].update({"unreviewed": True}),
    lambda payload: payload["story_bindings"][0]["fields"].update({"category": {}}),
    lambda payload: payload["story_bindings"][0]["fields"]["headline"].update({"approved": True}),
    lambda payload: payload["source_permissions"][0].update({"scope": "all"}),
    lambda payload: payload["source_permissions"][0].update({"operation": "full_text_storage"}),
])
def test_flutter_staging_manifest_rejects_unknown_or_unsupported_fields(mutate):
    payload = _manifest()
    mutate(payload)
    with pytest.raises(ValueError):
        validate_payload("flutter-staging-provenance", payload)


@pytest.mark.parametrize("path,value", [
    (("edition_revision",), True),
    (("story_bindings", 0, "story_revision"), False),
    (("story_bindings", 0, "fields", "headline", "revision"), 0),
    (("story_bindings", 0, "fields", "summary", "sha256"), "A" * 64),
    (("source_permissions", 0, "grant_id"), ""),
])
def test_flutter_staging_manifest_rejects_invalid_nested_values(path, value):
    payload = _manifest()
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_payload("flutter-staging-provenance", payload)


def test_flutter_staging_manifest_rejects_duplicate_permission_records():
    payload = _manifest()
    payload["source_permissions"].append(deepcopy(payload["source_permissions"][0]))
    with pytest.raises(ValueError):
        validate_payload("flutter-staging-provenance", payload)
