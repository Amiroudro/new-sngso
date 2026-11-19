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

The orchestrator passes only the requested operation, its explicit budget, and an immutable execution context containing the exact normalized domain allowlist registered for that source. A connector must pass this context allowlist unchanged to the vetted transport. The connector interface must not expose an unrestricted shared HTTP client or direct access to the database.

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

## 10. Remaining Section 4 gates

- A standalone HTTPS transport prototype now enforces a shared deadline across A/AAAA DNS resolution, public-IP-only answers, socket/read deadlines, streamed response-byte ceilings, bounded transient retries, redirect limits, and TLS hostname verification with IP-pinned connections. The fixture orchestrator still uses fixtures only; the transport is not wired into a live connector. GitHub Actions run #154 passed with 170 tests. Run #158 passed with 175 tests after adding response content-type and header-boundary tests.
- Complete remaining adversarial transport tests and review redirect/DNS/TLS behavior before any adapter is connected. The transport now enforces bounded DNS, content-type allowlisting, response-header count/byte limits, and unambiguous content-length handling. The fixture and SQLite boundaries reject locators outside the registered source domains.
- Expand adversarial tests for inactive sources/endpoints, expired grants, invalid metadata, oversized result batches, malformed domain allowlists, and sink transaction rollback. The current rate-limit and result-byte-budget tests cover the orchestrator boundary, not raw HTTP response bodies.
- CI run #128 passed after adding regression tests for out-of-scope locator hosts, out-of-scope final locators, permitted subdomains, and endpoint-host scope. CI run #133 passed after adding a storage-boundary regression test that bypasses the orchestrator and attempts to persist an out-of-scope locator. An intermediate run #132 exposed a misplaced validation reference; the validation was moved into the transaction where source domains are loaded. CI run #136 passed with further tests for inactive sources/endpoints, expired grants, disallowed metadata fields, and atomic observation-batch rollback. The standalone HTTPS transport suite is in `tests/collection/test_safe_transport.py`; run #148 passed with 167 tests after the guarded socket-timeout test-adapter correction.
- Review the collection module and observation storage together before marking this section complete.

No live connector, publisher approval, article-body storage, or production collection has been enabled.


### Transport integration boundary test milestone

A transport-backed fixture connector now exercises the orchestration-to-transport boundary without making network requests. The connector receives an immutable execution context issued by the orchestrator, and the test fake records the exact endpoint URL and registered domain allowlist passed to transport. Regression tests cover:
- denied operation: zero connector calls, zero transport calls, zero observations;
- approved operation: one transport call with the exact registered domain tuple;
- transport exception: no observation and no partial audit write;
- grant revocation after a successful transport response: storage-time revision check rejects the observation.

This is an integration-test harness only. The production HTTPS transport remains unregistered, no publisher network requests are made, and no live connector is enabled. The tests must pass in GitHub Actions before this milestone is considered verified.

Verification: GitHub Actions run #164 passed with **179 tests**. The four added boundary tests cover denied-permission zero-call behavior, exact source-domain propagation, transport failure atomicity, and post-response grant revocation. The public `CollectionExecutionContext` type is exported from the package root.

A subsequent adversarial review found that an injected resolver could bypass the public-IP check even though the default bounded DNS resolver already rejected private answers. The transport now validates every returned address again at the fetch boundary, including custom resolver adapters, and rejects the entire result if any address is private, reserved, malformed, or otherwise non-global. The obsolete unbounded system-DNS helper was removed. GitHub Actions run #170 passed with **181 tests**, including regression coverage for loopback, private, IPv6 unique-local, malformed, and mixed public/private resolver results.


### Additional transport adversarial review

Further boundary checks now reject ambiguous explicit URL ports (including port zero and an empty port), scoped IPv4/IPv6 resolver strings, and duplicate singleton response headers (Content-Type, Content-Encoding, Transfer-Encoding, and Location). This prevents a parser or downstream consumer from silently choosing one of multiple conflicting security-relevant header values.

A dedicated pinned-connection test verifies that the socket connects to the validated IP address while TLS still receives the original DNS hostname for certificate verification and SNI. This is a deterministic unit test of the connection boundary, not a live TLS handshake against a publisher.

GitHub Actions run #175 passed with **188 tests**. The latest run for commit f9cd0227990cbf6a2a02831f8a7077e8bd487fab passed with **189 tests**. These checks remain offline and fixture-based; no publisher endpoints were contacted.


### Request-deadline enforcement review

The remaining request deadline is now reapplied to the connected socket immediately before response headers are read, not only during connection setup and body streaming. This prevents time spent sending the request from leaving response-header reads with a stale, overly generous socket timeout. A deterministic fake-clock regression test advances time during request transmission and verifies that the reduced remaining budget is applied before getresponse(). GitHub Actions run #179 passed with **190 tests**. The change remains fixture-only and does not perform live network requests.


### Canonical source-domain identity

The orchestrator now canonicalizes DNS names to lowercase IDNA ASCII before scope comparison and before using the hostname as the per-domain rate-limit key. This closes a rate-limit identity bypass through case changes, trailing dots, or Unicode/punycode variants. Registered source allowlists must contain unique canonical DNS names; malformed labels and IP-literal entries are rejected. The immutable connector execution context independently enforces the same canonical, unique-domain requirement. GitHub Actions run #183 passed with **197 tests**, and run #184 passed with **200 tests** after adding execution-context rejection cases. No live collection was performed.


### Shared source-domain policy across trust boundaries

Canonical DNS normalization, IDNA handling, allowlist validation, and exact/subdomain matching now live in a shared policy module used by both the collection orchestrator and SQLite registry. This prevents the two boundaries from drifting apart. The registry validates source-domain allowlists at creation and independently revalidates them when persisting observations, even if a caller bypasses the orchestrator. The connector execution context requires an immutable tuple of unique canonical DNS names. The contract schema already rejects malformed trailing-dot entries, while registry checks provide an additional defense against duplicate or otherwise noncanonical records that reach the persistence layer. GitHub Actions run #195 passed with **203 tests**. Earlier intermediate runs caught test expectations that conflicted with the stricter schema; those tests were corrected to assert the schema's actual fail-closed behavior.
