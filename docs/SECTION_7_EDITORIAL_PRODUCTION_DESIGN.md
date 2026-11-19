# Section 7: Editorial production and bilingual quality

Status: initial learning-object persistence is implemented on the Section 6 branch dependency. This design defines the first deliverable for Section 7, not a complete production editor or generation pipeline.

## Core output model

A learning object is a story-linked editorial artifact in exactly one language. Bangla and English are separate records with independent revision and review states; neither language is silently treated as a translation of the other.

Initial kinds:

- `headline_summary`: a compact, single-focus description.
- `brief_summary`: the main short-form story summary.
- `detailed_summary`: a fuller account that preserves qualifications and chronology.
- `vocabulary`: a source-contextualized word or phrase with meaning and example.
- `comprehension_question`: a question answerable from linked claims.
- `analysis_question`: an interpretation question with an evidence-grounded explanation.
- `revision_card`: a concise exam-preparation learning unit.

Each artifact carries its story ID, language tag, claim IDs, structured content, lifecycle status, creation time, and revision number. Claim references must belong to the same story. The contract validates kind-specific minimum content: summaries/cards need text; vocabulary needs term, meaning, context, and example; questions need question, answer, and explanation.

## Lifecycle and review

New artifacts start as `draft` or `review_required`. Approval or rejection is a revision and requires reviewer identity, timezone-aware review timestamp, and a non-empty review note. Review cannot predate creation. Reviewer identity is recorded but not authenticated by this local registry.

Revisions use optimistic concurrency with an expected revision. Story, kind, language, ID, and creation time are immutable identity fields. Content, claim links, lifecycle state, and review metadata can change through a new revision. Revision history is append-only and is written by SQLite triggers in the same transaction as the current-record update. Stale edits are rejected.

## Editorial constraints

- Every factual learning object should link to the claims it uses. Empty claim lists remain representable for non-factual learning material, but they must not be used to disguise factual assertions without provenance.
- Vocabulary entries must preserve the sentence or context in which the word appeared. A dictionary meaning without source context is insufficient.
- Questions must have an answer and explanation. The answer must be supported by linked claims.
- Bangla and English outputs are reviewed independently. A reviewed English artifact does not approve a Bangla counterpart.
- Corrections create a new revision. Previously approved content is not silently overwritten.
- This stage persists and reviews editorial artifacts; it does not generate summaries, translate content, invent exam questions, or publish editions automatically.

## First acceptance slice

1. Contract validation enforces kind-specific content and review metadata.
2. SQLite migration 11 persists learning objects and immutable revision history.
3. The registry validates active story and same-story claim references.
4. New objects begin unapproved; approval/rejection requires review metadata.
5. Optimistic concurrency rejects stale revisions.
6. Bangla and English objects remain independent.
7. The Section 6 editorial snapshot includes existing learning objects.
8. CI verifies contract, migration, review, provenance, revision, and audit invariants.

## Deferred to later Section 7 slices

- Deterministic summary templates and claim-to-sentence traceability.
- Structured vocabulary fields for pronunciation, part of speech, synonyms, antonyms, collocations, and exam context.
- Question answerability and distractor-quality benchmarks.
- Bilingual terminology/glossary governance and separate language-quality review.
- Human-reviewed editorial benchmark fixtures for omission, distortion, attribution, and language quality.
- Daily briefing assembly and immutable edition publication belong to Sections 8 and 10.
