# Engine Implementation Programme

Status: Sections 1–5 implemented to their documented scope; Section 6 in progress; Sections 7–10 planned  
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

**Status: in progress.** The provenance-integrity and initial human claim-assessment workflows are implemented and verified by GitHub Actions [run #44609541576](https://github.com/Amiroudro/new-sngso/actions/runs/44609541576): **328 tests passed** on code head `c0c62caac9bc5fb32d2d3d1511a274dc9bc93f11`. The workflow also blocks changing evidence relationships while a claim remains reviewed. The append-only story merge/split decision ledger base passed GitHub Actions [run #44610479213](https://github.com/Amiroudro/new-sngso/actions/runs/44610479213), with 336 tests passing. The explicit per-article allocation extension and its regression fixtures are verified by GitHub Actions [run #44611679213](https://github.com/Amiroudro/new-sngso/actions/runs/44611679213): **337 tests passed** on the current branch head. Decision records now capture expected revisions for every affected story and claim, complete claim assignments, and provenance-compatible destinations. The registry rejects stale revisions, missing claim assignments, and split allocations that separate a claim from its provenance articles. GitHub Actions [run #44611964846](https://github.com/Amiroudro/new-sngso/actions/runs/44611964846) verified these safeguards with **341 tests passing**. The safe application workflow is specified in [Section 6 story decision application design](SECTION_6_STORY_DECISION_APPLICATION_DESIGN.md). A read-only preview now reports affected stories, articles, claims, evidence, and historical editions, and returns a deterministic impact fingerprint. GitHub Actions [run #44612277463](https://github.com/Amiroudro/new-sngso/actions/runs/44612277463) passed **343 tests** including preview determinism and read-only behavior. A follow-up test explicitly checks historical-edition reporting and is awaiting CI. Atomic application remains future work. Event chronology also remains future work.

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

**Goal:** produce useful Bangla and English editorial outputs from reviewed evidence.

Tasks:
- Build three-level summaries, daily briefings, timelines, and multi-source perspectives.
- Preserve attribution, uncertainty, dates, quantities, and distinctions between fact and analysis.
- Add separate Bangla and English review states and a controlled glossary.
- Create source-grounded vocabulary and exam-learning objects.
- Establish human-reviewed benchmark fixtures.

Acceptance:
- Editorial outputs link back to claims/evidence.
- No unsupported detail is introduced during compression or translation.
- Learning questions have answerable, evidence-backed explanations.

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
| 5. Ingestion and identity | **Complete:** canonical URL/text/timestamp/language normalization; metadata-only permission-rechecked ingestion; exact observation provenance; deterministic article identity; ordered immutable metadata revisions; idempotency; cross-publisher duplicate candidates; reversible duplicate resolutions; migration 4; 297 passing tests | Story assignment and event clustering belong to Section 6; no live publisher integration is enabled |
| 6. Story intelligence and evidence | **Next** | Depends on the completed Section 5 article identity layer |
| 7. Editorial production | Not started | Depends on Section 6 |
| 8. Edition assembly | Not started | Depends on Section 7 |
| 9. Operations and automation | Not started | Depends on stable pipeline and publication gates |
| 10. End-to-end handover | Not started | Depends on all prior sections |

No section is formally complete until its acceptance gates are verified. Sections 2–5 have passed their documented acceptance gates and are closed for this implementation stage. Production limitations and source-specific approval requirements remain explicit gates, not hidden assumptions.

## Execution order

Complete Section 1 baseline first, then Sections 2–10 in order. A later section may be researched early, but implementation must respect the dependencies and acceptance gates above.
