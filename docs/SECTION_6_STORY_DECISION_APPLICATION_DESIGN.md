# Atomic story merge/split application design

## Status

Design plus persistence groundwork. The current `story_decision` ledger stores immutable editorial intent and per-article destination assignments. Migration 6 adds `story_decision_applications`, an append-only table with a unique decision reference, editor/rationale, decision and impact fingerprints, and a JSON result snapshot. The table is deliberately not yet exposed through a write API. No story or claim membership changes are performed.

## Why application must be a separate operation

A decision can become stale after it is recorded. Stories may gain articles, claims may be revised, or another decision may affect the same story. Applying a decision must therefore re-check the complete current state and either make every permitted change or make none.

A merge/split must never be implemented as a loop of independent calls to `revise_domain_record`. A failure halfway through would leave the registry in a partially transformed state.

## Required two-step workflow

1. **Preview**: load the immutable decision, inspect all affected stories, articles, claims, evidence, editions, and prior decisions, and produce a deterministic impact report. Preview is read-only.
2. **Apply**: require explicit editor identity, rationale, and the exact expected revisions returned by the preview. Re-read and validate every affected record inside one `BEGIN IMMEDIATE` transaction. Apply all changes, write the application record, and append the audit event before committing.

The preview is advisory, not a lock. Apply must repeat all checks inside its transaction.

## Decision preconditions

The decision contract now captures expected revisions for every affected story and explicit claim-to-resulting-story assignments with expected claim revisions. Registry validation requires the complete set of claims currently linked to affected stories, including claims with no article-specific provenance, and rejects destinations that conflict with the article assignments in claim provenance. This is a verified precondition layer, not an application workflow.

The application implementation must still define explicit handling for claims whose provenance spans more than one destination and a policy for editions that already contain an affected story. Do not infer claim allocation from article allocation. A claim is a distinct editorial proposition and must not be moved automatically merely because one of its cited articles moves.

## Validation rules

### Common

- Decision exists and has not already been applied.
- Every affected story still exists and its revision matches the decision's captured revision.
- Every article assignment is complete, unique, and still matches current membership.
- Every claim in the affected stories is accounted for exactly once.
- Every claim destination is one of the resulting story IDs.
- All claim provenance remains valid against the destination story's article membership.
- Evidence records keep their original claim IDs and source/article/observation links. Evidence is never silently reassigned to a different claim.
- Any stale revision, incomplete assignment, orphan risk, unknown reference, or unsupported edition impact aborts the entire transaction.

### Merge

- The canonical story remains the surviving story.
- Every article from every affected story is assigned to the canonical story.
- Every affected claim is explicitly assigned to the canonical story.
- The surviving story receives the union of article IDs and a single revision increment.
- Non-canonical stories become `archived` with empty active article membership and a revision increment. Their identity and revision history remain stored.
- Claim story references are revised to the canonical story, preserving claim identity, provenance, evidence links, and prior revisions.

### Split

- The decision must define at least two resulting stories and assign every article and claim to exactly one result.
- Each resulting story must retain at least one article. A claim whose provenance cannot be satisfied by one destination must be explicitly reviewed and resolved before application; it must not be duplicated or dropped.
- Existing story identities are retained where explicitly designated by the decision. Any newly created story must have a stable ID and complete required metadata.
- Each affected story and claim is revised at most once in the transaction.

## Edition safety

Edition snapshots are immutable. The application must never rewrite or delete an edition. If any affected story appears in an edition, the preview must list those editions and application must preserve their original story references and content hashes. New editions may reflect the new story graph only through the normal edition assembly and publication gate. Do not silently rewrite historical published editions.

## Persistence and audit

Migration 6 now creates the append-only `story_decision_applications` table with:

- unique application ID;
- unique foreign-key-like reference to one story decision (one successful application maximum);
- applied timestamp and editor;
- decision ID and fingerprint;
- before/after story and claim revision references;
- deterministic impact summary and its fingerprint.

Use database uniqueness and append-only triggers to prevent duplicate applications and mutation/deletion. In the same SQLite transaction:

1. verify the decision and preconditions;
2. revalidate article and claim assignments;
3. update story records with optimistic revision checks;
4. update claim records with optimistic revision checks;
5. append domain revision-history rows for every changed record;
6. insert the application record;
7. append one audit-chain event that binds the decision fingerprint, application fingerprint, actor, and all changed IDs.

If any statement or audit append fails, rollback everything. A retry after a successful commit must return the existing application result without applying changes again. A concurrent second apply must fail or return that same completed result, never duplicate revisions.

## API shape (proposed)

- `preview_story_decision_application(decision_id)`: read-only structured report; no writes.
- `apply_story_decision(decision_id, *, expected_impact_fingerprint, actor, rationale)`: atomic, idempotent application guarded by the exact preview fingerprint.

The impact fingerprint must cover every record and revision used by the preview, including stories, articles, claims, evidence, and affected editions. Application recomputes it under the write transaction and rejects a mismatch with a clear stale-preview error.

## Required regression tests

- merge moves all articles and claims and archives non-canonical stories;
- split creates/retains resulting stories and assigns every article and claim exactly once;
- missing, duplicate, or out-of-scope article/claim assignments are rejected;
- a claim with provenance spanning multiple split destinations blocks application;
- stale story or claim revisions block application;
- an edition reference never changes and historical edition hashes remain identical;
- evidence IDs and provenance links remain unchanged;
- a failure injected after the first story update rolls back all story, claim, revision, application, and audit writes;
- concurrent apply attempts produce exactly one application and one revision set;
- retry after successful application is idempotent;
- audit-chain verification succeeds after apply and after rejected attempts;
- a decision cannot be applied twice under a different request.

## Implementation gate

The first coding slice is implemented and verified: decision records capture story and claim revision preconditions, explicit article and claim allocations, and registry-level completeness/provenance validation. The second slice implements read-only preview with a deterministic impact fingerprint and an edition-impact report. The third slice adds the application ledger schema, including database-enforced uniqueness and append-only triggers. The atomic apply transaction and its regression suite are still required before any live story or claim mutation is allowed. GitHub Actions [run #44612277463](https://github.com/Amiroudro/new-sngso/actions/runs/44612277463) passed **343 tests** including read-only and stale-decision preview checks. The follow-up test adds an explicit historical-edition assertion and is awaiting its own CI run. Only after preview behavior and edition-impact policy are fully tested should the atomic transaction be introduced. Keep live collection, publisher access, Android integration, and publication automation out of this scope.
