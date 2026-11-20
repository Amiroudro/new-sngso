# Section 8.1: benchmark acceptance evidence in story editorial snapshots

## Purpose

Section 7 created immutable human-review, study-execution, and independent acceptance records. Section 8.1 connects those records to the story dossier so editors can see whether benchmark evidence remains usable for a narrow, declared purpose.

## Snapshot integration

A story snapshot now includes `benchmark_acceptance_reviews` for acceptance decisions whose benchmark artifacts are already linked to the story through registered benchmark-review source articles. Each entry includes a current quality report rather than presenting the historical acceptance decision as a permanent green light.

The report rechecks:

- the immutable acceptance payload fingerprint;
- exact registered protocol version and fingerprint;
- exact execution fingerprint and its protocol relationship;
- benchmark artifact fingerprint and execution binding;
- acceptance contract, decision, and all three domain outcomes;
- at least two distinct active human benchmark reviewers whose exact review revisions have provider attestations and whose source permissions remain currently valid.

If a source permission expires, is suspended, is revoked, loses operation scope, or no longer has verified evidence, the current report blocks scoped reference. Historical records remain intact.

## Decision semantics

The report uses `eligible_for_scoped_reference`, not a production-use or publication authorization flag. It includes the declared intended use and scope limitations, named checks, and blockers.

Every report continues to return `production_ready: false` and `authorization_granted: false`. It does not establish factual correctness, reproduce the statistical analysis, replace editorial judgment, or override Section 7.18.

## Verification boundaries

Regression tests cover exact protocol/execution/artifact checks and fail-closed behavior when current, attested human source reviews are absent. Story snapshots explicitly expose an empty acceptance-review collection when no linked benchmark evidence exists.

The next slice should add a bilingual, cross-artifact quality report that compares Bangla and English coverage without averaging away language-specific failures or treating synthetic fixtures as human evidence.
