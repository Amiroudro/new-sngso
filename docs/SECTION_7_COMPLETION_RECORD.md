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
- Added direct tests for authenticated rubric capture, identity attestation, missing/failed rubric rejection, non-MCQ misuse, and quality-gate enforcement. GitHub Actions [run #44749250816](https://github.com/Amiroudro/new-sngso/actions/runs/44749250816) passed the full Python 3.12 suite: **693 tests in 6.24 seconds** on code head cc7f4f5e7e3d38a4d6709a9c0b5fdee4c3fe1abe.
- The tests verify deterministic control behavior only; they are not editorial-quality certification. Real human use and benchmark evidence remain outstanding. Section 7 remains in progress.


## Section 7.41: engine-to-Flutter compatibility boundary (design only)

- Recorded the existing Flutter client's legacy Edition/Story/MCQ data shape and the difference from the evidence-first engine records.
- Added a compatibility design and staging gates in docs/ENGINE_TO_FLUTTER_COMPATIBILITY.md, including explicit preservation of claim/evidence/permission provenance outside fields that the current client parser discards.
- The adapter, manifest validator, executable shared contract, Flutter parser validation, and cross-repository golden tests remain unimplemented. Flutter tooling was not available in this session.
- No Sngs-plus files, bundled assets, existing edition snapshots, or publication schedules were changed.
- Section 7 remains open; this design note does not pass the compatibility exit criterion.


## Section 7.42: staging-only Flutter compatibility verifier (CI verified)

- Added a read-only verifier that binds a draft legacy client payload to an approved registered edition and exact approved English headline/summary learning-object revisions.
- It rechecks reviewer qualification, source/article citation metadata, republication grant status, endpoint scope, and exact story coverage.
- It blocks published payload status, stale records, mismatched text or citations, unsupported fields/collections, and missing or denied permission evidence. It makes no writes and grants no publication authorization.
- Synthetic tests cover successful staging verification and failure conditions. GitHub Actions [run #44752476466](https://github.com/Amiroudro/new-sngso/actions/runs/44752476466) passed the full Python 3.12 suite: **701 tests in 6.38 seconds** on code head ec06daf4d3abba0b8034d56c1f84bb470d63229d.
- The serializer, actual Flutter parser/golden tests, bilingual export, source grants, and production evidence remain open. Section 7 is not complete.


## Section 7.43: English Flutter staging builder (CI verified)

- Added an in-memory staging builder for the narrow English story subset.
- It requires explicit approved headline and summary object IDs for every story in an approved registered edition, then binds their exact revisions and fingerprints into a provenance manifest.
- It builds citation references from registered active sources/articles and delegates to the fail-closed verifier for reviewer, source grant, endpoint, and content checks.
- The result remains staging-only. It writes no edition file, changes no app asset, and grants no publication authorization.
- Tests verify successful candidate construction and incomplete-selection rejection. GitHub Actions [run #44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682) passed the full Python 3.12 suite: **703 tests in 6.68 seconds** on code head f7889295a9ca329852b518f96af68fb0b2b4b315.
- Flutter parser/golden tests, actual source republishing grants, bilingual and study-object mappings, and real human/operational evidence remain unresolved. Section 7 is still in progress.


## Section 7.52: bounded implementation close and handoff

**Implementation status: CLOSED for the engine-side scope defined in Section 7.** This closes the software workstream, not the real-world or production acceptance gates.

The Section 7 implementation now includes:
- Registered learning objects for editorial and exam-learning content, with revision history, evidence bindings, language-specific quality gates, and review traceability.
- Story-level editorial snapshots, narrative assembly, attribution and perspective separation, uncertainty/contradiction visibility, and source-independence evidence-gap reporting.
- Structured quality checks for summaries, vocabulary, comprehension/analysis questions, MCQs, and revision cards, including claim/evidence linkage and explicit human-review requirements.
- Separate Bangla and English review states, variant claim-coverage reporting, and a fail-closed pair verifier requiring independent language-qualified reviewer identities.
- A narrow English-only Flutter staging adapter and provenance contract. It remains an integration boundary, not a bilingual edition exporter or proof of Flutter runtime compatibility.

### What this close does and does not mean

Section 7's engine-side implementation scope is closed so work can proceed to Section 8 without adding more numbered Section 7 features. Do not reopen Section 7 merely to build edition packaging, publication controls, scheduled operations, or downstream app integration; those belong to Sections 8–10.

This is **not** production acceptance, editorial certification, or permission to publish. The following external evidence gates remain open and must be carried into the relevant later sections:
1. Genuine, independently conducted Bangla and English editorial reviews on representative real stories and learning objects.
2. Verified provider-side identity authentication, including real issuer/JWKS/revocation configuration and protected administrator bootstrap.
3. Current publisher permission evidence for every intended collection, quotation, and republication operation.
4. Independent editorial/statistical assessment of benchmark evidence and actual reviewer agreement; synthetic fixtures are not human-study results.
5. Deployed host/database security controls, backup and restore evidence, and operational signoff.
6. Flutter parser/runtime and cross-repository golden verification before any downstream client integration.

### Verification record and limits

The latest recorded full Python-suite result in this workstream is **753 passed** on code head `14ad3d02bdb285d98c995e7082785824805cf4c8`, reported by workflow run [#44906408948](https://github.com/Amiroudro/new-sngso/actions/runs/44906408948). Inspection of that workflow's job steps shows that run #44906408948 was actually named **Contract validation** and executed the contract-validation step, not `pytest`. Therefore the 753-test full-suite claim is **not independently substantiated by that run and must not be presented as CI-verified**. The subsequent run [#44906480066](https://github.com/Amiroudro/new-sngso/actions/runs/44906480066) on the documentation head also ran contract validation only. A dedicated full-suite CI result on the current head is still required before claiming a full CI test pass.

No Flutter runtime test, real publisher permission check, genuine human review, production security acceptance, publication, or live app update is claimed here.

### Handoff

- Section 7: implementation scope closed, external acceptance gates explicitly outstanding.
- Section 8: next. Begin with edition assembly and publication safety, using only approved and explicitly bound bilingual content.
- Section 9: operations, observability, and free-first automation.
- Section 10: end-to-end verification and controlled handover.

PR #7 remains draft and unmerged. No published edition, live schedule, Flutter repository, website, or production destination was changed.
