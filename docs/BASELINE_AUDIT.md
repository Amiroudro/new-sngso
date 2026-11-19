# Repository Baseline Audit

Audit date: 2026-11-20  
Audited ref: `fix/engine-ci-source-triggers`  
Scope: the greenfield `Amiroudro/new-sngso` engine repository only.

## Repository boundary

This repository is the new Python engine. The Android client, legacy engine work, and published-edition archives are separate and remain untouched. No production destination, publisher connector, secret, or paid service has been configured.

## Technology baseline

| Area | Current implementation |
|---|---|
| Runtime | Python >=3.12 |
| Packaging | setuptools, `pyproject.toml` |
| Runtime dependency | `jsonschema[format]>=4.23,<5` |
| Contract format | JSON Schema Draft 2020-12 |
| Persistence | Python standard-library SQLite, migrations v1 and v2 |
| Tests | pytest, fixture-based; no publisher network dependency |
| CI | GitHub Actions, Python 3.12, full pytest suite |

## Implemented foundations

### Source governance and permission contracts

The repository contains separate contracts for publisher sources, source endpoints, permission evidence, and scoped permission grants. The evaluator is operation-specific and fail-closed. Grants are time-bounded and scoped to exact source and endpoint identities. Approved grants require verified evidence. No real publisher is approved.

### Domain records and persistence

Typed immutable record views cover sources, endpoints, permission evidence, grants, articles, stories, claims, factual evidence, and editions. SQLite migrations create relational ownership constraints, grant and editorial revision history, append-only audit events, and append-only collection observations. The registry validates cross-record relationships inside transactions. Permission decisions retain policy version, grant revision, evidence state, and evaluation time.

### Permission-first collection

The fixture-first orchestrator supports discovery and metadata only. It verifies source, endpoint, connector capability, and exact operation permission before connector invocation. It rechecks permission immediately before invocation and the persistent sink rechecks grant revision, source/endpoint state, evidence, and permission before storing observations. The fixture boundary applies a per-domain rate limiter, item and observation-byte budgets, operation-specific output fields, and source-domain restrictions for endpoint and returned locator/final-locator hosts. The SQLite observation sink repeats the endpoint and locator host checks inside its write transaction, so callers cannot bypass the domain boundary by calling the storage API directly.

There is no live network transport. Timeout and retry values are bounded request declarations, not active transport enforcement. DNS/IP restrictions, redirect handling, response-body byte limits, and real network timeouts/retries must be implemented and reviewed before any live connector is considered.

## Verification evidence

- GitHub Actions run #127 passed the full pytest workflow for the first verified 137-test collection milestone.
- GitHub Actions run #128 passed after adding source-domain scope regression tests for endpoint hosts, returned locators, final locators, and allowed subdomains.
- An intermediate run #132 failed during the storage-boundary change because a domain check referenced transaction data before it was loaded. The check was moved into the transaction; run #133 passed with the storage-bypass regression test.
- The workflow runs the full test suite on relevant source, contract, test, packaging, and workflow changes. It does not contact publisher websites.
- A passing fixture test suite proves the tested software behavior only. It does not approve publishers, establish copyright permission, or prove production readiness.

Latest verified code change for the source-domain persistence boundary: `a1157f22fb453f0adf27a6e3d8a60c0f0dfa0782`, with the corresponding regression test in `a0d251df56f805f3597a913dcde50213c8e7fae5`. GitHub Actions run #133 passed for the corrected code. Later documentation-only commits should still receive their own routine CI checks before merge.

## Ten-section programme status

| Section | Status | Remaining gate |
|---|---|---|
| 1. Repository audit | Targeted audit documented | Exhaustive dependency/security/static/runtime audit remains future work |
| 2. Source governance | Complete for foundation scope | Human reviewer authentication/authorization and real publisher rights review remain future controls |
| 3. Domain models and persistence | Complete for foundation scope | Backups, restore drills, external audit anchoring, and production authorization remain future controls |
| 4. Permission-first collection | In progress | Reviewed network transport, actual timeouts/retries/response-byte enforcement, DNS/IP and redirect controls, remaining adversarial tests, and final review |
| 5. Ingestion and identity | Not started | Depends on Section 4 |
| 6. Story intelligence and evidence | Not started | Depends on Section 5 |
| 7. Editorial production | Not started | Depends on Section 6 |
| 8. Edition assembly and publication safety | Not started | Depends on Section 7 |
| 9. Operations and free-first automation | Not started | Depends on stable pipeline and publication gates |
| 10. End-to-end verification and handover | Not started | Depends on all preceding gates |

## Known risks and deliberate limits

- Human reviewer identity is recorded, but authentication and role-based authorization are not implemented.
- Permission conditions without a typed evaluator fail closed.
- Source and endpoint lifecycle changes do not yet have dedicated governance APIs.
- Collection observations store bounded discovery/metadata only, not article bodies, excerpts, images, or translations.
- No live connector or publisher approval exists.
- SQLite is intended for local and early low-volume operation, not yet validated for hosted multi-user production.
- Audit history is tamper-evident, not tamper-proof against a privileged database operator.
- Backup/restore, encryption-at-rest policy, retention automation, external audit anchoring, and pipeline-run persistence remain future work.
- The current CI is a foundation gate. Lint, formatting, static typing, dependency/security scans, and full publication integration tests are not yet configured.

## Preservation and release rules

- Keep PR #1 draft and unmerged until review and explicit approval.
- Do not connect live publishers or treat a public URL as permission.
- Do not store full article text, translate, or republish without separate operation-specific permission.
- Do not publish or export into legacy repositories.
- Do not change the Android client or published editions as part of this engine programme.
- Do not claim production readiness from fixture CI alone.
