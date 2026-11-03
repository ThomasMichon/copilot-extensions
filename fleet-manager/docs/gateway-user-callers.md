# Explicit user bearer callers

The optional `user-bearer-v1` SDK profile verifies original issuer-issued RS256
JWTs for **callers only**. It closes the Gateway vision's
[human session](../../visions/machine-fleet/gateway/README.md#human-sessions-and-machine-enrollment)
source prerequisite without requiring a separately enrolled browser signing key.
It is not a provider login implementation, cookie/session adapter, service
identity, machine enrollment, new issuer or fleet-administration grant.

## Pure public trust and authenticated policy

Register `user_identity_provider=IdentityProvider(name, prepare)` independently
of the existing service `identity_provider`, on `ServiceAuthority`, `build_app`
or `start`. The existing pure `prepare(configuration_bytes) -> IdentityTrust`
contract returns only `IdentityTrust(issuer, audience, jwks_bytes,
trust_valid_until)`. It cannot return identities, verifiers or permits.
No parsing, verification or preparation performs discovery, login, network I/O
or credential acquisition. Authenticated snapshot acquisition, publication and
revocation freshness belong to the embedding owner.

`jwks_bytes` is canonical public RSA JWKS JSON, copied into the verifier's
private snapshot. One through 16 unique bounded `kid` keys are allowed; each
has `kty`, `n`, `e`, `kid`, optional `alg: RS256`, `use: sig`,
`key_ops: ["verify"]`, and optional public `x5t`/`x5t#S256`. RSA moduli are
2048–4096 bits and exponents are odd, at least 65537 and below `2**32`.
Private parameters, certificate chains, remote key URLs and other fields refuse.
The trust adapter may select these public JWK fields from its authenticated
snapshot; it must not transform or re-sign the actual token.

Complete `fleet-manager.gateway-service-policy` **version 5** has exactly:

```json
{
  "schema": "fleet-manager.gateway-service-policy",
  "version": 5,
  "manifest": {
    "schema": "fleet-manager.gateway-manifest",
    "version": 2,
    "fleet": "example",
    "gateway": "gateway",
    "revision": 1,
    "roles": ["control"],
    "key_sources": [],
    "slots": [{
      "name": "local-control",
      "role": "control",
      "delivery": "local",
      "routes": [{
        "prefix": "/control",
        "methods": ["POST"],
        "capabilities": ["http"]
      }]
    }]
  },
  "service": null,
  "user_callers": {
    "identity": {
      "provider": "example-user-provider",
      "profile": "user-bearer-v1",
      "configuration": {"snapshot": "selected"}
    },
    "principal_claim": "user_id",
    "required_claims": [
      {"claim": "realm", "values": ["selected-realm"], "shape": "scalar"},
      {"claim": "roles", "values": ["gateway-user"], "shape": "list"}
    ],
    "mappings": [{"subject": "issuer-owned-user-id", "principal": "operator-a"}],
    "callers": [{
      "principal": "operator-a",
      "slots": ["local-control"],
      "methods": ["POST"]
    }]
  }
}
```

Local-only composition needs **no service identity, proof-key binding or
enrollment**. Register the parsed local `ServiceSlot` through the existing
`LocalHandlerBinding(slot, handler)`; its `principal` and `key_source` are `None`.
Configuration cannot load a handler or executable. An unregistered local slot
is unavailable; it cannot accept an outbound connector. Existing HTTP-only,
per-slot concurrency, request lifetime and exact-current-binding checks remain.

For remote connectors, `service` instead contains a complete unchanged version
1–4 service-policy document. Its manifest remains version 1. Its fleet, Gateway,
revision, key sources and complete remote slots must match the outer version 2
manifest. The latter can additionally declare local slots. Existing remote
principal/key-source assignments, crypto, schemas, enrollment and service
admission remain intact; no remote service profile is inferred from a bearer.
User callers may explicitly reach declared remote routes, but authenticating
their connector remains independent.

All required-claim entries must pass. A signed scalar string must equal one
listed value; a signed unique string list must contain at least one listed
value. An optional `shape: "scalar"` or `shape: "list"` first requires that exact
signed JSON claim type, without coercion or rewriting. Other shape values,
including null and booleans, refuse. The SDK equivalent is
`ClaimRequirement(claim, values, shape="scalar")` or `shape="list"`.
Omitting the field (`shape=None` in the SDK) preserves the original either-shape
matching and normalized policy/digests, with no added default field.
Changing an explicit shape is a material user trust-policy change and
invalidates retained user handles and bound operation permits.
Publish explicit shapes only after selecting a shape-capable SDK owner;
earlier version-5 parsers reject this unknown requirement field. Do not remove
a required shape to work around that refusal.
The policy has at most 16 distinct claim requirements, each with 1–64
distinct values; consumed strings are nonempty visible ASCII, at most 256
characters, and token lists have the same 64-item bound. Nested/boolean/null
values refuse. Claim names use `[A-Za-z][A-Za-z0-9_.:-]{0,63}`.
The selected principal claim must itself be a scalar string. At most 128
literal, unambiguous subject mappings select existing canonical caller
principals. No username/`sub`/role fallback or token-supplied principal is used.
The policy can select an issuer-owned stable user ID and independently require
realm and application-role claims without copying or rewriting token claims.

Caller entries have only `principal`, `slots`, `methods` (at most 128 unique
principals). User and proof-caller principals cannot overlap. Namespace/route
grants are separate from signature and claim checks; mappings create no
operation grants. Empty mappings withdraw all users. Collection ordering
normalizes; material user trust, requirements or mapping changes replace only
the user verifier and invalidate its retained handles. Remote verifier
nonces/replay fences and active service handles survive user-only changes.
Each identity mode retains its own current trust expiry.
Invalid candidates retain the prior bounded publication, never extending expiry.
A combined policy can still publish revocations while either configured mode
remains current; an expired mode cannot veto the still-current mode's policy
withdrawal. Expired identities and handles remain denied independently.

## Request and operation boundary

Send exactly one explicit `Authorization` header using the Bearer scheme.
The scheme is case-insensitive; original token bytes are never case-normalized.
Only that scheme selects user verification; a failed DPoP or machine proof
never retries as bearer. The original compact token is bounded to 8192 bytes,
has `alg: RS256`, a bounded `kid`, optional `typ: JWT`, and optional bounded
public `x5t`/`x5t#S256` metadata. Token-supplied JWK, `jku`, `x5u`, critical
extensions, unsupported algorithms, proof-bound `cnf`, malformed claims,
unknown keys and invalid signatures refuse. **Every request verifies a real
RSA signature with maintained `joserfc`**, exact configured issuer/audience
(a string, not an array), required `iat`/`exp`, optional `nbf`, current trust,
claim constraints and literal user mapping. Expiry is strict; future issuance
and not-before dates have the existing bounded 30-second clock skew.

Server-authenticated TLS, canonical Host/external origin, protected private
origin and existing transport limits are prerequisites. Cookies, DPoP/machine/
enrollment proofs and identity-forwarding headers cannot be mixed into bearer
requests. No cookie, forwarded-user or trusted-proxy identity fallback exists.
Cross-origin `Origin` and cross-site `Sec-Fetch-Site` requests refuse; there is
no credentialed CORS or ambient-cookie authentication. An application adding
browser cookie sessions later must supply its own reviewed request-forgery
boundary; this profile does not silently implement one.

**Bearer is replayable until expiry/revocation. TLS protects transport; it is
not proof of possession or replay-proof request signing.** Verification event
evidence is fresh per request, so one bearer can legitimately make multiple
requests. No nonce/replay fence or proof key is asserted for it.

`BearerIdentityVerifier(..., principal_claim=..., required_claims=tuple(
ClaimRequirement(...)), mappings=tuple(PrincipalMapping(...)))` exposes
`verify(token, *, method, target_uri, kind="caller")` to trusted SDK embeddings.
Any other kind refuses. Its frozen `BearerIdentity` carries the verified
issuer, **canonical mapped** `subject`, audience, issue/expiry/verification
times, value-free event evidence and profile; it carries neither raw token/
claim payload nor a pretend proof key, and is not `VerifiedIdentity`.
These internal records are not a wire authentication interface.

`ServiceAuthority.authenticate_user_caller(token, *, method, target_uri, slot,
request_method)` issues the same retained `CallerGrant` used by normal
Gateway routing. `LocalServiceRequest.authorize_operation` then passes that
original typed identity and current route/request checkpoints into the existing
`AdmissionAuthority`. The independently authenticated `AuthoritySnapshot` must
explicitly select `identity_profile="user-bearer-v1"` and the same issuer/audience.
Its `Enrollment.subject` is the canonical mapped caller principal, not the
issuer's raw user ID. Explicit current actor/account/role/installation and exact
operation grants remain required. TLS, a signature, application role or
code-registered local handler alone cannot grant an operation.
Policy removal/revocation/profile changes and expired trust/identity invalidate
admission and subsequent permit checkpoints. One internal verification event
still cannot authorize multiple operations.

## Compatibility, validation and remaining gates

This is SDK-only opt-in expansion. Existing service-policy versions 1–4,
manifest version 1, default profiles and CLI provider registration are unchanged.
Deploy a capable SDK owner before publishing version 5; older owners reject it
as unsupported rather than downgrade. `user_profile()` reports the configured
capability of the actual authority, not a package-version guess. Rollback
requires an explicit older complete policy and its deployment owner; never
reinterpret user bearer input as an older proof profile.

Focused source coverage is `tests/test_bearer_identity.py` and
`tests/test_gateway_user_callers.py`, alongside the existing identity, manifest,
service, local-handler and admission regressions. The real TLS loopback test
exercises Gateway → local handler → current `AdmissionAuthority` → permit,
including repeat requests, mixed authentication and current policy denial.
All issuer keys/tokens/policies in these tests are synthetic. They do **not**
qualify real-provider login, clean-room installation, issuance, renewal,
revocation propagation, authenticated policy publication or deployment.
Those are separate owner gates; source merge is not release, installation,
activation or end-user journey completion.
