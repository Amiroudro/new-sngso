"""Synthetic regression tests for omission, distortion, attribution, and language."""
import json
from pathlib import Path

import pytest

from sngso_engine.benchmarks import evaluate_editorial_benchmark

FIXTURES = Path(__file__).parent / "fixtures" / "editorial_benchmarks"


def _load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename", ["story_notice_bn.json", "story_notice_en.json"])
def test_bilingual_golden_fixtures_pass(filename):
    benchmark = _load(filename)
    expected = benchmark["expected"]
    artifact = {
        "kind": benchmark["kind"],
        "language": benchmark["language"],
        "claim_ids": expected["claim_ids"],
        "content": {"text": expected["text"], "segments": expected["segments"]},
    }
    result = evaluate_editorial_benchmark(benchmark, artifact)
    assert result["passed"] is True
    assert result["failures"] == []


def test_benchmark_detects_omission_and_distortion():
    benchmark = _load("story_notice_en.json")
    artifact = {
        "kind": "brief_summary", "language": "en", "claim_ids": ["claim:notice"],
        "content": {
            "text": "The authority issued a public notice.",
            "segments": [{"text": "The authority issued a public notice.",
                          "claim_ids": ["claim:notice"], "role": "fact"}],
        },
    }
    result = evaluate_editorial_benchmark(benchmark, artifact)
    failed = {item["name"] for item in result["failures"]}
    assert {"expected_text", "segment_traceability", "claim_coverage", "must_preserve"} <= failed


def test_benchmark_detects_lost_attribution_and_unsupported_assertion():
    benchmark = _load("story_notice_en.json")
    artifact = {
        "kind": "brief_summary", "language": "en",
        "claim_ids": ["claim:notice", "claim:consultation"],
        "content": {
            "text": "The authority issued a public notice on 19 November. The notice was unlawful.",
            "segments": [
                {"text": "The authority issued a public notice on 19 November.",
                 "claim_ids": ["claim:notice"], "role": "fact"},
                {"text": "The notice was unlawful.", "claim_ids": ["claim:consultation"], "role": "fact"},
            ],
        },
    }
    result = evaluate_editorial_benchmark(benchmark, artifact)
    failed = {item["name"] for item in result["failures"]}
    assert {"expected_text", "segment_traceability", "unsupported_detail"} <= failed


def test_benchmark_detects_wrong_language_and_never_emits_a_quality_score():
    benchmark = _load("story_notice_bn.json")
    expected = benchmark["expected"]
    artifact = {
        "kind": "brief_summary", "language": "en", "claim_ids": expected["claim_ids"],
        "content": {"text": expected["text"], "segments": expected["segments"]},
    }
    result = evaluate_editorial_benchmark(benchmark, artifact)
    assert "fixture_language" in {item["name"] for item in result["failures"]}
    assert "score" not in result
    assert "truth" in result["scope_note"]
