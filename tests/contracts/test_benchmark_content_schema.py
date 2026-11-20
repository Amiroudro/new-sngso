import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sngso_engine.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "editorial-benchmark-content.schema.json"


def _content():
    return {
        "schema_version": "1.0.0",
        "items": [{
            "item_id": "summary-bn-001",
            "task_type": "summary",
            "input": {"source_text": "একটি নমুনা প্রতিবেদন"},
            "expected_output": "প্রতিবেদনের সংক্ষিপ্ত ও নির্ভুল সারাংশ",
            "scoring_criteria": ["মূল তথ্য অক্ষুণ্ণ", "অসমর্থিত তথ্য যোগ নয়"],
        }],
    }


def test_benchmark_content_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_structured_bangla_benchmark_content_passes_contract():
    validate_payload("editorial-benchmark-content", _content())


def test_benchmark_content_requires_item_identity_and_scoring_criteria():
    content = _content()
    content["items"][0].pop("scoring_criteria")
    try:
        validate_payload("editorial-benchmark-content", content)
    except ValueError:
        pass
    else:
        raise AssertionError("benchmark items without scoring criteria must fail")
