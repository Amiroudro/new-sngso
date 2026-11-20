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

This is not reviewer authentication or authorization. A reviewer ID is a recorded declaration, not proof of identity. The registry does not independently establish that an editorial judgement is correct. Synthetic fixtures are not a substitute for a real, bilingual, human-reviewed benchmark set. Do not present metadata validation as proof of factual correctness.

The review record retains the fingerprint declaration, but migration 14 now binds it to stored canonical artifact content and independently recomputes it at review and eligibility checks.

## 7.5 Quality reporting and story snapshot integration

The registry exposes a read-only benchmark review quality report. It separates the recorded review decision and metadata validation from a fresh check that each source still has verified evidence and a currently usable grant for its declared endpoint and operation. A suspended, revoked, expired, or otherwise unusable grant blocks current-use eligibility even when the historical review remains approved.

The story editorial snapshot includes benchmark review records whose declared source articles belong to that story. Each included record carries its current revision, append-only review history, and a current-use quality report. This is provenance visibility, not an editorial quality score. The report explicitly warns that reviewer identity is not authenticated and that it does not determine factual correctness.

## 7.6 Immutable benchmark artifact registry

Schema migration 14 adds an immutable benchmark-artifact table. Curators register the actual structured JSON content with a stable artifact ID and language. The registry canonicalizes that content using `canonical-json-v1`, computes its SHA-256 fingerprint itself, and records the curator, timestamp, and reason. Direct SQL updates and deletes are rejected by database triggers.

A human review must now reference a registered artifact by its artifact ID. On review creation and revision, the registry re-reads the stored artifact, recomputes its fingerprint, and checks both the declared review fingerprint and language. The read-only quality report repeats this artifact check, and current-use eligibility fails closed if the artifact is missing, mismatched, or cannot be verified.

This stage stores structured JSON benchmark content. It does not yet define a domain-specific benchmark-item schema or authenticate curator/reviewer identities. Canonicalization is explicitly versioned so a future change cannot silently reinterpret old fingerprints.

## Next gates

- Add a domain-specific schema for benchmark item content and stronger curator/reviewer identity and role authorization.
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
