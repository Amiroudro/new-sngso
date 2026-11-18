# Contract Registry

## Layout

- `v1/` contains the first versioned JSON Schema contracts.
- Every schema uses JSON Schema Draft 2020-12.
- Shared primitives live in `v1/common.schema.json`.
- Every entity payload declares `schema_version: "1.0.0"`.

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
python -m pytest tests/contracts -q
```

CI validates schema meta-structure, common-reference resolution through real instances, representative valid records, and selected invalid records.

## Important distinction

A schema proves structural conformance, not factual accuracy, copyright permission, source independence, translation quality, or editorial approval. Those are separate gates in the engine.
