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

Before a review is stored, its declared article, publisher, and permission-evidence references must exist. They must belong to the same publisher; the canonical URI must match the registered article; the permission-evidence record must be marked verified. The source content fingerprint is checked against the registered article when that article has a fingerprint.

## Limits that remain

This is not reviewer authentication or authorization. A reviewer ID is a recorded declaration, not proof of identity. A permission-evidence record marked verified is not, by itself, proof that every intended use is covered by a currently effective permission grant. Those rights must be checked against the operation-specific grant before collection, translation, storage, or publication.

The registry does not independently establish that an editorial judgement is correct. Synthetic fixtures are not a substitute for a real, bilingual, human-reviewed benchmark set. Do not present metadata validation as proof of factual correctness.

## Next gates

- Add an operation-specific permission-grant check for benchmark source use.
- Add a read-only benchmark quality report and expose review provenance in the story editorial snapshot.
- Add a controlled reviewer identity and role authorization mechanism.
- Populate separate Bangla and English benchmark sets with qualified human reviewers, adjudication, and correction provenance.
