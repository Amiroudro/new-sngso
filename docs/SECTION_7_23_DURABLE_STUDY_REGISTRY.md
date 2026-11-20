# Section 7.23: durable benchmark study protocol registry

## Scope

Section 7.22 defined a protocol contract and design validator. This section adds durable persistence so approved study plans are not merely transient Python objects or mutable files.

## Immutable version ledger

SQLite migration 21 creates `benchmark_study_protocols`. Each row stores:
- study ID and monotonically sequential version;
- exact registered benchmark ID and SHA-256 content fingerprint;
- rubric version and canonical protocol fingerprint;
- complete protocol payload, fixed `protocol_only` status, timestamp, authenticated actor, and reason.

Database constraints and triggers enforce unique study versions and protocol fingerprints, payload/index agreement, sequential versions, unchanged benchmark and rubric binding across versions, and no update/delete of protocol history. A changed plan is a new version, not an in-place edit. A changed benchmark or rubric requires a new study ID so the study lineage cannot silently change its target.

## Registration checks

Before insertion, the registry:
1. validates the versioned protocol contract and recomputes its fingerprint;
2. requires an active benchmark curator;
3. verifies the registered benchmark's stored content fingerprint;
4. requires exact benchmark fingerprint equality;
5. checks every partition item exists in the benchmark artifact;
6. checks each blind assignment's task type against its exact benchmark item;
7. checks the protocol's declared language strata against the artifact language.

Registration and audit event are committed in the same transaction. A failed validation, role check, artifact check, or insert leaves no protocol row or audit event.

## Application boundary

`AuthenticatedBenchmarkAccess.register_study_protocol` derives the actor from the trusted identity provider and requires curator role. Retrieval and history through the facade require curator, adjudicator, or security-admin role. Request handlers should expose this facade, not raw registry methods. Direct database access remains a trusted deployment boundary.

## Limits

The registry proves that a particular protocol payload was recorded and has not been changed through the guarded database interface. It does not prove a reviewer is genuinely qualified, that training took place, that the sampling plan was executed, or that a statistical analysis is valid. The validator and registry always preserve `protocol_only`; neither grants benchmark production approval. Independent human review, deployed identity infrastructure, actual study execution, a design-appropriate reliability analysis with uncertainty, and separate production acceptance remain mandatory.
