# Source Governance and Permission Review Policy

Status: operational policy draft; no publisher is approved by this document.  
Applies to: the new Sarangsho Engine only.

## 1. Purpose and governing rule

Collection is allowed only when the exact operation, publisher/source, and endpoint are covered by a current, reviewed permission grant. Public accessibility, an RSS feed, an API key, a robots.txt allowance, or the absence of a login wall does not by itself establish copyright permission or a right to store, translate, or republish material.

When evidence is absent, unclear, contradictory, expired, withdrawn, or outside its stated scope, deny the affected operation and send the case for review. Do not infer permission from another publisher, domain, endpoint, article, content type, or operation.

This is an internal risk-control policy, not legal advice or a legal determination. Uncertain or high-impact rights questions require review by a suitably qualified person.

## 2. Separate records and identities

- **Source:** the publisher or originating organization. It is identified by a stable source ID and canonical publisher identity.
- **Endpoint:** a specific RSS/Atom feed, API route, sitemap, or web page location. It has its own stable endpoint ID and belongs to one source.
- **Permission evidence:** an immutable reference to the material relied upon, including its URI, capture time, content fingerprint, scope description, and review state.
- **Permission grant:** a time-bounded decision that links one source to explicitly named endpoint IDs and explicitly evaluated operations.

A source record is not a permission. An endpoint record is not a permission. A permission grant for one endpoint must not authorize sibling endpoints or an entire publisher domain unless those endpoints are explicitly named in the grant.

## 3. Operation meanings

Each operation is reviewed independently:

| Operation | Meaning |
|---|---|
| `discovery` | Request or inspect an endpoint to identify potentially relevant records |
| `metadata` | Retrieve and retain permitted bibliographic fields such as title, byline, timestamps, and canonical URL |
| `excerpt_storage` | Retain a source excerpt, quote, or passage |
| `full_text_storage` | Retain the full article or other complete source text |
| `translation` | Translate source material or retain a translated derivative |
| `image_storage` | Download or retain publisher images or other media |
| `republication` | Distribute source material or a derivative beyond internal processing |

Approval for one operation never implies approval for another. In particular, discovery or metadata permission does not authorize full-text retention, translation, image use, or republication.

## 4. Evidence handling

A permission-evidence record must identify the source, evidence type, URI, capture time, fingerprint, and exact scope being assessed. A verified status means a reviewer has checked that the captured evidence is authentic enough for the stated internal decision and has recorded a rationale; it is not a universal legal guarantee.

Reviewers should:
- preserve the original evidence location and capture fingerprint;
- identify the relevant rights holder or authorized grantor where possible;
- check territory, content types, endpoints, operations, attribution, duration, retention, and onward-distribution limits;
- record exclusions and conditions in plain language;
- distinguish publisher terms from an explicit copyright licence or written grant;
- escalate unclear legal language, conflicting terms, or uncertain authority for qualified review.

A terms-of-use page, public-domain assessment, statutory basis, or API agreement must be interpreted in context. Merely recording its URL does not establish that every requested operation is permitted.

## 5. Grant requirements

Every approved grant must:
- identify one source and one or more explicit endpoint IDs;
- reference at least one permission-evidence record;
- contain a decision for all seven operations;
- include operation-level evidence references for every allowed operation;
- have an explicit effective start and end, with end later than start;
- identify the reviewer and review timestamp;
- state the basis and any restrictions in understandable language.

No wildcard endpoint, implicit domain inheritance, indefinite grant, or default-allow behavior is permitted. If a grant has conditions that the engine cannot evaluate, the affected operation remains denied until a typed condition evaluator or human-approved exception process exists.

## 6. Review states and decision roles

Evidence review states:
- `unreviewed`: captured but not assessed; cannot support an approved grant.
- `verified`: reviewed for the specific stated scope.
- `rejected`: unreliable, insufficient, inapplicable, or superseded.
- `needs_legal_review`: requires qualified interpretation; cannot support collection approval until resolved.

Grant states:
- `pending`: awaiting review; deny.
- `approved`: may be evaluated only within the exact scope and effective interval.
- `denied`: deny.
- `suspended`: deny immediately pending review.
- `revoked`: deny immediately and retain the revocation record.

The person recording evidence should not silently approve their own interpretation where independent review is required. A reviewer must be identifiable by a stable internal identity or role. The project must define authorized reviewer roles before production use.

## 7. Expiry, change, and revocation

- Every approved grant has a finite expiry.
- Review before expiry is required for renewal; renewal creates a new grant or a new auditable revision, not an invisible extension.
- A publisher withdrawal, licence change, endpoint change, ownership change, or credible rights complaint triggers immediate suspension of affected operations while reviewed.
- Revocation records include timestamp and reason.
- New collection stops immediately after a grant is suspended, revoked, or expired.
- Existing retained material is quarantined pending a documented retention/deletion decision. Do not silently erase the audit trail or silently republish restricted material.
- A new permission grant does not retroactively authorize earlier collection.

## 8. Enforcement sequence

Before any network request or retention action, a future connector must:
1. resolve the exact source ID;
2. resolve the exact endpoint ID and verify it belongs to that source;
3. load the current grant and referenced evidence records;
4. verify the evidence records exist, belong to the source, and are in an acceptable review state;
5. evaluate the exact requested operation, source ID, endpoint ID, current time, grant status, operation status, evidence, and conditions;
6. deny before the network or storage action if any check fails;
7. write an immutable decision/audit event with the policy version and reason.

The SQLite registry now resolves source and endpoint status, verifies that the grant and evidence records belong to the same source, checks evidence review states, evaluates operation and time scope, and writes an auditable decision event with the policy version and grant revision. It still does not guard connector calls because the permission-first collection service is Section 4 work. Human reviewer authentication and typed condition evaluation are also not implemented.

## 9. Data minimization and retention

Store only the data necessary for the permitted purpose. Full text, excerpts, images, and translated derivatives require their own explicit operation approval and retention rules. If only metadata is authorized, the connector must not fetch or persist full text as a side effect. Keep secrets and private contact details out of evidence notes and public artifacts.

## 10. No pre-approved publisher list

The registry begins empty. No outlet, agency, public institution, or international organization is approved by reputation, public accessibility, or inclusion in a source shortlist. Each proposed source and endpoint requires its own documented evidence and review. Synthetic fixtures are test data only and never represent actual permission.
