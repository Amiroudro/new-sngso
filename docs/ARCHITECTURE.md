# Proposed Architecture

Status: design proposal. This document defines boundaries; it does not claim the modules already exist.

## Architectural style

Use a modular monolith with explicit ports and adapters. One Python application is simpler and cheaper to test and operate than premature microservices. Keep internal boundaries strict so components can evolve independently. Split services only when measured scale or operational isolation justifies the cost.

## Conceptual flow

1. Source policy decides whether an operation is allowed.
2. Connectors discover permitted records and retrieve only permitted fields or content.
3. Ingestion normalizes records into source observations.
4. Story intelligence links observations referring to the same evolving event.
5. Evidence management extracts attributable claims and supporting passages.
6. Verification evaluates support, contradiction, recency, and source independence.
7. Editorial production creates summaries, timelines, perspective comparisons, and learning material from approved evidence.
8. Quality control runs structural, factual, linguistic, rights, and editorial gates.
9. Publishing creates immutable edition artifacts and a manifest.

No downstream stage may bypass rights decisions or erase provenance.

## Bounded contexts

### Source governance
Publisher identity, domain ownership, route-level access, permission dimensions, terms, review dates, withdrawals, and suppressions.

### Collection
Feed, sitemap, API, and approved page connectors. Connectors return observations, not editorial stories. Each declares supported fields, permission requirements, rate limits, timeouts, and retry behavior.

### Ingestion and normalization
Canonical URLs, timestamps, language, publisher identity, content fingerprints, metadata normalization, and duplicate detection.

### Story intelligence
Story identity, event membership, topic taxonomy, entities, chronology, and story relationships. Distinguish an evolving story from an individual article.

### Evidence and verification
Atomic claims, source passages, attribution, corroboration, contradiction, evidence quality, review status, and uncertainty. Evidence records are append-only; corrections supersede prior records rather than silently rewriting history.

### Editorial production
Three-level summaries, briefings, timelines, multi-source perspectives, explainers, and context. Editorial claims retain evidence links wherever feasible.

### Learning
Exam relevance, syllabus mapping, vocabulary, question generation, answer explanations, revision scheduling, and learning-object provenance.

### Publication
Edition assembly, schema validation, policy gates, immutable artifacts, manifests, release metadata, and downstream compatibility exports.

### Operations
Scheduling, retries, rate limits, health checks, audit logs, metrics, alert conditions, and recovery procedures.

## Core domain entities

Publisher; SourcePermission; SourceObservation; Article; Story; StoryArticle; Claim; Evidence; Event; Perspective; LearningObject; Edition; PipelineRun; EditorialDecision.

Use stable IDs and explicit relationships. A URL is not the sole identity of an article, and a headline is not the identity of a story.

## Storage

Use SQLite for local development and early low-volume operation through the transactional `SQLiteRegistry` and versioned migration 1. It persists source governance plus article, story, claim, factual evidence, and edition records, with cross-record validation and append-only revision/audit tables. Adopt PostgreSQL only if measured concurrency or volume requires it. See `docs/PERSISTENCE.md` for guarantees and limitations.

Published editions are immutable JSON artifacts with a manifest and content hashes. Store raw or full-text material only where permission allows and retention policy requires it.

## Contracts and interfaces

- Versioned JSON Schema for external artifacts and interchange.
- Typed Python models internally; validate at system boundaries.
- Every schema change requires compatibility review and fixture tests.
- Connector operations are separately permission-gated: discover, fetch metadata, and optionally fetch content.
- Pipeline stages consume declared inputs and emit declared outputs plus a structured stage report.
- Modules must not import another module's private storage implementation.

## Publication gates

Fail closed if source permission is absent or expired; provenance is missing; material claims lack traceable support; material contradictions are unresolved or undisclosed; chronology is invalid; translation review requirements are unmet; schemas or hashes fail; or approval is absent.

## Technology direction

Python 3.12+; JSON Schema; sqlite3 initially; pytest or unittest as one chosen primary runner; ruff; static typing after conventions are settled; GitHub Actions for CI and controlled scheduling. Select HTTP and feed libraries only after connector requirements are defined.

Do not adopt a web framework, task queue, vector database, LLM framework, or orchestration platform until a demonstrated requirement exists.

## Operational boundaries

Jobs are idempotent. Network calls have per-domain rate limits, timeouts, bounded retries, and backoff. Secrets are injected at runtime and never logged. A failed run cannot overwrite the last known-good edition. Production promotion is separate from generation.

## Decisions still to validate

1. Edition cadence and supported time zones.
2. Permission evidence and legal review process.
3. Local runner versus GitHub Actions operational target.
4. Expected volume and retention limits.
5. Human review workflow and authorized approvers.
6. Compatibility requirements for the existing Android client.
