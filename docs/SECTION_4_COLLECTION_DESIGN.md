# Section 4: Permission-First Collection Design

Status: **complete for the fixture-first collection framework**; live publisher adapters remain separately gated  
Scope: fixture-first collection framework in the new engine only

## 1. Non-negotiable boundary

A registered, trusted connector is invoked only after the engine evaluates a current permission grant for the exact source, endpoint, and requested operation. Public accessibility, a robots.txt rule, an RSS URL, or a previously allowed operation is not a substitute for a grant.

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

The orchestrator passes only the requested operation, its explicit budget, and an immutable execution context containing the exact normalized domain allowlist registered for that source. A connector must pass this context allowlist unchanged to the vetted transport. The connector interface does not expose an unrestricted shared HTTP client or direct access to the database. **This is not a Python sandbox:** arbitrary in-process connector code can import networking libraries and bypass the approved transport. Only reviewed, trusted built-in connectors may be registered. If third-party or untrusted connector code is ever supported, it must run behind an operating-system/container sandbox and enforced network-egress policy. The current fixture connector is trusted test code only.

The standalone HTTPS transport prototype now uses dnspython to resolve both A and AAAA records under one shared DNS deadline, rejects every non-public answer, and pins the socket connection to a validated address while retaining the original hostname for TLS certificate verification. It rejects non-HTTPS URLs, credentials, non-standard ports, malformed hosts, out-of-scope redirect destinations, compressed responses, and oversized response bodies. It streams under a request deadline, caps redirects and retries, and uses a fixed user-agent. It is not registered with the fixture orchestrator and no live publisher connector is enabled. Transport response policy now caps header count and aggregate header bytes, rejects conflicting or ambiguous length headers, and allowlists XML, RSS, Atom, HTML, XHTML, and JSON media types. DNS/TLS/redirect integration review and remaining adversarial tests are still required.

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
- an authorized connector receives only the exact registered source-domain allowlist;
- transport failure creates no partial observation;
- permission revoked after transport returns is rejected by the storage-time grant recheck;
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

The current implementation is in `src/sngso_engine/collection.py` and the SQLite observation adapter in `src/sngso_engine/registry.py`. The connector call receives a frozen `CollectionExecutionContext` with the source domain allowlist; the orchestrator creates it only after the permission recheck immediately before connector invocation.

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

## 10. Acceptance and closure

**Section 4 is complete for the fixture-first collection framework.** Its acceptance is deliberately scoped to trusted, in-process connectors and offline fixture execution. It does not claim that Python can sandbox arbitrary plugin code, nor does it authorize a production publisher adapter.

| Acceptance check | Evidence |
|---|---|
| Denied permission causes zero connector and transport calls | Permission-first integration fixtures |
| Exact source, endpoint, operation and connector capability are checked | Orchestrator and negative-scope tests |
| Permission is rechecked immediately before connector invocation and again at storage | Grant revision checks and revocation tests |
| Cross-connection revocation/storage order is serialized correctly | Two deterministic SQLite concurrency tests |
| Observation provenance includes source, endpoint, grant/revision, operation, connector/version, timestamp, HTTP method, locator/final locator, fingerprint, response metadata and decision reason | Collection model, schema migration 3, persistence tests |
| Locator and source-domain scope are checked at both orchestration and SQLite boundaries | Shared DNS/URI policy and bypass regression tests |
| Output, item count, metadata, retries, timeouts and response bytes are bounded | Request validation and adversarial fixtures |
| Batch persistence is atomic and append-only | Rollback and immutable-row tests |
| DNS answers, HTTPS URLs, redirects, TLS hostname verification, headers, transfer coding, content type, response size and retry budgets are constrained | Offline transport tests |
| CI does not depend on publisher availability | Fixture-only GitHub Actions suite |

GitHub Actions run [#44604665149](https://github.com/Amiroudro/new-sngso/actions/runs/44604665149) passed with **264 tests** on code/schema head `49e4896558c60974ba9cf7202a33f8d8eb7e781e`, including schema migration 3 and HTTP-method provenance. The current PR head adds a roadmap-only clarification of the trusted-connector boundary. The suite is fixture-only and does not contact publisher endpoints.

### Explicit operational limits

- The standalone HTTPS transport is tested but remains unregistered in the orchestrator. No live publisher network requests are made.
- The in-memory per-domain rate limiter is process-local. A multi-worker production deployment needs a shared limiter before live collection.
- Connector time budgets are part of the connector contract; Python cannot forcibly stop arbitrary in-process code. The vetted HTTPS transport enforces its own request/DNS/socket deadlines.
- The engine does not authenticate human rights reviewers. A real source still requires documented, human-reviewed permission and a separate approval process.
- A local audit hash chain cannot detect tail deletion without an independently stored checkpoint.
- No paywall bypass, full-text storage, translation, image collection, republication, or production publishing is enabled. Those operations remain denied or unimplemented.

These are explicit production gates, not unfinished fixture-framework acceptance items. Section 4 is closed at its stated scope. Live adapters remain gated on source-specific rights review, trusted connector review, shared rate limiting, and deployment-level egress controls.
