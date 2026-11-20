from copy import deepcopy
import pytest
from sngso_engine.benchmark_study_execution import (
    BenchmarkStudyExecutionError, validate_benchmark_study_execution,
)

def _execution():
    return {
      "schema_version":"1.0.0","execution_id":"execution:pilot:one",
      "study_id":"study:bn:headline:pilot","study_version":1,
      "protocol_fingerprint":"sha256:"+"a"*64,
      "benchmark_id":"artifact:bn:headlines","benchmark_fingerprint":"sha256:"+"b"*64,
      "rubric_version":"headline-rubric/2.1",
      "started_at":"2026-11-20T09:00:00Z","completed_at":"2026-11-20T12:00:00Z",
      "score_records":[
        {"score_id":"score:1","item_id":"blind:1","evaluator_id":"human:one"},
        {"score_id":"score:2","item_id":"blind:1","evaluator_id":"human:two"}],
      "missing_records":[{"item_id":"blind:2","evaluator_id":"human:two","reason":"Reviewer withdrew before scoring; no replacement was available."}],
      "analysis":{
        "measurement_scale":"ordinal","statistic_name":"Ordinal Krippendorff alpha",
        "estimate":0.81,"confidence_interval":{"lower":0.72,"upper":0.88},
        "confidence_level":0.95,"sampled_item_count":2,"scored_pair_count":2,"missing_pair_count":1,
        "analysis_artifact_fingerprint":"sha256:"+"c"*64,
        "missing_data_handling":"Report the missing reviewer-item pair and conduct the prespecified sensitivity analysis.",
        "independence_assessment":"The two reviewers scored independently without access to peer scores or adjudication outcomes.",
        "methodologist_id":"methodologist:one",
        "methodologist_rationale":"The selected coefficient matches the ordinal scale and the observed incomplete crossed-rater design, with limitations reported.",
        "reviewed_at":"2026-11-20T12:30:00Z"
      },
      "limitations":["A persisted analysis summary is not independent reproduction of the statistical computation."]
    }

def test_valid_execution_binds_counts_and_stays_non_production_ready():
    result=validate_benchmark_study_execution(_execution())
    assert result["valid"] is True
    assert result["score_record_count"]==2
    assert result["missing_record_count"]==1
    assert result["production_ready"] is False
    assert result["execution_fingerprint"].startswith("sha256:")

@pytest.mark.parametrize("mutator,message",[
    (lambda p:p["score_records"].append(deepcopy(p["score_records"][0])),"score IDs must be unique"),
    (lambda p:p["missing_records"].append(deepcopy(p["missing_records"][0])),"missing item/evaluator pairs must be unique"),
    (lambda p:p["analysis"].update(scored_pair_count=99),"analysis counts must match"),
    (lambda p:p["analysis"]["confidence_interval"].update(lower=0.9,upper=0.8),"lower bound"),
    (lambda p:p.update(completed_at="2026-11-20T08:00:00Z"),"completion cannot precede"),
    (lambda p:p["analysis"].update(reviewed_at="2026-11-20T11:00:00Z"),"review cannot precede"),
])
def test_execution_rejects_inconsistent_evidence(mutator,message):
    payload=_execution(); mutator(payload)
    with pytest.raises(BenchmarkStudyExecutionError,match=message):
        validate_benchmark_study_execution(payload)
