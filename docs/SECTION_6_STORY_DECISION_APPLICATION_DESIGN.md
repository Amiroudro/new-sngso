# Atomic story merge/split application design

## Status

Implemented for existing story identities. The immutable `story_decision` ledger stores editorial intent and explicit article/claim allocations. `preview_story_decision_application` is read-only and produces a deterministic impact fingerprint. `apply_story_decision` rechecks that preview inside one `BEGIN IMMEDIATE` transaction, updates all affected stories and claims, appends their revision history, inserts the unique append-only application record, and adds one audit-chain event. Any exception rolls back the complete operation.

## Why application must be a separate operation

A decision can become stale after it is recorded. Stories may gain articles, claims may be revised, or another decision may affect the same story. Applying a decision must therefore re-check the complete current state and either make every permitted change or make none.

A merge/split must never be implemented as a loop of independent calls to `revise_domain_record`. A failure halfway through would leave the registry in a partially transformed state.

## Required two-step workflow

1. **Preview**: load the immutable decision, inspect all affected stories, articles, claims, evidence, editions, and prior decisions, and produce a deterministic impact report. Preview is read-only.
2. **Apply**: require explicit editor identity, rationale, and the exact expected revisions returned by the preview. Re-read and validate every affected record inside one `BEGIN IMMEDIATE` transaction. Apply all changes, write the application record, and append the audit event before committing.

The preview is advisory, not a lock. Apply must repeat all checks inside its transaction.

## Decision preconditions

The decision contract captures expected revisions for every affected story and claim and requires complete, unique article and claim allocations. A claim whose article-specific provenance would span multiple resulting stories is rejected before application. Claims without article-specific provenance still require an explicit editor-assigned destination. The current split contract only redistributes among the existing story IDs in the decision. It does not create new story identities.

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
- Split decisions must leave at least one article in every existing resulting story.

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
- Split decisions retain the existing story identities named in the decision. Creating new story identities is deliberately outside the current contract.
- Each affected story and claim is revised at most once in the transaction.

## Edition safety

Edition snapshots are immutable. The preview lists affected editions; application does not update edition rows, story references, or content hashes. New editions may reflect the new story graph only through the normal edition assembly and publication gate. Historical editions can therefore continue to reference a story that is now archived, as a record of what that edition contained.

## Persistence and audit

Migration 6 now creates the append-only `story_decision_applications` table with:

- unique application ID;
- unique foreign-key-like reference to one story decision (one successful application maximum);
- applied timestamp and editor;
- decision ID and fingerprint;
- before/after story and claim revision references;
- deterministic impact summary and its fingerprint.

Database uniqueness and append-only triggers prevent duplicate applications and mutation/deletion. The implementation performs the following steps in the same SQLite transaction:

1. verify the decision and preconditions;
2. revalidate article and claim assignments;
3. update story records with optimistic revision checks;
4. update claim records with optimistic revision checks;
5. append domain revision-history rows for every changed record;
6. insert the application record;
7. append one audit-chain event that binds the decision fingerprint, application fingerprint, actor, and all changed IDs.

If any statement or audit append fails, rollback everything. A retry after a successful commit must return the existing application result without applying changes again. A concurrent second apply must fail or return that same completed result, never duplicate revisions.

## API

- `preview_story_decision_application(decision_id)`: read-only structured report; no writes.
- `apply_story_decision(decision_id, *, expected_impact_fingerprint, actor, rationale)`: atomic, idempotent application guarded by the exact preview fingerprint. A successful retry returns the stored result without creating another revision or audit event.

The impact fingerprint must cover every record and revision used by the preview, including stories, articles, claims, evidence, and affected editions. Application recomputes it under the write transaction and rejects a mismatch with a clear stale-preview error.

## Regression coverage

- merge moves all articles and claims and archives non-canonical stories;
- split retains existing resulting stories and assigns every article and claim exactly once;
- missing, duplicate, or out-of-scope article/claim assignments are rejected;
- a claim with provenance spanning multiple split destinations blocks application;
- stale story or claim revisions block application;
- an edition reference never changes and historical edition hashes remain identical;
- evidence IDs and provenance links remain unchanged;
- a failure injected at audit append rolls back all story, claim, revision, application, and audit writes;
- concurrent apply attempts produce exactly one application and one revision set;
- retry after successful application is idempotent;
- audit-chain verification succeeds after apply and after rejected attempts;
- a decision cannot be applied twice under a different request.

## Implementation gate

The atomic application workflow is implemented and verified. GitHub Actions [run #44614814558](https://github.com/Amiroudro/new-sngso/actions/runs/44614814558) passed **349 tests** on code head `6c076ce45999214abd65e78bcbb3a6a8eee475da`, covering merge, split, stale-preview rejection, sequential retry idempotency, and rollback when audit append fails. A second test adds two independent SQLite registry connections applying the same decision concurrently. GitHub Actions [run #44614967997](https://github.com/Amiroudro/new-sngso/actions/runs/44614967997) passed **350 tests in 1.87 seconds** on code head `b0965891c56b686f842873551e53687b881f718c`. The concurrent callers receive the same application ID; only one application record and one revision set are created.

The current split operation only reallocates among the existing story IDs included in the decision. Every resulting story must retain at least one article. A claim with provenance that crosses destinations is rejected rather than duplicated, dropped, or guessed. Historical editions and evidence records remain unchanged. Event chronology, new-story creation during splits, automatic clustering, and live publisher collection remain outside this implementation. Keep Android integration and publication automation out of this scope.
