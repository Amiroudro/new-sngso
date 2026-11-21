from __future__ import annotations

from copy import deepcopy

import pytest

from sngso_engine.benchmark_agreement import (
    BenchmarkAgreementError,
    benchmark_evaluator_agreement,
)


def _sheet(evaluator: str, score: int):
    return {
        "benchmark_id": "artifact:bn:one",
        "benchmark_fingerprint": "sha256:" + "a" * 64,
        "item_id": "item:headline:one",
        "task_type": "headline",
        "language": "bn",
        "evaluator_id": evaluator,
        "criterion_scores": [
            {"criterion_id": "accuracy", "weight": 0.7, "score": score},
            {"criterion_id": "clarity", "weight": 0.3, "score": 4},
        ],
    }


def test_agreement_report_calculates_pairwise_descriptive_differences():
    report = benchmark_evaluator_agreement([
        _sheet("reviewer:one", 3),
        _sheet("reviewer:two", 2),
    ])
    assert report["status"] == "descriptive_only"
    assert report["evaluator_count"] == 2
    assert report["pair_count"] == 1
    assert report["weighted_mean_absolute_score_difference"] == "0.7000"
    assert report["criterion_results"][0]["exact_agreement_rate"] == "0.0000"
    assert report["criterion_results"][0]["within_one_point_rate"] == "1.0000"
    assert "not an inter-rater reliability coefficient" in report["interpretation"]


def test_agreement_requires_two_distinct_evaluators():
    with pytest.raises(BenchmarkAgreementError, match="at least two"):
        benchmark_evaluator_agreement([_sheet("reviewer:one", 3)])
    with pytest.raises(BenchmarkAgreementError, match="only one score sheet"):
        benchmark_evaluator_agreement([
            _sheet("reviewer:one", 3), _sheet("reviewer:one", 2)
        ])


def test_agreement_rejects_different_artifact_item_or_rubric():
    first = _sheet("reviewer:one", 3)
    other = _sheet("reviewer:two", 2)
    other["item_id"] = "item:other"
    with pytest.raises(BenchmarkAgreementError, match="same artifact"):
        benchmark_evaluator_agreement([first, other])
    other = _sheet("reviewer:two", 2)
    other["criterion_scores"][0]["weight"] = 0.6
    with pytest.raises(BenchmarkAgreementError, match="weight mismatch"):
        benchmark_evaluator_agreement([first, other])


def test_agreement_rejects_critical_failure_and_criterion_set_mismatch():
    first = _sheet("reviewer:one", 3)
    second = _sheet("reviewer:two", 2)
    second["critical_failure"] = True
    with pytest.raises(BenchmarkAgreementError, match="critical-failure"):
        benchmark_evaluator_agreement([first, second])
    second = _sheet("reviewer:two", 2)
    second["criterion_scores"].pop()
    with pytest.raises(BenchmarkAgreementError, match="same criterion IDs"):
        benchmark_evaluator_agreement([first, second])
