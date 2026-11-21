# Section 7.18: operational security review and Section 7 acceptance gate

## Review conclusion

**Status: NOT ACCEPTED FOR PRODUCTION.**

The repository now contains tested application and registry controls for benchmark artifacts, reviews, assignments, identity attestations, adjudication, and principal lifecycle. Passing automated tests establishes behavior for the tested code paths and fixtures. It does not establish that a deployed service, identity provider, database, or human review operation is secure.

This gate deliberately distinguishes repository implementation from deployment evidence. No production identity provider, JWKS endpoint, revocation service, real administrator bootstrap, production database boundary, or genuine independently reviewed Bangla/English benchmark corpus is configured by this repository.

## Threat model and controls reviewed

| Threat | Repository control | Remaining deployment proof |
|---|---|---|
| Request forges actor identity | Authenticated facade derives actor from provider identity and checks issuer/subject mapping | Verify every production handler uses the facade and cannot reach raw registry mutation |
| Forged or downgraded OIDC token | Explicit asymmetric algorithm allowlist; signature, issuer, audience, claims and authentication context checks | Configure trusted issuer metadata, pinned/rotated keys, secure token extraction, and real-provider integration tests |
| Stolen or revoked session | Injected revocation check; fail-closed unknown status; short authentication freshness for review submission | Operate and monitor a reliable revocation source; test outage and propagation behavior |
| Unauthorized role escalation | Registry role checks; dedicated security-admin role; facade restrictions; self-operation prevention | Secure first-admin bootstrap, operator access, account recovery, and separation of duties |
| Reviewer qualification or language mismatch | Active role and language checks at assignment and submission | Verify qualifications and language competence through a documented human onboarding process |
| Review altered after decision | Immutable artifact, append-only review history, review freeze after final adjudication | Restrict database file and host access; monitor privileged database operations |
| Permission expires after approval | Current-use checks re-evaluate source grants and endpoint/operation scope | Confirm permission evidence and grants are legally and operationally valid for the actual use |
| Adjudication is a rubber stamp | Exact review binding, independent adjudicator role, issue-level resolution ledger, evidence and rationale | Human audit of high-severity decisions and periodic sampling for omitted disagreements |
| Data loss or hostile restore | Append-only records and documented backup guidance | Encrypted off-site backups, restricted restore authority, tested recovery, and post-restore revocation reconciliation |
| Synthetic data mistaken for human quality | Principal classification and provider-backed attestations; readiness report does not grant production approval | Genuine independent Bangla and English reviewers, documented samples, and external quality audit |

## Mandatory production acceptance evidence

The following items are release blockers. A checked box must mean evidence exists outside a test fixture, not merely that a class or interface exists in the repository.

- [ ] A real OIDC issuer is selected and configured with fixed issuer and audience values.
- [ ] The signing-key resolver validates trusted issuer keys, handles rotation, rejects unknown keys, and has monitored failure behavior.
- [ ] Token extraction and session revocation are integrated with the actual provider. Revocation outages fail closed and are observable.
- [ ] The provider subject-to-principal mapping is provisioned through a protected, audited process. Email and display names are not identity keys.
- [ ] First security-administrator bootstrap is completed out of band, independently verified, and documented. At least two independently controlled administrators are available.
- [ ] Production request handlers expose only the authenticated facade. Raw registry methods and database access are not reachable by untrusted request paths.
- [ ] Host and database permissions, secret handling, deployment configuration, and administrative access are independently reviewed.
- [ ] Encrypted backups and restore drills are demonstrated. A restore procedure reconciles provider revocations and administrator state before service reopening.
- [ ] Source permission evidence is reviewed for each publisher, endpoint, and operation used by the benchmark workflow.
- [ ] Qualified, independent human reviewers complete real Bangla and English benchmark reviews. Synthetic and automated principals are excluded.
- [ ] Adjudicators resolve material disagreements with evidence. High-severity decisions receive an independent audit.
- [ ] A release owner reviews the threat model, test results, operational evidence, known limitations, and incident-response procedure and signs the acceptance record.

## Safe operating rules before acceptance

1. Keep benchmark data in development or controlled evaluation environments only.
2. Do not label synthetic fixtures or administratively classified principals as genuine human review.
3. Do not treat `ready_for_adjudication` as approval or production readiness.
4. Do not expose raw SQLite registry access through a web handler, notebook, or user-controlled process.
5. Do not publish or reuse benchmark material unless current source permission and the exact artifact/review policy both pass.
6. Preserve failed CI runs and security findings in the release record; do not replace them with only the latest passing run.
7. Re-run the full test suite and deployment-specific security tests after every security-sensitive change.

## Acceptance decision

Section 7 is **implementation-complete for the repository controls described in Sections 7.1–7.17, but not production-accepted**. The remaining work is operational integration and independent human evidence, not a reason to weaken the gates. Production approval must remain blocked until every mandatory evidence item above is satisfied and reviewed by an authorized release owner.

This document is a review checklist and explicit go/no-go decision. It is not a penetration-test report, legal opinion, certification, or proof that an external deployment has implemented the controls.
