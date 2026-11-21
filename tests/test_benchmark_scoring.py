from sngso_engine.benchmark_scoring import validate_benchmark_scoring
from sngso_engine.contracts import validate_payload


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
        "criterion_scores": [
            {"criterion_id": "factual_fidelity", "weight": 0.6, "score": 4,
             "anchor": "All material facts and attribution are preserved.",
             "rationale": "The summary preserves the source's key facts and attribution without adding claims.",
             "evidence_references": ["source:article-1#paragraph-2"]},
            {"criterion_id": "conciseness", "weight": 0.4, "score": 3,
             "anchor": "Mostly concise with one unnecessary detail.",
             "rationale": "The summary is readable but retains one detail that does not serve the central point.",
             "evidence_references": ["output:item-1#sentence-3"]},
        ],
    }


def test_valid_score_sheet_computes_weighted_score():
    result = validate_benchmark_scoring(_score())
    assert result["valid"] is True
    assert result["eligible_for_comparison"] is True
    assert result["weighted_score"] == "3.6000"


def test_weights_must_sum_to_one():
    payload = _score()
    payload["criterion_scores"][0]["weight"] = 0.5
    result = validate_benchmark_scoring(payload)
    assert result["valid"] is False
    assert result["eligible_for_comparison"] is False


def test_criterion_ids_must_be_unique():
    payload = _score()
    payload["criterion_scores"][1]["criterion_id"] = "factual_fidelity"
    result = validate_benchmark_scoring(payload)
    assert result["valid"] is False


def test_evidence_and_rationale_are_required_by_schema():
    payload = _score()
    payload["criterion_scores"][0]["evidence_references"] = []
    try:
        validate_benchmark_scoring(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("scores without evidence must be rejected")


def test_critical_failure_blocks_comparison_even_with_high_score():
    payload = _score()
    payload["critical_failure"] = True
    payload["critical_failure_reason"] = "The output invents a material casualty figure absent from the source."
    result = validate_benchmark_scoring(payload)
    assert result["valid"] is True
    assert result["eligible_for_comparison"] is False


def test_critical_failure_requires_reason():
    payload = _score()
    payload["critical_failure"] = True
    try:
        validate_benchmark_scoring(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("critical failure without reason must be rejected")


def test_scoring_contract_rejects_task_and_language_mismatch_values():
    payload = _score()
    payload["language"] = "fr"
    try:
        validate_payload("editorial-benchmark-scoring", payload)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported language must be rejected")
