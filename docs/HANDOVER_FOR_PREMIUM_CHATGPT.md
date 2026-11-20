# Sarangsho Plus: Engineering Handover for Premium ChatGPT

**Purpose:** This document is the durable handover for a new ChatGPT session with stronger reasoning/coding capability. Read it before changing code. It records the project boundaries, verified repository state, original master plan, engineering priorities, and the working protocol.

**Last verified:** 20 November 2026  
**Repository:** `Amiroudro/new-sngso`  
**Working branch:** `feat/section-7-human-benchmark-review-contract`  
**Open pull request:** [PR #7](https://github.com/Amiroudro/new-sngso/pull/7)  
**Latest full-suite-verified code head:** `f7889295a9ca329852b518f96af68fb0b2b4b315` — [run #44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682), 703 passed in 6.68 seconds. Recheck the live PR head before further edits.

---

## 1. Product mission

Sarangsho Plus is intended to turn lawfully accessible, attributable reporting into accurate, explainable Bangla and English news briefings and useful learning material for readers and exam candidates.

The product must preserve:
- Evidence and source attribution for material factual claims.
- Clear separation between facts, allegations, attributed positions, analysis, and uncertainty.
- Operation-specific source permissions. Discovery permission is not permission to store full text, translate, or republish.
- Bilingual quality as two separately reviewed outputs, not one combined score.
- Human editorial control over sensitive, disputed, translated, or publication-critical decisions.
- Reproducible runs, immutable evidence/history, audit trails, and safe publication gates.
- Free-first operation where practical. Do not introduce paid APIs, hosted services, or recurring costs without explicit approval.

The goal is a reliable product, not merely a large codebase or a green test suite.

## 2. Repository boundaries and safety rules

### Current authorized project scope

On 20 November 2026, the user explicitly authorized engineering changes across Sarangsho Plus repositories, including the engine, app UI, intelligence workflows, documentation, and tests. Use this repository map to avoid duplicating or confusing components:

| Repository | Current role | Working assumption |
|---|---|---|
| `Amiroudro/Sngs-plus` | Integrated Flutter app, Engine v2, web frontend, backend, and working edition snapshots | Current integrated product repository. Its README says the external daily job has not yet been switched to Engine v2. |
| `Amiroudro/new-sngso` | Evidence-first Python editorial engine and contracts | The current work repository. PR #7 is open and draft; this engine is not yet shown as integrated into the Flutter app. |
| `Amiroudro/Sngso-app-all` | Kotlin/Compose Android screen prototype with a documented 47-destination inventory | UX and implementation reference; do not assume it replaces the Flutter app. |
| `Amiroudro/Engine` | Earlier/standalone Engine v2 scripts, outputs, and operating notes | Compare with the copy in `Sngs-plus/engine-v2` before changing or copying either version. |
| `Amiroudro/sarangsho-plus-editions` | Public edition feed consumed by the app | Treat existing editions as published records; use the repository's validation and release process for additions or corrections. |

The user has authorized changes in these repositories. Coordinate cross-repository contracts and keep each change reviewable. Do not infer that the two Python engines are interchangeable or that the Compose prototype is the shipped client; establish the integration path from current code and tests before joining or replacing components.

### Safety and release controls

- Keep PR #7 open and draft while its dependency and cross-repository integration status are unresolved. The user's broad code-change authorization does not itself publish an edition or switch a live schedule.
- Preserve existing published edition history. Make corrections or new releases through the established validation and review path; do not alter live schedules as an implementation side effect.
- Do not expose secrets, tokens, credentials, private source material, or personal data.
- Do not add paid infrastructure or services without approval.
- Do not claim that synthetic fixtures demonstrate real-world editorial quality.
- Do not claim production readiness merely because tests pass.
- Do not bypass source permission checks, human review, or publication controls to make a demo appear complete.
- When GitHub Actions is unavailable, report the exact blocker and use available local/static verification; never report unrun checks as passing.

## 3. Original ten-section master plan

Keep this exact order. Do not rename, merge, skip, or prematurely mark sections complete.

1. **Repository audit and engineering baseline**  
   Establish what exists, what is missing, and the verification status.
2. **Source governance and permission contracts**  
   Define publishers, endpoints, permission evidence, scope, expiry, and restrictions.
3. **Domain models, persistence and audit trail**  
   Build versioned domain contracts, durable storage, integrity checks, and audit history.
4. **Permission-first collection framework**  
   Ensure every collection/storage/translation operation is authorized before execution.
5. **Ingestion, normalization and identity**  
   Normalize metadata and content, deduplicate carefully, and preserve provenance.
6. **Story intelligence, claims and evidence**  
   Group reporting into stories while preserving claim-level evidence, attribution, disagreement, and uncertainty.
7. **Editorial production and bilingual quality**  
   Produce reviewed Bangla and English briefings and learning objects with traceable evidence.
8. **Edition assembly and publication safety**  
   Assemble immutable editions/manifests, apply approval gates, manage corrections/supersession, preserve last-known-good output, and export to staging safely.
9. **Operations, observability and free-first automation**  
   Add monitoring, logs, failure recovery, schedules, cost controls, and safe automation.
10. **End-to-end verification and controlled handover**  
    Verify the complete system, document limitations and operations, and hand over without unauthorized publication.

**Current position:** Section 7 remains in progress. Sections 7.35 vocabulary evidence binding, 7.36 question answer-evidence binding, 7.37 revision-card claim traceability, 7.38 bilingual variant claim-coverage reporting, and 7.39 language-rubric quality gating are verified. Sections 8, 9, and 10 have not started. Do not describe Section 7 as complete.

## 4. Verified repository and PR state

- PR #7 title: **Section 7: human-reviewed editorial benchmark protocol**.
- PR #7 is a draft and is not merged.
- Base branch: `feat/section-7-editorial-benchmarks`.
- Head branch: `feat/section-7-human-benchmark-review-contract`.
- Latest full-suite-verified code head: `f7889295a9ca329852b518f96af68fb0b2b4b315`; GitHub Actions run [#44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682) passed with **703 tests in 6.68 seconds**. Recheck PR #7 for any later commits.
- PR #7 is based on the benchmark branch from PR #6, which remains open and draft. Keep PR #7 draft until the dependency is integrated or the target branch is deliberately changed.
- This checkpoint reflects PR metadata verified on 20 November 2026; the PR currently reports 72 changed files.

- No Android, website, or published-edition paths are included in PR #7.

### Cross-repository audit checkpoint (20 November 2026)

- `Amiroudro/Sngs-plus` main identifies itself as the integrated Flutter app + Engine v2 + web/backend project. Its main head is `4d22d99a19b17a1724d36922096ab97fe6029dec`; PRs #18, #24, and #26 are open drafts. PR #18 is not currently mergeable. The latest checks for UI PRs #24 and #26 failed; PR #26 reports the account's recent GitHub Actions payment failures. Flutter, Dart, and Gradle are not installed in this execution environment, so app builds cannot currently be verified locally.
- `Amiroudro/Sngso-app-all` is a separate Kotlin/Compose prototype with a documented screen inventory; its build-verification PR #2 remains open. Do not treat it as the shipped client without an explicit product integration decision.
- `Amiroudro/Engine` contains a standalone/earlier Engine v2 copy. Compare it with `Sngs-plus/engine-v2` before transferring changes.
- `Amiroudro/sarangsho-plus-editions` is the public app feed. Its existing edition files are published history; corrections need a new validated release record.
- The evidence-first engine in this repository is not yet connected to the Flutter app. PR #7 is based on the branch of open draft PR #6.


Always re-check the live PR head, changed-file list, and latest workflow run before reporting status. The figures above are a dated checkpoint, not a promise that the branch has not changed since.

### Documentation caution

The PR description has accumulated historical implementation notes. Some older notes refer to a misnumbered “Section 8.1”; the master-plan alignment correction says that benchmark-to-story integration belongs to Section 7. Treat the ten-section master plan and current section-specific documents as authoritative. Reconcile stale README or PR wording when safe, without rewriting history or changing scope.

## 5. Current Section 7 implementation checkpoint

The branch includes a substantial set of contracts, registry workflows, SQLite migrations, authenticated review boundaries, quality reports, tests, and documentation. The most important capabilities include:

- Versioned human benchmark review and benchmark-content contracts.
- Immutable benchmark artifacts and review revision history.
- Operation-scoped source-permission rechecks.
- Registry-managed curator, reviewer, adjudicator, security-admin, methodologist, and acceptance-reviewer roles.
- Authenticated application facade and an OIDC verification adapter with explicit deployment dependencies.
- Durable reviewer assignments, identity attestations, criterion-level scoring, descriptive evaluator-agreement reports, calibration-study protocols and execution records, and independent acceptance reviews.
- Story snapshots that expose benchmark acceptance evidence, bilingual quality reports, perspective attribution, source-independence evidence gaps, and narrative assembly.
- Language-specific Bangla and English review rubrics and authenticated learning-object reviews.
- Fail-closed handling of missing, rejected, or needs-revision language reviews.

### Latest completed increments

**7.30: Reviewed bilingual narrative assembly**
- Sentence-level narrative segments preserve language, role, exact text, claim links, claim assessments, provenance, and evidence locators.
- A factual segment is supported only when all linked claims are assessed supported and have supporting evidence.
- Bangla and English are evaluated separately.
- Contradictions remain visible; accepted review does not silently erase a contradiction.
- Assembly is read-only and does not grant publication authorization.

**7.31: Source-independence evidence gap report**
- Distinguishes duplicate-article review from genuinely independent reporting.
- Distinct article records do not prove independent reporting.
- Independent reporting count remains unset until lineage, syndication, shared-source, and primary-source evidence is reviewed.

**7.32: Language-review enforcement**
- A rejected language review or any needs-revision rubric finding blocks the language's narrative assembly report.
- A review record's mere existence is not enough.

**7.33: MCQ distractor rationale coverage**
- Every incorrect MCQ option requires exactly one non-empty rationale.
- The correct answer must not have a distractor rationale.
- Duplicate, missing, extra, or misassigned rationales are rejected.
- Human review is still required to judge plausibility, ambiguity, misconception alignment, parallel construction, and accidental clues.

**7.34: MCQ rationale source-claim support**
- Every distractor rationale must identify one or more `basis_claim_ids` belonging to the learning object's story-linked claim set.
- The quality report checks that those claims exist, are assessed as supported or partially supported, and have linked evidence.
- Unsupported or unassessed rationale claims block the quality gate.
- This checks traceability, not logical entailment or pedagogical quality.

**7.35: Vocabulary source-evidence binding (verified)**
- Vocabulary objects require a registered evidence ID and exact evidence revision whose locator points to the source context.
- The evidence must belong to an object claim, remain linked from that claim, and match current source/article/observation provenance.
- Create, revision, quality-report, and SQLite boundaries recheck the binding. Tests cover missing/unknown/stale references, direct-SQL guards, and migration 23→24.
- Source text is not copied into the learning object. Human review remains necessary to judge contextual meaning and glossary correctness.
- GitHub Actions run [#44720406104](https://github.com/Amiroudro/new-sngso/actions/runs/44720406104) passed **681 tests in 8.15 seconds** on code head `c2401950927a9ba26b5f7d05678a213543405940`.

### What these checks do not prove

- They do not prove a source's factual accuracy.
- They do not authenticate real humans unless a real production identity provider is deployed and configured.
- They do not establish independent reporting merely from different publishers or article IDs.
- They do not establish translation quality or editorial truth automatically.
- They do not represent a real human-reviewed Bangla/English benchmark dataset.
- They do not authorize production or publication.

The Section 7.18 operational security review recorded **NOT ACCEPTED FOR PRODUCTION** pending real identity infrastructure, protected administrator bootstrap, host/database controls, backup and restore drills, genuine independent human reviews, source permission checks, and formal operational sign-off. Re-check the current document before changing this status.

## 6. Immediate engineering priorities

Continue Section 7. Do not jump to Section 8 until Section 7 exit criteria are demonstrably met.

### Remaining Section 7 work

The source-evidence bindings, revision-card segment checks, bilingual claim-coverage diagnostic, language-rubric quality gate, authenticated MCQ rubric capture, and staging-only Flutter compatibility verifier are implemented and covered by synthetic regression tests. Real reviewers, source evidence, and Flutter integration remain unproven. Continue in Section 7 while these exit blockers remain:

1. The authenticated MCQ review gate and direct regression coverage are implemented (Section 7.40). Qualified human reviewers still need to use it; calibration and independent acceptance evidence remain outstanding.
2. Obtain genuine, qualified Bangla and English human review data, prespecified calibration evidence, and independent acceptance evidence. Keep synthetic tests clearly separate.
3. Collect actual reporting-lineage evidence and current permission evidence for each intended source operation. Do not infer independence or rights from publisher counts.
4. Finish production identity-provider, protected administrator bootstrap, database/host controls, backup/restore drills, and the Section 7.18 operational sign-off.
5. A staging-only English story serializer and verifier now bind an approved edition to exact English headline/summary revisions, current citation records, and republication permission. Validate the generated payload against the actual Flutter parser with cross-repository golden tests. Keep bilingual, vocabulary, phrase, MCQ, and learning-module export blocked until separately mapped and tested.
6. Verify realistic daily briefing inputs through the editorial workflow without invented sources or claims.

The engineering workflow can continue while external evidence is gathered. Keep Section 7 open until every exit criterion is evidenced or explicitly recorded as unresolved; do not start Section 8 ahead of it.


## 7. Future sections: required direction

### Section 8: Edition assembly and publication safety
Plan for immutable edition IDs and manifests; exact story/object revisions; source and permission snapshots; deterministic build fingerprints; separate draft, reviewed, approved, staged, published, superseded, and corrected states; approval authority; correction notices; rollback; last-known-good preservation; staging-only export; and prevention of accidental overwrite. No live publication without explicit user authorization.

### Section 9: Operations, observability and free-first automation
Plan for structured logs, run IDs, stage-level metrics, error classification, retry policies, idempotency, failure alerts, stale-data detection, permission-expiry monitoring, quota and cost limits, schedule controls, secrets handling, and recovery procedures. Prefer GitHub Actions/free-tier-compatible operation. Never create paid usage or billing exposure without approval.

### Section 10: End-to-end verification and controlled handover
Run reproducible tests from lawful source input through story intelligence, editorial review, edition staging, and recovery. Verify security boundaries and permissions, document actual deployment configuration, provide an operator guide, report unresolved risks, and obtain explicit approval before any merge or publication.

## 8. Working protocol for the next ChatGPT

1. Start by reading this handover and the repository's current README, architecture, roadmap, baseline audit, source-governance docs, quality gates, Section 7 design, Section 7 completion record, and relevant contracts.
2. Verify the current branch, PR #7 state, exact head SHA, changed paths, and latest CI run. Do not rely only on this checkpoint.
3. Inspect relevant implementation and tests before deciding what to change. Search the repository for existing models and helpers first.
4. Continue implementation directly when safe. Verify the live PR and CI state, then audit the remaining Section 7 learning-object and review gaps before choosing the next slice. Do not jump to Section 8.
5. Work in small, coherent commits on the authorized feature branch. Do not make concurrent writes to the same file.
6. Add tests for both successful behavior and failure conditions. Run the full suite and contract validation.
7. Verify the workflow result against the exact code head. If only docs change after a code-tested commit, state that distinction.
8. Update the roadmap and completion record with precise claims and limitations.
9. Recheck changed paths to ensure no frontend, website, published-edition, secrets, or unrelated files changed.
10. Keep PR #7 draft and unmerged. Never merge, publish, change live schedules, or incur paid costs without explicit user permission.
11. If access, permissions, credentials, real human reviewers, publisher authorization, or a decision only the user can make is missing, explain the exact blocker and ask only for that required action.
12. Explain progress to the user in plain language. The user is learning software engineering and should not be expected to understand Git internals, Python, CI, or billing jargon.

## 9. Definition of a good result

A good result is not “many commits” or “all tests green.” It is a working, understandable, maintainable, secure, permission-aware product whose outputs can be traced to real evidence and reviewed by accountable humans.

For every milestone, report:
- What changed and why.
- Which files/contracts/tests changed.
- Exact commit and branch.
- Exact test and CI result, including failures.
- What remains unverified or dependent on external setup.
- Whether any user action is required.
- Confirmation that protected repositories and publication outputs were left untouched.

---

# Copy/paste instruction for Premium ChatGPT

Use the following as the opening instruction in the new Premium ChatGPT conversation. Connect the GitHub integration to the repository if available, and give it access only to the repositories needed for the specific task.

> You are taking over the Sarangsho Plus engineering project. First read `docs/HANDOVER_FOR_PREMIUM_CHATGPT.md` in `Amiroudro/new-sngso` completely. Treat it as the project handover and follow its repository boundaries and ten-section master plan.
>
> Your job is to **continue implementing and completing the product**, not merely explain how I could do it. I am a beginner, so handle the engineering work yourself wherever access permits, use plain language when reporting, and tell me exactly what action you need from me only when something genuinely requires my permission, credentials, account, human judgment, or decision.
>
> Start with a fresh audit of the live repositories, current branches, relevant PRs, changed files, tests, and latest GitHub Actions results. Treat `Sngs-plus` as the integrated Flutter product and `new-sngso` as the evidence-first engine under active development. Verify exact commits before editing. Do not assume the handover's dated SHA or test counts are current.
>
> Continue from the actual state of the code. Sections 7.35–7.39 are verified. After checking the current engine head, continue with structured human MCQ validity review, genuine human evidence, source-lineage evidence, operational acceptance, and the interface between the evidence-first engine and the integrated Flutter app. Improve existing contracts and workflows instead of duplicating them. Add robust positive and negative tests, run the full suite, verify CI on the exact code head, and update the roadmap and completion record.
>
> Follow the original ten-section sequence exactly. Do not begin Section 8 until Section 7 exit criteria are actually met. Do not claim that synthetic tests prove editorial quality, that different publishers prove independent reporting, or that passing tests establish production readiness. Preserve uncertainty, attribution, source permissions, and human review.
>
> **Non-negotiable boundaries:** the user has explicitly authorized cross-repository engineering changes in the listed app, engine, and prototype repositories. Keep PR #7 draft while its dependencies and integration remain unresolved. Do not publish editions or change the live schedule as a side effect of code work. Preserve source rights, human review, evidence integrity, and secret protection. Paid infrastructure still requires approval.
>
> Do not stop after planning. Make the next safe, meaningful implementation change, test it, inspect the CI result, document it, and continue in coherent steps. If blocked, identify the exact missing permission or input rather than guessing. At each checkpoint, tell me what is genuinely complete, what is not, what the tests prove, what they do not prove, and what you will implement next.
>
> My goal is a complete, reliable Sarangsho Plus product: accurate bilingual daily briefings, transparent source and evidence trails, useful exam-oriented learning material, a safe edition workflow, a maintainable frontend/backend boundary, and dependable low-cost operations. Optimize for correctness, safety, usability, and maintainability rather than speed or impressive-looking feature counts.

**7.36: Question answer-evidence binding (verified)**
- Comprehension, analysis, and MCQ answer claims must each map to supporting evidence with an exact evidence revision.
- The registry, quality report, and SQLite migration recheck claim ownership, source provenance, evidence relationship, locator, and staleness.
- Test coverage includes missing/unknown/stale references, unlinked evidence, direct-SQL guards, and migration 24→25.
- These checks establish traceability only. Human review still needs to judge answer defensibility, uncertainty, attribution, and question quality.
- GitHub Actions [run #44723149350](https://github.com/Amiroudro/new-sngso/actions/runs/44723149350) passed the full Python 3.12 suite: **685 passed in 6.86 seconds** on code head `49bfed5e9929ca2501bf11e1ebe2399743b7a9d4`. Section 7 remains in progress; no production or publication authorization is added.


**7.37: Revision-card claim traceability (verified)**
- Revision-card segments now use the same exact text-to-segment and segment-to-object claim mapping as headline, brief, and detailed summaries.
- Revision-card factual segments now block the quality report when their claims are unassessed, disputed, contradicted, retracted, or lack linked evidence. Attributed and analysis segments preserve the existing qualification warnings.
- Regression tests cover an out-of-object claim, text omitted from the segments, and an unassessed factual claim.
- GitHub Actions [run #44724008090](https://github.com/Amiroudro/new-sngso/actions/runs/44724008090) passed the full Python 3.12 suite: **687 passed in 8.63 seconds** on code head `872e422460af970d85a87d2bfb7f55d376b36d91`.
- This is evidence-link and assessment enforcement, not proof that revision-card content is accurate or useful. Qualified human review remains required. Section 7 remains in progress.


**7.38: Bilingual variant claim-coverage report (verified)**
- The bilingual editorial quality report now compares claim-ID sets for learning objects explicitly paired by the same kind and `variant_group_id`.
- It reports missing counterparts, matching sets, and Bangla-only/English-only claim IDs for mismatches. Unpaired objects are not guessed to be counterparts.
- This is a structural coverage diagnostic. Matching claim IDs do not establish translation fidelity, equivalent nuance, or linguistic quality. The report continues to return `production_ready=false` and `authorization_granted=false`.
- Tests cover both matched and mismatched Bangla/English variant groups.
- GitHub Actions [run #44725012064](https://github.com/Amiroudro/new-sngso/actions/runs/44725012064) passed the full Python 3.12 suite: **688 passed in 7.75 seconds** on code head `bf1559f352455055070303177a051eed357d38ac`. Section 7 remains in progress.


## Section 7.39: language-rubric quality gate (verified)

- `learning_object_quality_report` now requires the exact Bangla or English review rubric to pass in addition to generic approval status. Generic approval alone cannot make an object pass its quality gate.
- Positive fixtures now carry explicit language-specific rubric outcomes. A regression case proves that an otherwise approved object without the rubric is blocked.
- The gate remains a deterministic completeness check; it does not establish reviewer identity or factual correctness by itself. The authenticated review facade and the Section 7.18 production gate remain separate controls.
- GitHub Actions [run #44731019456](https://github.com/Amiroudro/new-sngso/actions/runs/44731019456) passed the full Python 3.12 suite: **689 passed in 8.59 seconds** on code head `1ecf6a9345c996366363d5f8a08191485f6db66e`.
- Section 7 remains in progress; Sections 8–10 remain future work.


## Section 7.40: authenticated MCQ pedagogical review rubric (CI verified)

- The learning-object contract defines mcq-editorial-v1 with ten review dimensions: single defensible key, stem clarity and precision, distractor plausibility, unambiguous incorrectness, rationale quality, learner-level fit, misconception alignment, option parallelism, absence of accidental clues, and explanation fidelity.
- The trusted review facade requires all ten dimensions for MCQ decisions, restricts values to pass/needs_revision, requires all-pass findings for approval, and records provider-backed identity attestation.
- Registry validation rejects malformed or mismatched MCQ review metadata. The quality report blocks an approved MCQ without a complete all-pass pedagogical rubric. Automated completeness does not judge pedagogical quality.
- Added direct regression cases for authenticated MCQ review capture and provider attestation, missing/failed rubric rejection, non-MCQ misuse, and the approved-MCQ quality gate. GitHub Actions [run #44749250816](https://github.com/Amiroudro/new-sngso/actions/runs/44749250816) passed the full Python 3.12 suite: **693 tests in 6.24 seconds** on code head cc7f4f5e7e3d38a4d6709a9c0b5fdee4c3fe1abe.
- Section 7 remains open. Real human review records, calibration/independent acceptance, source lineage and permission evidence, production operations sign-off, Flutter compatibility, and realistic daily inputs remain outstanding.


## Section 7.41: engine-to-Flutter compatibility boundary (design only)

- Audited the current Flutter Edition/Story/MCQ parser and the Engine v2 converter and integration status in Amiroudro/Sngs-plus.
- Added docs/ENGINE_TO_FLUTTER_COMPATIBILITY.md to define the legacy consumer shape, candidate mapping, provenance sidecar requirements, fail-closed staging gates, and explicit blockers.
- The document records that the current Flutter model does not consume a claim-level permission/evidence manifest and that no shared executable contract or cross-repository golden test exists.
- This is a design checkpoint only. No integrated repo files or bundled editions were modified; no adapter was implemented or tested with Flutter. The compatibility exit gate remains open.
- Section 7 remains in progress; Sections 8–10 remain future work.


## Section 7.42: staging-only Flutter compatibility verifier (CI verified)

- Added a read-only verifier for the current legacy Flutter edition shape. It binds an approved registered edition revision to an exact story set and exact approved English headline/summary object revisions.
- It verifies registered publisher/article citation fields; active human reviewer classification and language qualification; current source republication grants; and that each article URL is covered by the permitted endpoint.
- It rejects published status, unbound display metadata, unsupported story fields and content collections, stale revisions, mismatched source/citation records, and denied or wrong-source permissions. The return value explicitly says publication is not authorized.
- Added synthetic regression coverage for tampering, stale records, wrong source grants, denied permissions, unsupported collections, reviewer qualifications, timestamps, and source endpoint scope.
- GitHub Actions [run #44752476466](https://github.com/Amiroudro/new-sngso/actions/runs/44752476466) passed the full Python 3.12 suite: **701 tests in 6.38 seconds** on code head ec06daf4d3abba0b8034d56c1f84bb470d63229d.
- This does not serialize editions or run Flutter. The integrated client cannot validate the provenance sidecar, there is no cross-repository golden test, and no source currently has confirmed republication permission. Section 7 remains open.


## Section 7.43: explicit English staging builder (CI verified)

- Added build_flutter_staging_candidate, which takes an approved edition ID, explicit per-story headline/summary object selections, and source permission bindings. It builds only the supported English story subset in memory and immediately verifies it.
- The generated client payload uses exact approved object text, registered article/source fields, a derived weekday, empty unbound metadata, and a staging status. The provenance manifest carries edition/story revisions and fingerprints plus selected object revisions and fingerprints.
- Current republication grants are re-evaluated for the exact source and endpoint, and every article URL must fall inside a permitted registered endpoint. The builder writes no file or feed and never grants publication authorization.
- Tests cover a successful build and explicit-selection failure, in addition to verifier failure cases.
- GitHub Actions [run #44753534682](https://github.com/Amiroudro/new-sngso/actions/runs/44753534682) passed the full Python 3.12 suite: **703 tests in 6.68 seconds** on code head f7889295a9ca329852b518f96af68fb0b2b4b315.
- This is not a Flutter runtime or cross-repository golden test. No confirmed republication grant exists; the integrated app does not consume the sidecar. Bilingual and learning-object export remain unsupported. Section 7 stays open.

## Continuation update: Section 7.44 (20 November 2026)

The staging-only Flutter compatibility verifier received an additional defensive validation pass:
- Rejects boolean revision values for editions, stories, and learning objects.
- Rejects boolean, negative, float, and string collection counts; rejects unknown count keys.
- Removes duplicate validation branches.
- Adds focused regression tests for these cases.

Implementation and tests are committed on the existing draft PR branch. GitHub Actions run [#44767401224](https://github.com/Amiroudro/new-sngso/actions/runs/44767401224) passed the full Python 3.12 suite: **709 passed in 6.09 seconds** on code-bearing head `d3d055099d10630b16db12ca2f0f56b2f2f16e0d`. The latest PR head is `db56d3c33a0541a406f54bf053c01220d86da234`; the commits after the tested code head update documentation only. Keep PR #7 draft and unmerged. No app, website, or published-edition repository was changed.
