# Section 8.1: edition assembly and publication-safety baseline audit

**Status:** Audit complete. No publication workflow has been added by this document.

## Scope

Section 8 is responsible for turning reviewed editorial material into immutable edition artifacts, validating the entire artifact, and preventing accidental or unauthorized release. Section 7's learning-object quality checks and bilingual reviewer-pair verifier are inputs to this process, not a substitute for edition-level controls.

## What already exists

1. `contracts/v1/edition.schema.json` defines edition metadata: ID, edition key, creation time, status, content hash, pipeline-run ID, story IDs, language variants, and optional approval/publication timestamps and supersession metadata.
2. `EditionRecord` is a typed immutable view of that metadata.
3. `SQLiteRegistry.add_domain_record("edition", ...)` validates the schema and checks that every referenced story exists.
4. `SQLiteRegistry.revise_domain_record("edition", ...)` rejects revisions. A regression test confirms an inserted published snapshot cannot be revised.
5. The Section 7 Flutter adapter can build and verify a narrow **English-only, story-only staging candidate** with a separate provenance manifest. It does not write or publish an edition and does not validate the full bilingual edition workflow.

## Gaps that Section 8 must close

- **No edition artifact store:** the registry stores metadata and a content hash, not a canonical immutable bilingual payload/manifest pair that can be independently reconstructed and verified.
- **No pipeline-run foreign-key check:** the edition schema requires `pipeline_run_id`, but edition reference validation currently checks story IDs only. It does not prove that the referenced pipeline run exists and succeeded.
- **No fail-closed publication transition:** the schema permits `status: "published"`, and the generic edition insert path does not require a separate approval decision or a validated transition. A caller can insert a record already labelled published.
- **No edition-level approval ledger:** there is no dedicated append-only decision binding an approver, role, timestamp, exact edition revision, manifest fingerprint, scope, and decision.
- **No full bilingual assembly gate:** the current Flutter builder is English-only. The Section 7 pair verifier checks a pair of learning objects, but no edition assembler requires complete Bangla and English coverage for every included story and content kind.
- **No complete publication-time rights/evidence recheck:** edition insertion checks story existence, not every article, current scoped permission grant, source evidence, review state, or the selected learning-object revisions across the whole edition.
- **No correction and promotion lifecycle:** supersession fields exist in the schema, but no validated correction chain, last-known-good pointer, atomic promotion, or rollback protocol is established.
- **No complete staging export contract:** the existing adapter covers a constrained legacy Flutter subset, not the complete bilingual edition and its learning collections.

## Bounded Section 8 delivery plan

Keep this section to five implementation gates, followed by one end-to-end acceptance pass:

1. **Artifact and manifest contract:** define canonical edition payload and provenance manifest, immutable IDs/revisions/fingerprints, strict schemas, and deterministic hashing.
2. **Bilingual assembly:** require explicit approved Bangla and English bindings for every included story and supported learning-object kind; invoke the Section 7 independent-pair verifier; reject missing, duplicate, stale, or mismatched bindings.
3. **Whole-edition validation:** recheck story snapshots, claim/evidence links, reviewer and language gates, current source permissions, article endpoint scope, coverage, counts, and all hashes before an edition can enter review.
4. **Approval and release state machine:** make draft/review/approved/published transitions explicit, append-only and role-bound; prohibit direct insertion as published; keep production destinations disabled by default.
5. **Correction, last-known-good and staging export:** corrections create a new immutable edition linked to the superseded edition; failed candidates never replace the last-known-good artifact; export remains staging-only until separately authorized.

The final Section 8 acceptance pass must exercise the entire lifecycle, including rejection paths and crash/failure behavior. It must not be replaced by isolated schema tests.

## Safety boundary

No live edition, existing published file, frontend repository, production destination, or schedule is modified by this audit. PR #7 remains draft and unmerged. The Section 7 external acceptance blockers remain visible and must be enforced where relevant; Section 8 does not convert synthetic evidence into real human review or publisher permission.


## Section 8.2: immutable edition artifact manifest contract

Added `contracts/v1/edition-artifact-manifest.schema.json` and focused schema/runtime tests.

The manifest contract binds:
- Immutable edition ID, revision, edition key, creation timestamp, and pipeline-run ID.
- SHA-256 of the canonical edition payload.
- Every included story ID, exact story revision, and story-snapshot fingerprint.
- Explicit learning-object IDs, revisions, fingerprints, content kind, and language for each story. Every story binding must include at least one Bangla and one English learning object.
- Source, endpoint, and permission-grant IDs, with the operation fixed to `republication`.

The schema rejects unknown fields at every level, malformed fingerprints, non-positive revisions, duplicate bindings, missing language variants, empty permission bindings, and any permission operation other than republication. The contract deliberately does not contain a `publication_authorized` boolean: a provenance manifest records what an artifact is bound to; it cannot grant publication authority.

**Limit:** this is a structural provenance contract only. It does not yet retrieve registry records, prove that hashes match actual stored payloads, verify current grant validity, check pipeline-run success, or assemble an edition. Those are runtime gates for the next Section 8 work. No edition payload was written and nothing was published.


## Section 8.3: canonical in-memory artifact integrity verifier

Added `src/sngso_engine/edition_artifacts.py` with `verify_edition_artifact(payload, manifest)` and immutable `VerifiedEditionArtifact` byte output.

The verifier:
- Validates the strict manifest contract before using it.
- Requires the payload's edition ID, revision, and edition key to match the manifest.
- Rejects booleans masquerading as integer revisions.
- Canonically serializes the payload and checks its SHA-256 fingerprint against the manifest.
- Returns immutable payload and manifest bytes plus the verified identity and digest.
- Rejects changed content and mismatched identity metadata.

Regression tests cover successful verification, payload tampering, manifest tampering, each identity mismatch, boolean revisions, and stability of returned bytes after caller-owned input objects are mutated.

**Boundary:** this is an in-memory integrity check, not a registry-backed assembler. It does not prove that story or learning-object references exist, that referenced revisions/fingerprints match registry history, that grants are current, or that the pipeline run succeeded. It writes no files and authorizes no publication.


## Section 8.4: registry capability inspection and implementation dependency

A focused inspection of the current registry APIs identified which provenance checks can be implemented directly and one blocking persistence gap:

- **Story revisions:** `get_domain_record("story", id)` and `domain_record_history("story", id)` expose the current story record and immutable revision history. `story_editorial_snapshot(id)` returns a deterministic dossier and its recorded story revision/fingerprint.
- **Learning-object revisions:** `get_learning_object(id)` and `learning_object_history(id)` expose the current record and immutable revisions. The selected manifest revision can therefore be resolved against history and its exact canonical payload fingerprint recomputed.
- **Current source permission:** `get_source`, `get_endpoint`, `get_permission_grant`, and `evaluate_grant(grant_id, "republication", endpoint_id=..., now=...)` provide a fail-closed current grant check. Article URL coverage still needs to be checked against the registered endpoint boundary for every included article.
- **Pipeline-run binding is not currently resolvable:** `_DOMAIN_MODELS` contains article, story, claim, evidence, and edition records, but no pipeline-run record. There is no `pipeline_runs` table or public pipeline-run lookup API in `SQLiteRegistry`. The edition and manifest contracts require a `pipeline_run_id`, but the registry cannot establish that it exists, belongs to the edition build, or finished with `status: succeeded`.

**Decision:** do not implement or label a registry-backed edition verifier complete until pipeline-run persistence and lookup have a defined, immutable contract. The next code gate must add a versioned pipeline-run registry record and tests for missing, failed, running, mismatched, and succeeded runs. After that, the verifier can resolve exact story and learning-object history, recheck every current endpoint-scoped republication grant, and reject stale or incomplete provenance. The existing in-memory verifier remains only a payload/manifest integrity check.

No registry records, live editions, files, or publication destinations were changed during this inspection.


## Section 8.5: persisted pipeline-run lifecycle

The existing pipeline-run schema was reused and tightened rather than replaced. It now requires an initiator and enforces lifecycle shape: terminal runs require a finish timestamp; successful runs require output fingerprints and cannot carry failure fields; failed/rejected runs require a failure code; non-success states cannot claim output fingerprints.

Added SQLite migration 26 with:
- A current pipeline_runs record and append-only pipeline_run_revisions history.
- Identity/input immutability for run ID, start time, software version, configuration fingerprint, input fingerprints, and initiator.
- Sequential revision enforcement and immutable revision history.
- Audit-chain events for creation and status transitions.
- Public add_pipeline_run, get_pipeline_run, transition_pipeline_run, and pipeline_run_history methods.

The API only allows initial queued/running records and legal lifecycle transitions: queued to running/cancelled/rejected, and running to succeeded/failed/cancelled. A terminal run cannot be restarted or edited. Optimistic revision checks prevent stale workers from overwriting a newer status.

Regression coverage was added for persistence, revision history, successful completion, invalid terminal insertion, immutable inputs, stale revision rejection, unknown IDs, and migration from schema version 25 to 26. The existing contract fixture was updated to include the required initiator.

**Limit:** this adds durable run provenance but does not yet bind a run to a specific edition candidate or prove that a run's output fingerprint equals the edition payload. The registry-backed edition verifier still needs to compare the manifest pipeline_run_id with a succeeded current run and verify its output fingerprint against the canonical artifact. CI verification is pending; no production database was migrated and no pipeline was executed.


**Verification update:** the first full-suite run exposed three old assertions hard-coded to schema version 25; the new pipeline-run tests passed. Those assertions were corrected, and SQLite-level guards were added for payload/index consistency, legal state transitions, and deletion prevention. GitHub Actions run [#44917871635](https://github.com/Amiroudro/new-sngso/actions/runs/44917871635) then passed the full Python 3.12 suite: **781 passed in 7.97 seconds** on code head c28c18958a2b5f2274e1e5b868378ddcfa21c409. This verifies the code and tests in that run, not a deployed database or production pipeline.


## Section 8.6: registry-backed pipeline output binding

Added verify_pipeline_run_for_artifact as a staging verification gate. It resolves the manifest's pipeline_run_id through the new SQLite registry, requires the current run state to be succeeded, requires the run's recorded output_hashes to include the exact manifest/payload fingerprint, and rejects a manifest timestamp earlier than pipeline completion. It returns the run ID, current revision, finish time, and verified output fingerprint. Missing, running, failed, mismatched-output, and temporally impossible cases are rejected.

The helper is exported from the engine package and has registry-backed tests using actual SQLiteRegistry records. GitHub Actions run [#44918034521](https://github.com/Amiroudro/new-sngso/actions/runs/44918034521) passed the full Python 3.12 suite: **785 passed in 10.02 seconds** on code head 90d787a618e55ac51f41f62ad2179a73f5f5748d.

**Remaining:** this is one registry binding, not the complete edition verifier. Story snapshot revisions/fingerprints, exact approved learning-object revisions/fingerprints and quality gates, and current endpoint-scoped republication grants still need to be verified together before the bilingual assembler can rely on the manifest.


## Section 8.7: whole-edition registry-backed staging verification

Added `verify_registry_backed_edition_artifact` as the single fail-closed read-only gate for a complete bilingual edition artifact. It composes the canonical payload/manifest verifier and successful pipeline-output verifier, then checks the live registry references before returning a staging-only verification result.

The verifier now requires:
- The registered edition exists at the exact manifest revision, remains approved, matches the edition key and pipeline-run ID, and binds the canonical artifact hash in its `content_hash`.
- Manifest story bindings cover every edition story exactly once. Each story must still be at the exact current revision, and its current deterministic editorial snapshot fingerprint must equal the manifest fingerprint.
- Every learning object exists at the exact current revision and canonical fingerprint, belongs to the bound story, has the declared kind/language, and remains approved. Every declared kind must have both Bangla and English variants. The existing independent bilingual review gate is invoked for each pair.
- Every active source article in every included story has a registered active source and canonical URL. Each manifest republication grant is re-evaluated at the supplied timezone-aware evaluation time, must match the exact source and endpoint, and its registered endpoint URL must cover each included article URL using the hardened URL-boundary validator. Missing, unused, or duplicate endpoint bindings are rejected.

The result includes immutable artifact bytes, verified pipeline-run evidence, the exact story and learning-object IDs, and the number of current permission checks. It is explicitly staging-only and never grants publication authority. The verifier performs no writes.

Regression tests cover a successful bilingual staging bundle, a stale story snapshot, and a denied/expired republication grant. The integration fixture uses the real SQLite pipeline-run registry; other registry read interfaces and the bilingual verifier are exercised through focused fixtures and their existing dedicated tests. These tests do not establish real human review, live publisher permission, or production security.

GitHub Actions [run #44918763573](https://github.com/Amiroudro/new-sngso/actions/runs/44918763573) passed the complete Python 3.12 suite: **788 passed in 7.73 seconds** on head `937f054ac69124a9c02d7b102fe6ae41bc1489f1`.

**Remaining:** artifact persistence and retrieval, bilingual assembly from reviewed records, append-only approval/release decisions, guarded publication transitions, correction/supersession and last-known-good handling, and end-to-end acceptance are still open. This verifier is not a publication state machine and does not authorize release.
