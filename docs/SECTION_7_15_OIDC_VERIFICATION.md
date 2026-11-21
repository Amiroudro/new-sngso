# Section 7.15: cryptographic OIDC verification adapter

## What is implemented

`OIDCIdentityProvider` verifies signed JWTs using PyJWT and an explicit
asymmetric algorithm allowlist. It validates the configured issuer and
audience, token signature, expiry, issued-at time, required claims, subject,
authentication time, session ID, token ID, authentication methods, and
authentication context. Multi-audience tokens must carry the expected
authorized-party claim.

The adapter also requires:
- a trusted token extractor supplied by the application boundary;
- a signing-key resolver supplied by deployment infrastructure;
- a session/token revocation checker;
- an exact issuer-and-subject-to-principal mapping.

A missing key, unknown key ID, invalid signature, unmapped subject, stale
authentication, insufficient authentication context, or unavailable
revocation status fails closed. Only the configured asymmetric algorithms
RS256, PS256, and ES256 are accepted by default. The adapter never accepts a
JWKS URL, issuer, audience, or algorithm from the incoming request or token.

## Deployment requirements that remain

The key resolver is an infrastructure adapter, not an arbitrary URL fetcher.
It must be configured with the issuer's trusted HTTPS JWKS endpoint, validate
key use and algorithm, safely handle redirects and network timeouts, cache
keys, and refresh on unknown key IDs to support legitimate key rotation.
It must not follow attacker-controlled URLs or trust a token's `jku` or
`x5u` header.

The revocation checker must consult a trusted provider logout/session
revocation source and return exactly `False` only when the session/token is
known not to be revoked. Exceptions, unknown status, and `None` deny access.
The token extractor must read credentials from the application's protected
authentication mechanism, never from a user-editable principal field.

The issuer must issue tokens with the configured claims and the required
authentication method/context. The default policy requires `mfa` in `amr`
and a non-empty `acr`; deployments can require an exact `acr` value. The
default freshness window is 15 minutes with 30 seconds of clock tolerance.

## Verification boundary

Automated tests generate real RSA-signed JWTs and exercise signature failure,
unknown key IDs, algorithm rejection, issuer/audience/expiry checks, freshness,
authentication-method/context policy, subject mapping, revocation, multi-
audience authorization, malformed tokens, and key rotation through a
controlled resolver.

This adapter is production-oriented verification code, but it is not a
complete hosted identity service. No issuer, JWKS endpoint, client secret,
provider account, or administrator is configured by this repository. A real
deployment must supply and security-review the key resolver, revocation
checker, token extractor, and subject mapping. Do not call identity production
ready until those components are deployed and operationally tested.
