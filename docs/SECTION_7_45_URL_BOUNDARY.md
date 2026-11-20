# Section 7.45: staging URL boundary validation

**Status:** Implemented with regression tests; GitHub Actions contract validation passed on the latest PR head (`7eee3c18`). The full Python test suite has not yet been run on this exact head. This is a defensive improvement to the Section 7.42/7.43 staging-only Flutter adapter, not a publication authorization.

## Change

The adapter now validates endpoint and article URLs before comparing them:

- Only absolute HTTP and HTTPS URLs are accepted.
- URLs with embedded username/password credentials are rejected.
- Fragments are rejected because they do not identify a server-side article resource.
- Registered endpoint URLs may not contain a query string.
- Malformed ports, port zero, and empty/invalid host values are rejected.
- Origins are compared by normalized scheme, hostname, and effective port, rather than raw authority text.
- Hostname comparison is case-insensitive and ignores a trailing DNS dot.
- Article paths must equal the endpoint base path or fall below it on a path-segment boundary. A prefix such as `/newsroom` cannot pass an endpoint rooted at `/news`.

This prevents ambiguous authority parsing and unsafe URL forms from being treated as valid source-endpoint coverage. It does not establish that a publisher owns an endpoint or has granted republication rights; the existing registry grant check remains mandatory.

## Regression coverage

Added cases for valid case-insensitive host matching, path-boundary confusion, lookalike hostnames, scheme mismatch, embedded credentials, fragments, endpoint query strings, malformed ports, port zero, malformed URLs, and invalid host values.

## Verification status

The changes are on `feat/section-7-human-benchmark-review-contract`. The GitHub Actions lookup for the new commits returned no pull-request workflow runs, so **no test pass is claimed for this change yet**. Run the full Python 3.12 suite and contract validation on the exact latest PR head before treating this subsection as verified.

## Safety boundary

No Flutter app, website, published edition, live schedule, or production feed was changed. PR #7 remains draft and unmerged. The adapter remains staging-only. Real source permission evidence, real human editorial review, and actual Flutter parser/runtime compatibility remain separate outstanding requirements.


## Section 7.46: strict provenance manifest shape

The staging verifier now requires the provenance manifest to contain exactly the supported top-level fields. Unknown fields and missing required fields fail closed, rather than being silently ignored. This prevents callers from attaching unverified claims or control flags to a manifest and having them appear to be part of the accepted contract.

A regression test covers an injected `publication_authorized` extension. The builder still emits the same supported manifest shape. The change is committed on the feature branch; **CI and the full Python suite have not yet reported a result for this exact head**.
