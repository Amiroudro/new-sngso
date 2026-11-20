# Section 7: human-reviewed benchmark protocol

## Purpose

Synthetic regression fixtures test exact, intentionally encoded expectations. They are not evidence of natural Bangla, translation quality, or factual accuracy. This protocol defines the minimum metadata and storage controls required before a benchmark may be considered for human approval.

## Review record

Each review record binds to the exact benchmark content fingerprint and records:

- benchmark ID and language;
- reviewer ID, role, language, and a written qualification basis;
- timezone-aware review timestamp;
- source article and publisher IDs, canonical HTTPS URI, checked content fingerprint, checked timestamp, and permission-evidence reference;
- every correction with original text, corrected text, affected field, and rationale; and
- an adjudication note explaining the decision.

Bangla and English benchmarks require independent review by a reviewer working in the benchmark language. Review of one language does not approve another language's counterpart.

## Registry persistence (schema migration 13)

The SQLite registry now stores review heads and a separate revision history. Every insert and update to a review head creates its matching history row through SQLite triggers. The database rejects deletion of review heads, updates or deletion of history rows, non-sequential revisions, missing actors/reasons, and timestamps moving backwards. The registry API also uses optimistic concurrency to reject stale edits.

Before a review is stored, its declared article, publisher, permission-evidence, endpoint, and permission-grant references must exist. The article, evidence, and grant must belong to the same publisher; the canonical URI must match the registered article; and the evidence must be marked verified. The source content fingerprint is checked against the registered article when that article has a fingerprint. The operation-specific grant requirements are detailed in Section 7.4.

## Limits that remain

Registry-managed roles authorize declared principal IDs, but do not authenticate the human operating an account or independently establish editorial correctness. Synthetic fixtures are not a substitute for a real, bilingual, human-reviewed benchmark set. Do not present metadata validation as proof of factual correctness.

The review record retains the fingerprint declaration, but migration 14 now binds it to stored canonical artifact content and independently recomputes it at review and eligibility checks.

## 7.5 Quality reporting and story snapshot integration

The registry exposes a read-only benchmark review quality report. It separates the recorded review decision and metadata validation from a fresh check that each source still has verified evidence and a currently usable grant for its declared endpoint and operation. A suspended, revoked, expired, or otherwise unusable grant blocks current-use eligibility even when the historical review remains approved.

The story editorial snapshot includes benchmark review records whose declared source articles belong to that story. Each included record carries its current revision, append-only review history, and a current-use quality report. This is provenance visibility, not an editorial quality score. The report warns that registry roles do not authenticate the human operating an account and that it does not determine factual correctness.

## 7.6 Immutable benchmark artifact registry

Schema migration 14 adds an immutable benchmark-artifact table. Curators register the actual structured JSON content with a stable artifact ID and language. The registry canonicalizes that content using `canonical-json-v1`, computes its SHA-256 fingerprint itself, and records the curator, timestamp, and reason. Direct SQL updates and deletes are rejected by database triggers.

A human review must now reference a registered artifact by its artifact ID. On review creation and revision, the registry re-reads the stored artifact, recomputes its fingerprint, and checks both the declared review fingerprint and language. The read-only quality report repeats this artifact check, and current-use eligibility fails closed if the artifact is missing, mismatched, or cannot be verified.

This stage also defines a versioned benchmark-content contract. Each artifact contains one or more uniquely identified items with a task type, input, expected output, and explicit scoring criteria. The contract supports headline, summary, translation, claim extraction, fact-checking, vocabulary, and editorial-judgment tasks. Human identity authentication remains outside this stage. Canonicalization is explicitly versioned so a future change cannot silently reinterpret old fingerprints.

## 7.8 Registry-managed benchmark roles

Schema migration 15 adds registry-managed benchmark principals with the explicit roles `benchmark_curator`, `benchmark_reviewer`, and `benchmark_adjudicator`. Artifact registration requires an active curator principal. Review creation and revision require an active reviewer principal, and the acting principal ID must match the reviewer ID declared in the review. Unknown principals, inactive principals, and principals without the required role are rejected. Revocation is soft and audited; historical review and artifact records remain intact.

Provisioning is an administrative operation and must be performed only by a trusted deployment operator. The local SQLite registry does not authenticate that operator or the human behind a principal ID. A production deployment must bind the principal ID to a trusted login/session and restrict provisioning to an authenticated administrator. Role checks are a registry authorization control, not proof of real-world identity.

Adjudicator role registration is enforced when an adjudication is written. The registry requires the adjudicator principal to be active, to hold `benchmark_adjudicator`, and to match the declared adjudicator ID. The two referenced reviews must exist, bind to the same exact artifact fingerprint, and have reviewer IDs matching the stored review records. The adjudicator cannot be one of those reviewers. Adjudication records are immutable and audited. The record documents the resolution; it does not automatically rewrite review decisions or grant current-use approval. The read-only current-use quality report now applies an explicit fail-closed outcome policy: `uphold_existing` can preserve eligibility only for a referenced approved review; `return_for_revision` blocks use until a revised artifact is registered and reviewed; `reject_benchmark_item` blocks use; and `split_accepted_answers` blocks use until distinct accepted-answer items are represented in a revised artifact and freshly reviewed. The policy does not rewrite reviewers' historical decisions or grant approval to new artifact versions.

### Final adjudication and immutable artifact policy

Schema migration 16 makes the final adjudication unique per immutable benchmark artifact. A second adjudication for the same artifact is rejected at the SQLite boundary; a changed decision requires a newly registered artifact version with new reviews, preserving the old artifact's decision history. The current-use report also fails closed if legacy data contains multiple adjudications for one artifact.

A final adjudication also freezes revisions to reviews bound to that artifact. If the outcome requires correction, the workflow moves to a newly registered artifact with new review IDs rather than mutating the decision's inputs.

For `uphold_existing`, the adjudicator must name `upheld_review_ids`. The list must be a non-empty subset of the two or more referenced reviews, and every selected review must be approved in the registry. Only those exact selected reviews can remain eligible. Other outcomes must not include an upheld-review selection. This prevents an ambiguous “uphold” from preserving every approved review by default.

## Next gates

- Connect principal IDs to a trusted authentication/session provider and restrict provisioning to authenticated administrators.
- Populate separate Bangla and English benchmark sets with qualified human reviewers, adjudication, and correction provenance.

## 7.4 Operation-scoped permission enforcement

A verified permission-evidence record is not, by itself, an authorization to use an article in a benchmark. Each source-material entry now identifies its exact permission grant, endpoint, and permitted operation. Approved benchmark reviews accept only the explicitly declared benchmark-material operations: `excerpt_storage`, `full_text_storage`, or `translation`.

At registry write time, the grant must:
- exist and belong to the same publisher as the article and evidence;
- be approved and currently effective;
- explicitly include the declared endpoint;
- explicitly allow the declared operation;
- cite the same verified evidence at both grant and operation level;
- pass the fail-closed permission evaluator, including its handling of unimplemented conditions.

This check is performed when a review is created or revised. It does not authenticate the reviewer, prove that the grantor had legal authority, or make a legal determination about copyright. Permission can also be suspended or revoked after a review is recorded; downstream publication and use workflows must re-evaluate the grant at the point of use rather than treating an old review as permanent authorization.


## 7.7 Disagreement adjudication contract

A separate Draft 2020-12 adjudication record now captures the benchmark artifact fingerprint, at least two distinct review IDs, an adjudicator declaration, a controlled outcome, a reasoned decision, evidence references, and a timezone-aware resolution timestamp. Outcomes distinguish upholding an existing decision, returning work for revision, rejecting a benchmark item, and splitting multiple acceptable answers.

The validator rejects an adjudicator whose declared principal ID is also listed among the reviewers. This is a workflow-level independence check, not proof of identity or actual independence. The validator's declared-independence check is supplemented by registry role enforcement and checks against the stored reviewer records. Durable adjudication history is immutable and audited. Current-use eligibility applies the explicit outcome policy described in Section 7.8. The adjudication workflow still does not authenticate the human behind a principal ID.
