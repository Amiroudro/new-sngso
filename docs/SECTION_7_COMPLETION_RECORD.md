# Section 7 completion record: human benchmark governance

## Scope completed in the repository

Section 7 now covers the lifecycle from a declared human review through benchmark study execution and an independent, scope-limited acceptance decision.

| Section | Capability |
|---|---|
| 7.1–7.2 | Versioned human-review contract, evidence metadata, and deterministic validation |
| 7.3–7.5 | SQLite review history, source-permission rechecks, quality reporting, and story snapshot integration |
| 7.6–7.8.1 | Immutable benchmark artifacts, adjudication, registry-managed roles, and final-decision policy |
| 7.9–7.9.1 | Authenticated application boundary and trusted issuer/subject-to-principal binding |
| 7.10–7.11 | Reviewer assignment lifecycle, language qualification, durable queue, and submission handoff |
| 7.12–7.18 | Human/synthetic identity distinction, provider attestations, OIDC verification, privileged admin lifecycle, disagreement resolution, and operational security review |
| 7.19–7.21 | Criterion-level scoring, authenticated score ledger, and descriptive evaluator-agreement reporting |
| 7.22–7.24 | Prespecified calibration-study protocol, immutable protocol registry, execution evidence, and independent methodological review |
| 7.25 | Three-domain independent acceptance review and immutable authenticated decision ledger |
| 7.26 | Benchmark acceptance evidence in story snapshots and separate Bangla/English editorial coverage reporting |

## Persistence and trust boundaries

SQLite migrations 13–23 now cover the review, artifact, principal, assignment, identity-attestation, score, study-protocol, study-execution, and acceptance-decision records. Immutable records use database-level update/delete guards where specified. Decision and lifecycle actions are recorded in the hash-chained audit stream.

The application facade derives actors from a trusted identity-provider boundary. The repository includes an asymmetric OIDC verifier adapter, but no real issuer, JWKS endpoint, revocation service, credentials, deployment identity mapping, or protected first-admin bootstrap is configured here. Raw registry/database access remains part of the trusted deployment boundary.

## Final decision boundary

Section 7.25 requires separate evidence-backed passes for statistical validity, editorial validity, and operational security. It binds the decision to the exact protocol, execution, benchmark, and rubric fingerprints. The acceptance reviewer must be a distinct, active, human-classified, provider-authenticated principal and cannot be a blind evaluator or the study methodologist.

An accepted decision is limited to its declared intended use and limitations. It is not production approval. The implementation always reports production_ready=false and authorization_granted=false. The Section 7.18 operational security review remains **NOT ACCEPTED FOR PRODUCTION** until the deployment-specific blockers are independently evidenced and tested.

## Completion statement

The repository implementation for the Section 7 human-benchmark governance workflow is complete through the durable acceptance-decision boundary. This means the contracts, persistence, authorization checks, audit records, and regression tests are represented in code. It does not mean a real human study has been conducted, a benchmark has passed independent review, statistical reliability has been established, or production infrastructure has been accepted.

Synthetic tests validate software behavior only. Production use remains blocked pending genuine Bangla/English human evidence, independent statistical and editorial review, current publisher permissions, real provider infrastructure, host/database controls, backup/restore drills, and formal completion of the Section 7.18 go/no-go checklist.


## Section 7.26: benchmark-to-story evidence integration

- Story editorial snapshots now include acceptance decisions linked through registered benchmark source articles.
- A current acceptance-quality report rechecks protocol, execution, artifact, decision, provider-attested human source reviews, and operation-scoped source permissions.
- Expired, suspended, revoked, or otherwise unusable source permissions block scoped reference without rewriting historical reviews.
- The report exposes intended use, scope limitations, named checks, and blockers. It never grants publication or production authorization.
- See `docs/SECTION_7_26_BENCHMARK_STORY_INTEGRATION.md`.


## Relationship to the ten-section master plan

This completion record covers the human-benchmark governance slice and its story-snapshot/bilingual-report integration within **Section 7: Editorial production and bilingual quality**. It does not mark all of Section 7 complete. Section 7 still requires production workflows for three-level summaries, independent Bangla and English review, multi-source perspectives, bilingual terminology, source-grounded vocabulary and exam learning, and daily briefing assembly inputs.

Sections 8, 9, and 10 remain future ordered phases:

- **8. Edition assembly and publication safety:** immutable editions/manifests, validation and approval gates, correction/supersession, last-known-good protection, and staging-only export.
- **9. Operations, observability, and free-first automation:** structured run reports, health/alerts, recovery, scheduling after idempotency, cost controls, backup/restore, and incident response.
- **10. End-to-end verification and controlled handover:** complete engine verification, real editorial evaluation, reproducibility, security review, and safe downstream integration readiness.

The Section 7 benchmark acceptance integration is not a substitute for any of those deliverables.

## Section 7.30: reviewed narrative assembly report

- Added a read-only story narrative assembly report and embedded it in story editorial snapshots.
- Bangla and English are kept separate, with no cross-language quality score.
- Each sentence-level segment retains its role, language, claim IDs, claim text, assessment, provenance, and evidence locators.
- Factual segments are marked supported only when all linked claims are assessed as supported and each has at least one supporting evidence record. Other factual segments remain unverified/disputed and block readiness.
- Publisher framing and other attributed positions remain separate from facts; editorial analysis remains explicitly labelled.
- Contradictory claims and both claim texts remain visible. A reviewed/accepted contradiction is not silently treated as resolved.
- Missing language review and non-approved learning objects are blockers. The report never grants production authorization.
- This is traceability logic, not a truth engine, linguistic quality certification, or proof of independent reporting. Section 7 remains in progress; Sections 8–10 remain future phases.

## Section 7.31: source-independence evidence gap report

- Added a read-only source-independence evidence report and embedded it in story editorial snapshots.
- The report distinguishes duplicate article review from reporting independence. A reviewed-distinct article is not automatically an independent report; no duplicate candidate is not evidence of independence.
- Independent-reporting count remains unset and all article independence statuses remain not established until original-reporting provenance, syndication/republication, shared-source/common-origin, and relevant primary-source evidence are assessed.
- Confirmed duplicate groups and unresolved duplicate candidates remain visible.
- The report exposes missing evidence requirements and never authorizes production.
- Actual reporting-lineage evidence and human review remain outstanding. Section 7 remains in progress; Sections 8–10 remain future phases.

## Section 7.32: language-review enforcement in narrative assembly

- Narrative assembly now checks the full language-specific rubric using the same pass criteria as the registry quality workflow.
- A present but rejected language review no longer slips through as sufficient evidence. Missing reviews and any needs-revision dimension remain blockers.
- Per-language reports include object/segment counts, factual and traceable coverage, unsupported factual segments, passing review counts, blocker counts, and a deterministic blocked/review-required state.
- Bangla and English remain independently assessed. review_required is not publication approval, and production authorization remains false.
- Regression tests cover a rejected review record and the resulting language-level blocker summary. Section 7 remains in progress; Sections 8–10 remain future phases.

## Section 7.33: MCQ distractor rationale traceability

- The learning-object contract requires one non-empty rationale keyed to every incorrect MCQ option and forbids a rationale for the correct option.
- Registry validation enforces exact option-ID coverage and rejects missing or duplicate rationale records.
- The quality report exposes a dedicated rationale-coverage check.
- Every MCQ quality report retains a human-review warning for plausibility, unambiguous incorrectness, misconception alignment, parallel construction, and accidental clues. The system does not infer distractor quality from rationale presence.
- This improves inspectability and source-grounded editorial review; it does not establish item validity or publication readiness. Section 7 remains in progress; Sections 8–10 remain future phases.
