# Section 7: Editorial production and bilingual quality

Status: initial learning-object persistence is implemented on the Section 6 branch dependency. This design defines the first deliverable for Section 7, not a complete production editor or generation pipeline.

## Core output model

A learning object is a story-linked editorial artifact in exactly one language. Bangla and English are separate records with independent revision and review states; an optional variant group links their editorial relationship without treating either as an automatic translation of the other.

Initial kinds:

- `headline_summary`: a compact, single-focus description.
- `brief_summary`: the main short-form story summary.
- `detailed_summary`: a fuller account that preserves qualifications and chronology.
- `vocabulary`: a source-contextualized word or phrase with meaning and example.
- `comprehension_question`: a question answerable from linked claims.
- `analysis_question`: an interpretation question with an evidence-grounded explanation.\n- `mcq_question`: a multiple-choice item with 2–6 uniquely identified options, one correct-option key, an explanation, and answer-claim links.
- `revision_card`: a concise exam-preparation learning unit.

Each artifact carries its story ID, language tag, claim IDs, structured content, lifecycle status, creation time, and revision number. Optional `variant_group_id` groups related language versions of the same story artifact; group members must share story and kind, and only one object per language may occupy a group. Claim references must belong to the same story. The contract validates kind-specific minimum content: summaries/cards need text; vocabulary needs term, term language, part of speech, meaning, source context, and example; optional pronunciation, synonyms, antonyms, collocations, and exam context remain structured fields; questions need question, answer, explanation, and explicit answer-claim IDs. Summary kinds also require sentence-level segments. Each segment has a role (fact, attributed, or analysis) and one or more claim links; attributed segments name their attribution. The registry requires the rendered text to equal the ordered segment text and the union of segment claim IDs to equal the object's claim list.

## Lifecycle and review

New artifacts start as `draft` or `review_required`. Approval or rejection is a revision and requires reviewer identity, timezone-aware review timestamp, and a non-empty review note. Review cannot predate creation. Reviewer identity is recorded but not authenticated by this local registry.

Revisions use optimistic concurrency with an expected revision. Story, kind, language, ID, and creation time are immutable identity fields. Content, claim links, lifecycle state, and review metadata can change through a new revision. Revision history is append-only and is written by SQLite triggers in the same transaction as the current-record update. Stale edits are rejected.

### Vocabulary source provenance

Every vocabulary object must identify a registered evidence record and the exact evidence revision whose locator points to the source context. The evidence must belong to one of the object's linked claims, remain listed by that claim, and match its current source/article provenance. Store the evidence ID and revision rather than copying a source sentence into the learning object. The registry rechecks this binding on create, revision, and quality reporting; SQLite repeats it for direct writes. An absent, mismatched, or stale evidence link blocks the quality gate. This is traceability, not proof that the contextual sense or glossary definition is linguistically correct; a qualified editor must review those separately.


## Editorial constraints

- Every factual learning object should link to the claims it uses. Summary segments must each link to one or more claims, and the object-level claim list must exactly match the segment links. Empty claim lists remain representable for non-factual learning material, but they must not be used to disguise factual assertions without provenance.
- Vocabulary entries must preserve the sentence or context in which the word appeared. A dictionary meaning without source context is insufficient.
- Questions must have an answer and explanation. Every comprehension, analysis, and MCQ answer claim must map to a registered supporting evidence record and exact evidence revision. The registry verifies the evidence still belongs to that claim, is linked from it, and has a locator matching current source/article provenance. Quality reports recheck this binding and block missing, stale, unrelated, or non-supporting evidence. This is traceability; it does not prove that answer wording logically follows from the source, so qualified human review remains required.
- Bangla and English outputs are reviewed independently. A reviewed English artifact does not approve a Bangla counterpart.
- Corrections create a new revision. Previously approved content is not silently overwritten.
- This stage persists and reviews editorial artifacts; it does not generate summaries, translate content, invent exam questions, or publish editions automatically.

## First acceptance slice

1. Contract validation enforces kind-specific content, sentence-level claim mapping, and review metadata.
2. SQLite migration 11 persists learning objects and immutable revision history.
3. The registry validates active story and same-story claim references.
4. New objects begin unapproved; approval/rejection requires review metadata. Vocabulary objects must reference a matching glossary entry.
5. Optimistic concurrency rejects stale revisions.
6. Bangla and English objects remain independent.
7. The story editorial snapshot includes existing learning objects and the glossary entries those vocabulary objects reference.
8. CI verifies contract, migration, review, provenance, revision, glossary-link, and audit invariants.

## Deferred to later Section 7 slices

- Deterministic summary templates and claim-to-sentence traceability.
- Structured vocabulary fields for pronunciation, part of speech, synonyms, antonyms, collocations, and exam context.
- Question answerability and distractor-quality benchmarks beyond explicit answer-claim linkage.
- Bilingual terminology/glossary governance and separate language-quality review.
- Human-reviewed editorial benchmark fixtures for omission, distortion, attribution, and language quality.
- Daily briefing assembly and immutable edition publication belong to Sections 8 and 10.


## First-slice verification

GitHub Actions [run #44624081082](https://github.com/Amiroudro/new-sngso/actions/runs/44624081082) passed the full Python 3.12 suite: **428 passed in 3.27 seconds** on code head `0df67afa72651b2b4b69789ce2095047187b1489`. The run includes contract checks, schema v10-to-v11 and v11-to-v12 migrations, same-story claim enforcement, language-variant independence, kind-specific content requirements, sentence-to-claim traceability, question answer-claim links, controlled glossary entries and vocabulary consistency, review metadata, optimistic concurrency, immutable revision history, SQLite glossary guards, and inclusion of learning objects plus referenced glossary entries in the story editorial snapshot.


Question objects must identify `answer_claim_ids`, a non-empty subset of the object's claim IDs. This separates the claims that ground the answer from other claims that may only provide context for the question. MCQs must use unique option IDs and unique option text, and the correct-option key must point to one of the supplied options. These are structural checks only; distractor quality and ambiguity still require editorial benchmark review. Vocabulary objects distinguish the learning-object language from the headword's `term_language`, so an English term can have a Bangla explanation without mislabelling the language of the headword. Each vocabulary object references a controlled glossary entry whose lemma, term language, and part of speech must match. Part of speech is required; pronunciation, synonyms, antonyms, collocations, and exam context have dedicated fields. Glossary entries contain one or more language-specific senses, independent review state, and immutable revision history. A vocabulary learning object cannot be approved until its referenced glossary entry is approved.


## Controlled glossary verification

The glossary registry is stored under schema migration 12. A headword is unique by term language and part of speech, contains one or more language-specific senses, and has its own review and revision history. Vocabulary learning objects must point to a matching registered headword; an approved vocabulary item also requires an approved glossary entry. The story dossier includes the referenced glossary entries alongside the learning objects. This keeps translations and lexical metadata consistent across stories without pretending that translation is automatic.


## Deterministic editorial quality report

`learning_object_quality_report(learning_object_id)` checks workflow and provenance conditions without attempting to decide truth. It returns named checks, blockers, and warnings. The registry gate blocks an unapproved artifact, missing claim/provenance references, factual summary segments whose claims are not supported/partially supported with evidence, question answers whose answer-claim links are not supported/partially supported with evidence, and vocabulary objects whose controlled glossary entry is not approved or does not match. Disputed, contradicted, retracted, or unassessed claims in attributed/analysis segments remain visible as warnings so the wording can preserve the qualification.

The report is deterministic and read-only. It does not generate a numerical quality score, rank claims, or replace an editor's judgement. It provides a machine-checkable hand-off for later edition assembly.

GitHub Actions [run #44625107271](https://github.com/Amiroudro/new-sngso/actions/runs/44625107271) passed **434 tests in 3.70 seconds** on code head `99316bb5423ce36f1dafd4b0afc0f764a299e8a9`. The suite includes quality-report pass/blocker cases, proof that reports do not write audit events, MCQ option/key validation, and a direct SQLite guard against duplicate MCQ option IDs.


## Independent, versioned Bangla and English language-review rubrics

Structured language review now carries an explicit rubric version. Bangla uses bn-editorial-v1 with grammar/spelling, naturalness/register, terminology consistency, factual fidelity, attribution, omissions, and translation fidelity. English uses en-editorial-v1 with grammar/style, clarity/readability, terminology consistency, factual fidelity, attribution, omissions, and translation fidelity. The distinct language-specific criteria prevent a generic language-accuracy checkbox from standing in for meaningful review.

A review can be recorded as approved or rejected. An approved review cannot contain a needs_revision finding; a rejected review must contain at least one. The exact expected dimension set is enforced, so missing, extra, or cross-language criteria fail closed. A rejected review remains a durable revision rather than being discarded. These checks validate the review record's structure and declared findings, not the reviewer's competence or the actual quality of the prose. Reviewer identity is still not authenticated by the learning-object registry.

The story summary-tier report counts a language review as passed only when the artifact is approved and its complete, language-matched rubric passes. Bangla and English remain independent. No combined score or publication authorization is produced.


## Authenticated editorial review boundary

The authenticated application facade now provides a reviewer-only operation for learning objects. It resolves the reviewer from the trusted identity provider, requires an active human-classified benchmark reviewer, checks recent authentication (15 minutes), verifies issuer/subject-to-principal binding, requires provider key/adapter and authentication-context evidence, and checks the principal's registered language qualification before recording a decision. The decision is attached to the exact expected learning-object revision using optimistic concurrency.

The immutable learning-object revision contains a structured identity attestation with principal, issuer, subject, provider key ID, adapter, authentication time/method/context, and attestation time. Raw session identifiers are not stored. The registry validates the attestation's completeness, principal binding, and timestamp binding. Direct registry mutation remains a trusted deployment boundary; production request handlers must expose the authenticated facade rather than raw registry methods. This is an application boundary and durable provenance record, not proof that a reviewer performed a competent or truthful assessment. Real OIDC infrastructure and protected handler deployment remain required.


## Multi-source perspective and attribution report

The story dossier now includes a deterministic `perspective_attribution_report`. It keeps each current perspective's exact attribution, source and article IDs (when applicable), language, text, linked claim IDs, and review state. It summarizes perspective coverage by kind and language, lists source-linked perspectives separately from stakeholder/editorial statements without publisher-source links, and preserves current claim-contradiction relations with their review state and any resolution note.

The report deliberately does not turn publisher count into an independence score. Distinct publishers or articles do not prove independent reporting: syndication, common sourcing, and shared-origin material need separate evidence. Unreviewed perspectives and proposed contradictions remain visible. The report is read-only and always returns `production_ready=false` and `authorization_granted=false`; it is an editorial hand-off, not a publication gate.

## Section 7.30: reviewed bilingual narrative assembly

The story dossier now exposes a read-only narrative assembly report. It assembles existing sentence-level learning-object segments without flattening publisher framing, stakeholder positions, or editorial analysis into one undifferentiated narrative.

- Bangla and English are reported separately. The report does not average language quality or treat a missing counterpart as a failed score.
- Every segment retains its role, exact text, language, linked claim IDs, claim assessments, claim provenance, and evidence locators.
- A factual segment is labelled supported only when every linked claim is assessed as supported and has at least one supporting evidence record. Otherwise it remains unverified/disputed and is listed as a blocker.
- Attributed segments retain their attribution and remain distinct from factual statements. Publisher framing is grouped separately with its source and article IDs.
- Editorial-analysis segments remain explicitly labelled as analysis.
- Contradictory claim relationships and both claim texts remain visible. A contradiction is treated as unresolved unless its relationship has been rejected; an accepted contradiction is still shown because acceptance of the relationship does not itself resolve the underlying factual dispute.
- Non-approved learning objects and missing language-review evidence are blockers. The report never grants publication or production authorization.
- The snapshot embeds the report, and the public report includes the exact snapshot fingerprint used to assemble it.

This is deterministic traceability and workflow reporting, not an automated truth judgement, a linguistic-quality evaluation, or proof that sources are independent. The report cannot replace human editorial review.

## Section 7.31: source-independence evidence gap report

The story dossier now includes a source-independence evidence report alongside the existing duplicate-review summary. It distinguishes article-record identity from reporting independence:

- A confirmed duplicate remains a duplicate group; a possible duplicate remains unresolved.
- A reviewed-distinct result means only that the article records were judged distinct. It does not mean their reporting origins are independent.
- Articles without duplicate candidates remain unreviewed for independence, not independent by default.
- The report leaves independent-reporting count unset and every article's independence status as not established until original-reporting provenance, syndication/republication, shared-source/common-origin, and relevant primary-source evidence have been assessed.
- The report is embedded in the story snapshot and is explicitly non-authorizing.

This report exposes evidence gaps; it does not manufacture lineage evidence or infer independence from publisher count, URL, or wording.

## Section 7.32: language-review enforcement in narrative assembly

Narrative assembly now evaluates the actual language-specific review rubric, not merely the presence of a review record. Each language report exposes per-object language_review_pass, an aggregate assembly status, and counts for learning objects, segments, factual segments, traceable segments, unsupported factual segments, passing language reviews, and blockers.

A review passes this gate only when its language and rubric version match the relevant language and every required rubric dimension passes (with translation fidelity allowed to be marked not applicable). Missing, rejected, or needs-revision language reviews remain blockers. Bangla and English are calculated independently. review_required means the deterministic blockers are absent but human editorial workflow remains; it is not a publication approval. Production authorization remains false.

## Section 7.33: MCQ distractor rationale traceability

MCQ learning objects now require one non-empty rationale for every incorrect option and no rationale for the correct option. Each rationale is keyed to the exact option ID. The registry rejects missing, duplicate, extra, or correct-answer rationale entries. This makes the author's intended distinction between the correct answer and each distractor inspectable and keeps rationale coverage stable if option order changes.

The deterministic learning-object quality report exposes a dedicated distractor-rationale coverage check. It also emits a permanent human-review warning: rationale presence does not prove that a distractor is plausible, unambiguously incorrect under the exact question wording, misconception-based, parallel in form, or free of accidental clues. Editors must review those properties against the source claims and the intended learner level. The registry does not automatically score distractor quality or promote an MCQ to publication readiness.

## Section 7.34: source-claim support for MCQ distractor rationales

Each MCQ distractor rationale now carries one or more basis_claim_ids. These IDs must be a non-empty subset of the learning object's story-linked claims. The quality report separately checks whether every rationale basis claim still exists, is assessed as supported or partially supported, and has linked evidence. Missing, unassessed, disputed, contradicted, retracted, or evidence-free rationale claims block the registry quality gate.

This is a conservative evidence-link check, not proof that the rationale logically follows from the claim or that the distractor is pedagogically effective. Human review remains necessary to judge the wording, ambiguity, plausibility, and learner-level suitability of each option.


## Section 7.39: language-rubric quality gate

Every learning object must pass its language-specific editorial rubric before the deterministic registry quality report can return a passing quality gate. Generic approval metadata alone is insufficient. Bangla and English use their exact rubric versions and dimension sets. This check establishes recorded rubric completeness only; trusted reviewer authentication, factual correctness, question pedagogy, genuine human evidence, and production authorization remain separate requirements.
