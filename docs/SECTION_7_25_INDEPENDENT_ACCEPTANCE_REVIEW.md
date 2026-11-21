# Section 7.25: independent benchmark acceptance review

## Purpose

A registered study protocol and immutable execution record do not prove that the study is statistically defensible, that its benchmark is editorially sound, or that the operating environment is secure. This section makes those three dimensions explicit and requires an independent decision tied to the exact study evidence.

## Three mandatory domains

1. **Statistical validity:** statistic/measurement-scale fit, assumptions, sampling and stratum coverage, dependence and clustering, uncertainty interval method, missingness and sensitivity analysis, and reproducibility of the analysis artifact.
2. **Editorial validity:** source traceability and current permission, factual support and claim scope, language quality, task/language coverage, adjudication outcomes, answer leakage, and benchmark contamination.
3. **Operational security:** provider-backed identity and role provenance, separation of reviewers and methodologist, immutable protocol/execution/score evidence, audit integrity, current permission checks, and the Section 7.18 threat/control requirements.

Each domain records a status, findings, evidence references, and rationale. Acceptance is invalid unless all three domains pass. Strong performance in one domain cannot compensate for a failed or incomplete domain.

## Exact evidence and independence

Every decision binds the study ID/version, protocol fingerprint, execution ID/fingerprint, benchmark fingerprint, and rubric version. The validator recomputes protocol and execution fingerprints from the supplied immutable payloads. The registry additionally retrieves the exact registered protocol and execution rows and verifies their stored fingerprints and relationship before accepting a decision.

The reviewer must be an active human-classified benchmark_acceptance_reviewer with recent provider-backed authentication. The reviewer ID must match the authenticated issuer/subject-mapped principal. A blind evaluator or the study methodologist cannot issue the acceptance review. A conflict declaration is mandatory and a declared conflict blocks acceptance.

## Durable decision ledger

SQLite migration 23 adds benchmark_acceptance_reviews. The ledger stores the decision, exact evidence fingerprints, reviewer, provider provenance, timestamp, reason, and a canonical acceptance-record fingerprint. Database constraints enforce unique review IDs and at most one final acceptance decision per execution. Triggers enforce payload/index consistency and prevent updates or deletion. A hash-chained audit event records registration.

The authenticated facade exposes submission to the acceptance-reviewer role. Retrieval is scoped to the submitting reviewer or authorized governance roles; listing is restricted to curator, adjudicator, security administrator, and methodologist roles. The role is provisioned through the existing security-admin lifecycle. The raw registry remains a trusted deployment boundary and must not be exposed to request handlers.

## Decision semantics and limits

Decisions are not_reviewed, needs_revision, rejected, accepted_for_limited_use, and accepted_for_proposed_use. Acceptance requires all three domains to pass. A reviewer must state intended use, scope limitations, evidence references, and rationale.

These are evidence-backed review outcomes, not production authorization. Stored records always report production_ready=false and authorization_granted=false. The decision cannot bypass Section 7.18 or promote a benchmark automatically. A new immutable execution record is required before another acceptance decision can be registered for the same study execution.

## What this section does not prove

The registry verifies declared evidence linkage and reviewer authorization; it does not independently reproduce the statistical calculation, authenticate the real-world truth of source claims, assess reviewer competence, or prove that a human has discovered every material defect. The domain findings and cited evidence still require genuine independent scrutiny.

No real human study or acceptance review is supplied by the test fixtures. Passing tests prove the tested controls behave as coded, not that a benchmark is accurate or suitable for production.
