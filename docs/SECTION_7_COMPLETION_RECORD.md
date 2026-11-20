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

## Section 7.34: source-claim support for MCQ distractor rationales

- Each distractor rationale must identify one or more basis claim IDs from the same learning object.
- The quality report checks that all basis claims exist, are assessed as supported or partially supported, and have linked evidence.
- Unassessed, disputed, contradicted, retracted, missing, or evidence-free basis claims block the registry quality gate.
- The check establishes source traceability only. It does not prove logical entailment, pedagogical quality, or that a distractor is unambiguously wrong. Human review remains required.
- Section 7 remains in progress; Sections 8–10 remain future phases.

## Section 7.35: vocabulary source-evidence binding (verified)

- Vocabulary objects now require a `source_evidence_id` and exact `source_evidence_revision` pointing to a registered evidence record and its locator.
- Create and revision validate that the evidence belongs to an object claim, remains linked from that claim, and matches current source/article/observation provenance and evidence revision.
- A read-only quality report repeats the check and blocks missing, stale, or mismatched references. SQLite migration 24 adds insert/update guards for direct writes.
- The contract and tests cover missing IDs, unknown evidence, stale evidence revisions, stale claim links, direct-SQL bypass, and migration 23→24.
- The vocabulary object stores an evidence reference, not a copied source sentence. Linkage gives traceability; glossary correctness and contextual meaning still need qualified human review.
- GitHub Actions [run #44720406104](https://github.com/Amiroudro/new-sngso/actions/runs/44720406104) passed the full Python 3.12 suite: **681 passed in 8.15 seconds** on code head `c2401950927a9ba26b5f7d05678a213543405940`. Section 7 remains in progress; this slice does not close its production/security gates.

- Comprehension, analysis, and MCQ objects require a structured `answer_evidence` mapping for each `answer_claim_id`.
- Each mapping pins a registered evidence record and revision. Registry creation/revision checks the claim link, supporting relationship, exact revision, locator, and current source/article/observation provenance.
- The quality report rechecks each mapping and blocks missing, unrelated, stale, or non-supporting references. SQLite migration 25 enforces the same binding for direct inserts and updates.
- Tests cover missing/unknown/stale references, stale claim links, migration 24→25, and direct-SQL bypass.
- The evidence link establishes traceability, not logical entailment or question quality. Human review remains necessary to judge whether the answer is defensible and preserves uncertainty and attribution.
- GitHub Actions [run #44723149350](https://github.com/Amiroudro/new-sngso/actions/runs/44723149350) passed the full Python 3.12 suite: **685 passed in 6.86 seconds** on code head `49bfed5e9929ca2501bf11e1ebe2399743b7a9d4`. Section 7 remains in progress; no production or publication authorization is added.


## Section 7.36: question answer-evidence binding (verified)

- Comprehension, analysis, and MCQ objects require a structured `answer_evidence` mapping for each answer claim, pinned to a registered evidence record and exact revision.
- Registry creation/revision, quality reporting, and SQLite migration 25 recheck claim ownership, current claim linkage, supporting relationship, locator, source provenance, and staleness.
- Tests cover missing, unknown, stale, unrelated, and direct-SQL bypass references, plus migration 24→25.
- GitHub Actions [run #44723149350](https://github.com/Amiroudro/new-sngso/actions/runs/44723149350) passed the full Python 3.12 suite: **685 passed in 6.86 seconds** on code head `49bfed5e9929ca2501bf11e1ebe2399743b7a9d4`.
- Traceability does not prove logical entailment or question quality. Human review remains necessary; Section 7 is still in progress.

## Section 7.37: revision-card claim traceability (verified)

- Revision-card segments now receive exact text-to-segment and segment-to-object claim checks, matching the summary traceability contract.
- The quality report applies factual-segment assessment and evidence requirements to revision cards, while retaining qualification warnings for attributed and analysis segments.
- Regression tests cover out-of-object claims, text/segment mismatch, and unassessed factual claims.
- GitHub Actions [run #44724008090](https://github.com/Amiroudro/new-sngso/actions/runs/44724008090) passed the full Python 3.12 suite: **687 passed in 8.63 seconds** on code head `872e422460af970d85a87d2bfb7f55d376b36d91`.
- These safeguards establish traceability and assessment checks, not factual correctness or pedagogical value. Section 7 remains in progress; Sections 8–10 remain future work.


## Section 7.38: bilingual variant claim-coverage report (verified)

- The bilingual report now compares claim-ID sets only for learning objects sharing an explicit `variant_group_id` and kind.
- It reports matching coverage, missing language counterparts, or exact Bangla-only and English-only claim IDs. It does not infer a pairing from kind or language alone.
- Tests cover matching and mismatched claim sets and confirm the report remains non-authorizing.
- GitHub Actions [run #44725012064](https://github.com/Amiroudro/new-sngso/actions/runs/44725012064) passed the full Python 3.12 suite: **688 passed in 7.75 seconds** on code head `bf1559f352455055070303177a051eed357d38ac`.
- Claim-set parity is structural only; it does not prove translation fidelity or equivalent nuance. Section 7 remains in progress.


## Section 7.39: language-rubric quality gate (verified)

- The learning-object quality report now requires a complete, passing language-specific rubric in addition to approved status. Generic approval without Bangla/English rubric evidence blocks the quality gate.
- Positive approval fixtures now carry language-appropriate rubric dimensions, and a negative test verifies that generic approval alone cannot pass.
- This deterministic gate checks rubric completeness only. It does not authenticate the reviewer or prove editorial truth; the trusted review facade and operational acceptance remain separate requirements.
- GitHub Actions [run #44731019456](https://github.com/Amiroudro/new-sngso/actions/runs/44731019456) passed **689 tests in 8.59 seconds** on code head `1ecf6a9345c996366363d5f8a08191485f6db66e`.
- Section 7 remains in progress.


## Section 7.40: authenticated MCQ pedagogical review rubric (CI verified)

- Added the optional mcq_review contract with version mcq-editorial-v1 and ten required dimensions covering key defensibility, stem clarity, distractor plausibility and rationale quality, learner-level fit, misconception alignment, option construction, accidental clues, and explanation fidelity.
- The authenticated reviewer facade requires the complete rubric for MCQ approvals/rejections, records provider-backed attestation, and rejects approval with any needs_revision finding.
- Registry validation checks reviewer, timestamp, language, version, and rubric structure. The quality report blocks approved MCQs lacking a complete all-pass rubric.
- GitHub Actions [run #44733507721](https://github.com/Amiroudro/new-sngso/actions/runs/44733507721) passed the full Python 3.12 suite: **689 tests in 7.56 seconds** on code head d6921b020e92355a5117910babf88430b514c44b. This run contains no dedicated regression cases for the new MCQ rubric path.
- No dedicated tests were added for the new MCQ route in this slice. Treat this as implemented but awaiting targeted regression coverage and real human use; it is not an editorial-quality certification. Section 7 remains in progress.
