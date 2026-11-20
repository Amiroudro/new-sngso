# Section 7: human-reviewed benchmark protocol

## Purpose

Synthetic regression fixtures test exact, intentionally encoded expectations. They are not evidence of natural Bangla, translation quality, or factual accuracy. This protocol defines the minimum metadata and storage controls required before a benchmark may be considered for human approval.

## Review record

Each review record binds to the exact benchmark content fingerprint and records:

- benchmark ID and language;
- reviewer ID, role, language, and a written qualification basis;
- timezone-aware review timestamp;
- source article and publisher IDs, canonical HTTPS URI, checked content fingerprint, checked timestamp, and permission-evidence reference;
- every correction with original text, corrected text, affected field, and rationale; and
- an adjudication note explaining the decision.

Bangla and English benchmarks require independent review by a reviewer working in the benchmark language. Review of one language does not approve another language's counterpart.

## Registry persistence (schema migration 13)

The SQLite registry now stores review heads and a separate revision history. Every insert and update to a review head creates its matching history row through SQLite triggers. The database rejects deletion of review heads, updates or deletion of history rows, non-sequential revisions, missing actors/reasons, and timestamps moving backwards. The registry API also uses optimistic concurrency to reject stale edits.

Before a review is stored, its declared article, publisher, permission-evidence, endpoint, and permission-grant references must exist. The article, evidence, and grant must belong to the same publisher; the canonical URI must match the registered article; and the evidence must be marked verified. The source content fingerprint is checked against the registered article when that article has a fingerprint. The operation-specific grant requirements are detailed in Section 7.4.

## Limits that remain

Registry-managed roles authorize declared principal IDs, but do not authenticate the human operating an account or independently establish editorial correctness. Synthetic fixtures are not a substitute for a real, bilingual, human-reviewed benchmark set. Do not present metadata validation as proof of factual correctness.

The review record retains the fingerprint declaration, but migration 14 now binds it to stored canonical artifact content and independently recomputes it at review and eligibility checks.

## 7.5 Quality reporting and story snapshot integration

The registry exposes a read-only benchmark review quality report. It separates the recorded review decision and metadata validation from a fresh check that each source still has verified evidence and a currently usable grant for its declared endpoint and operation. A suspended, revoked, expired, or otherwise unusable grant blocks current-use eligibility even when the historical review remains approved.

The story editorial snapshot includes benchmark review records whose declared source articles belong to that story. Each included record carries its current revision, append-only review history, and a current-use quality report. This is provenance visibility, not an editorial quality score. The report warns that registry roles do not authenticate the human operating an account and that it does not determine factual correctness.

## 7.6 Immutable benchmark artifact registry

Schema migration 14 adds an immutable benchmark-artifact table. Curators register the actual structured JSON content with a stable artifact ID and language. The registry canonicalizes that content using `canonical-json-v1`, computes its SHA-256 fingerprint itself, and records the curator, timestamp, and reason. Direct SQL updates and deletes are rejected by database triggers.

A human review must now reference a registered artifact by its artifact ID. On review creation and revision, the registry re-reads the stored artifact, recomputes its fingerprint, and checks both the declared review fingerprint and language. The read-only quality report repeats this artifact check, and current-use eligibility fails closed if the artifact is missing, mismatched, or cannot be verified.

This stage also defines a versioned benchmark-content contract. Each artifact contains one or more uniquely identified items with a task type, input, expected output, and explicit scoring criteria. The contract supports headline, summary, translation, claim extraction, fact-checking, vocabulary, and editorial-judgment tasks. Human identity authentication remains outside this stage. Canonicalization is explicitly versioned so a future change cannot silently reinterpret old fingerprints.

## 7.8 Registry-managed benchmark roles

Schema migration 15 adds registry-managed benchmark principals with the explicit roles `benchmark_curator`, `benchmark_reviewer`, and `benchmark_adjudicator`. Artifact registration requires an active curator principal. Review creation and revision require an active reviewer principal, and the acting principal ID must match the reviewer ID declared in the review. Unknown principals, inactive principals, and principals without the required role are rejected. Revocation is soft and audited; historical review and artifact records remain intact.

Provisioning is an administrative operation and must be performed only by a trusted deployment operator. The local SQLite registry does not authenticate that operator or the human behind a principal ID. A production deployment must bind the principal ID to a trusted login/session and restrict provisioning to an authenticated administrator. Role checks are a registry authorization control, not proof of real-world identity.

Adjudicator role registration is enforced when an adjudication is written. The registry requires the adjudicator principal to be active, to hold `benchmark_adjudicator`, and to match the declared adjudicator ID. The two referenced reviews must exist, bind to the same exact artifact fingerprint, and have reviewer IDs matching the stored review records. The adjudicator cannot be one of those reviewers. Adjudication records are immutable and audited. The record documents the resolution; it does not automatically rewrite review decisions or grant current-use approval. The read-only current-use quality report now applies an explicit fail-closed outcome policy: `uphold_existing` can preserve eligibility only for a referenced approved review; `return_for_revision` blocks use until a revised artifact is registered and reviewed; `reject_benchmark_item` blocks use; and `split_accepted_answers` blocks use until distinct accepted-answer items are represented in a revised artifact and freshly reviewed. The policy does not rewrite reviewers' historical decisions or grant approval to new artifact versions.

### Final adjudication and immutable artifact policy

Schema migration 16 makes the final adjudication unique per immutable benchmark artifact. A second adjudication for the same artifact is rejected at the SQLite boundary; a changed decision requires a newly registered artifact version with new reviews, preserving the old artifact's decision history. The current-use report also fails closed if legacy data contains multiple adjudications for one artifact.

A final adjudication also freezes revisions to reviews bound to that artifact. If the outcome requires correction, the workflow moves to a newly registered artifact with new review IDs rather than mutating the decision's inputs.

For `uphold_existing`, the adjudicator must name `upheld_review_ids`. The list must be a non-empty subset of the two or more referenced reviews, and every selected review must be approved in the registry. Only those exact selected reviews can remain eligible. Other outcomes must not include an upheld-review selection. This prevents an ambiguous “uphold” from preserving every approved review by default.

## Next gates

- Connect principal IDs to a trusted authentication/session provider and restrict provisioning to authenticated administrators.
- Populate separate Bangla and English benchmark sets with qualified human reviewers, adjudication, and correction provenance.

## 7.4 Operation-scoped permission enforcement

A verified permission-evidence record is not, by itself, an authorization to use an article in a benchmark. Each source-material entry now identifies its exact permission grant, endpoint, and permitted operation. Approved benchmark reviews accept only the explicitly declared benchmark-material operations: `excerpt_storage`, `full_text_storage`, or `translation`.

At registry write time, the grant must:
- exist and belong to the same publisher as the article and evidence;
- be approved and currently effective;
- explicitly include the declared endpoint;
- explicitly allow the declared operation;
- cite the same verified evidence at both grant and operation level;
- pass the fail-closed permission evaluator, including its handling of unimplemented conditions.

This check is performed when a review is created or revised. It does not authenticate the reviewer, prove that the grantor had legal authority, or make a legal determination about copyright. Permission can also be suspended or revoked after a review is recorded; downstream publication and use workflows must re-evaluate the grant at the point of use rather than treating an old review as permanent authorization.


## 7.7 Disagreement adjudication contract

A separate Draft 2020-12 adjudication record now captures the benchmark artifact fingerprint, at least two distinct review IDs, an adjudicator declaration, a controlled outcome, a reasoned decision, evidence references, and a timezone-aware resolution timestamp. Outcomes distinguish upholding an existing decision, returning work for revision, rejecting a benchmark item, and splitting multiple acceptable answers.

The validator rejects an adjudicator whose declared principal ID is also listed among the reviewers. This is a workflow-level independence check, not proof of identity or actual independence. The validator's declared-independence check is supplemented by registry role enforcement and checks against the stored reviewer records. Durable adjudication history is immutable and audited. Current-use eligibility applies the explicit outcome policy described in Section 7.8. The adjudication workflow still does not authenticate the human behind a principal ID.


## 7.9 Trusted-session application boundary

`src/sngso_engine/benchmark_access.py` adds an application-facing facade for artifact registration, review submission, and adjudication. It obtains the actor exclusively from a `BenchmarkIdentityProvider`, then checks issuer, audience, timezone-aware session validity, active registry principal, and the required registry role. Reviewer and adjudicator declarations must match the authenticated principal. The facade does not accept a client-supplied actor ID.

The identity provider is an infrastructure trust boundary, not a mock authentication mechanism. A production adapter must validate the actual OIDC token signature or server-side session, exact issuer and audience, expiry, session revocation, and authentication freshness before returning `AuthenticatedIdentity`. The local facade cannot prove that an arbitrary Python object came from a trustworthy provider. Application handlers must expose this facade rather than raw registry mutation methods, and provider construction/configuration must remain inside trusted server code. Principal provisioning remains a separate administrator-only deployment operation; this facade intentionally does not expose provisioning or revocation.

Tests use a fake provider only to verify the facade's enforcement behavior. They do not authenticate real users or constitute production identity integration.


## 7.10 Reviewer assignment lifecycle contract

The first assignment-workflow slice adds a versioned assignment record and a pure state machine. An assignment binds one reviewer to one exact immutable artifact ID, SHA-256 fingerprint, and language (bn or en), with an assigner, creation time, deadline, and reason. Self-assignment is rejected.

The lifecycle is assigned → accepted → submitted, with cancellation or expiry available only while the assignment remains open. Only the named reviewer may accept or submit; submission must reference a review ID and cannot happen before acceptance or at/after the deadline. The assigner or reviewer may cancel an open assignment. Only the assigner may record expiry, and only at/after the deadline. Every transition returns an actor, timestamp, and reason receipt; callers must persist each transition as append-only history rather than overwrite prior states.

The initial 7.10 commit introduced only the pure state machine. Section 7.11 now supplies durable SQLite persistence, language-qualified reviewer checks, authenticated application operations, and exact stored-review binding. Submission remains a workflow handoff only; it never approves the review. The existing review validator, artifact binding, source-permission checks, and adjudication gates remain authoritative.


## 7.11 Durable assignment ledger and language-qualified workflow

Migration 17 adds a SQLite assignment ledger and immutable assignment revision history. The current assignment row is a queryable projection; each creation and transition also appends a revision and a hash-chained audit event. Assignment identity, reviewer, assigner, artifact ID, fingerprint, language, and deadline are immutable. Revisions must advance sequentially, and current-state JSON is checked against its indexed columns. Assignment rows and revision history cannot be deleted or rewritten.

Reviewer principals can now be provisioned with explicit language qualifications (bn, en). A curator can assign only an active principal with the reviewer role and the matching language qualification. A partial unique index prevents more than one active assignment for the same reviewer and artifact. Different qualified reviewers can still independently review the same artifact.

The authenticated application facade exposes assignment and transition operations. The registry independently rechecks roles and language qualification at the time of each action, so a revoked principal or removed qualification cannot complete a later transition. Submission requires an already stored review whose reviewer ID, language, artifact ID, and fingerprint all match the assignment. It is a workflow handoff, not an approval. The existing source-permission and human-review quality gates, followed by independent adjudication where required, continue to determine eligibility.

The local registry still does not authenticate real people by itself. Production must supply the trusted identity provider described in Section 7.9, secure administrator provisioning, and a real reviewer onboarding process. A qualification is an administratively recorded credential claim, not independent proof of professional competence. The tests use fixtures and do not establish that genuine Bangla or English editorial reviews have been completed.

The registry also exposes filtered assignment queues by reviewer, status, and language. The authenticated reviewer queue always scopes results to the principal derived from the trusted session and rejects language filters outside that principal's registered qualifications.


## 7.12 Benchmark readiness report and reviewer classification

Migration 18 adds an explicit principal classification: `human`, `synthetic`, `automation`, or `unverified`. Existing principals migrate to `unverified`; the system never retroactively interprets historical test fixtures as people. New principals also default to `unverified`. The classification is set at provisioning, included in the audit event, and immutable at the SQLite boundary. The classification is an administrative declaration only and is not an authentication claim.

`benchmark_readiness_report(artifact_id)` produces a read-only artifact-level report. It verifies the stored artifact fingerprint, exact artifact/language binding, current review-use policy (including source permissions and adjudication outcome), active reviewer role, reviewer language qualification, distinct reviewer count, principal classifications, and adjudication presence. It reports individual review checks and explicit blockers.

Statuses are deliberately limited:
- `human_review_pending`: the required qualified and currently eligible human-classified review set is incomplete, or identity evidence is insufficient.
- `synthetic_only`: the eligible review set consists only of synthetic or automated principals.
- `ready_for_adjudication`: at least two distinct active, language-qualified, human-classified reviewers have currently eligible approved reviews and no final adjudication exists.

The report never emits `ready_for_use` and always returns `production_ready: false`. A `human` principal classification does not prove that the principal is a real person. This release does not persist a production identity-provider attestation with each review, and it does not compare review content to detect substantive disagreement. Consequently, even a report marked `ready_for_adjudication` remains blocked from production readiness. Existing review, permission, and adjudication policy gates remain authoritative; the report itself grants no approval.

Tests explicitly verify that migrated principals default to `unverified`, that test principals cannot satisfy human identity evidence, that two human-classified reviewers still require adjudication and identity-provider attestation, and that synthetic-only fixtures are never represented as human-reviewed evidence.


## 7.13 Provider-backed reviewer identity attestations

Migration 19 adds an immutable identity-attestation ledger keyed to an exact review ID and revision. A trusted identity-provider adapter must supply the issuer, audience, provider subject, authentication time, expiry, authentication methods and context, verification key identifier, and adapter identity. The opaque session ID is never persisted; only its SHA-256 fingerprint is stored. Attestation metadata is audited and cannot be updated or deleted.

The application facade now requires a reviewer principal classified as `human`, a matching authenticated principal, provider subject and verification metadata, and authentication within 15 minutes of review submission. The registry independently enforces the same 15-minute interval when persisting an attestation, preventing callers of the storage method from bypassing the freshness check. It stores the review first and then records the attestation. If attestation persistence fails, the review remains stored but cannot satisfy the readiness report. This deliberately fails closed rather than discarding the review or treating it as identity-verified.

Readiness now requires at least two distinct eligible human-classified reviewers with immutable attestations bound to their exact current review revisions before reporting `ready_for_adjudication`. A review revision does not inherit an earlier revision's attestation. Synthetic and unverified principals cannot receive human identity attestations through the registry API.

**Deployment boundary:** this repository defines the provider contract and persistence boundary, not a production OIDC implementation. The configured adapter must verify token signatures against trusted issuer keys, issuer and audience, token/session expiry and revocation, authentication freshness, authentication methods/context, and the stable provider-subject-to-registry-principal mapping. It must rotate and pin trusted keys safely and reject algorithm/key confusion. The local test provider is a fixture, not proof of real authentication. Until a production adapter and administrator provisioning path are deployed and independently reviewed, identity attestations in tests are not evidence of real people and production readiness remains false.


## 7.17 Reviewer disagreement and adjudication quality

Adjudication records now require an explicit issue-resolutions ledger. Each entry identifies a unique disagreement, its editorial category and severity, a concise description, the position recorded for every referenced review, a controlled disposition, the adjudicator's evidence-based resolution, and supporting evidence references.

The validator checks that each issue names exactly the review set bound to the adjudication. It rejects missing reviewer positions, duplicate issue IDs, missing resolution text, and empty issue-level evidence. Categories distinguish factual accuracy, source support, claim scope, translation meaning, language quality, accepted answers, scoring criteria, and other issues. Severity is explicitly classified as critical, major, or minor. Dispositions record whether one reviewer was upheld, both were partly upheld, both were rejected, or the difference was not material.

This is a completeness and auditability gate, not an automated truth detector. The adjudicator must still identify all material disagreements; the schema cannot prove that the list is exhaustive, that reviewer positions are quoted faithfully, or that cited evidence supports the resolution. A second reviewer or governance process should audit high-severity adjudications before operational use. Existing immutable artifact, reviewer independence, role, source-permission, and current-use gates remain in force.


## 7.18 Operational security review and acceptance gate

The operational review concludes **NOT ACCEPTED FOR PRODUCTION**. The repository controls are tested, but real OIDC issuer/JWKS/revocation infrastructure, protected administrator bootstrap, production database and host access controls, backup/restore drills, publisher permission evidence, and genuine independent Bangla/English human reviews remain deployment evidence requirements. The full threat/control matrix and mandatory go/no-go checklist are in `docs/SECTION_7_18_OPERATIONAL_SECURITY_REVIEW.md`. A passing test suite is not a deployment security certification or editorial quality proof.


## 7.19 Criterion-level scoring and calibration

A versioned scoring contract and deterministic validator now require item-bound score sheets, criterion IDs, explicit weights, anchored 0–4 scores, evaluator rationale, and evidence references. Weights must sum to one; a declared critical failure requires a reason and blocks comparison eligibility regardless of the weighted score. The validator computes a decimal weighted score to four decimal places. This is a reproducible aggregation of declared judgments, not an automated measure of editorial truth. Score sheets are not yet persisted in the registry, evaluator identity is not authenticated by this module, and no real inter-rater reliability has been established. See `docs/SECTION_7_19_SCORING_CALIBRATION.md`.


## Section 7.20: authenticated durable criterion-level scoring

- SQLite migration 20 adds an immutable score ledger with one score per accepted reviewer assignment and benchmark item. Database triggers reject updates and deletes.
- The score record binds the evaluator, assignment, exact artifact fingerprint, item ID, task type, language, evaluation timestamp, deterministic weighted result, comparison eligibility, and score fingerprint.
- Registry validation resolves the item and task type from the stored immutable artifact. Client-declared artifact metadata is not trusted.
- Scoring is exposed through the authenticated access facade only. The evaluator must have a recent authenticated session, provider verification metadata, an active human-classified reviewer principal, and the exact accepted language-qualified assignment.
- Final adjudication blocks new score records. A score with a declared critical failure is retained as an immutable judgment but is marked ineligible for comparison.
- Provider issuer, subject, key ID, adapter identity, and authentication time are retained as provenance. Raw session IDs and tokens are not stored.
- This ledger records judgments; it does not prove evidence correctness, rubric validity, evaluator competence, or inter-rater reliability. Deployment must still provide a real cryptographic identity provider and independently validated reviewer qualifications.


## Section 7.21: evaluator agreement and calibration intelligence

- Added deterministic descriptive agreement reporting for persisted, independently submitted score sheets on one exact artifact fingerprint, item, task type, language, and matching criterion rubric.
- Reports include pairwise exact agreement, within-one-point agreement, mean absolute score difference per criterion, and criterion-weighted mean absolute difference.
- Registry reports require provider provenance and human-classified evaluator principals. The authenticated facade exposes peer-score reports only to curators and adjudicators, preserving reviewer blindness during independent scoring.
- Critical-failure score sheets are excluded from agreement comparison. Mismatched items, artifacts, criterion sets, or weights fail closed.
- Explicit statistical boundary: these are descriptive disagreement measures, not reliability coefficients. No generalization or claim of inter-rater reliability is supported. Genuine human calibration data and an independently designed reliability study remain necessary.


## Section 7.22: benchmark calibration study protocol

A versioned study contract and validator now require an explicit sampling frame, selection method, inclusion/exclusion criteria, Bangla/English task strata, reviewer training and qualification declarations, blind assignment plan, measurement scale, proposed statistic and rationale, uncertainty-interval plan, missing-data plan, independence assumptions, and independent methodological reviewer. Calibration, blind evaluation, and adjudication item IDs must be disjoint. Every blind item must have exactly one assignment-plan row and at least two distinct, trained, declared reviewers qualified for its language and task.

The validator fingerprints the full protocol and always reports protocol_only with production_ready=false. It does not persist protocol versions, prove training or human identity, execute sampling, prescribe a universal sample-size threshold, or establish reliability. A deployment must store approved protocol versions in an append-only study registry before use. The statistical method must be justified for the scale, rater/item sampling design, clustering, missingness, and intended inference, with uncertainty intervals and independent methods review. See docs/SECTION_7_22_CALIBRATION_STUDY_PROTOCOL.md.


## Section 7.23: durable benchmark study protocol registry

SQLite migration 21 persists study protocol versions in an immutable ledger. Each version binds the exact registered benchmark ID/fingerprint, rubric version, full protocol fingerprint, actor, timestamp, and reason. Database triggers enforce payload/index consistency, sequential versions, stable benchmark/rubric binding, and append-only history. Registration checks all partition IDs against the registered artifact and verifies each blind assignment task type against its item. Curator-only registration is exposed through the authenticated facade; retrieval/history are restricted to curator, adjudicator, and security-admin roles. The registry records a prespecified design, not actual training or study execution, and it never changes the `protocol_only` status or grants production approval. See `docs/SECTION_7_23_DURABLE_STUDY_REGISTRY.md`.


## Section 7.24: study execution evidence and independent analysis review

SQLite migration 22 adds immutable execution records linked to an exact registered protocol version and benchmark fingerprint. A newly provisioned `benchmark_methodologist` role submits completed execution evidence through the authenticated facade. The registry binds score IDs to the protocol's blind item/evaluator pairs, requires every planned pair to be accounted for as scored or documented missing/excluded, checks score eligibility and execution timestamps, and rejects a methodologist who is also a blind evaluator. It stores the analysis estimate, confidence interval, missing-data and independence statements, and analysis artifact fingerprint. It does not independently recompute the statistic, certify methodological competence, or approve production use. See `docs/SECTION_7_24_STUDY_EXECUTION_EVIDENCE.md`.


## Section 7.25: independent benchmark acceptance review

A versioned acceptance-review contract now separates statistical validity, editorial validity, and operational security into three mandatory evidence-backed review domains. Each decision binds the exact registered study ID/version, protocol fingerprint, execution ID/fingerprint, benchmark fingerprint, and rubric version. The independent acceptance reviewer must be distinct from every planned blind evaluator and the methodologist, and must declare conflicts. Acceptance requires all three domains to pass; rejection and revision decisions must match their domain findings. Decisions are explicitly scoped to a stated intended use and limitations. The pure validator never grants authorization or reports production readiness. This is currently a contract/validation slice: append-only decision persistence, authenticated independent-reviewer authorization, and registry-enforced linkage remain follow-on work. See docs/SECTION_7_25_INDEPENDENT_ACCEPTANCE_REVIEW.md.
