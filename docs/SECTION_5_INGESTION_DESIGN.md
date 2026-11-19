# Section 5: Ingestion, normalization, and identity

Status: **complete** for metadata-only, fixture-first ingestion; no live publisher access is enabled.

## Boundary

Section 4 stores permission-approved collection observations. Section 5 turns only stored metadata observations into article records. It does not make network requests, fetch page bodies, store excerpts/full text, translate content, or create publication output.

Identity remains layered:

1. **Collection observation:** one immutable record of what a permitted connector observed at a particular time.
2. **Article:** one publisher-specific bibliographic identity, keyed deterministically by source ID and canonical article URL.
3. **Story:** a separate evolving editorial event identity. Section 5 does not create, merge, or assign stories. That belongs to the later story-intelligence and editorial workflow.

A source observation is never deleted when an article is created. The ingestion decision links the two.

## Normalization rules

- URLs must be absolute HTTP(S), use a valid DNS hostname, contain no credentials, fragment, control characters, backslashes, malformed percent escapes, or noncanonical explicit port spelling.
- Hostnames are converted to lowercase IDNA ASCII. Default ports are removed. Dot segments in paths are normalized, while path case and unknown query parameters are preserved.
- Only known tracking parameters (utm_*, fbclid, gclid, and a short explicit list) are removed. Unknown parameters are retained because publishers may use them as content identifiers.
- Query keys are sorted deterministically. Repeated values for the same key retain their relative order.
- Text is NFC-normalized and whitespace is collapsed. The original locator and metadata remain in the immutable collection observation.
- Timestamps must include an explicit timezone and are stored in UTC. Naive local timestamps are rejected rather than guessed.
- Language tags are normalized for casing and must be compatible with the registered endpoint's declared language set.
- Article title is required. Byline, publication timestamp, and summary metadata are optional.

## Article identity and duplicate candidates

The article ID is a deterministic SHA-256-derived identifier over (source_id, canonical_uri). Repeated observations of the same source URL reuse the existing article. A changed tracking parameter does not create another article. Later observations of the same URL refresh mutable metadata only when their observation timestamp is newer; each refresh creates an article revision and audit event. Candidate matching uses only the latest current fingerprint for each article, not stale earlier headlines. Older or equal-time observations remain linked as provenance without overwriting newer metadata.

A separate duplicate fingerprint is calculated only when both title and publication timestamp are present. It uses normalized title, normalized language, and normalized UTC publication timestamp. A cross-publisher match creates a **possible duplicate** decision, not a merge. Each publisher retains its own article record.

This conservative fingerprint is a candidate-generation signal, not proof of syndication or independent corroboration. It does not compare full article bodies. No story is automatically merged, and a duplicate candidate must not be counted as independent evidence until reviewed.

## Decisions, idempotency, and reversibility

- Each stored observation can have one current ingestion decision.
- Reprocessing the same observation returns its current decision and creates no new article or audit event.
- New decisions and revisions are append-only. A separate current-pointer table identifies the latest decision without rewriting history.
- Duplicate resolution is explicit: confirmed_duplicate or distinct, with actor and reason.
- A later review can reverse a previous decision by appending another revision. Neither article is deleted or merged.
- The database checks that decision source, observation, article, and duplicate target references agree.
- Article creation, decision insertion, current-pointer update, and audit append occur in one SQLite transaction.
- Ingestion rechecks that the original metadata grant is still approved, unchanged, in scope, and supported by verified evidence. A revoked or revised grant blocks new ingestion.

## Known limits

- The duplicate fingerprint is intentionally conservative and may miss paraphrased or differently dated syndicated articles. It may also flag same-title, same-time articles that are not the same report. Human review is required.
- A missing publication timestamp means no cross-publisher duplicate candidate is generated.
- No raw article body is fetched or stored.
- No automatic story assignment, event clustering, claim extraction, or corroboration scoring is implemented here.
- SQLite remains the local/low-volume adapter. The current implementation does not claim multi-process production throughput.

## Acceptance evidence

Section 5 acceptance was verified by GitHub Actions run [#44607783960](https://github.com/Amiroudro/new-sngso/actions/runs/44607783960), which passed with **297 tests** on code head 5ae0a5f9ca624f14d9350b6d039ef7abe86690b7. Later changes on the current PR head are documentation-only.

The verified acceptance checks were:

- canonical URL, text, timestamp, and language normalization, including malformed input rejection;
- original observation values remain unchanged;
- same-observation idempotency and same-source URL reuse;
- separate article identities across publishers;
- explainable duplicate candidates with reversible, append-only human resolutions;
- permission revocation/revision blocks ingestion;
- atomic persistence, schema migration, and audit-chain integrity.


## Language declaration matching

Language matching is shared by the ingestion entry point and the SQLite persistence boundary. A primary-language declaration such as `en` permits regional variants such as `en-US` and `en-GB`. A regional declaration such as `en-US` permits only that normalized regional tag, not `en-GB` or the less-specific `en`. Invalid endpoint language declarations fail closed. This prevents one layer from accepting an observation that the next layer rejects.


## Verification record

The complete Python 3.12 fixture suite passed in GitHub Actions [run #44607997206](https://github.com/Amiroudro/new-sngso/actions/runs/44607997206): **311 passed in 1.72 seconds**. This run includes the shared endpoint-language policy regression tests. Section 5 is closed only for the metadata-only scope described above; it does not authorize story assignment, full-text collection, translation, or publication.
