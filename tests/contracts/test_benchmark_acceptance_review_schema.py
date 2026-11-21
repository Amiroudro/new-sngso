import json
from pathlib import Path
from jsonschema import Draft202012Validator
from sngso_engine.contracts import validate_payload

ROOT=Path(__file__).resolve().parents[2]
SCHEMA_PATH=ROOT/"contracts"/"v1"/"editorial-benchmark-acceptance-review.schema.json"

def test_acceptance_review_schema_is_valid_draft_2020_12():
    schema=json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"]=="https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)

def test_schema_requires_three_separate_review_domains():
    schema=json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert set(schema["properties"]["domains"]["required"])=={
        "statistical_validity","editorial_validity","operational_security"
    }

def test_schema_does_not_define_production_approval_field():
    schema=json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert "production_ready" not in schema["properties"]
    assert "authorization_granted" not in schema["properties"]
