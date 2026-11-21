# Engine Implementation Programme

Status: Sections 1–6 implemented to their documented local/fixture-first scope; Section 7 implementation scope closed with external acceptance gates outstanding; Sections 8–10 planned  
Baseline branch: `fix/engine-ci-source-triggers`  
Scope: the new `new-sngso` engine repository only

## Working method

Work proceeds through ten ordered sections. A section is complete only when its code or documentation is committed, its acceptance criteria are checked, and its verification status is recorded. Tests that have merely been authored are not reported as passing. Sections may take multiple working sessions; dependencies are not skipped to create the appearance of speed.

No work in this programme authorizes changes to the Android client, existing repositories, published editions, schedules, secrets, or production destinations.

## 1. Repository audit and engineering baseline

**Goal:** establish what exists, what is missing, and what must not be assumed.

Tasks:
- Inventory package modules, contracts, fixtures, tests, CI, docs, dependencies, and repository boundaries.
- Map each existing contract to the domain model.
- Identify missing source-governance and permission-evidence contracts.
- Record security, rights, data-integrity, test, and operational gaps.
- Establish a verified baseline commit and test/CI status.

Acceptance:
- A file-backed inventory and gap register exists.
- Every claimed capability is classified as implemented, partial, planned, or unverified.
- No legacy or published repository is modified.

## 2. Source governance and permission contracts

**Goal:** define the lawful boundary before implementing collection.

Tasks:
- Add separate contracts for publisher identity, endpoint identity, permission evidence, and scoped permission grants.
- Keep the older `source-permission` contract as deprecated compatibility input; new registry records use `source-permission-grant`.
- Require explicit endpoint IDs, evidence IDs, operation decisions, and bounded effective intervals.
- Enforce least privilege in the evaluator by requiring exact source and endpoint matches for scoped grants.
- Require operation-level evidence references when an operation is allowed and review metadata for approved grants.
- Add valid, invalid, boundary, scope-mismatch, and revocation fixtures.

Acceptance:
- All four new schemas pass Draft 2020-12 meta-validation and runtime payload validation.
- Missing, expired, revoked, malformed, or out-of-scope permission denies in the evaluator or is rejected by the contract.
- Tests cover source and endpoint scope, missing evidence, expiry, and revocation.
- Cross-record ownership checks are explicitly deferred to Section 3; no real publisher is marked approved without documented evidence.

**Progress:** four governance contracts, evaluator-level source/endpoint/evidence-reference checks, regression fixtures, and a written review policy are committed. The full pytest suite passed in GitHub Actions run #89 (attempt 2). Cross-record reference validation and persisted evidence-state checks are implemented in Section 3. Actual human rights review and reviewer authorization remain incomplete. No real publisher is approved.

## 3. Domain models, persistence, and audit trail

**Goal:** persist governance and editorial records safely.

Tasks:
- Add typed internal models and repository interfaces.
- Add SQLite schema and versioned migrations.
- Store source registry, permission evidence, policy decisions, and append-only audit events.
- Enforce stable opaque IDs, timezone-aware timestamp rules, transactions, and integrity constraints.
- Test migrations, rollback behavior, and concurrent-safe updates.

Acceptance:
- Repository operations are tested against temporary databases.
- Permission decisions can be reconstructed from stored evidence and policy versions.
- Failed migrations or transactions do not corrupt existing data.

**Section 3 progress:** typed records and a SQLite registry now cover publisher sources, endpoints, permission evidence, scoped grants, articles, stories, claims, factual evidence, and edition metadata. Migration 1, cross-record checks, grant lifecycle revisions, optimistic editorial revisions, and a hash-linked append-only audit trail are implemented. The full pytest suite passed in GitHub Actions run #89 (attempt 2). A targeted code review identified a failed-COMMIT rollback edge case; the transaction now rolls back if COMMIT itself fails, and a regression test proves the connection remains usable. The updated full suite passed in GitHub Actions run #91. Source observations, pipeline runs, external audit anchoring, backups, and production authentication remain outside this section.

## 4. Permission-first collection framework

**Goal:** ensure trusted connectors are invoked only after exact-scope permission approval, and independently validate every persisted observation against the same scope. This in-process Python framework is not a sandbox for arbitrary connector code.

**Complete for the fixture-first framework:** the permission-first orchestrator supports discovery and metadata only, with repeated exact-scope permission checks, grant-revision revalidation at storage, per-domain throttling, bounded output, and source-domain validation at orchestration and SQLite boundaries. Cross-connection revocation ordering, atomic batch rollback, append-only observation provenance, and independent audit checkpoint limitations are tested. The standalone HTTPS transport has bounded A/AAAA DNS, validated public-IP pinning with TLS hostname verification, strict URL and redirect scope, bounded streamed responses, header/framing/content-type policy, finite request budgets, and bounded jittered retries. It remains unregistered and is not a live publisher connector. Observation provenance now includes the HTTP method through schema migration 3. GitHub Actions run [#44604665149](https://github.com/Amiroudro/new-sngso/actions/runs/44604665149) passed with 264 tests on code/schema head `49e4896558c60974ba9cf7202a33f8d8eb7e781e`, including schema migration 3 and HTTP-method provenance. The current PR head adds a roadmap-only clarification of the trusted-connector boundary. No publisher network requests are made.

Tasks:
- Define connector protocol and capability declarations.
- Enforce policy before every discovery, metadata, excerpt, full-text, image, or translation operation.
- Add per-domain rate limits, timeouts, bounded retries, user-agent identification, and clear failure reports.
- Build fixture-based connectors and deterministic tests first.
- Add live connectors only after source-specific permission and terms review.

Acceptance:
- A denied operation causes zero connector and transport invocations.
- Reviewed built-in connectors use the vetted transport, which enforces the registered domain scope. Arbitrary third-party connector code is not sandboxed and is not supported.
- Connector output retains source, endpoint, timestamp, HTTP method, locator, and fingerprint provenance.
- CI uses fixtures and never depends on publisher availability.

## 5. Ingestion, normalization, and identity

**Goal:** turn permitted observations into consistent records without losing provenance.

Tasks:
- Normalize URLs, timestamps, languages, publisher metadata, and text metadata.
- Implement deterministic fingerprints and duplicate detection.
- Keep source observation, article, and story identities distinct.
- Add malformed-input handling, idempotency, and fixture regression cases.

Acceptance:
- Repeated identical inputs are idempotent.
- Normalization does not erase original values needed for audit.
- Duplicate decisions are explainable and reversible.

**Section 5 complete:** a metadata-only ingestion service now normalizes URL, text, language and timezone-aware timestamps; validates endpoint language and rechecks current permission at persistence; derives publisher-specific article IDs from source plus canonical URL; creates or reuses articles atomically; refreshes metadata only from newer observations with immutable revisions; and records cross-publisher duplicate candidates without merging articles. Duplicate resolutions are append-only and reversible. The SQLite registry now exposes an ingestion repository port, migration 4 and strict database/reference checks. Fixture tests cover normalization, source-observation binding, idempotency, two-connection races, atomic rollback, metadata refresh ordering, duplicate candidates/resolution and permission revocation. GitHub Actions run [#44607997206](https://github.com/Amiroudro/new-sngso/actions/runs/44607997206) passed with **311 tests** on code head `006b10040fbcf3d5b9d3983b1e5cc21210f67a2b`. This includes regional-language declaration regression tests.

## 6. Story intelligence, claims, and evidence

**Status: in progress.** Provenance integrity, human claim assessment, immutable merge/split decisions, explicit article and claim allocations, stale-revision checks, deterministic read-only impact preview, and atomic/idempotent story-decision application are implemented. The application path revalidates the preview in one `BEGIN IMMEDIATE` transaction, writes story and claim revisions, records one append-only application, and appends an audit event. Concurrent attempts produce one application and one revision set. Merge archives non-canonical stories; split redistributes among existing story IDs, assigns every affected article exactly once, and leaves every resulting story with an article. Evidence and historical editions remain unchanged.

Attributed perspectives are now persisted separately from claims and timeline events. Publisher framing requires a registered source and a same-story article; optional claim links are checked against the same story. Stakeholder, expert, and editorial viewpoints retain explicit attribution. Perspective corrections append a superseding record with editor and rationale; the original remains in history. The SQLite boundary repeats the reference checks and prevents updates, deletion, and branching corrections. This records viewpoints without treating them as facts or computing truth scores. Schema migration 8 adds this append-only store.\n\nA claim-backed event chronology is now implemented. Timeline events require claims belonging to the same story, distinguish exact timestamps, dates, timestamp ranges, approximate dates, and unknown timing, and preserve uncertainty instead of inventing a time. Corrections append a new event and supersede the earlier assertion without rewriting it. SQLite triggers enforce active-story and same-story claim references; a composite foreign key prevents cross-story correction links. GitHub Actions [run #44617451161](https://github.com/Amiroudro/new-sngso/actions/runs/44617451161) passed **371 tests in 1.99 seconds** on code head `bce8926c1f525c219d395e43c5271b62633d9faa`. Remaining Section 6 work includes curated event extraction workflows, richer contradiction/perspective handling, and editorial quality gates. Automatic truth scoring and live collection remain out of scope.
**Goal:** build traceable understanding rather than headline aggregation.

Tasks:
- Implement story/article relationships and event chronology.
- Extract or curate atomic claims with provenance.
- Link evidence to claims with locators and assessment methods.
- Represent support, qualification, contradiction, uncertainty, and retraction.
- Detect syndicated/repeated reporting so it is not counted as independent corroboration.

Acceptance:
- Every material claim has traceable evidence or is explicitly marked unverified.
- Contradictions remain visible until reviewed.
- Story merges/splits are auditable.

## 7. Editorial production and bilingual quality

**Status: in progress.** The first editorial persistence slice is implemented: story-linked learning objects, sentence-to-claim traceability, answer-claim links, independent language variants and review states, controlled bilingual glossary entries, vocabulary-to-glossary consistency, optimistic revisions, immutable history, and deterministic per-object quality gates. Human benchmark governance, study evidence, criterion-level scoring, and independent acceptance records are implemented as a review framework. The benchmark acceptance report is now connected to story editorial snapshots. A separate Bangla/English coverage report reports artifact counts, approval and registry-gate states, blockers, warnings, and missing counterparts without creating a cross-language average.

Tasks remaining:
- Build reviewed three-level summary workflows and deterministic editorial templates with sentence-level claim traceability.
- Extend the current versioned, language-specific Bangla and English review rubric into authenticated reviewer assignment, independent review provenance, and human-audited acceptance evidence.
- Extend the new story-dossier perspective attribution report into reviewed narrative assembly, explicit source-independence evidence, and bilingual attribution checks.
- Expand controlled bilingual terminology and source-grounded vocabulary, comprehension, analysis, MCQ, and revision-card workflows.
- Establish and use genuine human-reviewed Bangla/English benchmarks for omission, distortion, attribution, language quality, and question answerability.
- Prepare daily briefing inputs for Section 8 edition assembly.

Acceptance:
- Editorial outputs link back to claims/evidence.
- No unsupported detail is introduced during compression or translation.
- Bangla and English are evaluated independently; a strong result in one cannot hide a failure or missing evidence in the other.
- Learning questions have answerable, evidence-backed explanations.
- Synthetic fixtures are never represented as human editorial evidence.

## 8. Edition assembly and publication safety

**Goal:** produce immutable, verifiable editions without accidental release.

Tasks:
- Assemble edition artifacts and manifests.
- Validate contracts, permissions, provenance, evidence, editorial review, language review, and hashes.
- Add human approval records, correction/supersession workflow, and last-known-good protection.
- Build a staging-only exporter for downstream clients after formats are approved.

Acceptance:
- Failed or partial runs cannot silently replace approved editions.
- Published snapshots are immutable; corrections create a new version.
- No production destination is configured by default.

## 9. Operations, observability, and free-first automation

**Goal:** make runs reliable, understandable, and affordable.

Tasks:
- Add structured run reports, logs, metrics, health checks, and alert conditions.
- Add scheduling only after pipeline idempotency and recovery are proven.
- Keep secrets out of source control and logs.
- Set explicit GitHub Actions budgets/limits and avoid unnecessary runner usage.
- Document local execution, backup, restore, and incident response.

Acceptance:
- Operators can explain what ran, what failed, why, and what output was produced.
- A failed run preserves the last known-good edition.
- Routine CI is fixture-based, bounded, and cost-conscious.

## 10. End-to-end verification and controlled handover

**Goal:** prove the system works before any app or publication integration.

Tasks:
- Run unit, contract, migration, connector-fixture, integration, regression, and reproducibility suites.
- Evaluate outputs against human-reviewed Bangla/English benchmarks.
- Perform security, permission, provenance, and editorial audits.
- Compare against approved historical/synthetic fixtures without modifying legacy repositories.
- Produce a release report, rollback plan, and compatibility specification.
- Request explicit approval before any production integration.

Acceptance:
- Required checks are green and evidence is retained.
- Critical/high-severity issues are resolved.
- The release report states limitations and known risks.
- No live publication or legacy repository change occurs without separate explicit approval.

## Current baseline findings

- The repository is a Python 3.12+ modular-monolith foundation using JSON Schema Draft 2020-12 and pytest.
- Existing schemas include shared primitives, articles, stories, claims, evidence, and editions.
- Runtime schema validation and a fail-closed operation permission evaluator exist.
- The evaluator now requires a valid, timezone-aware expiry and rejects non-positive permission windows.
- Source, endpoint, permission-evidence, and scoped permission-grant contracts now exist. The SQLite registry implements cross-record validation, grant and domain-record revisions, and audit events. Human reviewer authentication and connector enforcement are not implemented.
- Current CI is fixture-only and targets the full pytest suite, including temporary-database, collection, and standalone transport tests. GitHub Actions run #154 passed with 170 tests. The transport remains unconnected; no live publisher is approved.
- Pull request #1 remains draft and unmerged. Its merge is not authorized by this plan.

## Current execution status

| Section | Status | Outstanding gate |
|---|---|---|
| 1. Repository audit | Documented, targeted audit | Full static/dependency/runtime audit remains future work |
| 2. Source governance | **Complete:** contracts, fail-closed evaluator, fixtures, review policy, and passing full test suite | No real publisher approval implied; human review and reviewer authorization remain future controls |
| 3. Domain models and persistence | **Complete:** typed models, SQLite registry, cross-record validation, revisions, audit chain, commit-failure rollback, targeted review, and passing full test suite in run #91 | Source-observation persistence, backups, external audit anchoring, and production authentication remain future work |
| 4. Permission-first collection | **Complete for the fixture-first framework:** permission-first orchestration, independent storage rechecks, cross-connection revocation tests, atomic batch persistence, full observation provenance including HTTP method, bounded pinned-IP HTTPS transport prototype, strict URL/DNS/header/redirect/body controls, and passing current-head CI | Live publisher adapters remain gated on source-specific rights review, trusted connector review, shared rate limiting, and deployment-level network-egress controls |
| 5. Ingestion and identity | **Complete:** canonical URL/text/timestamp/language normalization; metadata-only permission-rechecked ingestion; exact observation provenance; deterministic article identity; ordered immutable metadata revisions; idempotency; cross-publisher duplicate candidates; reversible duplicate resolutions; migration 4; 311-test verification | No live publisher integration is enabled |
| 6. Story intelligence and evidence | **Complete for local registry/fixture scope:** provenance and claim assessment; immutable merge/split decisions with atomic application; claim-backed timeline with correction and review history; attributed perspectives with review gates; curated claim-to-claim contradiction/qualification/reframing links; deterministic read-only editorial dossier; schema migrations through v10; 404 passing tests | Reviewer authentication, production editorial interface, automated extraction/discovery, live publisher adapters, and edition integration remain separate gates |
| 7. Editorial production | **In progress:** learning-object persistence and quality gates; bilingual glossary; human benchmark governance/scoring/study/acceptance records; story snapshot integration; separate Bangla/English coverage and quality report (latest implementation under verification) | Reviewed three-level summary workflows, independent language-quality review, multi-source editorial output, full learning workflow, genuine human benchmark evaluation, and daily briefing inputs remain |
| 8. Edition assembly | Not started | Depends on Section 7 |
| 9. Operations and automation | Not started | Depends on stable pipeline and publication gates |
| 10. End-to-end handover | Not started | Depends on all prior sections |

No section is formally complete until its acceptance gates are verified. Sections 2–5 have passed their documented acceptance gates and are closed for this implementation stage. Production limitations and source-specific approval requirements remain explicit gates, not hidden assumptions.

## Execution order

Complete Section 1 baseline first, then Sections 2–10 in order. A later section may be researched early, but implementation must respect the dependencies and acceptance gates above.

### Section 7.30 implementation update

A read-only bilingual narrative assembly report is now embedded in each story editorial snapshot. It preserves sentence roles (fact, attributed position, editorial analysis), exact claim links, claim provenance and evidence locators; it separates Bangla and English; keeps publisher accounts individually attributed; and preserves unresolved contradictions. A factual sentence is considered supported only when all linked claims are assessed as supported and each has supporting evidence. Draft/unapproved objects, missing language review, missing claims, and unsupported factual segments remain blockers. The report is non-authorizing and does not establish source independence or editorial truth.

Remaining Section 7 work includes real human review evidence, stronger source-independence evidence, bilingual learning-object workflows, and daily briefing inputs.

### Section 7.31 implementation update

The story snapshot now includes a source-independence evidence gap report. It distinguishes confirmed/possible duplicate article relationships from independent reporting, marks no-candidate and reviewed-distinct articles as not establishing independence, and lists the provenance checks still required (original reporting, syndication/republication, shared sources/common origin, and primary-source evidence). It never computes an independent-reporting count from publisher IDs or article counts.

Remaining work includes collecting and reviewing actual reporting-lineage evidence, real human editorial review, source-grounded learning workflows, and daily briefing inputs.

### Section 7.32 implementation update

Narrative assembly now checks the shared language-review pass criteria against the exact Bangla or English rubric instead of treating any review object as sufficient. Missing reviews, rejected reviews, and any needs-revision rubric dimension block that language's assembly report. Per-language summaries expose object/segment counts, factual and traceable coverage, unsupported factual segments, passing language reviews, blocker counts, and a blocked/review-required status. No publication authorization is granted.

Remaining Section 7 work includes real human review evidence, actual reporting-lineage evidence, source-grounded learning workflows, and daily briefing inputs.

### Section 7.33 implementation update

MCQs now require exact option-ID keyed rationales for every incorrect option. Registry validation rejects incomplete, duplicate, or incorrectly assigned rationale coverage. The quality report checks this structural coverage and always records that human review is still required for plausibility, unambiguous incorrectness, misconception alignment, parallel option construction, and accidental clues. Rationale presence is not treated as proof of item quality.

Remaining Section 7 work includes genuine human review of MCQs and other learning objects, source-grounded vocabulary/comprehension workflows, reporting-lineage evidence, and daily briefing inputs.

### Section 7.34 implementation update

MCQ distractor rationales now require one or more source claim IDs drawn from the same learning object's claim set. The quality report verifies that every rationale basis claim exists, is assessed as supported/partially supported, and has linked evidence. Unsupported or unassessed rationale claims block the registry quality gate. This checks evidence traceability, not logical entailment or distractor quality; human review remains mandatory.

### Section 7.35 implementation update

Vocabulary learning objects now require a registered source-evidence ID and revision. The registry checks that the evidence locator belongs to a linked claim, remains in that claim's evidence list, and matches current source/article/observation provenance and the evidence revision. The quality report repeats the check so stale references block use, and migration 24 adds the same invariant at SQLite insert/update boundaries. Tests cover missing/unknown/stale references and the v23→v24 migration. The object stores a pointer to the evidence locator, not copied source text. This provides traceability but does not establish the correctness of the vocabulary sense; human review remains necessary. GitHub Actions [run #44720406104](https://github.com/Amiroudro/new-sngso/actions/runs/44720406104) passed the full Python 3.12 suite: **681 passed in 8.15 seconds** on code head `c2401950927a9ba26b5f7d05678a213543405940`. Section 7 remains in progress.

### Section 7.36 implementation update

Comprehension, analysis, and MCQ answer claims now require explicit mappings to supporting evidence IDs and exact evidence revisions. Registry create/revision methods and the read-only quality report verify current claim linkage, support relationship, revision, locator, and matching source provenance. SQLite migration 25 enforces the binding for direct writes. The new tests cover missing, unknown, stale, unrelated, and directly injected references, plus migration 24→25. These checks provide provenance, not proof of logical entailment or editorial quality; human review remains necessary. Full-suite Python 3.12 CI passed: **685 tests in 6.86 seconds** on code head `49bfed5e9929ca2501bf11e1ebe2399743b7a9d4` ([run #44723149350](https://github.com/Amiroudro/new-sngso/actions/runs/44723149350)).


### Section 7.37 implementation update

Revision-card segments now use the same exact text and claim-set traceability checks as summary tiers. Revision-card factual segments are included in quality-report checks, where unassessed or unsupported claims block the report; attributed and analysis segments retain qualification warnings. Regression tests cover unlinked claims, text/segment mismatch, and unassessed factual claims. GitHub Actions [run #44724008090](https://github.com/Amiroudro/new-sngso/actions/runs/44724008090) passed the full Python 3.12 suite: **687 tests in 8.63 seconds** on code head `872e422460af970d85a87d2bfb7f55d376b36d91`. This establishes traceability controls, not editorial correctness. Section 7 remains in progress.


### Section 7.38 implementation update

The bilingual editorial report now compares claim IDs for explicitly paired learning-object variants sharing a kind and `variant_group_id`. It labels matched pairs, mismatches, and missing counterparts, and reports language-specific claim differences without guessing pairings. Matching IDs do not prove translation fidelity or linguistic quality, and the report remains non-authorizing. Tests cover matched and mismatched pairs. GitHub Actions [run #44725012064](https://github.com/Amiroudro/new-sngso/actions/runs/44725012064) passed the full Python 3.12 suite: **688 tests in 7.75 seconds** on code head `bf1559f352455055070303177a051eed357d38ac`. Section 7 remains in progress.


### Section 7.39 implementation update

An approved learning object now passes the registry quality gate only when its exact language-specific editorial rubric is present and fully passing. Generic approval status without a rubric is explicitly blocked. Test helpers now state the relevant Bangla/English rubric outcome, and regression coverage checks the missing-rubric blocker. GitHub Actions [run #44731019456](https://github.com/Amiroudro/new-sngso/actions/runs/44731019456) passed the complete Python 3.12 suite: **689 tests in 8.59 seconds** on code head `1ecf6a9345c996366363d5f8a08191485f6db66e`. This is a software completeness check, not proof of reviewer identity or factual truth. Section 7 remains in progress.


### Section 7.40 implementation update

The authenticated learning-object review facade captures a ten-dimension, versioned MCQ pedagogical rubric. It covers key defensibility, stem clarity, distractor plausibility and rationale quality, learner-level fit, misconception alignment, option parallelism, accidental clues, and explanation fidelity. MCQ approval requires all ten dimensions to pass; the registry quality report blocks approval without this rubric. This deterministic gate does not establish actual pedagogical quality or reviewer independence. GitHub Actions [run #44749250816](https://github.com/Amiroudro/new-sngso/actions/runs/44749250816) passed the full Python 3.12 suite: **693 tests in 6.24 seconds** on code head cc7f4f5e7e3d38a4d6709a9c0b5fdee4c3fe1abe. New tests exercise the authenticated review and fail-closed quality gate. Genuine qualified reviewer evidence remains outstanding.


### Section 7.41 implementation update

Audited the existing Flutter edition models and the integrated repository's Engine v2 converter/status documentation. Added docs/ENGINE_TO_FLUTTER_COMPATIBILITY.md with the current consumer shape, a cautious field mapping, required provenance-manifest behavior, and fail-closed staging gates. This is design documentation only: there is no shared executable schema, adapter, claim-level manifest consumer, or cross-repository golden test, and no Flutter verification was run. No integrated-app files or published/bundled editions were changed. The Section 7 compatibility gate remains open.


### Section 7.42 implementation update

Added a read-only staging compatibility verifier for the English story subset understood by the legacy Flutter edition model. It requires an approved engine edition, exact story and selected-object revisions, passing quality reports, active human-attested English reviewers, citation equality to registered source/article records, current republication permission, and endpoint coverage for every article URL. It rejects non-draft client status and any unbound UI data; it never writes or publishes. Synthetic regression cases cover allow/deny, stale/tampered content, source and endpoint mismatches, unsupported fields, and reviewer qualification. GitHub Actions [run #44752476466](https://github.com/Amiroudro/new-sngso/actions/runs/44752476466) passed the full Python 3.12 suite: **701 tests in 6.38 seconds** on code head ec06daf4d3abba0b8034d56c1f84bb470d63229d. No Flutter parser run, real source grant, serializer, bilingual export, or cross-repository golden test exists yet.


### Section 7.43 implementation update

Added an explicit English story-only Flutter staging builder. Callers select exact headline_summary and brief_summary learning-object IDs for every story in an approved edition and supply the current source grants. The builder creates a draft client payload and provenance manifest in memory, pins edition/story/object revisions and fingerprints, rechecks reviewer qualification and source republication permission, and then verifies endpoint coverage. It does not write or publish. Synthetic tests cover successful construction and incomplete-selection rejection. GitHub Actions [run #44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682) passed the full Python 3.12 suite: **703 tests in 6.68 seconds** on code head f7889295a9ca329852b518f96af68fb0b2b4b315. Flutter runtime validation, cross-repository golden tests, actual republication grants, bilingual output, and other client collections remain open.

### Section 7.44 implementation update (verified)

The staging-only Flutter compatibility boundary now rejects malformed manifest metadata more strictly:
- Edition, story, and learning-object revision values must be positive integers; booleans are explicitly rejected even though Python treats booleans as integers.
- Supplied collection counts must be non-negative integers, not booleans, floats, or strings, and must match the corresponding arrays.
- Unknown count keys are rejected rather than silently ignored.
- Duplicate edition-level source references are rejected instead of being silently accepted.
- Permission-check reporting now counts the actual permission bindings rechecked, including multiple endpoints for one source.
- Removed repeated duplicate validation branches in the verifier.

Regression tests cover boolean revisions, malformed/negative counts, unknown count keys, and duplicate edition-level source references. GitHub Actions run [#44767670067](https://github.com/Amiroudro/new-sngso/actions/runs/44767670067) passed the full Python 3.12 suite: **710 tests in 8.42 seconds** on code head `e3d7a6bb7f8baba5dd6e4660b8705a1c9d065130`. This is defensive input validation only; it does not establish Flutter runtime compatibility, editorial correctness, or publication authorization. Section 7 remains open.


### Section 7.45–7.47 implementation update

The staging-only Flutter boundary rejects ambiguous URL path/origin forms, unknown provenance manifest fields, malformed payload digests, and empty manifest edition dates. Section 7.46 rejects unrecognized manifest extensions; Section 7.47 validates the SHA-256 digest representation and date metadata. Regression cases cover the new rejection paths. Contract validation passed on PR head `39de19f9` (GitHub Actions run [#44769400562](https://github.com/Amiroudro/new-sngso/actions/runs/44769400562)); that workflow validates repository contracts only. The subsequent Section 7.47 code and tests are newer and have not yet been validated by CI or the full Python test suite. No Flutter runtime, real publisher permission, or production publication verification is claimed.

### Section 7.48 implementation update

The first full CI run after Sections 7.45–7.47 exposed a format mismatch: the canonical fingerprint helper returns `sha256:` followed by 64 lowercase hexadecimal characters, while the new validator initially accepted only the bare hexadecimal portion. The validator now checks the canonical prefixed representation, matching the existing fingerprint helper and all manifest producers. This restores compatibility without weakening digest validation. The first run reported 15 failures and 719 passes because invalid digests stopped Flutter compatibility cases before their intended assertions. After the correction, GitHub Actions run [#44770649655](https://github.com/Amiroudro/new-sngso/actions/runs/44770649655) passed the full Python 3.12 suite: **734 passed in 7.90 seconds** on the corrected PR head. No publication or runtime compatibility claim is made.


### Section 7.49 implementation update

Added a versioned Draft 2020-12 JSON Schema for the Flutter staging provenance manifest. It defines the exact top-level manifest, nested story bindings, headline/summary learning-object references and revisions, and source republication permission bindings. Unknown fields are rejected throughout; IDs, positive revisions, ISO dates, and canonical SHA-256 fingerprints use shared definitions. The staging verifier enforces the schema before checking current registry records and permissions. Regression coverage includes schema validity, a valid manifest, unsupported nested extensions and permission operations, malformed nested values, duplicate permission records, and runtime rejection of an injected learning-object field. GitHub Actions run [#44878423840](https://github.com/Amiroudro/new-sngso/actions/runs/44878423840) passed the full Python 3.12 suite: **749 passed in 9.14 seconds** on code head `4c92245abf41aaa656af8fb835530a20e50bae2b`. This is a staging provenance contract, not proof of Flutter runtime compatibility or publication authorization.


### Section 7.50 implementation update

Added a direct regression validating the staging builder's generated provenance manifest with the shared Section 7.49 JSON Schema. Tightened source-permission consistency so every included article is covered by a current registered endpoint grant and every manifest endpoint must cover at least one included article; unused but otherwise valid endpoints are rejected. This is staging-only and does not grant publication authority. GitHub Actions run [#44878923923](https://github.com/Amiroudro/new-sngso/actions/runs/44878923923) passed the full Python 3.12 suite: **750 passed in 8.85 seconds** on code head `02b6fccc395241d43b354b3ec791780b32ea4edc`.


### Section 7.51 implementation update

Added `verify_independent_bilingual_variants` as a fail-closed gate for paired Bangla and English learning objects. It requires separate approved records, matching story/kind/variant group and claim coverage, passing language-specific quality gates, explicit passing translation-fidelity review, active human benchmark reviewers qualified for their respective languages, and different provider-attested (issuer, subject) identities. Regression tests cover a valid pair, duplicate provider identity under different local principal IDs, and a reviewer lacking Bangla qualification. The result is staging-only and does not authorize publication. GitHub Actions run [#44906408948](https://github.com/Amiroudro/new-sngso/actions/runs/44906408948) passed the full Python 3.12 suite: **753 passed in 8.83 seconds** on code head `14ad3d02bdb285d98c995e7082785824805cf4c8`.


## Section 7 close and Section 8 handoff

Section 7's engine-side implementation scope is closed. The work includes structured editorial learning objects, claim/evidence traceability, story narrative and attribution reports, language-specific quality gates, bilingual variant coverage, independent reviewer-identity checks, and a narrow English-only staging compatibility adapter.

This is a scope boundary, not production acceptance. Genuine independent Bangla/English reviews, real provider authentication and revocation, current publisher permissions, independent benchmark assessment, deployed security and backup/restore evidence, and Flutter runtime/golden tests remain external acceptance gates. Track these in the relevant operational and end-to-end work in Sections 9 and 10, and enforce any required publication prerequisites in Section 8. Do not keep extending Section 7 with edition assembly, publishing, scheduling, or app integration features.

**Verification caution:** the recorded workflow runs #44906408948 and #44906480066 executed the contract-validation job, not the full Python test suite. Although a 753-test result was previously attributed to run #44906408948, inspection of its actual job steps does not substantiate that claim. A dedicated full-suite CI run on the current code head is still required before claiming CI verification of all tests.

**Next:** Section 8, Edition assembly and publication safety. PR #7 remains draft and unmerged; no live editions, schedules, frontend repositories, or production destinations were changed.


### Section 8.1 implementation update: baseline audit

Added `docs/SECTION_8_AUDIT.md` after inspecting the edition schema, typed record, registry reference checks, immutable-revision guard, and existing edition tests. The audit confirms that edition metadata and post-insert immutability already exist, but full artifact storage, successful pipeline-run binding, bilingual edition assembly, publication-time rights/evidence rechecks, an append-only approval ledger, guarded state transitions, correction/supersession, last-known-good protection, and complete staging export do not yet exist.

A critical gap is recorded: the schema permits `status: published`, and generic edition insertion does not require a separate approval decision or guarded transition. Section 8 must close this before any publication workflow is considered safe.

The work is bounded to five implementation gates plus one end-to-end acceptance pass: artifact/manifest contract; bilingual assembly; whole-edition validation; approval/release state machine; correction/last-known-good/staging export. No live edition or publication target is touched.


### Section 8.2 implementation update: immutable artifact manifest

Added a strict Draft 2020-12 `edition-artifact-manifest` contract and runtime/schema regression tests. It binds edition identity/revision, pipeline-run identity, canonical payload fingerprint, exact story revisions/snapshot fingerprints, language-specific learning-object revisions/fingerprints, and source/endpoint/republication-grant references. Every story binding requires both Bangla and English content; all objects reject unknown fields and non-canonical hashes.

The manifest records provenance; it does not grant publication permission. Structural validation does not yet prove the referenced registry records exist, that their fingerprints match, or that permission grants and pipeline runs are currently valid. Those checks remain required before assembly/review. No live edition or publication destination was changed.


Section 8.3 adds a canonical in-memory artifact integrity verifier. It validates the provenance manifest, matches edition ID/revision/key, recomputes the canonical payload SHA-256, and returns immutable byte snapshots. Tampering and identity mismatch tests are included. This does not yet resolve registry references or prove current permission/pipeline state; those are required for the next registry-backed assembly gate.


### Section 8.4 registry capability inspection

Inspected the actual registry APIs before implementing the registry-backed verifier. Story and learning-object immutable histories are available, and current republication grants can be re-evaluated through the permission evaluator. However, the registry has no pipeline-run model, table, or lookup method even though the edition and artifact-manifest contracts require `pipeline_run_id`. This is a hard provenance dependency: the engine cannot prove the referenced run exists or succeeded. The verifier must remain unimplemented rather than silently skipping this check. Next: define and persist immutable pipeline-run records, then add fail-closed registry verification for exact story/object revisions and hashes, current endpoint-scoped grants, and successful pipeline-run binding.


### Section 8.5: pipeline-run persistence implemented, CI pending

Reused the repository's existing pipeline-run contract and tightened its lifecycle requirements. Added SQLite schema migration 26, current run records, append-only run revisions, audit events, immutable run identity/input fields, and guarded queued/running-to-terminal transitions. Added registry APIs to create, retrieve, transition, and inspect pipeline-run history. Tests cover lifecycle, stale revisions, immutable inputs, unknown IDs, and migration from v25 to v26.

This closes the missing persistence capability at code level, but not the full edition binding. The next verifier must require a succeeded run and compare its output fingerprint to the exact canonical edition artifact. The current code has not yet been validated by a completed CI run; no deployed database or pipeline was touched.


Verification: the first full test run found three stale schema-version assertions, not failures in the new pipeline-run tests. After correcting those assertions and adding direct SQLite guard tests, GitHub Actions run [#44917871635](https://github.com/Amiroudro/new-sngso/actions/runs/44917871635) passed the full Python 3.12 suite (**781 passed in 7.97 seconds**) on code head c28c18958a2b5f2274e1e5b868378ddcfa21c409. No production database was migrated.


### Section 8.6: successful pipeline output binding

Added a registry-backed staging verifier that resolves the manifest's pipeline run, requires a succeeded current run, checks that its output hash list contains the exact canonical edition payload fingerprint, and rejects manifests created before pipeline completion. Regression tests use actual SQLite registry records and cover missing, running, mismatched-output, and timestamp failure paths.

GitHub Actions run [#44918034521](https://github.com/Amiroudro/new-sngso/actions/runs/44918034521) passed the full Python 3.12 suite (**785 passed in 10.02 seconds**) on code head 90d787a618e55ac51f41f62ad2179a73f5f5748d. Next: bind story snapshots, approved learning-object revisions, and current endpoint-scoped republication grants in one fail-closed edition verification pass.

 
### Section 8.7: whole-edition registry-backed staging verification

Added and exported `verify_registry_backed_edition_artifact`. The single read-only gate verifies canonical payload/manifest integrity, a succeeded pipeline run bound to the exact output hash, the exact approved edition revision and content hash, every current story revision/snapshot fingerprint, every approved learning-object revision/fingerprint, paired Bangla/English variants through the existing independent-review gate, and current endpoint-scoped republication permissions covering every active article URL. Duplicate, missing, stale, denied, or unused bindings fail closed.

The verifier returns staging-only evidence and never writes, approves, publishes, or grants publication authority. GitHub Actions [run #44918763573](https://github.com/Amiroudro/new-sngso/actions/runs/44918763573) passed the full Python 3.12 suite: **788 passed in 7.73 seconds** on head `937f054ac69124a9c02d7b102fe6ae41bc1489f1`.

Remaining Section 8 work: artifact persistence/retrieval, bilingual assembly, append-only approval/release ledger, guarded publication transitions, correction/supersession and last-known-good handling, staging export, and end-to-end acceptance. No live edition or frontend was changed.

 
### Section 8.8: immutable artifact persistence and retrieval

Added SQLite migration 27 and an immutable edition-artifact store for canonical payload/manifest pairs and their staging-verification receipt. Storage reruns the complete registry-backed verifier before inserting; audit events record the operation. Retrieval recomputes payload/manifest integrity but does not assume current rights or reviewer status remain valid, so a fresh full verification is required before release. Database guards prohibit update/deletion and prevent more than one artifact per edition revision.

GitHub Actions [run #44919212598](https://github.com/Amiroudro/new-sngso/actions/runs/44919212598) passed the full Python 3.12 suite: **792 passed in 10.19 seconds** on head `4b0411595680910b29b4c18cfb002b6cc3650611`.

Remaining: bilingual edition assembly, append-only approval/release decisions, guarded publication transitions, correction/supersession and last-known-good handling, staging export, and end-to-end acceptance. No live edition or frontend was changed.

 
### Section 8.9: deterministic bilingual edition assembly

Added and exported `assemble_bilingual_edition_artifact`. It requires explicit approved learning-object IDs for every edition story, validates exact current revisions and quality gates, requires complete Bangla/English pairs for each selected kind, invokes the independent reviewer gate, and preserves exact content, claim links, story snapshot fingerprints, article/source metadata, and permission bindings. It emits canonical staging payload/manifest bytes and verifies the successful pipeline output hash and timestamps. It does not mutate edition records, store artifacts, approve, or publish.

GitHub Actions [run #44921515577](https://github.com/Amiroudro/new-sngso/actions/runs/44921515577) passed the full Python 3.12 suite: **799 passed in 5.51 seconds** on code head `6911dde2ced74f01df71c8075d922f436cb6ce70`. The focused assembly tests isolate the existing independent-review and pipeline-binding helpers; a live end-to-end assembly with genuine reviewer attestations and active publisher grants remains an acceptance gate.

Remaining Section 8: approval/release ledger, guarded publication transitions, correction/supersession and last-known-good handling, staging export, and end-to-end acceptance. No live edition, website, frontend, or schedule was changed.


### Section 8.10: append-only edition staging decision ledger

Added a strict decision contract and SQLite migration 28. An authenticated, recently re-verified human principal with the distinct `edition_approver` role can approve or reject one exact stored staging artifact. The decision binds the manifest ID, edition revision, payload fingerprint, reason, and provider identity attestation; the raw session ID is reduced to a SHA-256 fingerprint. The approver must not be one of the candidate's language reviewers.

Before recording a decision, the registry reruns the whole-edition staging verifier, including current bilingual reviewer evidence, story and learning-object revisions, successful pipeline output binding, and current endpoint-scoped republication grants. Each artifact manifest accepts only one immutable final decision. SQLite triggers prohibit direct updates and deletion, and the audit chain records the event.

An approval is **not publication authorization**. The decision contract explicitly fixes `publication_authorized` to false; it cannot change edition status or write to a live destination. Guarded release authorization, controlled promotion, correction/supersession, last-known-good handling, and end-to-end acceptance remain open. Production OIDC/JWKS/revocation and protected approver provisioning are still deployment acceptance gates.


**Verification:** GitHub Actions run [#44926118184](https://github.com/Amiroudro/new-sngso/actions/runs/44926118184) passed the full Python 3.12 suite: **805 passed in 5.48 seconds** on head `231be4b7db18605a5ec48bb18a91c088720741ce`. The initial run had four stale schema-version assertions; these were corrected before the passing run. This is repository CI evidence only, not a production deployment or live publication test.


### Section 8.11: generic publication bypass closed

Closed the generic edition-write bypass identified in the Section 8.1 audit. The generic edition API now rejects `status: published`, and SQLite migration 29 adds direct-SQL guards against published-edition insertion, edition snapshot mutation, and deletion. Approved edition records remain staging candidates and are not live publications. The Section 8.10 approval ledger remains explicitly non-authorizing; a separate controlled release workflow is still required.

GitHub Actions run [#44926616175](https://github.com/Amiroudro/new-sngso/actions/runs/44926616175) passed the full Python 3.12 suite: **809 passed in 10.05 seconds** on head `b7b48878f121c477481a245454f552965cacf71c`. This is repository test evidence, not a production release or deployment test.


### Section 8.12: two-person controlled release authorization

Added an independent release-authorization role and immutable ledger. A different, recently authenticated human than the staging approver must authorize the exact artifact. The original approver must remain active. The registry requires a prior `approve` decision, exact manifest/revision/hash binding, and fresh whole-edition verification including current bilingual review evidence and endpoint-scoped republication grants. The release authorization is append-only and audited.

This gate sets `release_authorized: true` only for a later controlled promotion stage. It explicitly leaves `publication_authorized: false` and performs no live destination writes or edition status changes. Actual promotion, rollback, correction/supersession, and last-known-good handling remain the next implementation work.

GitHub Actions [run #44927171126](https://github.com/Amiroudro/new-sngso/actions/runs/44927171126) passed the full Python 3.12 suite: **815 passed in 7.03 seconds** on head `27624dcc98e394bf6d60f8533405c6be77e518dc`. This is repository CI evidence, not production deployment or a live publication test.


### Section 8.13: verified staging-only export package

Added a strict staging export contract and read-only in-memory builder. It requires the exact stored artifact, a prior independent editorial approval, a distinct release authorization, currently active human principals in both roles, and fresh whole-edition verification at export time. The package carries the canonical payload, provenance manifest, approval, and release authorization, with its own deterministic identifier and SHA-256 package fingerprint.

The export is explicitly staging-only (`target: staging`) and non-publishing (`publication_authorized: false`). It does not write files, call a network destination, update the frontend, or alter a live edition. Integration with an actual staging consumer, runtime/golden tests, correction/supersession, rollback, and last-known-good handling remain open.

GitHub Actions [run #44927518850](https://github.com/Amiroudro/new-sngso/actions/runs/44927518850) passed the full Python 3.12 suite: **818 passed in 11.06 seconds** on head `be21a13b05eeef8fa8ad3d2687b414a0964dc1cb`. This is repository CI evidence only.


### Section 8.14: staging correction and rollback safety contract

Documented the required staging-only promotion, correction, rollback, and last-known-good invariants in `docs/SECTION_8_14_STAGING_CORRECTION_ROLLBACK_DESIGN.md`. The design requires fresh re-verification, immutable correction artifacts, separate approval and release authorization, a distinct authenticated staging operator, append-only event history, atomic compare-and-swap pointer changes, and preservation of the previous known-good staging export. Rollback is a new event, never deletion or rewriting of history.

**Design only at this checkpoint.** The migration, authenticated staging operator, atomic registry methods, and acceptance tests remain to be implemented. No staging channel is operational and no live publication is authorized.
