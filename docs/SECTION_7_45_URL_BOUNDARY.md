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

The changes are on `feat/section-7-human-benchmark-review-contract`. At the time this subsection was first recorded, no workflow result was available for that then-current head. Subsequent Section 7.49 verification is recorded below.

## Safety boundary

No Flutter app, website, published edition, live schedule, or production feed was changed. PR #7 remains draft and unmerged. The adapter remains staging-only. Real source permission evidence, real human editorial review, and actual Flutter parser/runtime compatibility remain separate outstanding requirements.


## Section 7.46: strict provenance manifest shape

The staging verifier now requires the provenance manifest to contain exactly the supported top-level fields. Unknown fields and missing required fields fail closed, rather than being silently ignored. This prevents callers from attaching unverified claims or control flags to a manifest and having them appear to be part of the accepted contract.

A regression test covers an injected `publication_authorized` extension. The builder still emits the same supported manifest shape. The change is committed on the feature branch; **CI and the full Python suite have not yet reported a result for this exact head**.


## Section 7.47: manifest metadata validation

The staging verifier now validates the manifest payload digest in the engine's canonical `sha256:` plus 64 lowercase hexadecimal characters format and requires a non-empty manifest edition date before comparing it with the registered edition and client payload. Regression tests reject missing, non-string, uppercase, non-hex, and malformed digest values, as well as an empty date. The initial validator incorrectly omitted the canonical `sha256:` prefix returned by the engine fingerprint helper; Section 7.48 corrected the validator to require that prefix plus 64 lowercase hexadecimal characters. GitHub Actions run [#44770649655](https://github.com/Amiroudro/new-sngso/actions/runs/44770649655) passed the full Python 3.12 suite: **734 passed in 7.90 seconds** on the corrected PR head. These are schema-boundary checks, not proof that a real client consumes the sidecar or that the content is publishable.


## Section 7.49: versioned Flutter staging provenance schema

Added `contracts/v1/flutter-staging-provenance.schema.json` as the shared Draft 2020-12 contract for the story-only Flutter staging manifest. The schema pins the version and target client contract, requires the registered edition ID/revision/date and canonical SHA-256 fingerprint, and defines exact nested shapes for story bindings, headline/summary learning-object revisions, and republication permission records. Unknown properties are rejected at every level; revision values must be positive integers; dates and hashes use the shared common definitions.

The staging verifier now runs this contract before its registry-level semantic checks. A separate schema test validates the schema itself, a representative manifest, unknown nested fields, malformed values, unsupported permission operations, and duplicate permission records. A runtime regression test confirms that an injected nested learning-object field is rejected by the verifier.

GitHub Actions run [#44878423840](https://github.com/Amiroudro/new-sngso/actions/runs/44878423840) passed the full Python 3.12 suite: **749 passed in 9.14 seconds** on code head `4c92245abf41aaa656af8fb835530a20e50bae2b`. The schema is an engine-side staging contract only. It does not establish Flutter runtime compatibility, a live source permission grant, or publication authorization.


## Section 7.50: producer-schema and least-privilege endpoint consistency

The Section 7.43 staging builder already routes its generated provenance manifest through the staging verifier, which applies the shared Section 7.49 schema. A dedicated producer regression also validates the builder's returned manifest directly with `validate_payload("flutter-staging-provenance", ...)`. This protects the producer from drifting away from the versioned contract if verifier internals are later refactored.

The verifier checks permission endpoint use at the source/article level. Every included article URL must be covered by at least one registered, currently permitted endpoint for its source, and every permission binding must identify an endpoint that actually covers at least one included article. Extra valid-but-irrelevant endpoints are rejected. Exact source coverage, endpoint/grant source matching, and current grant evaluation remain mandatory.

Regression coverage verifies the builder-produced manifest against the shared schema and rejects an otherwise registered republication endpoint whose path covers none of the included articles. This remains staging-only and does not establish real-world publisher authority, Flutter runtime compatibility, or publication authorization. GitHub Actions run [#44878923923](https://github.com/Amiroudro/new-sngso/actions/runs/44878923923) passed the full Python 3.12 suite: **750 passed in 8.85 seconds** on code head `02b6fccc395241d43b354b3ec791780b32ea4edc`.


## Section 7.51: independently reviewed bilingual variant gate

Added an engine-side verifier for a paired Bangla/English learning object. Both variants must be separately registered and approved, share a story, learning-object kind, non-empty variant group, and exact claim coverage, and independently pass their language-specific registry quality gates. Both language reviews must explicitly pass translation fidelity.

Each reviewer must have a provider-attested identity bound to the approval record and an active registry principal classified as human, holding the benchmark-reviewer role, and qualified for the reviewed language. Independence is checked using the identity provider's exact issuer and subject pair, so two local principal IDs cannot disguise one provider identity.

The verifier returns a staging-only result and always reports `publication_authorized: false`. It checks stored identity-attestation evidence; it does not independently validate provider signatures or prove that the reviewers were genuine, independent people. Real OIDC verification, actual human review, and operational signoff remain outstanding. Full-suite CI for this subsection is pending.
