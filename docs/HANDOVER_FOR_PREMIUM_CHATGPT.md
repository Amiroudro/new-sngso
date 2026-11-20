# Sarangsho Plus: Engineering Handover for Premium ChatGPT

**Purpose:** This document is the durable handover for a new ChatGPT session with stronger reasoning/coding capability. Read it before changing code. It records the project boundaries, verified repository state, original master plan, engineering priorities, and the working protocol.

**Last verified:** 20 November 2026  
**Repository:** `Amiroudro/new-sngso`  
**Working branch:** `feat/section-7-human-benchmark-review-contract`  
**Open pull request:** [PR #7](https://github.com/Amiroudro/new-sngso/pull/7)  
**Latest full-suite-verified code head:** `872e422460af970d85a87d2bfb7f55d376b36d91` — [run #44724008090](https://github.com/Amiroudro/new-sngso/actions/runs/44724008090), 687 passed in 8.63 seconds. Recheck the live PR head before further edits.

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

### Current authorized engineering scope

Work on the Python editorial-intelligence engine in **`Amiroudro/new-sngso`**.

The Android frontend is a separate project and must remain separate from this Python engine. Existing website code and already-published editions are also separate and must remain untouched.

Do not:
- Merge PR #7 or any other PR without the user's explicit permission.
- Mark PR #7 ready for review without explicit permission.
- Change Android, website, or published-edition repositories as part of engine work.
- Overwrite existing published editions or change live schedules.
- expose secrets, tokens, credentials, private source material, or personal data.
- add paid infrastructure or services without approval.
- claim that synthetic fixtures demonstrate real-world editorial quality.
- claim production readiness merely because tests pass.
- bypass source permission checks, human review, or publication controls to make a demo appear complete.

If work genuinely requires a change outside this repository, explain the dependency and request permission before changing it.

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

**Current position:** Section 7 remains in progress. Sections 7.35 vocabulary evidence binding, 7.36 question answer-evidence binding, and 7.37 revision-card claim traceability are verified. Sections 8, 9, and 10 have not started. Do not describe Section 7 as complete.

## 4. Verified repository and PR state

- PR #7 title: **Section 7: human-reviewed editorial benchmark protocol**.
- PR #7 is a draft and is not merged.
- Base branch: `feat/section-7-editorial-benchmarks`.
- Head branch: `feat/section-7-human-benchmark-review-contract`.
- Latest full-suite-verified code head: `872e422460af970d85a87d2bfb7f55d376b36d91`; GitHub Actions run [#44724008090](https://github.com/Amiroudro/new-sngso/actions/runs/44724008090) passed with **687 tests in 8.63 seconds**. Recheck PR #7 for any later docs-only head.
- Final-head GitHub Actions run: [#44708874613](https://github.com/Amiroudro/new-sngso/actions/runs/44708874613).
- Final-head Python 3.12 test result: **677 passed in 6.94 seconds**.
- PR has 65 changed files at the last check.
- No Android, website, or published-edition paths are included in PR #7.

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

### Next: continue Section 7 from the live verified state

Audit existing schemas, validators, registry methods, quality reports, tests, and documentation before coding. Extend existing abstractions rather than creating duplicate frameworks.

Priorities:
1. **Vocabulary:** tie each entry to the exact source sentence or evidence locator, preserve context and part of speech, distinguish contextual meaning from general dictionary meaning, and record Bangla/English equivalents without inventing them.
2. **Comprehension questions:** require answer claim IDs and evidence locators; ensure the answer is actually supported by the cited source material; preserve uncertainty and attribution.
3. **MCQs:** validate correct-answer support, option uniqueness, rationale-to-claim links, and exact story/artifact binding. Human review must assess whether the correct answer is defensible and distractors are unambiguously wrong.
4. **Revision cards:** preserve source, date/context, claim links, uncertainty, attribution, and language-specific review status.
5. **Bilingual consistency:** review Bangla and English separately for meaning, omissions, terminology, attribution, and factual fidelity. Do not assume literal translation is correct.
6. **Quality reports:** fail closed for missing evidence, stale references, unsupported claims, rejected reviews, or permission drift. Do not invent numeric quality scores.
7. **Tests:** include adversarial and negative cases, migrations, stale revisions, evidence mismatch, missing/extra IDs, and language-specific failure cases.
8. **Documentation:** update the Section 7 design, roadmap, completion record, and relevant contracts.

After that, inspect remaining Section 7 gaps against the original plan: complete briefing tiers, source lineage, genuine human review, operational/security acceptance, and the real inputs needed for a daily briefing.

### Section 7 exit criteria

Do not mark Section 7 complete until:
- All intended editorial output types have versioned contracts and traceable evidence.
- Bangla and English review workflows are separate, complete, and fail closed.
- Source permissions and provenance are rechecked at use time.
- Independent reporting is supported by actual lineage evidence, not publisher counts.
- Genuine, qualified human review data exists and is distinguishable from synthetic fixtures.
- Production identity, admin bootstrap, security controls, backup/restore, and operational sign-off are genuinely deployed and verified, or are explicitly recorded as unresolved blockers.
- Realistic end-to-end inputs can pass the editorial workflow without inventing sources or claims.
- Completion record accurately distinguishes implemented code, tested behavior, external dependencies, and evidence still missing.

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
> Start with a fresh audit of the live repository, current branches, PRs, changed files, tests, and latest GitHub Actions results. Verify the current commit before editing. Do not assume the handover's dated SHA or test count is still current.
>
> Continue from the actual state of the code. Sections 7.35–7.37 are verified. After verifying the current code head, continue auditing MCQ validity, bilingual consistency, genuine human evidence, and Section 7 exit gates before selecting the next slice. Improve existing contracts and workflows instead of duplicating them. Add robust positive and negative tests, run the full suite, verify CI on the exact code head, and update the roadmap and completion record.
>
> Follow the original ten-section sequence exactly. Do not begin Section 8 until Section 7 exit criteria are actually met. Do not claim that synthetic tests prove editorial quality, that different publishers prove independent reporting, or that passing tests establish production readiness. Preserve uncertainty, attribution, source permissions, and human review.
>
> **Non-negotiable boundaries:** work only in the authorized Python engine repository unless I explicitly authorize another repository. Keep the Android frontend separate. Do not modify the existing website or published editions. Keep PR #7 draft and unmerged. Never merge, publish, overwrite editions, alter schedules, expose secrets, or create paid services/usage without my explicit permission.
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
