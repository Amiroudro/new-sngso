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

The current Section 9 draft branch contains versioned contracts, permission-aware collection and ingestion, story/claim/evidence intelligence, bilingual editorial review, benchmark and learning-object quality controls, immutable edition artifacts, separate approval/release authorization, and a staging-only promotion/rollback ledger. Section 7's engine implementation scope is closed, and Section 8's Python repository implementation is complete at the staging-only boundary, including an integrated real-registry lifecycle test.

Section 9 now includes durable stage checkpoints, SQLite worker leases, supervised subprocess execution, cancellation and recovery contracts, bounded local telemetry, schema-v36 export and inspection, PostgreSQL migrations through 0026, read-only source/target reconciliation, isolated-target preflight, and draft mapping tools. This is still an engineering foundation, not a production system. The SQLite registry remains authoritative. There is no reviewed production mapping, importer, cutover, distributed lease backend, real side-effect provider, configured production identity/RBAC, remote monitoring, or deployed backup/restore/failover process. No live publisher collection, scheduling, publication, or release is enabled.

Exact tested code head `e8381a1419a2bf6c61a955f3d281d4a1aaba67be` passed GitHub Actions [run #45420532668](https://github.com/Amiroudro/new-sngso/actions/runs/45420532668): **1,218 contract tests passed, 1 skipped, and 87 PostgreSQL integration tests passed**. Later changes to the roadmap and acceptance documents do not change that tested code. Section 10, end-to-end verification and controlled handover, has not started. A separate open PR contains a static editorial-console prototype with demo data and no backend connection. See `docs/IMPLEMENTATION_ROADMAP.md` and `docs/SECTION_9_FINAL_ACCEPTANCE_AND_CLOSURE_PLAN.md` for current scope and blockers.

## Documents

- docs/PRODUCT_PRINCIPLES.md
- docs/ARCHITECTURE.md
- docs/MIGRATION_PLAN.md
- docs/QUALITY_GATES.md
- docs/DOMAIN_MODEL.md
- docs/IMPLEMENTATION_ROADMAP.md
- docs/SECTION_9_AUDIT.md
- docs/SECTION_9_ACCEPTANCE.md
- docs/OPERATIONS_RUNBOOK.md
- docs/PIPELINE_WORKER_CONTRACT.md
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
