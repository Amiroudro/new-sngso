# Engine Implementation Programme

Status: execution plan  
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

**Progress:** four governance contracts, evaluator-level source/endpoint/evidence-reference checks, regression fixtures, and a written review policy have been committed. Tests are authored but await a successful CI run. Cross-record reference validation, evidence-record verification, and actual human review remain incomplete.

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

**Section 3 progress:** typed records and a SQLite registry now cover publisher sources, endpoints, permission evidence, scoped grants, articles, stories, claims, factual evidence, and edition metadata. Migration 1, cross-record checks, grant lifecycle revisions, optimistic editorial revisions, and a hash-linked append-only audit trail are implemented. Fixture tests cover the main integrity and rollback cases, but CI has not executed them successfully yet. Source observations, pipeline runs, external audit anchoring, backups, and production authentication remain outside this section.

## 4. Permission-first collection framework

**Goal:** make it technically impossible for a connector to collect beyond approved scope.

Tasks:
- Define connector protocol and capability declarations.
- Enforce policy before every discovery, metadata, excerpt, full-text, image, or translation operation.
- Add per-domain rate limits, timeouts, bounded retries, user-agent identification, and clear failure reports.
- Build fixture-based connectors and deterministic tests first.
- Add live connectors only after source-specific permission and terms review.

Acceptance:
- Denied operations cause no network request.
- Connector output retains source, endpoint, timestamp, method, locator, and fingerprint provenance.
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

## 6. Story intelligence, claims, and evidence

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
- Current CI is fixture-only and now targets the full pytest suite, including temporary-database tests. GitHub runner allocation has blocked successful execution; tests are not verified as passing.
- Pull request #1 remains draft and unmerged. Its merge is not authorized by this plan.

## Execution order

Complete Section 1 baseline first, then Sections 2–10 in order. A later section may be researched early, but implementation must respect the dependencies and acceptance gates above.
