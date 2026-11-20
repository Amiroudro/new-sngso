from copy import deepcopy
import pytest
from sngso_engine.benchmark_acceptance_review import BenchmarkAcceptanceReviewError, validate_benchmark_acceptance_review
from test_benchmark_study import _protocol
from test_benchmark_study_execution import _execution
from sngso_engine.canonical import sha256_fingerprint

def _review():
    return {
      "schema_version":"1.0.0","review_id":"acceptance-review:pilot:1",
      "study_id":"study:bn:headline:pilot","study_version":1,"protocol_fingerprint":"sha256:"+"0"*64,
      "execution_id":"execution:pilot:one","execution_fingerprint":"sha256:"+"0"*64,
      "benchmark_id":"artifact:bn:headlines","benchmark_fingerprint":"sha256:"+"a"*64,
      "rubric_version":"headline-rubric/2.1","reviewer_id":"independent:reviewer",
      "reviewer_role":"independent_acceptance_reviewer","reviewed_at":"2026-11-20T13:00:00Z",
      "conflict_declaration":{"no_conflict":True,"conflicts":[],"rationale":"The reviewer did not design the study, score its items, or conduct its analysis."},
      "domains":{
       "statistical_validity":{"status":"pass","findings":["The statistic and interval fit the declared ordinal design."],"evidence_references":["analysis:method-review"],"rationale":"Analysis choices and assumptions were examined against the registered design and limitations."},
       "editorial_validity":{"status":"pass","findings":["Bangla headline evidence and provenance were checked."],"evidence_references":["editorial:audit-sample"],"rationale":"Sampled benchmark items and source records were checked for support and language coverage."},
       "operational_security":{"status":"pass","findings":["Identity, permission, and immutable evidence controls were checked."],"evidence_references":["security:review"],"rationale":"The review checked identity provenance, current permission evidence, and applicable operational controls."}},
      "decision":"accepted_for_limited_use","intended_use":"A supervised internal calibration exercise for Bangla headline review only.",
      "scope_limitations":["This review does not authorize public production publication or automated editorial decisions."],
      "evidence_references":["protocol:registered-version","execution:immutable-record"],
      "rationale":"All three review domains passed for the narrow stated use, subject to the explicit scope limitations."
    }

def _validate(review=None, protocol=None, execution=None):
    protocol=protocol or _protocol(); execution=execution or _execution(); review=review or _review()
    review["protocol_fingerprint"]=sha256_fingerprint(protocol)
    review["execution_fingerprint"]=sha256_fingerprint(execution)
    return validate_benchmark_acceptance_review(review,protocol=protocol,execution=execution)

def test_acceptance_review_binds_exact_evidence_and_never_grants_production():
    result=_validate()
    assert result["valid"] and result["reviewer_independent"]
    assert result["domains_passed"]==3
    assert result["production_ready"] is False and result["authorization_granted"] is False

@pytest.mark.parametrize("field",["study_id","study_version","protocol_fingerprint","execution_id","execution_fingerprint","benchmark_id","benchmark_fingerprint","rubric_version"])
def test_review_rejects_stale_or_mismatched_evidence_binding(field):
    review=_review(); review[field]="stale-value"
    with pytest.raises(BenchmarkAcceptanceReviewError,match=field): _validate(review)

def test_review_rejects_blind_evaluator_or_methodologist_as_acceptance_reviewer():
    review=_review(); review["reviewer_id"]="human:one"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="independent"): _validate(review)
    review=_review(); review["reviewer_id"]="methodologist:one"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="independent"): _validate(review)

def test_review_rejects_conflict_declaration():
    review=_review(); review["conflict_declaration"]["no_conflict"]=False
    review["conflict_declaration"]["conflicts"]=["Designed the rubric"]
    with pytest.raises(BenchmarkAcceptanceReviewError,match="conflicted"): _validate(review)

def test_acceptance_requires_all_three_domains_to_pass():
    review=_review(); review["domains"]["editorial_validity"]["status"]="needs_revision"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="all pass"): _validate(review)

def test_rejection_and_revision_decisions_need_matching_domain_outcomes():
    review=_review(); review["decision"]="rejected"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="failed review domain"): _validate(review)
    review=_review(); review["decision"]="needs_revision"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="marked needs_revision"): _validate(review)

def test_reviewed_at_must_be_timezone_aware():
    review=_review(); review["reviewed_at"]="2026-11-20T13:00:00"
    with pytest.raises(BenchmarkAcceptanceReviewError,match="timezone-aware"): _validate(review)
