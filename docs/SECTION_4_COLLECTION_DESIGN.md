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

A future network adapter requires a separate review for redirects, DNS rebinding, private/link-local IP blocking, TLS, response size, content type, decompression limits, timeouts, retry policy, per-domain rate limits, and user-agent identification. Redirect destinations must not silently expand the approved endpoint scope.

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
- validation that the registered endpoint host and every returned locator/final locator remain within the source's registered domain allowlist (exact domains and their subdomains);
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

- Implement and test wall-clock deadlines, actual network response-byte limits, and bounded retry behavior inside a reviewed transport boundary before any live connector exists. The current timeout/retry fields are bounded declarations, not a claim that fixture connectors enforce network timeouts or retries.
- Add stronger URI and redirect-scope validation for a future network adapter, including DNS resolution and private/link-local IP protections. The fixture boundary now rejects locators and final locators outside the source's registered domain allowlist.
- Expand adversarial tests for inactive sources/endpoints, expired grants, invalid metadata, oversized result batches, malformed domain allowlists, and sink transaction rollback. The current rate-limit and result-byte-budget tests cover the orchestrator boundary, not raw HTTP response bodies.
- CI run #128 passed after adding regression tests for out-of-scope locator hosts, out-of-scope final locators, permitted subdomains, and endpoint-host scope.
- Review the collection module and observation storage together before marking this section complete.

No live connector, publisher approval, article-body storage, or production collection has been enabled.
