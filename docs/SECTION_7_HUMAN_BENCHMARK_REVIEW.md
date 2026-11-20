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

## Next gates

- Add a read-only benchmark quality report and expose eligible review provenance in the story editorial snapshot.
- Add a controlled reviewer identity and role authorization mechanism.
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
