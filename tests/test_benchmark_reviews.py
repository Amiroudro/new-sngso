from sngso_engine.benchmark_reviews import validate_human_benchmark_review


def _review():
    return {
        "schema_version": "1.0.0",
        "review_id": "review:notice-bn-1",
        "benchmark_id": "benchmark:notice-bn-v1",
        "benchmark_fingerprint": "sha256:" + "a" * 64,
        "language": "bn",
        "status": "approved",
        "reviewer": {
            "reviewer_id": "editor:bn-01",
            "role": "Bangla editor",
            "language": "bn",
            "qualification_basis": "Professional Bangla copy-editing and newsroom experience.",
        },
        "reviewed_at": "2026-11-20T00:00:00Z",
        "source_materials": [{
            "article_id": "article:notice-1",
            "source_id": "source:publisher-1",
            "canonical_uri": "https://example.org/notice",
            "content_fingerprint": "sha256:" + "b" * 64,
            "checked_at": "2026-11-19T18:00:00Z",
            "approved_use_reference": "permission-evidence:notice-1",
        }],
        "corrections": [{
            "field": "expected.segments[1].text",
            "original": "আগের বাক্য",
            "corrected": "সংশোধিত বাক্য",
            "rationale": "Attribution must remain explicit.",
        }],
        "adjudication_note": "Reviewed against the cited source; attribution and date retained.",
    }


def test_complete_review_is_eligible_but_not_claimed_authenticated():
    result = validate_human_benchmark_review(_review())
    assert result["valid"] is True
    assert result["approval_eligible"] is True
    assert "authenticate reviewer identity" in result["scope_note"]


def test_approved_review_without_source_material_is_blocked():
    review = _review()
    review["source_materials"] = []
    result = validate_human_benchmark_review(review)
    assert result["approval_eligible"] is False
    assert "approved_source_material" in {x["name"] for x in result["failures"]}


def test_reviewer_language_must_match_benchmark_language():
    review = _review()
    review["reviewer"]["language"] = "en"
    result = validate_human_benchmark_review(review)
    assert result["approval_eligible"] is False
    assert "reviewer_language" in {x["name"] for x in result["failures"]}


def test_timezone_and_fingerprint_are_required():
    review = _review()
    review["reviewed_at"] = "2026-11-20T00:00:00"
    review["benchmark_fingerprint"] = "not-a-fingerprint"
    result = validate_human_benchmark_review(review)
    failed = {x["name"] for x in result["failures"]}
    assert {"review_timestamp", "benchmark_fingerprint"} <= failed


def test_correction_requires_rationale():
    review = _review()
    review["corrections"][0]["rationale"] = ""
    result = validate_human_benchmark_review(review)
    assert result["approval_eligible"] is False
    assert "correction_log" in {x["name"] for x in result["failures"]}


def test_draft_can_be_valid_without_source_approval():
    review = _review()
    review["status"] = "draft"
    review["source_materials"] = []
    review["adjudication_note"] = ""
    result = validate_human_benchmark_review(review)
    assert result["valid"] is True
    assert result["approval_eligible"] is False
