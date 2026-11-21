# Sarangsho Engine

A greenfield editorial-intelligence and learning-content engine for Sarangsho Plus.

This is not a fork, port, or renamed copy of the previous Sarangsho pipeline. Existing repositories are reference-only and remain unchanged.

## Mission

Transform lawfully accessible, attributable reporting into accurate, explainable, bilingual news briefings and useful learning material. Every material claim must have traceable evidence, every source operation must pass an explicit permission policy, and every edition must pass reproducible quality gates.

## Initial engineering direction

- Python 3.12+ as a modular monolith.
- Typed domain models and versioned JSON contracts.
- SQLite initially, behind repository interfaces.
- Deterministic pipeline stages with immutable run manifests and content hashes.
- GitHub Actions for CI and controlled scheduled execution.
- Static, versioned edition artifacts for downstream clients.
- No paid API or LLM required for core operation.

## Non-goals for the first release

- Microservices, Kubernetes, message brokers, or paid always-on infrastructure.
- Autonomous publication without editorial approval.
- Treating robots.txt as a copyright licence.
- Collecting full article bodies unless permission and applicable terms allow it.
- Replacing human judgment in translation, disputed claims, or sensitive editorial decisions.

## Project status

The current engineering branch contains versioned contracts, permission-aware collection and ingestion, story/claim/evidence intelligence, bilingual editorial review, benchmark and learning-object quality controls, immutable edition artifacts, separate approval/release authorization, and a staging-only promotion/rollback ledger. Section 7's implementation workstream is closed, and Section 8's Python-engine implementation workstream is complete with a real-registry lifecycle test. Section 9's baseline audit is complete, and its first operational reporting and local backup/restore utilities are implemented; idempotent orchestration, runbooks, external audit anchoring and production backup operations remain. Section 10 (end-to-end verification and controlled handover) follows Section 9. The latest full Python 3.12 suite passed 869 tests. This is not a claim of production readiness, real publisher republication permission, genuine human-reviewed editorial evidence, deployed identity infrastructure, operational backups, an integrated staging consumer, or an operating daily publication pipeline. See `docs/IMPLEMENTATION_ROADMAP.md` and `docs/HANDOVER_FOR_PREMIUM_CHATGPT.md` for current scope and boundaries.

## Documents

- docs/PRODUCT_PRINCIPLES.md
- docs/ARCHITECTURE.md
- docs/MIGRATION_PLAN.md
- docs/QUALITY_GATES.md
- docs/DOMAIN_MODEL.md
- docs/IMPLEMENTATION_ROADMAP.md
- docs/SECTION_9_AUDIT.md
- docs/HANDOVER_FOR_PREMIUM_CHATGPT.md
- docs/ENGINE_TO_FLUTTER_COMPATIBILITY.md
- docs/BASELINE_AUDIT.md
- docs/SOURCE_GOVERNANCE.md
- docs/PERSISTENCE.md
- contracts/README.md
- contracts/v1/

## Local validation

Requires Python 3.12+.

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

## Preservation rule

The previous private application repository and public editions repository are not deployment targets. No write-back, merge, overwrite, schedule change, or publication is permitted without separate explicit approval.
