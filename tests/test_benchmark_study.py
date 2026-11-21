from copy import deepcopy
import pytest
from sngso_engine.benchmark_study import BenchmarkStudyError, validate_benchmark_study_protocol
from sngso_engine.contracts import ContractValidationError

def _protocol():
    return {
      "schema_version":"1.0.0","study_id":"study:bn:headline:pilot","study_version":1,"status":"protocol_only",
      "benchmark_id":"artifact:bn:headlines","benchmark_fingerprint":"sha256:"+"a"*64,"rubric_version":"headline-rubric/2.1",
      "language_task_strata":[{"language":"bn","task_type":"headline"}],
      "sampling_plan":{"sampling_frame_description":"All eligible Bangla headline benchmark items registered before the study freeze.","selection_method":"stratified_random","inclusion_criteria":["Registered immutable items with verified source provenance."],"exclusion_criteria":["Items with unresolved source permission or known answer leakage."],"target_rationale":"The target will be justified from planned precision and study design, not an arbitrary universal threshold."},
      "partition_membership":{"calibration_item_ids":["cal:1"],"blind_evaluation_item_ids":["blind:1","blind:2"],"adjudication_item_ids":["adj:1"]},
      "reviewer_plan":[
        {"reviewer_id":"human:one","principal_kind":"human","languages":["bn"],"task_types":["headline"],"training_completed":True,"training_record_reference":"training:one","qualification_basis":"Documented professional Bangla editing experience and task-specific assessment."},
        {"reviewer_id":"human:two","principal_kind":"human","languages":["bn"],"task_types":["headline"],"training_completed":True,"training_record_reference":"training:two","qualification_basis":"Documented professional Bangla journalism experience and task-specific assessment."}],
      "blind_assignment_plan":[
        {"item_id":"blind:1","language":"bn","task_type":"headline","reviewer_ids":["human:one","human:two"]},
        {"item_id":"blind:2","language":"bn","task_type":"headline","reviewer_ids":["human:one","human:two"]}],
      "analysis_plan":{"measurement_scale":"ordinal","proposed_statistic":"A statistic appropriate for ordinal ratings and crossed evaluator/item design","statistic_rationale":"The selected statistic must reflect ordinal scoring, multiple raters, and the planned sampling structure.","unit_of_analysis":"Item-level criterion ratings, with clustering by item and evaluator represented.","confidence_interval_plan":"Use a justified interval method that respects clustering and report its assumptions and limitations.","missing_data_plan":"Describe missingness, reasons, exclusions, and sensitivity analyses before examining outcomes.","independence_assumptions":"Reviewers score independently without seeing peers' scores or adjudication outcomes.","methodological_reviewer":"Independent statistician or research-methods reviewer, named before data collection."},
      "limitations":["Protocol validation does not establish actual execution, adequate sample size, or reliability."]
    }

def test_valid_protocol_is_fingerprinted_and_never_production_ready():
    result=validate_benchmark_study_protocol(_protocol())
    assert result["valid"] is True and result["status"]=="protocol_only"
    assert result["production_ready"] is False
    assert result["partition_counts"]=={"calibration":1,"blind_evaluation":2,"adjudication":1}
    assert result["protocol_fingerprint"].startswith("sha256:")

def test_protocol_rejects_item_leakage_between_partitions():
    payload=_protocol(); payload["partition_membership"]["adjudication_item_ids"].append("blind:1")
    with pytest.raises(BenchmarkStudyError,match="leaks across"): validate_benchmark_study_protocol(payload)

def test_protocol_rejects_duplicate_or_incomplete_blind_assignments():
    payload=_protocol(); payload["blind_assignment_plan"].pop()
    with pytest.raises(BenchmarkStudyError,match="every blind evaluation item"): validate_benchmark_study_protocol(payload)
    payload=_protocol(); payload["blind_assignment_plan"].append(deepcopy(payload["blind_assignment_plan"][0]))
    with pytest.raises(BenchmarkStudyError,match="exactly one"): validate_benchmark_study_protocol(payload)

def test_protocol_rejects_untrained_or_unqualified_reviewer():
    payload=_protocol(); payload["reviewer_plan"][1]["training_completed"]=False
    with pytest.raises(BenchmarkStudyError,match="completed training"): validate_benchmark_study_protocol(payload)
    payload=_protocol(); payload["reviewer_plan"][1]["languages"]=["en"]
    with pytest.raises(BenchmarkStudyError,match="lacks declared"): validate_benchmark_study_protocol(payload)

def test_protocol_rejects_reviewer_substitution_and_strata_mismatch():
    payload=_protocol(); payload["blind_assignment_plan"][0]["reviewer_ids"][1]="unknown"
    with pytest.raises(BenchmarkStudyError,match="undeclared reviewer"): validate_benchmark_study_protocol(payload)
    payload=_protocol(); payload["blind_assignment_plan"][0]["task_type"]="translation"
    with pytest.raises(BenchmarkStudyError,match="outside declared strata"): validate_benchmark_study_protocol(payload)

def test_protocol_rejects_missing_reviewer_blind_independence():
    payload=_protocol(); payload["blind_assignment_plan"][0]["reviewer_ids"]=["human:one","human:one"]
    with pytest.raises(ContractValidationError,match="non-unique elements"): validate_benchmark_study_protocol(payload)
