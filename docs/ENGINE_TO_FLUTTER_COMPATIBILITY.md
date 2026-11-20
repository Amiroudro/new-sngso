# Evidence-First Engine to Flutter Compatibility Boundary

**Status:** Staging-candidate verifier implemented and tested; serializer and app integration remain unapproved. No publisher or app output is approved by this document.  
**Observed repositories:** `Amiroudro/new-sngso` PR #7 code head `ec06daf4d3abba0b8034d56c1f84bb470d63229d`; `Amiroudro/Sngs-plus` main inspected 20 November 2026.  
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
- There is no shared executable schema or full serializer between the evidence-first contracts and the Flutter models, and no cross-repository golden test. The new verifier supports only English headline/summary stories and checks candidates against current registry records.
- The integrated repository's Engine v2 integration status is recorded as draft, not live. Its current formats must not be treated as the new engine's target contract without tests.
- No compatibility check has been run with Flutter in this environment.

## Section 7.42: staging-only verifier

The new-sngso verifier checks an already-prepared English story-only client candidate against an approved edition revision, exact approved learning-object text, current article/source records, human reviewer qualification, and current republication grants scoped to the exact endpoint. It returns a staging-only verification result and never writes to or publishes any feed. Its synthetic regression suite passed as part of GitHub Actions [run #44752476466](https://github.com/Amiroudro/new-sngso/actions/runs/44752476466) passed the full Python 3.12 suite: **701 tests in 6.38 seconds** on code head ec06daf4d3abba0b8034d56c1f84bb470d63229d.

The verifier is not a serializer and has not run against Flutter. It deliberately rejects bilingual overlays, vocabulary, phrases, MCQs, learning modules, and unbound client fields. The integrated app does not consume the provenance sidecar, and no cross-repository golden test or confirmed republication grant exists. The compatibility exit gate remains open; keep the engines and edition feeds separate.
