# Section 6: Story intelligence, claims, and evidence

Status: in progress. The first provenance and claim-assessment integrity slice is implemented and verified. GitHub Actions run #44609248199 passed with 327 tests on code head ccfed66a8de1c783fa3ca9f5c291e06693c554e8. Later changes on the current PR head are documentation-only. Automated story clustering, event chronology, claim extraction, and corroboration scoring remain unimplemented.

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

GitHub Actions [run #44609248199](https://github.com/Amiroudro/new-sngso/actions/runs/44609248199) executed the full Python 3.12 fixture suite: **327 passed in 1.15 seconds**. The verified slice covers source/article/observation provenance matching, evidence-to-claim binding, prevention of story revisions that orphan claim provenance, prevention of claim revisions that orphan evidence, immutable evidence identity links, evidence locator domain scope, reviewed claim evidence requirements, assessment/relationship consistency, and retraction review notes. This is a verified foundation, not completion of all Section 6 work.


## Human claim assessment workflow

- New claims start as unassessed.
- A reviewed assessment requires a timezone-aware reviewed_at timestamp and a non-empty review_note.
- A reviewed claim must list every evidence record currently linked to it. The evidence IDs in the claim cannot silently omit a recorded relationship.
- Supported requires supporting evidence and no unresolved contradiction.
- Partially supported requires supporting evidence plus qualifying or unclear evidence.
- Disputed requires both supporting and contradicting evidence.
- Contradicted requires contradicting evidence without supporting evidence.
- Retracted requires a recorded review rationale.
- Adding new evidence to a reviewed claim is blocked until an editor explicitly resets the claim to unassessed. The new evidence is then added, the complete evidence list is revised, and the claim is reviewed again.

These are conservative workflow consistency rules, not a machine-generated truth score. A human reviewer records the assessment, timestamp, actor, and rationale; the system checks that the selected status is consistent with the evidence relationships recorded in the registry.
