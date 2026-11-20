# Section 7.16: privileged principal lifecycle and recovery

## Role boundary

The registry now recognizes a dedicated `benchmark_security_admin` role.
The authenticated application facade exposes principal provisioning,
revocation, and reinstatement only to an active principal with that role.
Ordinary curators, reviewers, and adjudicators cannot use these methods.

Administrative actions derive the actor from the verified identity provider,
require the exact issuer/subject-to-principal mapping, and record the actor
and a non-empty reason in the registry audit chain. The acting administrator
cannot provision, revoke, or reinstate their own principal through the
application facade. This prevents self-promotion and self-recovery through
the ordinary application path.

Provisioning validates the role allowlist, principal classification, and
Bangla/English reviewer language qualifications. Revocation disables access
without deleting historical reviews, assignments, attestations, or audit
records. Reinstatement is an explicit operation for an existing inactive
principal; it does not silently restore or change that principal's roles or
language qualifications.

## First-administrator bootstrap

The first security administrator cannot be created by an already-authenticated
security administrator because none exists yet. Bootstrap is therefore an
out-of-band deployment operation performed by an authorized infrastructure
operator against the registry before the application is exposed. The
operator must verify the administrator's identity through the organization's
identity provider, record the change ticket and operator identity, and
provision the exact immutable provider issuer/subject mapping at the same
time.

The application must never expose the raw registry provisioning methods to
HTTP handlers, user scripts, notebook sessions, or ordinary application
workers. Only the authenticated facade belongs in the request-serving
dependency graph. The deployment's bootstrap procedure and database access
controls are part of the trusted computing base.

## Recovery and incident response

- Revoke a compromised principal immediately through the security-admin
  facade and revoke its identity-provider sessions independently.
- Preserve immutable review and audit history; revocation changes access,
  not historical authorship.
- Reinstate only after the identity provider account, subject mapping,
  authentication factors, and reviewer qualifications have been rechecked.
- Record a reason for every provision, revoke, and reinstate action.
- Keep at least two independently controlled security administrators in
  production to reduce lockout risk. This is an operational recommendation,
  not an automatic guarantee enforced by the local SQLite database.
- Back up the SQLite database securely, encrypt backups at rest, restrict
  restore permissions, and test restoration into an isolated environment.
- Treat a database restore as a security-sensitive event; reconcile provider
  revocations and principal status before reopening the service.

## Limits and remaining production work

The facade and its tests establish authorization behavior inside this Python
application boundary. They do not make direct database or raw registry access
safe. Anyone who can arbitrarily modify the SQLite file or call raw registry
provisioning methods outside the facade is inside the trusted deployment
boundary and must be controlled by operating-system permissions, secret
management, deployment review, and audited operator procedures.

This repository does not implement a hosted administrator portal, hardware
security key enrollment, provider-specific account recovery, encrypted
off-site backups, or a production database access-control service. The first
administrator bootstrap and the OIDC key/revocation infrastructure remain
deployment-specific. Do not claim production readiness until those controls
are configured and tested in the actual hosting environment.
