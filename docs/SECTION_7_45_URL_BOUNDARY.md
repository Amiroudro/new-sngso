undefined

## Section 7.50: producer-schema and least-privilege endpoint consistency

The Section 7.43 staging builder already routes its generated provenance manifest through the staging verifier, which applies the shared Section 7.49 schema. A dedicated producer regression now also validates the builder's returned manifest directly with `validate_payload("flutter-staging-provenance", ...)`. This protects the producer from drifting away from the versioned contract even if verifier internals are later refactored.

The verifier now checks permission endpoint use at the source/article level. Every included article URL must be covered by at least one registered, currently permitted endpoint for its source, and every permission binding must identify an endpoint that actually covers at least one included article. Extra valid-but-irrelevant endpoints are rejected. The existing exact source coverage, endpoint/grant source matching, and current grant evaluation remain in force.

Regression coverage verifies the builder-produced manifest against the shared schema and rejects an otherwise registered republication endpoint whose path covers none of the included articles. This remains a staging-only check. It does not establish the publisher's real-world authority, Flutter runtime compatibility, or publication authorization. Full-suite CI for the Section 7.50 head is pending.
