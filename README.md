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

Foundation milestone in progress. Versioned domain contracts, contract CI, runtime schema validation, deterministic content hashing, and a fail-closed permission evaluator are implemented. Collection, persistence adapters, editorial production, and publication are not implemented yet. No production pipeline or publisher permission is implied.

## Documents

- docs/PRODUCT_PRINCIPLES.md
- docs/ARCHITECTURE.md
- docs/MIGRATION_PLAN.md
- docs/QUALITY_GATES.md
- docs/DOMAIN_MODEL.md
- docs/IMPLEMENTATION_ROADMAP.md
- docs/BASELINE_AUDIT.md
- docs/SOURCE_GOVERNANCE.md
- contracts/README.md
- contracts/v1/

## Local validation

Requires Python 3.12+.

```bash
python -m pip install -e ".[test]"
python -m pytest tests/contracts -q
```

## Preservation rule

The previous private application repository and public editions repository are not deployment targets. No write-back, merge, overwrite, schedule change, or publication is permitted without separate explicit approval.
