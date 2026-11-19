# Section 6: Story intelligence, claims, and evidence

Status: in progress. Provenance, claim-assessment, and conservative reporting-independence foundations are implemented and verified. GitHub Actions run #44609756106 passed with 330 tests on code head 1025c41cfbdc775f6d365e7ef4e3900afe1ddd57. Later changes on the current PR head are documentation-only. Timeline persistence, explicit story merge/split records, automated story clustering, claim extraction, and corroboration scoring remain unimplemented.

## Core distinctions

- A **story** is an evolving editorial grouping of articles about a real-world subject.
- An **article** remains a publisher-specific report. It can be linked to more than one story.
- A **claim** is one atomic proposition attributed to a story and supported by one or more explicit provenance entries.
- **Evidence** is a reviewed relationship between one source locator and one claim. It is not itself a declaration that the claim is true.
- A **duplicate candidate** from Section 5 is a review hint. It is not proof that two reports are independent or that they are duplicates.

## Provenance invariants

1. Every claim must reference an existing story.
2. Every claim provenance source must exist.
3. If claim provenance names an article, that article must exist, belong to the named source, and be linked to the claim's story.
4. If claim provenance names a collection observation, that observation must exist and belong to the named source.
5. If a provenance entry names both an article and observation, the ingestion ledger must show that the observation produced that article.
6. Evidence must reference an existing claim and source.
7. If evidence names an article or observation, each must exist and belong to the evidence source. If both are named, the ingestion ledger must bind them.
8. Evidence source/article/observation must match one single provenance entry on the referenced claim. It is not enough for those identifiers to appear in different provenance entries.
9. A story revision cannot remove an article that is still cited by an existing claim. Reassignment or correction must be explicit and auditable.

These checks run at the SQLite persistence boundary, not only in a service layer. They apply to both new records and revisions because the registry revalidates cross-record references in the same write transaction.

## Evidence relationships

The contract distinguishes `supports`, `qualifies`, `contradicts`, `mentions`, and `unclear`. These labels describe the assessed relationship between a source and a proposition. They must not be collapsed into a single positive/negative truth score. Confidence, where recorded, is an assessment attribute and not a calibrated probability unless a separately validated method establishes that interpretation.

A contradiction remains visible. A qualifying source remains visible. A mention is not corroboration. Repeated or syndicated reporting must not be counted as independent corroboration merely because it appears under different publisher names.

## Claim assessment guardrails

A claim may remain unassessed while evidence is being gathered. Once assessed, it must reference every evidence record linked to that claim, include a review timestamp and a non-empty review note, and use a status consistent with the evidence relationships:

- supported: at least one supporting item and no contradicting item;
- partially supported: supporting evidence plus qualifying or unclear evidence;
- disputed: both supporting and contradicting evidence;
- contradicted: contradicting evidence without supporting evidence;
- retracted: a reviewed explanation and linked evidence.

Evidence locators are constrained to the registered source's domain allowlist. Section fragments remain valid locators. This is a workflow consistency rule, not an automated truth score.

## First implementation slice

- Enforce source/article/observation consistency in claim provenance.
- Require evidence to match a specific claim provenance entry.
- Prevent story revisions from orphaning claims that cite removed articles.
- Add regression fixtures for mismatched publishers, unrelated articles, missing observations, and attempted story unlinking.

## Conservative reporting-independence view

The registry exposes a read-only story reporting-independence view. It reports publisher count separately from article duplicate-review states, unresolved candidates, confirmed duplicate groups within the story, confirmed duplicates pointing outside the story, explicitly reviewed-distinct articles, and articles with no recorded duplicate candidate.

The view deliberately does **not** emit an independent-source count. A publisher count is not an origin count, and no detected duplicate candidate is not proof of independence. Possible duplicates remain unresolved. Confirmed duplicate relationships are grouped. A human distinct decision is retained as a review outcome, not converted into a universal truth score.

## Explicit non-goals in this slice

- No automatic article-to-story assignment or story merge.
- No claim extraction from article bodies.
- No AI-generated truth score or corroboration score.
- No timeline event extraction.
- No new publisher access, full-text collection, or translation.
- No automatic reclassification of evidence.

## Acceptance path

Section 6 will remain open until the full fixture suite verifies the provenance invariants, story/article relationship revisions, append-only decision history, and audit integrity. Later slices will add explicit story merge/split decisions, event chronology, and human-reviewed claim/evidence assessment workflows.


## First-slice verification

GitHub Actions [run #44609756106](https://github.com/Amiroudro/new-sngso/actions/runs/44609756106) executed the full Python 3.12 fixture suite: **330 passed in 1.27 seconds**. The verified slice covers source/article/observation provenance matching, evidence-to-claim binding, prevention of story revisions that orphan claim provenance, prevention of claim revisions that orphan evidence, immutable evidence identity links, evidence locator domain scope, reviewed claim evidence requirements, assessment/relationship consistency, retraction review notes, and conservative duplicate grouping for story reporting. This is a verified foundation, not completion of all Section 6 work.


## Human claim assessment workflow

- New claims start as unassessed.
- A reviewed assessment requires a timezone-aware reviewed_at timestamp and a non-empty review_note.
- A reviewed claim must list every evidence record currently linked to it. The evidence IDs in the claim cannot silently omit a recorded relationship.
- Supported requires supporting evidence and no unresolved contradiction.
- Partially supported requires supporting evidence plus qualifying or unclear evidence.
- Disputed requires both supporting and contradicting evidence.
- Contradicted requires contradicting evidence without supporting evidence.
- Retracted requires a recorded review rationale.
- Adding or revising evidence on a reviewed claim is blocked until an editor explicitly resets the claim to unassessed. The new or corrected evidence is then recorded, the complete evidence list is revised, and the claim is reviewed again.

These are conservative workflow consistency rules, not a machine-generated truth score. A human reviewer records the assessment, timestamp, actor, and rationale; the system checks that the selected status is consistent with the evidence relationships recorded in the registry.


## Append-only story relationship decision ledger

The registry now accepts a dedicated `story_decision` record for human-reviewed `merge` and `split` decisions. Each decision records:

- the operation (`merge` or `split`);
- the complete set of affected story IDs;
- the canonical story selected for the decision;
- an explicit article-to-resulting-story allocation for every article considered;
- the deciding editor, rationale, and timezone-aware decision timestamp.

The SQLite boundary verifies that every affected story and article exists, the canonical story belongs to the decision scope, and every listed article is already linked to at least one affected story. Each decision captures the expected revision of every affected story. It also captures an explicit assignment and expected revision for every existing claim linked to those stories. A merge must account for every article in every affected story and assign all of them to the canonical story; every affected claim must also be assigned to the canonical story. A split must allocate articles to at least two resulting stories, and each claim's assigned destination must be consistent with the destinations of all article-specific provenance entries. Duplicate assignments, stale revisions, incomplete claim coverage, and targets outside the decision scope are rejected. Decision records are immutable and included in the normal append-only audit chain. `story_decision_history(story_id)` retrieves the decisions involving a given story.

**Deliberate boundary:** this first ledger records and preserves the editorial decision; it does not silently rewrite story membership, reassign claims, or archive a story. Applying a merge or split to live story membership requires a separate atomic workflow that can reconcile claims and provenance. That workflow remains future work. This avoids pretending that recording a decision is the same as safely executing it.


## Story decision ledger verification

SQLite schema migration 5 creates a dedicated `story_decisions` table rather than weakening the existing `domain_records` type constraint. Database triggers reject updates and deletes. Decision creation is validated against existing story and article records and appended to the audit hash chain. Each article has an explicit `assigned_story_id`; merges must allocate every article in the affected stories to the canonical story, while splits must allocate articles to at least two resulting stories. GitHub Actions [run #44610479213](https://github.com/Amiroudro/new-sngso/actions/runs/44610479213) passed the full Python 3.12 suite: **336 passed in 1.81 seconds**. The ledger records editorial decisions but does not itself apply story membership changes.


The explicit article-allocation extension, story/claim revision preconditions, complete claim assignments, and provenance-compatible claim destinations are now verified by GitHub Actions [run #44611964846](https://github.com/Amiroudro/new-sngso/actions/runs/44611964846): **341 tests passed**. This run includes regression coverage for stale story revisions, stale claim revisions, incomplete claim assignments, and split assignments that conflict with claim provenance.


## Decision preconditions and claim allocation

The decision contract now captures `expected_story_revisions` for every affected story and `claim_assignments` for every claim currently linked to those stories. Each claim assignment includes the claim's expected revision and its intended resulting story. Registry validation rejects stale story or claim revisions, duplicate or incomplete claim assignments, destinations outside the decision scope, and assignments that separate a claim from its article-specific provenance. For merges, every claim must target the canonical story. These fields preserve the editorial state the decision was based on; they do not execute the decision.

GitHub Actions [run #44611964846](https://github.com/Amiroudro/new-sngso/actions/runs/44611964846) passed **341 tests** after the four focused regression tests for stale story revisions, stale claim revisions, missing claim assignments, and split/provenance mismatch were added.


## Claim-backed event chronology

Timeline events are editorial assertions about when a development occurred, not free-standing facts generated from article publication dates. Every event must link to one or more existing claims, and every linked claim must belong to the same story. The linked claim retains the source/article/observation provenance and evidence relationships.

The v1 event contract distinguishes five temporal precisions:

- `instant`: a timezone-aware timestamp;
- `date`: a calendar date without an invented time of day;
- `range`: timezone-aware start and end timestamps;
- `approximate`: a calendar date accompanied by an explicit uncertainty note;
- `unknown`: no start or end value.

An optional IANA timezone records the relevant local zone. The engine validates timestamps, calendar dates, interval ordering, and timezone names. Unknown and approximate times remain explicit; the engine never fills missing values with midnight or publication time.

Timeline event records are append-only. A correction creates a new event record with `supersedes_event_id`; it does not rewrite the earlier assertion. The database allows only one direct successor for a given event. `timeline_event_history(story_id)` returns the full correction history, while `story_timeline(story_id)` returns current non-superseded assertions in chronological order, with unknown times last.

The registry rejects events for unknown or archived stories, missing claims, claims belonging to another story, and corrections that point outside the same story. This is a curated timeline foundation, not automatic event extraction, date inference, conflict resolution, or a claim that every event is independently verified.
