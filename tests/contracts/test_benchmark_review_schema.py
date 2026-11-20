import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sngso_engine.contracts import validate_payload
from sngso_engine.benchmark_reviews import validate_human_benchmark_review

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "editorial-benchmark-review.schema.json"


def _payload():
    return {
        "schema_version": "1.0.0",
        "review_id": "review:sample-001",
        "benchmark_id": "benchmark:sample-001",
        "benchmark_fingerprint": "sha256:" + "a" * 64,
        "language": "en",
        "status": "approved",
        "reviewer": {
            "reviewer_id": "editor:en-01",
            "role": "English editor",
            "language": "en",
            "qualification_basis": "Professional English editing and newsroom experience.",
        },
        "reviewed_at": "2026-11-20T00:00:00Z",
        "source_materials": [{
            "article_id": "article:sample-001",
            "source_id": "source:sample-001",
            "canonical_uri": "https://example.org/story",
            "content_fingerprint": "sha256:" + "b" * 64,
            "checked_at": "2026-11-19T20:00:00Z",
            "approved_use_reference": "permission-evidence:sample-001",
        }],
        "corrections": [],
        "adjudication_note": "Reviewed against the source; all claims remain attributed.",
    }


def test_review_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_approved_review_passes_contract_and_metadata_gate():
    payload = _payload()
    validate_payload("editorial-benchmark-review", payload)
    assert validate_human_benchmark_review(payload)["approval_eligible"] is True


def test_approved_review_without_source_is_rejected_by_contract():
    payload = _payload()
    payload["source_materials"] = []
    try:
        validate_payload("editorial-benchmark-review", payload)
    except ValueError:
        pass
    else:
        raise AssertionError("approved review without source material must fail")
