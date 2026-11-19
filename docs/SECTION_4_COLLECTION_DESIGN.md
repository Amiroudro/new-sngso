# Section 4: Permission-First Collection Design

Status: first fixture-first implementation committed; Section 4 acceptance remains open  
Scope: fixture-first collection framework in the new engine only

## 1. Non-negotiable boundary

A connector must never be called until the engine has evaluated a current permission grant for the exact source, endpoint, and requested operation. Public accessibility, a robots.txt rule, an RSS URL, or a previously allowed operation is not a substitute for a grant.

The collection service owns authorization. A connector does not decide whether it is allowed to run.

## 2. Request identity

Every collection request carries:
- a stable source ID;
- a stable endpoint ID;
- a permission-grant ID;
- exactly one operation from the engine's operation vocabulary;
- a bounded request budget (deadline, item limit, and retry budget).

The engine resolves the endpoint and verifies that it belongs to the stated source. It then asks the governance repository to evaluate the exact grant and operation. Missing records, mismatches, expired grants, inactive sources/endpoints, unverified evidence, unsupported conditions, or evaluator errors deny the request.

## 3. Execution order

1. Validate the request shape and configured limits.
2. Resolve source, endpoint, and grant from the repository.
3. Confirm source and endpoint identity match the request.
4. Confirm the selected connector declares support for this endpoint type and operation.
5. Evaluate the permission grant for this exact operation.
6. If denied, return a structured denial and do not call the connector or transport.
7. If allowed, call only the connector method for that operation.
8. Validate and bound returned observations.
9. Append provenance and a decision record to the observation store.
10. Return a structured result without silently upgrading the requested operation.

Authorization must be repeated for every operation. Discovery permission does not carry forward to metadata, excerpts, full text, translation, image storage, or republication.

## 4. Connector boundary

The first implementation uses deterministic fixture connectors only. It must not make external network requests.

A connector declares:
- supported endpoint types;
- supported operations;
- a stable connector name and version;
- a bounded operation method that receives a validated request.

The orchestrator passes only the requested operation and its explicit budget. The connector interface must not expose an unrestricted shared HTTP client or direct access to the database.

The standalone HTTPS transport prototype now pins the socket connection to a DNS-resolved public IP while retaining the original hostname for TLS certificate verification. It rejects non-HTTPS URLs, credentials, non-standard ports, malformed hosts, non-public DNS answers, out-of-scope redirect destinations, compressed responses, and oversized response bodies. It streams under a request deadline, caps redirects and retries, and uses a fixed user-agent. It is not registered with the fixture orchestrator and no live publisher connector is enabled. DNS lookup itself still uses the operating-system resolver and is not yet independently deadline-bounded; a production adapter requires a resolver with an enforceable timeout, plus final TLS/redirect/IP review and integration tests.

## 5. Observation and provenance

A collection observation is an immutable record. At minimum it records:
- observation ID;
- source, endpoint, and grant IDs;
- operation and connector name/version;
- observation timestamp;
- requested locator and final locator, if applicable;
- response status and content type when available;
- bounded metadata payload;
- content fingerprint where content was legitimately obtained;
- permission decision ID/reason and grant revision.

Do not persist full article text, excerpts, images, or translations through the discovery/metadata path. Those require their own separately authorized operation and later storage policy.

## 6. Failure and resource controls

The service must enforce hard upper bounds for item count, response bytes, wall-clock time, and retries. Per-domain throttling must be applied before network access. Retry only explicitly transient failures, with bounded exponential backoff and jitter. Do not retry permission denials, malformed requests, or rights failures.

The fixture-first implementation should use deterministic clocks, fake rate limiters, and fake connectors so CI is independent of publisher availability and network state.

## 7. Test gates

Required tests include:
- denied permission produces zero connector calls;
- denied permission produces zero transport calls;
- exact source and endpoint mismatch denies;
- expired, revoked, suspended, inactive, or unverified grants deny;
- discovery permission cannot invoke metadata or full-text operations;
- unsupported connector capability fails before connector invocation;
- request budgets are enforced;
- connector output is schema-validated and bounded;
- observation provenance is retained and append-only;
- repeated fixture requests have deterministic outcomes.

## 8. Explicit non-goals

This section does not approve any real publisher, implement live scraping, bypass paywalls, infer copyright permission, retain full article text by default, translate unapproved content, or publish editions. No production destination or live source list is configured.

## 9. Implemented first milestone

The current implementation is in `src/sngso_engine/collection.py` and the SQLite observation adapter in `src/sngso_engine/registry.py`.

Implemented and CI-verified:
- exact source, endpoint, grant, and operation resolution before connector invocation;
- validation that the registered endpoint host and every returned locator/final locator remain within the source's registered domain allowlist (exact domains and their subdomains), enforced both by the orchestrator and again by the SQLite persistence boundary;
- source/endpoint active-state checks and connector capability checks;
- discovery and metadata as separate permission-gated operations;
- rejection of unimplemented operations, including full-text storage, translation, images, and republication;
- bounded item count, a fixed-window per-domain request limiter, an observation-byte budget, and strict operation-specific output fields;
- bounded timeout/retry budget values carried in the request contract for future network adapters (no live adapter currently exists);
- append-only SQLite observation storage with source/endpoint/grant foreign keys;
- a storage-time recheck of grant status, revision, evidence state, and effective permission;
- rejection of observations when a grant changes during connector execution;
- audit events for persisted observations;
- fixture-only tests with no publisher network calls.

GitHub Actions run #123 passed with 137 tests.

## 10. Remaining Section 4 gates

- A standalone HTTPS transport prototype now enforces socket/read deadlines, streamed response-byte ceilings, bounded transient retries, redirect limits, TLS hostname verification with IP-pinned connections, and public-IP DNS checks. The fixture orchestrator still uses fixtures only; the transport is not wired into a live connector. DNS resolver calls are not yet deadline-bounded.
- Complete adversarial transport tests, enforce a deadline-bounded DNS resolver, validate content types and response headers, and review redirect/DNS/TLS behavior before any adapter is connected. The fixture and SQLite boundaries reject locators outside the registered source domains.
- Expand adversarial tests for inactive sources/endpoints, expired grants, invalid metadata, oversized result batches, malformed domain allowlists, and sink transaction rollback. The current rate-limit and result-byte-budget tests cover the orchestrator boundary, not raw HTTP response bodies.
- CI run #128 passed after adding regression tests for out-of-scope locator hosts, out-of-scope final locators, permitted subdomains, and endpoint-host scope. CI run #133 passed after adding a storage-boundary regression test that bypasses the orchestrator and attempts to persist an out-of-scope locator. An intermediate run #132 exposed a misplaced validation reference; the validation was moved into the transaction where source domains are loaded. CI run #136 passed with further tests for inactive sources/endpoints, expired grants, disallowed metadata fields, and atomic observation-batch rollback.
- Review the collection module and observation storage together before marking this section complete.

No live connector, publisher approval, article-body storage, or production collection has been enabled.
