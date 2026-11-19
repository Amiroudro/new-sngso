# Repository Baseline Audit

Audit date: 2026-11-20  
Audited ref: `fix/engine-ci-source-triggers`  
Purpose: establish an evidence-based starting point for the ten-section implementation programme.

## 1. Repository identity and boundaries

The repository README describes a greenfield Python engine. It explicitly states that the prior app and editions repositories are reference-only and are not deployment targets. The migration plan prohibits writes to those repositories and prohibits publishing into the existing editions archive.

**Audit result:** the documented separation rule is clear. This audit made changes only in `Amiroudro/new-sngso`.

## 2. Technology baseline

| Area | Observed baseline |
|---|---|
| Runtime | Python >=3.12 |
| Packaging | setuptools, `pyproject.toml` |
| Runtime dependency | `jsonschema[format]>=4.23,<5` |
| Test dependency | `pytest>=8.3,<9` |
| Contract format | JSON Schema Draft 2020-12 |
| Persistence | No implemented database adapter verified in the inspected architecture/source files |
| Collection | No implemented connector framework verified |
| Publication | No implemented edition assembly/publisher verified |

## 3. Contracts inspected

The following versioned contracts were read from `contracts/v1/`:

- `common.schema.json`: shared IDs, language tags, timestamps, URI, SHA-256 hash, locator, provenance, and language-variant definitions.
- `article.schema.json`: article identity, source ID, canonical URI, title, language, discovery time, record status, optional permission ID and provenance.
- `story.schema.json`: story identity, title, status, topic/region tags, article IDs, and timestamps.
- `claim.schema.json`: atomic claim text, story relationship, language, assessment, evidence IDs, provenance, and review metadata.
- `evidence.schema.json`: claim/source relationship, support/qualification/contradiction state, locator, assessment method, and assessment time.
- `edition.schema.json`: edition identity, status, content hash, pipeline run ID, story IDs, language variants, and correction/supersession metadata.

The contract README states that schemas prove structural conformance, not factual accuracy, copyright permission, source independence, translation quality, or editorial approval.

### Contract gaps relevant to source governance

- No source registry schema was found at the inspected expected paths.
- No source permission schema was found at the inspected expected paths.
- No source observation schema was found at the inspected expected path.
- The article contract references `source_id` and optionally `permission_id`, but the inspected contract set does not yet define the full source/permission lifecycle.
- There is no contract yet for permission evidence, endpoint-specific scope, revocation, review history, or policy-decision audit events.

These are findings about the inspected repository paths, not a claim that every possible file path was exhaustively enumerated.

## 4. Runtime code inspected

### Contract validation

`src/sngso_engine/contracts.py` loads versioned schema files, checks schema meta-structure, resolves the shared common schema, applies format checking, and raises a structured validation error for invalid payloads.

The loader accepts a schema filename stem and prevents path separators. Repository adapters do not yet provide a verified persistence-boundary integration.

### Permission evaluation

`src/sngso_engine/permissions.py` contains an operation-specific, fail-closed evaluator for discovery, metadata, excerpt storage, full-text storage, translation, image storage, and republication.

The evaluator checks record shape, permission ID, global and operation approval, timezone-aware effective start and expiry, operation allow flag, and conditions. Conditions are denied pending a typed evaluator.

The current branch also requires `effective_until` and rejects an expiry that is equal to or earlier than `effective_from`. This is stricter than the previous implementation and is covered by newly authored tests.

**Boundary:** this is an evaluator for a permission record already selected for a source. It is not a source registry, evidence workflow, contract validator integration, or connector enforcement layer.

## 5. Tests and CI

The inspected permission tests cover explicit approval, missing/unapproved operations, malformed records and IDs, unhashable operation input, malformed evaluation time, future start, exact expiry, naive timestamps, unhandled conditions, malformed conditions, missing/invalid expiry, and invalid permission windows.

The workflow `.github/workflows/contracts.yml` runs on relevant contract/source/test/workflow changes, installs the package with test dependencies, and runs `pytest tests/contracts -q` on Python 3.12.

The workflow is fixture-based and does not contact publisher websites.

At audit time, the latest branch commit `875693bf25c43596f588ec059c12ab9ff5a9a67a` had no associated pull-request workflow run reported by the available commit-workflow lookup. Therefore, tests are authored but not verified as passing.

## 6. Documentation inspected

- `README.md`: mission, stack direction, non-goals, project status, and preservation rule.
- `docs/ARCHITECTURE.md`: proposed modular-monolith boundaries and conceptual pipeline; explicitly a design proposal, not proof of implementation.
- `docs/DOMAIN_MODEL.md`: proposed source, permission, observation, article, story, claim, evidence, timeline, learning, edition, and run concepts.
- `docs/PRODUCT_PRINCIPLES.md`: evidence-first, rights-before-collection, deterministic core, human accountability, bilingual integrity, reproducibility, and explicit failure.
- `docs/MIGRATION_PLAN.md`: read-only legacy reference policy and staged knowledge transfer.
- `docs/QUALITY_GATES.md`: target publication gates and a current implementation-status table.

## 7. Findings and risk register

| ID | Finding | Risk | Required response |
|---|---|---|---|
| GOV-01 | Source identity and source permission lifecycle lack dedicated contracts in inspected paths | High | Section 2 |
| GOV-02 | Permission evaluator is not yet connected to a source registry or collector | Critical before collection | Sections 2–4 |
| GOV-03 | No permission evidence/review/revocation workflow verified | High | Sections 2–3 |
| DATA-01 | No persistence adapter/migrations verified | High before operational runs | Section 3 |
| COL-01 | No connector framework verified | High before live collection | Section 4 |
| EVD-01 | Claim/evidence contracts exist, but end-to-end evidence pipeline is not implemented | High | Section 6 |
| PUB-01 | Edition contract exists, but publication assembly and last-known-good protection are not implemented | Critical before publishing | Section 8 |
| QA-01 | Current CI is a small contract test gate, not the complete quality policy | Medium | Sections 1, 9, 10 |
| QA-02 | Latest branch tests have no verified CI result | High | Re-run/repair CI before merge |
| OPS-01 | Scheduling, observability, backup, and recovery are not verified as implemented | Medium | Section 9 |

## 8. Baseline disposition

- Keep the current PR draft and unmerged.
- Do not connect live publishers yet.
- Do not mark any source as approved without permission evidence.
- Do not build collection before source/permission contracts and enforcement are in place.
- Do not publish or export into legacy repositories.
- Proceed to Section 2 only after this baseline is recorded.

## 9. Audit limitations

This was a targeted content audit of the core README, architecture/domain/quality/migration documents, package configuration, contract loader, permission evaluator, selected v1 contracts, permission tests, and CI workflow. It was not a full exhaustive file-tree, dependency vulnerability, static-analysis, runtime, or integration audit. Those checks remain future work.
