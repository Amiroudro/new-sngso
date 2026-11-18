# Domain Model

Status: proposed baseline  
Contract line: v1  
Date: 2026-11-19

## Design rule

Reporting, factual propositions, editorial interpretation, and learning content are distinct domain concepts. An article is not a story; a claim is not evidence; a summary is not a source record; a published edition is not a mutable working document.

## Entities

- **Source:** publisher or feed endpoint with a stable identity and governance record.
- **SourcePermission:** operation-specific permission record. Discovery, metadata storage, excerpt storage, full-text storage, translation, image storage, and republication are evaluated independently.
- **SourceObservation:** immutable record of what was observed, when, and by which connector. Observation does not grant a right to retain or republish the observed material.
- **Article:** normalized bibliographic/reporting record. It points to source and observation records. It does not imply permission to retain full text.
- **Story:** evolving real-world subject that can be covered by multiple articles and publishers. The relationship between stories and articles is many-to-many.
- **Claim:** one atomic, attributable proposition that evidence can support, qualify, contradict, or leave unresolved.
- **Evidence:** source-backed relationship to a claim, with a locator and assessment method. Evidence is not itself a declaration of truth.
- **TimelineEvent:** dated or time-bounded event within a story, linked to claims and evidence.
- **Perspective:** a publisher's framing or a stakeholder viewpoint, explicitly distinguished from verified fact.
- **LearningObject:** derived summary, vocabulary item, question, or revision card with traceable links to stories and claims.
- **EditorialDecision:** human review outcome, reviewer role, rationale, and timestamp.
- **PipelineRun:** immutable record of inputs, software/configuration versions, decisions, outcomes, and hashes.
- **Edition:** immutable, versioned publication snapshot. Corrections create a new snapshot or correction record, never a silent rewrite.

## Relationship sketch

Source -> SourcePermission  
Source -> SourceObservation -> Article  
Article <-> Story  
Story -> Claim -> Evidence  
Story -> TimelineEvent  
Story -> Perspective  
Story -> LearningObject  
PipelineRun -> EditorialDecision  
PipelineRun -> Edition

## Identity and time

Every persisted entity receives a stable opaque ID. IDs are not titles, URLs, row numbers, or array positions. All machine timestamps use RFC 3339 with an explicit offset; normalize instants to UTC while retaining a source's stated local time and timezone where relevant. A date-only event remains a date. Do not fabricate midnight as an event time. Distinguish published, updated, discovered, observed, ingested, and occurred timestamps.

## Provenance and evidence

Every extracted claim retains source identity, article and observation identity where available, a locator, extraction method and version, extraction timestamp, and assessment state. Claim states are workflow assessments: unassessed, supported, partially supported, disputed, contradicted, or retracted. Multiple publishers are not independent corroboration when they repeat the same wire report or underlying source.

Never invent a quotation, locator, publication timestamp, or independent source relationship. If retaining the source passage is not permitted, retain only a lawful locator and a permitted note.

## Permission model

Missing, ambiguous, expired, or conflicting permission means deny for the affected operation. Public accessibility, RSS availability, and robots.txt do not themselves grant copyright permission. Permission needs a basis, scope, effective interval, evidence reference, and review state. The collector must enforce permission before each operation, not merely at publication time. Permission changes must not silently rewrite historical editions.

## Language model

Bangla and English are first-class variants, not a primary text plus an assumed translation. Use BCP 47 language tags, initially en and bn. Each language variant has its own workflow state: not_started, draft, review_required, approved, or rejected. Source language and output language remain distinct.

## Immutability and correction

Observations and published editions are append-only subject to documented legal retention/deletion obligations. Mutable working records use explicit lifecycle states and revision numbers. Corrections append a correction record and produce a new publication snapshot. A failed or rejected run cannot replace the last known-good edition.

## Implementation order

1. IDs, timestamps, language variants, and provenance primitives.
2. Source permissions and source observations.
3. Article normalization and story/article relationships.
4. Atomic claims and evidence assessments.
5. Editorial decisions and immutable edition manifests.
6. Timeline events, perspectives, and learning objects.

Do not build summarization, embeddings, a vector database, a web API, or a complex event bus before these contracts are validated with fixtures and editorial workflows.
