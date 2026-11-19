# Quality, Testing, and Release Gates

## Guiding rule

A successful process exit is not proof of a correct edition. Quality is assessed at each stage and at publication.

## Test layers

### Unit tests
Parsers, normalizers, policy decisions, fingerprints, clustering primitives, scoring rules, schema validators, and deterministic transformations.

### Contract tests
Validate every persisted and published object against a versioned schema. Include valid, invalid, boundary, and backward-compatibility fixtures.

### Connector tests
Use recorded or synthetic fixtures by default. Live network tests are optional, rate-limited, and never required for ordinary CI.

### Integration tests
Exercise permission evaluation through collection, normalization, evidence creation, editorial generation, and artifact assembly.

### Regression tests
Keep synthetic cases for duplicate stories, contradictory reports, ambiguous dates, number formatting, Bangla text, transliteration, malformed feeds, source outages, and rights withdrawal.

### Editorial evaluation
Use a human-reviewed benchmark set. Measure factual support, attribution, omission, distortion, chronology, perspective fidelity, Bangla fluency, and exam usefulness. A single aggregate score must not hide a critical failure.

## Mandatory publication gates

1. Permission: every source operation is allowed under a current, documented permission record.
2. Provenance: each observation has identity, URL, publisher, timestamps, method, and fingerprint.
3. Evidence: every material factual claim has traceable support or is explicitly marked unverified or attributed.
4. Conflict: material contradictions are resolved, attributed, or disclosed.
5. Story: story identity and event chronology pass validation.
6. Editorial: summaries preserve meaning, attribution, qualifications, and uncertainty.
7. Language: each language output meets its own review state.
8. Learning: questions have supported answers and explanations; vocabulary has context and provenance.
9. Schema: artifacts validate against the declared edition schema.
10. Integrity: artifact and manifest hashes match.
11. Operations: incomplete runs are visibly marked and cannot silently replace complete editions.
12. Approval: an authorized human approval exists for publication.

## Failure behavior

Fail closed for missing or expired permissions, missing provenance, invalid schemas, hash mismatch, and unresolved critical editorial issues. Retry only transient network/service errors with bounded backoff. Preserve the last known-good edition when a run fails. Partial runs cannot silently replace complete runs.

Each stage report includes status, counts, warnings, errors, duration, and input/output identifiers.

## Current implementation status (2026-11-20)

The repository is at the foundation milestone. The following is the verified scope of the current code and workflow, not a claim that the full engine is operational.

| Area | Current state | Not yet delivered |
|---|---|---|
| Versioned contracts | JSON Schema Draft 2020-12 contracts exist for the core domain records | Cross-record relational integrity checks and full publication-policy validation |
| Runtime validation | Python loader validates schema definitions and payloads with format checking and local common-schema references | Persistence-boundary integration because repository adapters are not implemented |
| Source governance contracts | Separate source, endpoint, permission-evidence, and scoped permission-grant schemas; legacy permission schema marked deprecated | Cross-record source/endpoint/evidence ownership validation and persisted registry |
| Permission evaluator | Operation-specific, time-bounded, fail-closed evaluator; scoped grants require exact source/endpoint matches and evidence references; malformed records, identifiers, operation values, evaluation times, and unhandled conditions are denied | Verified evidence-record lookup, typed condition evaluator, human review workflow, and collector enforcement |
| Content fingerprints | Deterministic JSON serialization and SHA-256 fingerprint helper exist | Frozen run manifests and end-to-end artifact reproducibility |
| Automated tests | Contract fixtures, schema checks, source-governance fixtures for scoped grants/evidence/revocation, permission evaluator tests for malformed inputs and unhandled-condition denial, and canonical serialization tests are authored | A verified green CI run and broader integration/regression suites |
| GitHub Actions | Python 3.12 workflow installs the package and runs `pytest tests/contracts` | Lint, formatting, static typing, dependency/security scans, migration tests, and publication integration tests |
| Persistence | SQLite migration v1, typed records, cross-record checks, grant/domain revisions, and append-only hash-linked audit events are implemented in the registry layer | Successful CI execution, source-observation/pipeline-run storage, backup/restore drills, external audit anchoring, and production authorization |\n| Collection and publication | Not implemented | Connectors, permission-before-network enforcement, editorial review workflow, edition assembly gates, and publisher/export adapters |

The current workflow is intentionally fixture-only and does not contact publisher websites. Source governance contracts and evaluator scope checks have been added, but no live publisher is approved and no connector enforces these decisions yet. Its path filters include `src/**` and `tests/**`, so changes to the runtime evaluator or its tests trigger the contract check.

## CI requirements

Every pull request should eventually run environment checks, formatting, lint, static type checks when configured, unit and contract tests, fixture-based integration tests, migration tests, secret scanning, dependency checks, and deterministic artifact tests.

CI must not depend on publisher websites being online. Live-source checks belong in a separate controlled operational workflow.

The current workflow is a smaller foundation gate, not the complete CI policy described above. Do not mark the broader CI requirements complete until the corresponding tools and checks exist and have passed.

## Release criteria

A release requires all mandatory CI checks green; reviewed schema and migration notes; no unresolved critical/high-severity defects; reproducible run report; permission and provenance audit; editorial approval; immutable artifact and manifest; and a documented rollback to the last known-good artifact.

## Metrics

Track permitted-source coverage; fetch success and freshness; story-clustering precision and recall on reviewed benchmarks; material claim support; unresolved contradiction rate; correction rate; summary meaning preservation; Bangla editorial acceptance; question answerability; and run duration/failure rate.

Every metric must define its denominator and review method. Avoid unsupported vanity percentages.
