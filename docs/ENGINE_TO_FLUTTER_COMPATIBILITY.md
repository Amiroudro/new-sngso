# Evidence-First Engine to Flutter Compatibility Boundary

**Status:** English story-only staging builder/verifier implemented and tested; Flutter runtime validation and app integration remain unapproved. No publisher or app output is approved by this document.  
**Observed repositories:** `Amiroudro/new-sngso` PR #7 code head `f7889295a9ca329852b518f96af68fb0b2b4b315`; `Amiroudro/Sngs-plus` main inspected 20 November 2026.  
**Purpose:** Specify what must be proven before the new evidence-first engine can feed the existing Flutter app.

## Current consumer contract

The integrated client parses a legacy edition JSON object into `Edition` in `Sngs-plus/app/lib/models.dart`. Its top-level fields are:

- `edition_date`, `edition_day`, `coverage`, `status`, `counts`
- `stories`, `words`, `revision_words`, `phrases`, `mcqs`, `sources`

The client model reads whitelisted story fields such as `id`, `headline`, `category`, `event_date`, `article_date`, `summary`, `sources`, and optional learning fields. Source references are rendered from publication, headline, date, and URL fields. MCQ options are consumed as a key-to-text map, with a separate correct-answer key. English stories can have a separate Bangla overlay; absent Bangla fields fall back to English.

The existing v2 converter describes its output as the legacy v1 app contract. This is distinct from the typed records and versioned contracts in `new-sngso`. No direct JSON interchangeability has been established.

## Required mapping boundary

| Evidence-first engine concept | Legacy client concept | Required adapter rule |
| --- | --- | --- |
| Edition run and immutable source snapshots | Top-level edition metadata | Preserve a stable edition ID, exact source/run fingerprints, and a manifest outside the current client model. Do not set `status=published` during conversion. |
| Story record and article membership | `stories[]` | Preserve story and article IDs in a sidecar manifest; do not merge distinct stories based on title similarity alone. |
| Approved English headline/summary learning objects | Story headline and summary fields | Export only an explicitly selected, approved object revision whose full quality report passes. Preserve fact, attribution, analysis, uncertainty, and claim/evidence links in the manifest. |
| Explicitly paired Bangla variants | `bn` overlay file | Require the exact same story, kind, and explicit variant group; require a passing Bangla review independently. Never use English fallback as a completed translation. |
| Source and article records | Story `sources[]` | Map publisher, headline, publication date, and canonical URL. The legacy source object alone does not carry permission scope, claim-level evidence, source lineage, or rights expiry; retain these in a validated manifest. |
| MCQ learning object with stable option IDs | `mcqs[]` or learning-module MCQ | Map option IDs to client keys only when the IDs are unique and valid for the client's parser. Preserve the answer key. Retain distractor rationales, basis claims, evidence, rubric, and review attestation in the manifest because current client models do not represent them. |
| Vocabulary / learning-object records | `words[]`, `phrases[]`, or learning-module fields | Map only fields with equivalent semantics. Do not fill missing client fields with generated guesses or conflate glossary definitions with editorial summaries. |

## Fail-closed gates before any adapter can export

1. Require an explicit export selection with exact story, learning-object, article, evidence, and source revisions.
2. Recheck source-operation permission at export time; permission may expire or be revoked after ingestion.
3. Require a passing registry quality report and authenticated review for every selected language-specific object. MCQs additionally require the full ten-dimension pedagogical rubric.
4. Reject unsupported, disputed, contradicted, retracted, stale, or evidence-free factual claims; preserve allegations and attributed positions as such.
5. Generate a deterministic manifest binding the client JSON to all selected record IDs, revisions, evidence IDs/revisions, permission decisions, review IDs, and content hashes.
6. Validate the client JSON against the actual Flutter parser and golden edition fixtures without changing, replacing, or syncing existing editions.
7. Keep the result in a separate staging directory and mark it draft. An adapter must not write to the published feed, bundled assets, daily schedule, or release branch.

## Current blockers

- The new engine has no reviewed, accepted production benchmark or real-world edition acceptance evidence.
- The Flutter parser currently exposes summary-level source references and does not consume a claim-level evidence/permission manifest. A read-only manifest verifier exists in new-sngso. The Flutter client still has no consumer-side manifest validation or UI path.
- The new-sngso builder serializes only English headline/summary stories in a staging-only in-memory bundle; it pins approved revisions and rechecks current sources and grants. There is still no shared executable schema or cross-repository golden test.
- The integrated repository's Engine v2 integration status is recorded as draft, not live. Its current formats must not be treated as the new engine's target contract without tests.
- No compatibility check has been run with Flutter in this environment.

## Section 7.42: staging-only verifier

The verifier checks an English story-only client candidate against a current approved edition, exact learning-object text/revisions, active human reviewer qualification, registered citations, and current republication grants scoped to registered endpoints. It returns a staging-only verification result and never writes or publishes. Tests cover successful and blocked candidates.

## Section 7.43: English staging builder

The builder creates an in-memory candidate for every story in an approved edition from explicit approved headline and summary object selections. It emits a provenance manifest with edition, story, and learning-object revisions/fingerprints, then invokes the verifier. It rejects unbound fields and unsupported collections, including bilingual overlays, vocabulary, phrases, MCQs, and learning modules. GitHub Actions [run #44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682) passed the full Python 3.12 suite: **703 tests in 6.68 seconds** on code head f7889295a9ca329852b518f96af68fb0b2b4b315.

Neither component has been run through the Flutter parser. The integrated app does not consume the provenance sidecar, and no cross-repository golden test or confirmed republication grant exists. The compatibility exit gate remains open; keep the engines and edition feeds separate.
