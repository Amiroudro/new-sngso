import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sngso_engine.benchmark_adjudication import validate_benchmark_adjudication
from sngso_engine.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts" / "v1" / "editorial-benchmark-adjudication.schema.json"


def _payload():
    return {
        "schema_version": "1.0.0",
        "adjudication_id": "adjudication:sample-001",
        "benchmark_id": "benchmark:sample-001",
        "benchmark_fingerprint": "sha256:" + "a" * 64,
        "review_ids": ["review:one", "review:two"],
        "reviewer_ids": ["editor:one", "editor:two"],
        "adjudicator": {
            "principal_id": "adjudicator:senior",
            "role": "benchmark_adjudicator",
            "qualification_basis": "Senior bilingual editor with documented fact-checking experience.",
            "independent_of_reviewers": True,
        },
        "outcome": "split_accepted_answers",
        "rationale": "Both submissions preserve the verified facts, but each captures a distinct valid interpretation.",
        "evidence_references": ["source:article-1", "evidence:claim-map-1"],
        "resolved_at": "2026-11-20T00:00:00Z",
        "issue_resolutions": [{
            "issue_id": "claim-1",
            "category": "factual_accuracy",
            "severity": "major",
            "description": "The reviewers disagree about whether the stated figure is supported by the source.",
            "reviewer_positions": {
                "review:one": "The figure is directly supported by the cited source.",
                "review:two": "The source reports a different figure and does not support this claim."
            },
            "disposition": "reviewer_two_upheld",
            "resolution": "The primary source table reports the second figure; the first review relied on a secondary paraphrase.",
            "evidence_references": ["source:article-1#table-2"]
        }],
    }


def test_adjudication_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)


def test_independent_adjudication_passes_contract():
    result = validate_benchmark_adjudication(_payload())
    assert result["valid"] is True
    assert result["eligible_for_adjudication"] is True


def test_adjudicator_cannot_be_one_of_the_reviewers():
    payload = _payload()
    payload["adjudicator"]["principal_id"] = "editor:one"
    result = validate_benchmark_adjudication(payload)
    assert result["valid"] is True
    assert result["eligible_for_adjudication"] is False
    assert "adjudicator_not_a_reviewer" in {x["name"] for x in result["checks"] if not x["passed"]}


def test_adjudication_requires_two_distinct_reviews():
    payload = _payload()
    payload["review_ids"] = ["review:one", "review:one"]
    with pytest.raises(ValueError):
        validate_payload("editorial-benchmark-adjudication", payload)


def test_uphold_existing_requires_an_explicit_subset_of_review_ids():
    payload = _payload()
    payload["outcome"] = "uphold_existing"
    with pytest.raises(ValueError):
        validate_benchmark_adjudication(payload)
    payload["upheld_review_ids"] = ["review:one"]
    result = validate_benchmark_adjudication(payload)
    assert result["valid"] is True
    payload["upheld_review_ids"] = ["review:not-referenced"]
    assert validate_benchmark_adjudication(payload)["valid"] is False


def test_non_uphold_outcomes_cannot_select_upheld_reviews():
    payload = _payload()
    payload["upheld_review_ids"] = ["review:one"]
    with pytest.raises(ValueError):
        validate_benchmark_adjudication(payload)


def test_adjudication_requires_explicit_rationale_and_evidence():
    payload = _payload()
    payload["rationale"] = "Not enough."
    payload["evidence_references"] = []
    with pytest.raises(ValueError):
        validate_payload("editorial-benchmark-adjudication", payload)


def test_each_material_disagreement_requires_exact_positions_for_both_reviews():
    payload = _payload()
    payload["issue_resolutions"][0]["reviewer_positions"].pop("review:two")
    result = validate_benchmark_adjudication(payload)
    assert result["valid"] is False
    assert "every_review_position_recorded" in {x["name"] for x in result["checks"] if not x["passed"]}


def test_each_disagreement_requires_evidence_backed_resolution():
    payload = _payload()
    payload["issue_resolutions"][0]["evidence_references"] = []
    result = validate_benchmark_adjudication(payload)
    assert result["valid"] is False
    assert "each_disagreement_resolved_with_evidence" in {x["name"] for x in result["checks"] if not x["passed"]}


def test_duplicate_disagreement_ids_fail_quality_validation():
    payload = _payload()
    payload["issue_resolutions"].append(dict(payload["issue_resolutions"][0]))
    result = validate_benchmark_adjudication(payload)
    assert result["valid"] is False
    assert "unique_disagreement_ids" in {x["name"] for x in result["checks"] if not x["passed"]}
