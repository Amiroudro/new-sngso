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


## Section 8.8: immutable artifact persistence and integrity-checked retrieval

Added SQLite migration 27 and a dedicated `edition_artifacts` store. It persists the canonical payload JSON, provenance manifest JSON, exact payload fingerprint, edition identity/revision, and a verification receipt. The manifest ID is the record key, and one edition revision can have only one stored artifact. Database triggers reject updates and deletions, and an insert guard keeps indexed identity/hash columns aligned with the stored manifest.

`SQLiteRegistry.store_verified_edition_artifact` accepts only the registry-verification result type, parses its immutable bytes, and reruns the complete registry-backed staging verifier at the time of storage. The recheck and insert are protected by the registry instance lock. The stored receipt records the pipeline revision, story/object bindings, permission-check count, verification timestamp, and explicit non-authorizing status. Every store operation emits an audit-chain event.

`get_edition_artifact` retrieves the exact payload/manifest pair and recomputes canonical integrity before returning it. Retrieval is deliberately narrower than fresh staging verification: it verifies stored bytes and receipt binding, not current source rights or reviewer eligibility. A stored artifact must be fully reverified before any later release decision. The API always returns `publication_authorized=false`.

Tests cover schema migration 26→27, immutable storage/retrieval, duplicate insertion rejection, direct SQL update/delete guards, and the explicit re-verification requirement. GitHub Actions [run #44919212598](https://github.com/Amiroudro/new-sngso/actions/runs/44919212598) passed the full Python 3.12 suite: **792 passed in 10.19 seconds** on code head `4b0411595680910b29b4c18cfb002b6cc3650611`.

**Limit:** this is durable staging-artifact retention, not an approval ledger or release system. No publication state transition, correction/supersession, last-known-good promotion, or live edition write is implemented.


## Section 8.9: deterministic bilingual edition assembly

Added `assemble_bilingual_edition_artifact` in `src/sngso_engine/edition_assembly.py`. The caller must explicitly select learning-object IDs for every story; the builder does not guess which drafts to include or generate/translate editorial text.

For each selected record, assembly requires the exact current registry revision, approved status, a passing registry quality report, story ownership, and a valid language/kind. Every selected kind must have exactly one Bangla and one English object, and the existing independent bilingual reviewer gate is invoked for each pair. The canonical payload preserves each approved object's content and claim IDs, plus the exact story snapshot revision/fingerprint and active article/source metadata. The manifest binds all selected object revisions/fingerprints, story snapshots, pipeline run, edition revision, and caller-supplied republication permission bindings.

The builder validates the manifest contract, canonical payload fingerprint, and successful pipeline output binding (including pipeline completion preceding manifest creation). It returns immutable artifact bytes and a staging result only. It does not change the edition record, write the artifact store, create an approval decision, grant permissions, or publish.

Regression tests cover exact bilingual text/claim preservation, source and story provenance, incomplete language pairs, unapproved objects, stale learning-object history, incomplete story selection, missing active sources, and timezone-aware manifest timestamps. The tests use a focused registry fixture and isolate the existing independent-review and pipeline-binding helpers; those helpers retain their separate integration tests. A real end-to-end assembly against live human-reviewed records and live grants remains outstanding.

GitHub Actions [run #44921515577](https://github.com/Amiroudro/new-sngso/actions/runs/44921515577) passed the full Python 3.12 suite: **799 passed in 5.51 seconds** on code head `6911dde2ced74f01df71c8075d922f436cb6ce70`.

**Remaining Section 8 work:** approval/release decision ledger, guarded publication transitions, correction/supersession and last-known-good handling, controlled staging export, and end-to-end acceptance. Assembly is not publication authorization.


## Section 8.10: append-only edition staging decision ledger

Added the strict `edition-decision` contract and SQLite migration 28. The new `edition_decisions` ledger stores one final decision per immutable artifact manifest and binds the decision to the exact edition ID/revision and canonical payload fingerprint. Database guards keep indexed columns aligned with the decision payload and reject updates/deletions. Every accepted decision also creates an audit-chain event.

The decision API accepts only a recent, unexpired `AuthenticatedIdentity`, an active human-classified principal with the separately provisioned `edition_approver` role, and complete provider identity evidence. The authenticated application facade derives that identity from its configured provider and enforces the same role boundary. The approver must differ from every recorded language reviewer on the candidate. Before recording a decision, the registry reruns whole-edition verification against current story/object revisions, bilingual review evidence, pipeline output, and current republication grants.

The ledger supports `approve` and `reject` for the exact staging candidate. A second decision for the same manifest is rejected; a changed candidate needs a new artifact/revision and a fresh decision. Raw session IDs are never stored; only a SHA-256 session fingerprint is recorded.

**Critical boundary:** an `approve` decision means only that an independent, authenticated edition approver accepted that exact staging candidate for the next controlled release gate. The record always sets `publication_authorized: false`. It does not mark an edition published, write to a live destination, or authorize the app/website to consume it. The next work must implement guarded release authorization and promotion separately. The real OIDC provider, protected principal provisioning, and deployment controls remain external acceptance requirements.


**Verification:** the first run exposed four stale assertions expecting schema version 27; those were updated to 28. The corrected full Python 3.12 suite passed GitHub Actions run [#44926118184](https://github.com/Amiroudro/new-sngso/actions/runs/44926118184): **805 passed in 5.48 seconds** on code/test head `231be4b7db18605a5ec48bb18a91c088720741ce`. The run included the new migration, contract validation, decision-ledger tests, and the existing suite. No production database was migrated and no edition was approved or published.


## Section 8.11: close the generic published-edition write path

The baseline audit identified that a caller could create an edition domain record with `status: published` through the generic record API, bypassing any separate release decision. That path is now blocked at two levels. `SQLiteRegistry.add_domain_record("edition", ...)` rejects a published status with a clear error, and SQLite migration 29 adds database triggers that reject direct SQL insertion of a published edition. Additional triggers reject direct SQL updates and deletion of edition snapshots, matching the existing application-level immutable-revision rule.

Approved edition records remain staging candidates; the word `approved` does not mean live publication. The only implemented edition decision in Section 8.10 remains non-authorizing. No generic or direct-SQL path can now turn an edition record into `published`. A future controlled release workflow must be implemented separately and must bind to the exact approved artifact and fresh release checks before any real destination is considered.

Regression coverage checks API-level rejection, direct-SQL rejection, edition update/delete guards, and migration 28→29. The existing preview test was updated to represent an approved staging candidate rather than a published edition.

**Verification:** GitHub Actions run [#44926616175](https://github.com/Amiroudro/new-sngso/actions/runs/44926616175) passed the full Python 3.12 suite: **809 passed in 10.05 seconds** on code/test head `b7b48878f121c477481a245454f552965cacf71c`. Earlier attempts exposed stale schema-version assertions and tests that constructed published editions through the now-blocked generic path; those were corrected before this passing run. No production database was migrated and no edition was published.


## Section 8.12: two-person controlled release authorization

Added a separate release-authorization contract, SQLite migration 30, append-only `edition_release_authorizations` ledger, and authenticated application operation. A release authorizer must be a recently authenticated, active human principal with the distinct `edition_release_authorizer` role. The authorizer must differ from the staging approver; the original approver must also remain active and retain the `edition_approver` role. The decision binds the exact manifest, edition revision, canonical payload fingerprint, and prior approval-decision ID.

Before authorization, the registry checks that the prior staging decision is `approve`, verifies that it refers to the exact stored artifact, and reruns the whole-edition registry verifier at the authorization time. Current story/object revisions, bilingual review evidence, pipeline output binding, and endpoint-scoped republication grants are therefore rechecked. The authorization stores provider-backed identity evidence and a hashed session fingerprint. SQLite insert guards enforce the artifact/approval binding, distinct approver and authorizer, active human authorizer role, and immutable decision record. An audit-chain event is appended.

**Critical boundary:** the authorization is scoped to `controlled_release_gate`; it sets `release_authorized: true` but permanently sets `publication_authorized: false`. It is permission to proceed to a later controlled promotion step, not permission for this code to write to the live website, app, CDN, or publisher-facing destination. No edition status is changed. There is one authorization per immutable artifact; a changed artifact requires a new approval and authorization. Production identity infrastructure, protected role provisioning, actual publisher grants, and deployed operational controls remain external acceptance requirements.

Regression tests cover prior approval requirements, rejection handling, same-person approval/authorization denial, active-human and recent-authentication checks, provider-backed application access, duplicate and direct-mutation guards, and migration 29→30. GitHub Actions [run #44927171126](https://github.com/Amiroudro/new-sngso/actions/runs/44927171126) passed the full Python 3.12 suite: **815 passed in 7.03 seconds** on code/test head `27624dcc98e394bf6d60f8533405c6be77e518dc`.


## Section 8.13: verified staging-only export package

Added `build_verified_staging_export` and a strict `edition-staging-export` contract. The builder requires the exact stored artifact, an `approve` staging decision, a distinct two-person release authorization, and currently active human principals holding both required roles. It checks that the approval and authorization bind the same manifest, edition revision, payload fingerprint, and approval-decision ID. It then reruns the whole-edition registry verifier at export time, including current story/object revisions, bilingual review evidence, pipeline output provenance, and endpoint-scoped republication grants.

The result is an immutable in-memory package containing the canonical edition payload, provenance manifest, staging decision, and release authorization. It includes a deterministic export identifier and a SHA-256 fingerprint of the complete package. The contract fixes `target: staging` and `publication_authorized: false`. The function performs no filesystem writes, network calls, frontend integration, live destination writes, edition status changes, or publication. The package is ready for a separately authorized staging consumer, but no such consumer is integrated or runtime-tested here.

The contract loader now registers all versioned schemas by `$id`, allowing composed contracts to validate nested provenance, decision, and release-authorization objects. Regression tests cover successful package construction, missing authorization, revoked release authority, exact package contents, and non-publication boundaries. GitHub Actions [run #44927518850](https://github.com/Amiroudro/new-sngso/actions/runs/44927518850) passed the full Python 3.12 suite: **818 passed in 11.06 seconds** on code/test head `be21a13b05eeef8fa8ad3d2687b414a0964dc1cb`.


## Section 8.14: staging correction, supersession, rollback and last-known-good design

Added `docs/SECTION_8_14_STAGING_CORRECTION_ROLLBACK_DESIGN.md` as the implementation contract for the next controlled staging layer. It defines a staging-only channel, fresh verification on every operation, exact artifact/export fingerprint binding, a distinct authenticated staging operator, append-only promotion/rollback events, compare-and-swap pointer updates, last-known-good preservation, and rollback as a new event rather than history rewriting. Corrections must use a new immutable artifact and fresh approval/release authorization. No live destination, frontend, published status, or publication authorization is permitted.

**Status: design documented, implementation not yet claimed.** The next code work must add the SQLite event/state migration, authenticated operator boundary, atomic promotion and rollback methods, database guards, and the listed concurrency, rights-revocation, rollback, and failure-atomicity tests. The staging channel must not be treated as operational until the full suite passes.


## Section 8.14: atomic staging promotion, correction and rollback

Implemented migration 31 with an append-only `edition_staging_events` ledger and a guarded single-row `edition_staging_channel_state` projection. The only supported channel is the literal `staging`. Database guards bind every event to the exact stored artifact, approved decision, separate release authorization, current active human approver/authorizer, and active human staging operator. Event history and channel state cannot be deleted; events cannot be updated.

Added `promote_edition_to_staging` and `rollback_edition_staging`, exposed through the authenticated application facade. Both operations rebuild and reverify the complete staging export inside a SQLite write transaction, so current reviewer/rights/identity checks and the pointer update share the same serialized write boundary. A compare-and-swap on both state revision and expected current export prevents stale operators from replacing newer state. Successful promotion moves the prior current export to last-known-good. Rollback is a new immutable event targeting an earlier successfully promoted export, and moves the prior current export to LKG. Corrections use new immutable artifacts and fresh approval/release authorization. Export identity is deterministic for the exact manifest, edition revision and payload fingerprint, so rollback can reproduce and verify the original package.

The staging event contract fixes the channel and target to staging and `publication_authorized: false`. No live destination, frontend, website, app, schedule, edition status, or publication state is changed.

Regression tests cover schema 30→31 migration, first and second promotion, LKG preservation, rollback as a new event, stale compare-and-swap rejection, role separation, append-only event history and non-deletable state. GitHub Actions run [#44932795855](https://github.com/Amiroudro/new-sngso/actions/runs/44932795855) passed the full Python 3.12 suite: **824 passed in 9.62 seconds** on code/test head `11bb329f8a207914160ab6f335d2eedd2b5bcf9f`.

**Acceptance boundary:** tests use synthetic fixtures and a mocked whole-edition verifier. They do not establish real publisher rights, genuine independent human review, deployed OIDC/JWKS/revocation, cross-process production concurrency, backup/restore, or staging-consumer integration. Section 8.14's registry implementation is complete; real staging operation and production acceptance are not.


## Section 8.15: end-to-end acceptance register

Added [`docs/SECTION_8_15_END_TO_END_ACCEPTANCE.md`](SECTION_8_15_END_TO_END_ACCEPTANCE.md), which records the verified Section 8.14 implementation, the full Python 3.12 suite result (**824 passed in 11.04 seconds**, run [#44932902651](https://github.com/Amiroudro/new-sngso/actions/runs/44932902651)), and a gate-by-gate acceptance matrix. The current staging lifecycle tests use synthetic records and a monkeypatched whole-edition verifier; they do not yet prove one fully connected genuine-record lifecycle. Open engineering tests include separate-connection concurrency, transaction-failure atomicity, revocation immediately before promotion, and a real-registry integrated assembly-to-rollback path. Real human reviews, publisher grants, deployed identity/revocation, backup/restore and a downstream staging consumer remain external acceptance gates.

**Decision:** Section 8 implementation is substantially complete, but end-to-end acceptance and production readiness are not. No live destination or frontend was changed; publication remains unauthorized.


## Section 8.16: staging concurrency and failure-path verification

Added cross-connection and failure-path regression coverage in `tests/storage/test_edition_decisions.py`.

- Two separate `SQLiteRegistry` connections to one temporary database attempt promotion against the same expected channel revision. The compare-and-swap gate permits exactly one; the competing operation receives a stale-state rejection. The durable state contains one event and one pointer update.
- An injected SQLite trigger failure between event insertion and channel-pointer update proves transaction rollback removes both effects.
- A staging operator revoked before promotion is rejected, with no event or channel-state mutation.

GitHub Actions run [#44937132497](https://github.com/Amiroudro/new-sngso/actions/runs/44937132497) passed the full Python 3.12 suite: **827 passed in 7.61 seconds** on commit `d85d661fe5d54e6c1988ccb49239eae3608bb515`.

**Limit:** the concurrency and failure tests use synthetic staging fixtures and a mocked whole-edition verifier. They establish SQLite transaction/CAS behavior for this tested path, not the complete genuine-record lifecycle or production database behavior. The integrated real-registry assembly-to-rollback test, real human/rights/identity evidence, operational backup/restore, and downstream staging consumer remain outstanding.

## Additional authorization-revocation regression

A further staging test revokes the original human approver after the approval and separate release authorization have been recorded, but before staging promotion. Promotion must re-run export checks, reject the now-inactive approver, and leave the staging pointer and event ledger unchanged.

The first test run exposed an expected exception-wrapping boundary: the staging API translates the lower-level export rejection into `EditionStagingError`. The assertion was corrected to check the public staging API contract. The corrected full Python 3.12 suite passed: **828 passed in 11.22 seconds**, GitHub Actions run [#44937450038](https://github.com/Amiroudro/new-sngso/actions/runs/44937450038), commit `39ca24dd512f8ca4bca7f0d59c79eccd61a03d0f`.

This is repository-level regression evidence using synthetic staging fixtures. It does not replace the still-open integrated genuine-record lifecycle test or external operational acceptance gates.

## Integrated real-registry lifecycle verification (Section 8 acceptance update)

The previous open engineering gate for an integrated registry-backed staging lifecycle is now covered by `test_real_registry_bilingual_edition_lifecycle_through_rollback` in `tests/test_edition_assembly.py`.

The test creates actual SQLite registry records for a source, endpoint, verified permission evidence, scoped republication grant, article, story, supported claim/evidence, two independently reviewed language variants, pipeline runs, editions and immutable artifact records. It then exercises the unmocked registry-backed whole-edition verifier through artifact persistence, separate human-classified approval, independent release authorization, staging export, two promotions and rollback. It verifies the final channel pointer, last-known-good pointer, three-event history and audit chain.

The first genuine promotion attempt exposed a real integration defect: staging held a write transaction while nested registry verification helpers opened their own transactions on the same SQLite connection. SQLite rejected this with `cannot start a transaction within a transaction`. The registry transaction manager now uses nested SQLite savepoints under its existing re-entrant lock. This preserves the outer atomic transaction while allowing composed registry reads and verification. Two direct regression tests cover inner-savepoint rollback and outer-transaction rollback of successful nested work.

GitHub Actions run [#44938061164](https://github.com/Amiroudro/new-sngso/actions/runs/44938061164) passed the full Python 3.12 suite: **831 passed in 11.35 seconds** on code commit `7fa89bc5aa36231c47839fb3a02a554da791718e`.

**Evidence boundary:** this is a real SQLite registry integration test with synthetic fixture data and test identities. The language-review records, publisher permission grant and human-classified principals are test fixtures, not proof of genuine human review or an actual publisher licence. The pipeline output hash is precomputed in a test-only preview step; the actual assembly, pipeline binding, whole-edition verification, persistence, approval, release authorization, export, promotion and rollback then run without mocking the whole-edition verifier. No production database, deployed identity provider, real publisher grant, external staging consumer, live publication or production acceptance is established.

### Current republication-rights revocation before promotion

The integrated real-registry lifecycle test now also suspends the registered republication grant after approval and release authorization but immediately before promotion. The promotion attempt is rejected, with the staging state revision and event ledger unchanged. The test then restores the fixture grant and completes the two promotions and rollback.

GitHub Actions run [#44938218038](https://github.com/Amiroudro/new-sngso/actions/runs/44938218038) passed the full Python 3.12 suite: **831 passed in 7.60 seconds** on code commit `86b6793ac55aac824326c2e584996b1e7013aad0`.

This closes the repository-level rights-revocation-before-promotion regression gap for the tested SQLite path. The grant is synthetic test data, not a real publisher authorization.
