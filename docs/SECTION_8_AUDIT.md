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
