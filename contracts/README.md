# Contract Registry

## Layout

- `v1/` contains the first versioned JSON Schema contracts.
- Every schema uses JSON Schema Draft 2020-12.
- Shared primitives live in `v1/common.schema.json`.
- Source governance uses `source.schema.json`, `source-endpoint.schema.json`, `permission-evidence.schema.json`, and `source-permission-grant.schema.json`.
- `source-permission.schema.json` is a deprecated legacy shape retained for existing fixtures; new grants must use `source-permission-grant`.
- Every entity payload declares `schema_version: "1.0.0"`.
- `timeline-event.schema.json` defines claim-backed event assertions with explicit temporal precision and correction links.\n- `perspective.schema.json` defines attributed publisher framing and stakeholder/expert/editorial viewpoints; publisher framing requires a registered source and article at the registry boundary, and corrections use append-only supersession.\n- `claim-relation.schema.json` defines curated claim-to-claim contradiction, qualification, narrowing, and reframing links. Both claims must belong to the same story; reviewed links require reviewer metadata.

## Compatibility policy

1. Do not change the meaning of an existing field in place.
2. Adding an optional field is normally backward-compatible.
3. Making a field required, narrowing an accepted value, renaming/removing a field, or changing semantics is breaking.
4. Breaking changes require a new major contract line (for example, v2), a migration note, and fixtures for both old and new representations.
5. A patch version may clarify descriptions or fix a schema defect only when accepted payload meaning does not change.
6. Published edition payloads retain the exact contract version and content hash used at publication.
7. Consumers must reject unknown major versions and must not silently coerce invalid data.

## Validation

Run locally:

```bash
python -m pip install -e ".[test]"
python -m pytest -q
```

CI validates schema meta-structure, common-reference resolution through real instances, representative valid records, and selected invalid records.

## Source governance contract rules

- A source record identifies a publisher; it does not grant permission.
- An endpoint is a separately identified feed, API, sitemap, or page and is not automatically authorized by the publisher record.
- A permission grant must name one or more endpoint IDs, a non-empty evidence ID set, an explicit operation decision for every supported operation, and a bounded effective interval.
- An operation marked allowed requires approved status and operation-level evidence IDs.
- Verified permission evidence requires reviewer identity and review timestamp. The registry/persistence layer must later verify that referenced source, endpoint, and evidence records exist and belong together; JSON Schema alone cannot enforce those cross-record relationships.
- The runtime evaluator additionally checks the exact source and endpoint IDs for scoped grants. Connector enforcement before network access remains a later implementation gate.

## Important distinction

A schema proves structural conformance, not factual accuracy, copyright permission, source independence, translation quality, or editorial approval. Those are separate gates in the engine.
