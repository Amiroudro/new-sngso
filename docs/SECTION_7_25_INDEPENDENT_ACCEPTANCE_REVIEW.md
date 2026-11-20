# Section 7.25: independent benchmark acceptance review

## Purpose and separation of duties

Sections 7.22–7.24 establish a registered study plan and immutable execution evidence. They do not establish that the study is statistically sound, that the benchmark is editorially correct, or that the operating environment is secure. Section 7.25 defines a separate review contract that evaluates those dimensions without allowing one successful dimension to conceal failure in another.

## Three mandatory review domains

1. **Statistical validity:** suitability of the statistic for the measurement scale and sampling design; assumptions, clustering and dependence; sample and stratum coverage; uncertainty interval method; missingness and sensitivity analysis; reproducibility of the analysis artifact.
2. **Editorial validity:** source traceability and current permission; factual support and claim scope; language quality; task and language coverage; adjudication outcomes; known answer leakage and benchmark contamination.
3. **Operational security:** provider-backed identity and role provenance; reviewer and methodologist separation; immutable protocol, execution and score evidence; audit integrity; current permission checks; deployment controls and the Section 7.18 threat/control requirements.

Each domain must include a status, findings, evidence references, and a reasoned rationale. Acceptance is rejected by the validator unless all three domains pass. A reviewer cannot turn a missing domain into a pass by citing a strong result in another domain.

## Independence and evidence binding

The review binds the exact study ID/version, protocol fingerprint, execution ID/fingerprint, benchmark fingerprint, and rubric version. These values are recomputed from the supplied protocol and execution payloads and compared exactly. A changed protocol or execution invalidates the review and requires a new review.

The acceptance reviewer must be distinct from every planned blind evaluator and the methodologist. A conflict declaration is mandatory; a declared conflict blocks the decision. The contract requires the independent-acceptance-reviewer role, but this pure validator does not authenticate that role or identity. A future durable registry and authenticated facade must enforce it at the trust boundary.

## Decision semantics and scope

Decisions are not_reviewed, needs_revision, rejected, accepted_for_limited_use, and accepted_for_proposed_use. Acceptance requires all three domains to pass. Rejection requires at least one failed domain; a revision decision requires at least one domain marked for revision.

Every record states its intended use, limitations, evidence references, and rationale. The labels describe review outcomes only. They do not authorize production publication, automated editorial decisions, or bypass of the Section 7.18 security gate. No decision in this section can set production_ready or grant authorization.

## Current implementation boundary

This section adds a versioned JSON Schema, deterministic cross-record validator, and adversarial tests for stale fingerprints, reviewer conflicts, incomplete domain outcomes, and decision consistency. It is a contract/validation slice, not yet a durable decision ledger. The next implementation step should persist review decisions append-only, bind them to registry protocol/execution rows, enforce the independent reviewer role through authenticated sessions, and preserve the no-production-promotion invariant in database constraints and facade responses.

No real study or independent acceptance review is supplied by synthetic tests. Passing tests prove only that these declared validation rules behave as tested.
