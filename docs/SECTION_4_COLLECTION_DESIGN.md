# Section 4: Permission-First Collection Design

Status: design baseline; implementation not yet accepted  
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
