# Section 9 Content, Provenance, Ingestion, and Learning Trigger Semantic Review

Status: **source-level semantic review completed for all 49 content/provenance/ingestion triggers, including schema-v34 and v35 revision guards. Together with the companion review documents, this completes source-level semantic review of all 139 SQLite triggers. It does not complete direct-SQL conformance, PostgreSQL implementation, or Section 9 production acceptance.**

This review covers permission-grant and domain revision history, collection observations, ingestion decisions/current pointers, story perspectives and claim relations, learning objects and their evidence, and bilingual glossary entries.

## 1. Revision history and permission grant state (9 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `grant_revisions_no_update` | Permission grant revision history cannot be rewritten | BEFORE UPDATE trigger and restricted role | Permission-first collection, revocation and audit tests |
| `grant_revisions_no_delete` | Permission grant revision history cannot be deleted | BEFORE DELETE trigger and restricted role | Same |
| `permission_grant_revisions_validate_insert` | New revision must match the current grant head or be exactly the next legal status revision, with non-empty actor/reason | BEFORE INSERT trigger checking current/next revision, immutable grant/source identity and legal status transition | New schema-v34 direct-SQL revision guard tests |
| `permission_grants_revision_guard` | Grant ID/source/effective window immutable; revision +1; payload matches indexed ID/source/status; update requires a matching revision; only pending→approved/denied/suspended/revoked, approved→suspended/revoked, and suspended→approved/revoked are allowed | BEFORE UPDATE trigger with revision-row check and explicit transition table | Existing grant lifecycle tests plus new schema-v34 direct-SQL update rejection test |
| `domain_revisions_no_update` | Domain-record revision history cannot be rewritten | BEFORE UPDATE trigger and restricted role | Ingestion revision and story correction tests |
| `domain_revisions_no_delete` | Domain-record revision history cannot be deleted | BEFORE DELETE trigger and restricted role | Same |
| `domain_record_revisions_validate_insert` | Revision must match the current domain-record head's type, ID, revision and exact payload; actor/reason required | BEFORE INSERT trigger bound to the already-updated head, inside the same transaction | Schema-v34 forged-revision rejection test |
| `domain_records_revision_guard` | Domain record type/ID/creation time immutable; revision increments exactly one; actor/reason/timestamp required; payload ID and optional payload revision match indexed identity/revision; revision time cannot move backward; article/evidence identity links cannot be rebound | BEFORE UPDATE trigger with typed head checks and null-safe JSON comparisons | Schema-v35 direct-SQL revision-jump and article-rebinding rejection tests |
| `domain_records_record_update` | Every domain-record head update automatically appends its matching revision snapshot in the same transaction | AFTER UPDATE trigger inserts the exact new head into append-only revision history | Schema-v35 direct-SQL automatic snapshot test; existing story merge, ingestion refresh and generic revision tests |
| `collection_observations_no_update` | Original collection observations are immutable | BEFORE UPDATE trigger and restricted role | Observation preservation and idempotent ingestion tests |
| `collection_observations_no_delete` | Collection observations cannot be deleted | BEFORE DELETE trigger and restricted role | Observation provenance and append-only tests |

**Verified database boundary:** schema v34 added `domain_record_revisions_validate_insert`, `permission_grant_revisions_validate_insert`, and `permission_grants_revision_guard`. Schema v35 added `domain_records_revision_guard` and `domain_records_record_update`. The direct-SQL tests in `tests/storage/test_registry.py` exercise forged revision inserts, permission-grant revision/head coupling, revision-jump and identity-rewrite rejection, and automatic domain-record revision snapshots. These SQLite guards close the previously documented insert/head-coupling gap at source level. PostgreSQL must preserve the same ordering and atomicity under its own transaction and privilege model.

**Remaining verification boundary:** the direct-SQL cases above are repository tests, not proof of PostgreSQL equivalence or of execution under the restricted production role. The schema-v35 head update and automatic revision snapshot must remain one transaction, and failure injection must prove both changes roll back together.

## 2. Ingestion decision ledger and current pointer (6 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `ingestion_decisions_validate_article` | Decision article must exist and match source and canonical URI; observation must belong to same source; duplicate target, when supplied, must be an article from another source | BEFORE INSERT trigger with typed FKs and row locks for target/source/observation validation | Ingestion source binding, duplicate decision, cross-publisher, provenance and tamper tests |
| `ingestion_decisions_no_update` | Ingestion decisions are immutable | BEFORE UPDATE trigger and restricted role | `test_ingestion_decision_history_is_append_only` |
| `ingestion_decisions_no_delete` | Ingestion decisions cannot be deleted | BEFORE DELETE trigger and restricted role | Same |
| `ingestion_current_validate` | Initial current pointer must reference a decision for the same observation | BEFORE INSERT trigger plus unique observation pointer | Idempotent ingestion and current-decision tests |
| `ingestion_current_validate_update` | Updated current pointer must still reference a decision for the same observation | BEFORE UPDATE trigger; preserve observation identity and serialize competing updates | Newer/older observation ordering and stale duplicate-resolution tests |
| `ingestion_current_no_delete` | Current ingestion pointer cannot be deleted | BEFORE DELETE trigger and restricted role | Ingestion history and idempotency tests |

**Translation hazard:** the current-pointer update triggers validate that the selected decision belongs to the observation, but the trigger SQL does not itself establish a monotonic decision ordering or immutable observation identity across UPDATE. Confirm the table's primary/unique constraints and registry compare-and-swap logic. PostgreSQL must prevent a direct writer from moving the current pointer to an older or unrelated decision while preserving the intended ability to reverse a duplicate decision through a new append-only decision.

The ingestion decision guard checks cross-publisher duplicate targets. It does not by itself prove that the duplicate relationship is correct editorially; the decision payload, operator authority, source status, and current article metadata remain governed by registry/application policy.

## 3. Story perspectives and claim relations (6 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `perspectives_validate_insert` | Story must be active; optional source must be registered; publisher framing requires source and article; article must belong to source and story; claims must belong to story; review metadata must match reviewed/unreviewed state | BEFORE INSERT trigger with FKs and normalized story/article/claim relations; JSONB review metadata checks | Perspective attribution, publisher framing, cross-story rejection, and review metadata tests |
| `perspectives_no_update` | Perspective records are append-only | BEFORE UPDATE trigger and restricted role | Correction preserves prior record/history tests |
| `perspectives_no_delete` | Perspective records cannot be deleted | BEFORE DELETE trigger and restricted role | Correction/history tests |
| `claim_relations_validate_insert` | Active story required; endpoints differ; both claims belong to same story; reviewed relation requires complete review metadata; unreviewed relation cannot carry it | BEFORE INSERT trigger with two claim FKs, same-story composite FK or locked validation, typed review fields | Self-reference, same-story, correction, canonical-order and review metadata tests |
| `claim_relations_no_update` | Claim relations are append-only | BEFORE UPDATE trigger and restricted role | Correction cannot rewrite prior assertion tests |
| `claim_relations_no_delete` | Claim relations cannot be deleted | BEFORE DELETE trigger and restricted role | Same |

**Translation hazards:**
- Preserve claim relation symmetry/canonical endpoint ordering for contradiction relationships; do not rely on application normalization alone.
- Preserve perspective article/source/story three-way binding.
- The perspective validator requires the claim list to resolve to claims in the story, but the shown trigger does not require a non-empty claim list. Verify the JSON contract and decide whether empty perspectives are allowed; do not accidentally tighten or weaken semantics in PostgreSQL.
- Review-status checks are payload-driven. Test missing key, explicit JSON null, wrong scalar type, and unknown status values.

## 4. Learning-object story binding, review lifecycle, and revisions (15 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `learning_objects_variant_group_validate_insert` | New variant must share story and kind with existing members of its variant group | Unique/indexed variant group plus BEFORE INSERT trigger | Duplicate language and mismatched kind tests |
| `learning_objects_variant_group_validate_update` | Updating a variant cannot make group members disagree on story or kind | BEFORE UPDATE trigger under group-level lock | Variant-group SQL boundary test |
| `learning_objects_validate_insert` | Active story required; claim list must resolve to claims in story; new object starts draft/review-required; initial payload cannot contain review metadata | BEFORE INSERT trigger and FKs; status CHECK; story/claim association table | Learning-object story binding and initial review-state tests |
| `learning_objects_validate_update` | ID/story/kind/language/variant group/creation time immutable; revision +1; actor/reason required; timestamp monotonic; active story and claims rechecked; reviewed status requires metadata and cannot predate creation; unreviewed status cannot carry review metadata | BEFORE UPDATE trigger with row lock and null-safe identity checks; typed timestamps and explicit transition constraints | Revision, stale update, approval, review-date, and direct SQL revision tests |
| `learning_objects_no_delete` | Learning objects cannot be deleted | BEFORE DELETE trigger and restricted role | Revision history/immutability tests |
| `learning_objects_record_insert` | Initial head automatically writes revision snapshot | AFTER INSERT trigger in same transaction | Initial revision/audit test |
| `learning_objects_record_update` | Every head update automatically writes revision snapshot | AFTER UPDATE trigger in same transaction | Revision history tests |
| `learning_object_revisions_validate_insert` | Revision row must match current head ID/revision/payload and have actor/reason | BEFORE INSERT trigger with locked head and unique (object, revision) | Revision integrity tests |
| `learning_object_revisions_no_update` | Revision history cannot be rewritten | BEFORE UPDATE trigger and restricted role | Append-only history test |
| `learning_object_revisions_no_delete` | Revision history cannot be deleted | BEFORE DELETE trigger and restricted role | Same |

**High-priority boundary finding:** `learning_objects_validate_update` does not enumerate a complete status-transition graph. It enforces revision, identity, time and review-metadata shape, while richer approval eligibility, language-rubric, pedagogical-review, evidence quality, and bilingual independence rules live in the registry/quality gates. A direct SQL update may therefore satisfy the trigger while bypassing the full application transition policy. PostgreSQL must explicitly enforce permitted transitions or expose a restricted stored procedure that performs the complete authorized transition.

The revision AFTER triggers and revision INSERT validator form a useful atomic invariant: update the head, append exactly matching snapshot, and roll back both on failure. Preserve that transaction boundary. Do not make revision writing asynchronous.

## 5. Learning-object evidence and vocabulary/MCQ rules (8 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `learning_object_vocabulary_source_evidence_insert` | Vocabulary source evidence must be the current evidence revision, linked to an object claim, listed on the claim, and match claim provenance source/article/observation | BEFORE INSERT trigger joining object claims, claim evidence, evidence revision and provenance | Unknown/stale vocabulary evidence and provenance tests |
| `learning_object_vocabulary_source_evidence_update` | Same source-evidence invariant on payload update | BEFORE UPDATE OF payload trigger with locked evidence/claim records | Source evidence remains linked to current claim provenance |
| `learning_object_question_answer_evidence_insert` | Questions require non-empty unique evidence refs; each answer claim is covered; claim/evidence exist, belong to object and answer set, evidence revision is current, relationship supports, locator exists, and provenance matches | BEFORE INSERT trigger with normalized answer-evidence join table and composite FKs | Question answer evidence completeness, stale ref and SQL-boundary tests |
| `learning_object_question_answer_evidence_update` | Same question evidence invariant on payload update | BEFORE UPDATE trigger | Quality-report and stale evidence tests |
| `learning_objects_mcq_validate_insert` | MCQ has 2–6 options, unique option IDs/text, and correct option references an existing option | JSONB validation trigger or normalized option table with unique constraints | MCQ duplicate ID, unknown answer, and schema tests |
| `learning_objects_mcq_validate_update` | Same MCQ option rules on update | BEFORE UPDATE trigger | MCQ update and quality tests |
| `learning_objects_glossary_validate_insert` | Vocabulary object must match registered glossary lemma/language/POS/sense; approved object requires approved glossary entry | BEFORE INSERT trigger joining glossary head and sense; FK to glossary entry | Vocabulary/glossary exact-match and approval tests |
| `learning_objects_glossary_validate_update` | Same glossary match and approval condition on update | BEFORE UPDATE trigger | Same |

**Important semantic details for PostgreSQL:**
- Evidence revision equality is an exact current-revision check, not merely evidence-ID existence.
- Source/article/observation provenance must all align; a same-publisher or same-story approximation is not enough.
- The question guard requires answer evidence for every answer claim and forbids duplicate (claim, evidence) pairs.
- MCQ text uniqueness is currently based on lowercased, trimmed text. Preserve this normalization or explicitly version the behavior.
- MCQ database triggers check option count/identity/correct-answer membership, while complete distractor explanation and pedagogical rubric requirements are quality-gate/application responsibilities.
- Vocabulary-to-glossary matching includes a pronunciation condition that allows a missing pronunciation on either side. Preserve the current behavior unless a deliberate contract revision is approved.

## 6. Glossary entry and revision lifecycle (5 triggers)

| Trigger | Current SQLite invariant | PostgreSQL mapping | Existing evidence |
|---|---|---|---|
| `glossary_entries_validate_insert` | New glossary head starts draft/review-required and cannot contain review metadata | BEFORE INSERT trigger and status CHECK | Glossary bilingual senses and initial-state tests |
| `glossary_entries_validate_update` | ID/lemma/key/language/POS/creation time immutable; revision +1; actor/reason required; timestamp monotonic; approved/rejected requires reviewer/time/note; unreviewed states cannot carry review metadata | BEFORE UPDATE trigger with row lock, typed timestamps and null-safe identity checks | Stale update, append-only revision and review metadata tests |
| `glossary_entries_no_delete` | Glossary head cannot be deleted | BEFORE DELETE trigger and restricted role | Glossary revision tests |
| `glossary_entries_record_insert` | Initial glossary revision snapshot is automatic | AFTER INSERT trigger in same transaction | Bilingual glossary persistence/history test |
| `glossary_entries_record_update` | Every update appends revision snapshot | AFTER UPDATE trigger in same transaction | Append-only/stale-update tests |

The glossary revision table also has three separate triggers: `glossary_entry_revisions_validate_insert` requires the inserted revision to match the current head and actor/reason to be non-empty; `glossary_entry_revisions_no_update` and `glossary_entry_revisions_no_delete` enforce append-only history. These three are included in the 44-trigger count above, not additional triggers.

**Boundary finding:** as with learning objects, the glossary update trigger does not enumerate all allowed status transitions. The application/schema checks bilingual senses and review semantics. PostgreSQL must enforce the same transition contract at the database boundary or prevent direct head writes except through a validated transaction function.

## 7. PostgreSQL translation hazards

1. `json_each`, `json_array_length`, `json_type`, and `json_extract` need explicit JSONB equivalents with missing/null/wrong-type behavior preserved.
2. `IS` / `IS NOT` and `IS NOT OLD.variant_group_id` require null-safe PostgreSQL comparisons.
3. Revision/head validation must serialize writers and be atomic. Use row locks and unique revision keys.
4. Domain and permission revision ledgers currently lack INSERT validation triggers. Add exact head-binding validation before accepting PostgreSQL parity.
5. Learning-object and glossary status transitions are not fully expressed in the current trigger SQL. Preserve application policy and close direct-SQL bypass paths.
6. Collection observations, ingestion decisions, perspectives, claim relations, and revision histories must remain append-only under the actual application role.
7. Provenance joins must preserve source/article/observation equality, not just claim/story membership.
8. Test concurrency across separate PostgreSQL connections: observation idempotency, permission revocation racing persistence, ingestion current-pointer updates, variant-group insertion, and learning/glossary revision updates.
9. Translate date/time checks to typed timestamptz semantics and reject invalid/naive timestamps consistently with application contracts.
10. Use database constraints for canonical identity, unique head keys, foreign keys, and one-to-one/current-pointer relations wherever possible; triggers should protect cross-row invariants, not replace simple constraints.

## 8. Required direct-SQL and PostgreSQL acceptance tests

- Reject direct UPDATE/DELETE on collection observations, ingestion decisions, perspectives, claim relations, learning objects, glossary heads, and every revision table.
- Reject fabricated domain-record and permission-grant revision inserts that do not match an existing head.
- Reject invalid learning-object/glossary status transitions even when revision, actor, reason and timestamps are otherwise valid.
- Reject source evidence whose claim, source, article, observation or evidence revision differs by one field.
- Reject question answer evidence that is missing, duplicated, stale, unrelated, non-supporting, or lacks a locator.
- Reject MCQs with 1 or 7 options, duplicate IDs, duplicate normalized text, or an unknown correct option.
- Reject vocabulary objects with mismatched lemma/language/POS/sense or unapproved glossary source when object is approved.
- Reject perspectives with article/source/story mismatch and claim relations with cross-story/self references.
- Prove all head+revision writes roll back together under injected failure.
- Test ingestion current-pointer updates and permission revocation races using independent PostgreSQL connections and the restricted production application role.

## 9. Review boundary

This document covers 49 content/provenance/ingestion triggers, including three schema-v34 guards and two schema-v35 domain revision guards. Migration 35 now records the last actor, reason and revision timestamp on each domain-record head and appends matching revision snapshots automatically on every update. Together with the 23 operational triggers, 28 publication/audit/story/timeline triggers, and 39 benchmark-governance triggers in the companion documents, **all 139 of 139 SQLite triggers now have source-level semantic review records**.

This is a major review milestone, not production completion. Several database-boundary gaps are explicitly identified above and in the companion benchmark/operational reviews. No PostgreSQL migration or PostgreSQL conformance suite has been implemented yet. P0 direct-SQL acceptance and Section 9 production acceptance remain open.
