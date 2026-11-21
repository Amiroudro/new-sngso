# Section 7.24: study execution evidence and independent analysis review

## Purpose

A protocol is a plan, not evidence that a study occurred. Section 7.24 introduces an immutable execution record that binds a completed blind round and its analysis artifact to one exact registered protocol version.

## Methodologist boundary

The new `benchmark_methodologist` role is reserved for an active, human-classified principal. Submission occurs through the authenticated application facade, which verifies the provider-backed session, issuer/subject mapping, recent authentication, provider key/adapter provenance, and exact match between the authenticated principal and the declared methodologist. The methodologist must not be one of the protocol's blind evaluators.

Provisioning this role remains a security-administrator operation. A role label and provider attestation are not proof of statistical competence; qualification and independent methodological review remain organizational responsibilities.

## Execution completeness

The execution record binds:
- exact study ID/version and protocol fingerprint;
- benchmark ID/fingerprint and rubric version;
- start and completion timestamps;
- each included score ID, item ID, and evaluator ID;
- each missing or excluded item/evaluator pair, with a reason and optional score ID for a recorded but ineligible score;
- analysis scale, statistic name, estimate, confidence interval and confidence level;
- sampled item, scored pair, and missing pair counts;
- analysis artifact fingerprint, missing-data handling, independence assessment, and methodologist rationale.

The registry verifies that every planned blind item/evaluator pair is accounted for exactly once by either an eligible persisted score or a missing/excluded record. Included score IDs must exist in the durable score ledger, match the exact item/evaluator and benchmark fingerprint, and fall within the declared execution window. Excluded score references must identify the correct ineligible score. Unrecorded pairs must have a documented reason. The methodologist must be independent of every planned evaluator.

## Immutable evidence

SQLite migration 22 stores execution records with protocol and benchmark foreign keys, provider identity provenance, canonical execution fingerprint, audit event, and append-only database guards. A stored execution cannot be edited or deleted through the guarded registry interface.

## Statistical and production limits

The registry stores the reported analysis estimate, confidence interval, and fingerprint of the analysis artifact. It does not rerun the analysis code, verify the mathematical correctness of the coefficient or interval, prove that the methodological reviewer is competent, or establish editorial truth. The selected statistic must still match the scale and sampling design, and its assumptions and uncertainty must be independently reviewed.

An execution record is evidence of a declared completed study and its submitted analysis, not a production-approval decision. Benchmark production acceptance remains separate and subject to the Section 7.18 operational security gate, real independent human evidence, current source permissions, and explicit governance approval.
