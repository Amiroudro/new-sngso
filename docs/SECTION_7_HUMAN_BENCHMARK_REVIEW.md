# Section 7: human-reviewed benchmark protocol

## Purpose

Synthetic regression fixtures test exact, intentionally encoded expectations. They are not evidence of natural Bangla, translation quality, or factual accuracy. This protocol defines the minimum metadata required before a benchmark may be considered for human approval.

## Review record

Each review record binds to the exact benchmark content fingerprint and records:

- benchmark ID and language;
- reviewer ID, role, language, and a written qualification basis;
- timezone-aware review timestamp;
- source article and publisher IDs, canonical HTTPS URI, checked content fingerprint, checked timestamp, and approved-use evidence reference;
- every correction with original text, corrected text, affected field, and rationale; and
- an adjudication note explaining the decision.

Bangla and English benchmarks require independent review by a reviewer working in the benchmark language. Review of one language does not approve another language's counterpart.

## Approval rules

A record is approval-eligible only when all declared metadata checks pass and the source material and adjudication note are present. A benchmark fingerprint change invalidates the association with the prior review. Corrections must create a new benchmark fingerprint and a new review record; prior records must remain available as history.

The current validator checks metadata shape and consistency only. It does not authenticate reviewer identity, verify rights evidence, inspect the referenced registry records, or persist an append-only review history. Therefore it must not be treated as a production approval gate.

## Next persistence gate

Before human-reviewed benchmarks are used in release decisions, implement registry persistence with append-only review history, foreign-key validation to registered articles and sources, permission-evidence linkage, fingerprint binding, optimistic concurrency, and SQLite-level immutability. Add a read-only benchmark quality report and include review history in the story editorial snapshot. Reviewer authentication and authorization remain a separate security requirement.
