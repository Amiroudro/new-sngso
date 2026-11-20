# Section 7.26: benchmark acceptance evidence in story editorial snapshots

## Master-plan alignment

This work belongs to **Section 7: Editorial production and bilingual quality**. It does not start Section 8, whose scope is edition assembly and publication safety.

## Purpose

Section 7's human benchmark workflow now has an explicit hand-off into the story dossier. Editors can inspect whether a benchmark acceptance decision is still eligible for its declared, limited purpose without mistaking a historical approval record for permanent permission or publication authorization.

## Snapshot integration

A story snapshot includes acceptance decisions for benchmark artifacts already linked to the story through registered benchmark-review source articles. Each entry carries a current quality report rather than presenting the historical acceptance decision as a permanent green light.

The report rechecks:

- the immutable acceptance payload fingerprint;
- exact registered protocol version and fingerprint;
- exact execution fingerprint and its protocol relationship;
- benchmark artifact fingerprint and execution binding;
- acceptance contract, decision, and all three domain outcomes;
- at least two distinct active human benchmark reviewers whose exact review revisions have provider attestations and whose source permissions remain currently valid.

A source permission that expires, is suspended, revoked, loses operation scope, or no longer has verified evidence blocks scoped reference. Historical records remain intact.

## Decision semantics

The report uses `eligible_for_scoped_reference`, not a production-use or publication-authorization flag. It includes the declared intended use and scope limitations, named checks, and blockers.

Every report returns `production_ready: false` and `authorization_granted: false`. It does not establish factual correctness, reproduce statistical analysis, replace editorial judgment, or override the Section 7.18 operational security gate.

## Verification

Regression tests cover exact protocol/execution/artifact checks and fail-closed behavior when current, attested human source reviews are absent. Story snapshots explicitly expose an empty acceptance-review collection when no linked benchmark evidence exists.

The latest verified CI run for this work is [#44679603028](https://github.com/Amiroudro/new-sngso/actions/runs/44679603028): 656 tests passed in 6.45 seconds on code head `91bd46a7a4489891a28378020964223fcc6fc11c`.

## Remaining Section 7 work

This integration is a governance/evidence hand-off, not completion of editorial production. Section 7 still needs practical production workflows for reviewed three-level summaries, independent Bangla and English editorial/language review, multi-source perspectives, controlled bilingual terminology, source-grounded vocabulary and exam-learning objects, and daily briefing assembly inputs. Genuine human evaluation remains required; synthetic fixtures are not proof of editorial quality.

Section 8 must begin only after Section 7's editorial outputs and quality gates are sufficiently complete. Section 8 will then own immutable edition assembly, publication approval, correction/supersession, last-known-good protection, and staging-only export.


## Bilingual coverage and quality reporting

The repository now exposes `bilingual_editorial_quality_report(story_id)`. It reports Bangla and English separately, including artifact counts, approval counts, deterministic registry quality-gate results, blockers, warnings, and per-kind coverage. Missing language counterparts are explicit. A language with no artifacts is reported as unassessed, not as a zero-quality language.

The report deliberately has no overall quality score and never averages Bangla and English into one number. It checks snapshot stability while collecting per-artifact quality reports and fails if the story dossier changes during assembly. This remains a deterministic registry report, not a substitute for native-speaker linguistic review or human editorial judgment.


## Three-tier summary-set reporting

The story-level `story_summary_tier_report(story_id)` reports the headline, brief, and detailed summary tiers separately for Bangla and English. It identifies missing tiers, duplicate artifacts, unapproved or ambiguous tiers, and artifacts blocked by existing deterministic registry quality checks. A complete set requires exactly one approved, quality-gate-passing artifact for each tier in that language. It does not infer prose quality, source completeness, or factual truth, and it never authorizes production. The report rechecks the story snapshot fingerprint after collecting per-artifact reports to avoid returning a mixed-revision view.
