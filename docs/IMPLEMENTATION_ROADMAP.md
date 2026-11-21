undefined

### Section 7.50 implementation update

Added a direct regression that validates the staging builder's generated provenance manifest with the shared Section 7.49 JSON Schema. Tightened source-permission consistency so every included article is covered by a current registered endpoint grant and every manifest endpoint must actually cover at least one included article; unused but otherwise valid endpoints are rejected. The change is staging-only and does not grant publication authority. GitHub Actions run [#44878923923](https://github.com/Amiroudro/new-sngso/actions/runs/44878923923) passed the full Python 3.12 suite: **750 passed in 8.85 seconds** on code head `02b6fccc395241d43b354b3ec791780b32ea4edc`.
