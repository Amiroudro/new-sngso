# Section 6: Story intelligence, claims, and evidence

Status: first integrity layer implemented; chronology, source-independence service, and explicit story merge/split records remain planned.

## Identity boundaries

- A source observation records what a connector saw.
- An article represents one publisher-specific report.
- A story represents one evolving real-world subject.
- A claim is one proposition that can be assessed.
- Evidence is a source-backed relationship to a claim, not a declaration that the claim is true.

Story membership is currently represented by the story's article_ids array. Changes use the existing optimistic revision API and append-only domain_record_revisions history. No automatic story creation, merge, or split is performed.

## Claim and evidence integrity

A claim's provenance must reference registered sources. If it names an article, that article must belong to the claim's story and the source must match. If it names a collection observation, that observation must exist and belong to the same source; when both article and observation are named, their canonical URL identities must agree.

An evidence record must reference an existing claim and source. An optional article must belong to the claim's story and match the evidence source. An optional observation must exist, match the source, and, when an article is also supplied, resolve to that article's canonical URL. HTTP evidence locators must remain inside the registered source-domain allowlist. Fragment locators are permitted for paragraph/section references.

Claims with an assessment other than unassessed must link to evidence records belonging to that exact claim:

- supported requires at least one supports relationship and no contradicts relationship;
- partially_supported requires support plus qualifying or unclear evidence;
- disputed requires both supports and contradicts;
- contradicted requires contradicting evidence without supporting evidence;
- retracted requires a non-empty review note and linked evidence.

The intended write order is: create an unassessed claim, add evidence records, then revise the claim to link evidence and apply an assessment. A failed or stale revision leaves prior state unchanged.

## Syndication and independence policy

Section 5 produces possible_duplicate, confirmed_duplicate, and distinct decisions. A possible duplicate is unresolved and must not count as independent corroboration. A confirmed duplicate must be grouped with its target article when calculating independent reporting. A distinct resolution allows the article to count separately. Article records are never merged or deleted by this policy.

The first integrity layer does not yet provide a full independent-source scoring service. That service must use current duplicate decisions, retain unresolved candidates as uncertainty, and distinguish publisher count from independent-origin count.

## Timeline policy

Timeline events are not yet persisted as a separate entity. The planned record must support:
- date-only, instant, and bounded-interval precision;
- explicit timezone offsets for instants;
- source-local wording and normalized UTC instants where both are available;
- claim/evidence links for each event;
- uncertain or disputed time ranges without fabricated midnight values;
- append-only corrections and event revisions.

A timeline must never infer a precise event time from an article publication timestamp.

## Remaining Section 6 work

1. Add typed timeline-event contracts and persistence with explicit time precision.
2. Add auditable story-article relationship decisions and explicit merge/split/supersession relationships.
3. Add independent-source grouping based on current duplicate decisions.
4. Add story chronology and event ordering that preserves uncertain dates and ranges.
5. Add fixtures for contradictory claims, retractions, duplicate syndication, story splits/merges, and chronology corrections.
6. Verify all changes with the complete fixture suite before closing Section 6.

No live publisher access, automated factual conclusions, or publication behavior is enabled by this section.
