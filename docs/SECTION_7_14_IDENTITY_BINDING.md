# Section 7.14: trusted identity subject binding

## Security invariant

A provider-authenticated OIDC subject is not interchangeable with an application
principal ID. The exact pair `(issuer, subject)` must be mapped by trusted
deployment configuration to exactly one registry principal. The mapping must
not be derived from request parameters, display names, email addresses, or
unverified token claims.

`AuthenticatedBenchmarkAccess` now requires a `subject_to_principal`
resolver at construction. Every authenticated operation checks the provider's
issuer and audience and then requires the resolver's result to equal the
provider-returned principal ID. Missing subjects, unmapped subjects, and
subject/principal substitutions fail closed before registry mutation.

The resolver is deliberately injected rather than implemented as an implicit
email lookup. A deployment can back it with a reviewed, version-controlled
mapping or a protected identity-link table managed through an authenticated
administrator workflow. Mapping changes must be access-controlled, audited,
and reviewed for account-recovery and deprovisioning consequences.

## Deployment contract

- Configure an exact issuer and audience. Do not accept either from a client.
- Resolve the exact issuer/subject pair to a pre-provisioned registry principal.
- Never auto-link accounts by email, username, or display name.
- Do not allow a subject to be rebound to another principal without a separately
  authenticated, audited account-linking procedure.
- Keep principal revocation effective independently of provider login validity.
- Fail closed when the provider subject is absent, the mapping is missing, or
  the mapping conflicts with the provider adapter's principal identifier.

## Verification boundary

This section closes the subject-to-principal substitution gap in the
application facade. It does **not** implement OIDC token signature verification,
JWKS retrieval/key rotation, provider logout/revocation introspection, or
administrator provisioning. Those remain deployment infrastructure work.
The identity provider adapter must perform cryptographic token/session
verification before returning `AuthenticatedIdentity`; the subject resolver
must be a trusted deployment component. Unit tests with a fake provider prove
the facade's checks, not real-world identity.

## Regression coverage

- A valid configured issuer/subject pair maps to the expected principal.
- A different subject cannot submit a review as the same registry principal.
- A missing subject is rejected before artifact registration.
- Existing issuer, audience, expiry, role, reviewer, adjudicator, and assignment
  authorization checks remain in the same facade.
