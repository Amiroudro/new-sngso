import json
from pathlib import Path

from jsonschema import Draft202012Validator
from sngso_engine.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "editorial-benchmark-scoring.schema.json"


def _score():
    return {
        "schema_version": "1.0.0",
        "benchmark_id": "benchmark:summary-bn-v1",
        "benchmark_fingerprint": "sha256:" + "a" * 64,
        "item_id": "summary-bn-001",
        "task_type": "summary",
        "language": "bn",
        "evaluator_id": "reviewer:bn-01",
        "evaluated_at": "2026-11-20T04:00:00Z",
        "scale": {"minimum": 0, "maximum": 4},
        "criterion_scores": [{
            "criterion_id": "factual_fidelity",
            "weight": 1.0,
            "score": 4,
            "anchor": "All material facts and attribution are preserved.",
            "rationale": "The evaluation cites the source and confirms every material claim.",
            "evidence_references": ["source:article-1#paragraph-2"]
        }]
    }


def test_scoring_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_complete_score_sheet_passes_contract():
    validate_payload("editorial-benchmark-scoring", _score())


def test_critical_failure_requires_reason():
    payload = _score()
    payload["critical_failure"] = True
    try:
        validate_payload("editorial-benchmark-scoring", payload)
    except ValueError:
        pass
    else:
        raise AssertionError("critical failure must carry a reason")
