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

## CI requirements

Every pull request should run environment checks, formatting, lint, static type checks when configured, unit and contract tests, fixture-based integration tests, migration tests, secret scanning, dependency checks, and deterministic artifact tests.

CI must not depend on publisher websites being online. Live-source checks belong in a separate controlled operational workflow.

## Release criteria

A release requires all mandatory CI checks green; reviewed schema and migration notes; no unresolved critical/high-severity defects; reproducible run report; permission and provenance audit; editorial approval; immutable artifact and manifest; and a documented rollback to the last known-good artifact.

## Metrics

Track permitted-source coverage; fetch success and freshness; story-clustering precision and recall on reviewed benchmarks; material claim support; unresolved contradiction rate; correction rate; summary meaning preservation; Bangla editorial acceptance; question answerability; and run duration/failure rate.

Every metric must define its denominator and review method. Avoid unsupported vanity percentages.
