# Explicit runtime identity verification

`fleet_manager.runtime_identity.IdentityVerifier` implements the first isolated
`jwt-pop-v1` verification prerequisite from the reviewed
[runtime authentication interface](../../efforts/active/fleet-control-v2/runtime-authentication.md).
It is **not enrollment, policy authorization, a Gateway endpoint, provider
adapter, delegation credential or a deployed authentication service**.

Install the explicit `fleet-manager[runtime-authentication]` extra for maintained
`joserfc` JOSE signature, public-key and RFC 7638 thumbprint operations. Ordinary
client commands remain dependency-free and do not import this capability.
Missing support explicitly refuses `unsupported-profile`.

The initial supported profile is deliberately narrow: ES256/P-256 for both
access-token and DPoP signatures, `at+jwt` access-token type, `dpop+jwt` proof
type and one exact configured audience string. Other algorithms, audience arrays,
token types and profiles refuse; this does not advertise compatibility with a
particular identity provider. Issuer selection and provider renewal/enrollment
remain separate. Runtime identity trust is never release-signing trust.

## Trusted caller contract

Construct one serving owner's verifier with an explicit HTTPS issuer, audience,
public JWKS snapshot and `trust_valid_until` epoch. At most 16 uniquely named
public signing keys and 64 KiB of key configuration are accepted. Keys cannot
contain private material, conflicting algorithm/use or non-verification
operations. The caller owns independently authenticated key acquisition,
rotation, currentness and revocation. This module does no discovery, key URL
fetching, account selection, login or network I/O. Unknown keys refuse.
The required trusted `clock` callable returns integer epoch seconds; it is
sampled under the owner's lock, not taken from individual request timestamps.

Call `verify(access_token, proof, method=..., target_uri=...)` only
**inside an already authenticated encrypted server transport boundary**.
The adapter supplies its actual method and canonical external HTTPS target URI
without query or fragment. It must validate
proxy termination and URI normalization itself, not trust forwarded headers.
Passing a URL into this API does not establish TLS. There is no bearer-only,
OS-peer or local-outage fallback.

Verification checks both signatures, issuer, subject, audience, expiry,
issue/not-before times, public-key binding, DPoP method/URI, access-token hash,
proof timestamp, proof ID and server nonce. Unsupported JOSE header extensions
and token-supplied discovery references refuse. JSON keys are unique and input
parsing is bounded before JOSE verification. Access credentials allow 8 KiB;
proofs allow 4 KiB. Clock skew is at most 30 seconds; proof age is at most
60 seconds plus that skew. Expiry is strict: skew never extends token validity.

The immutable `VerifiedIdentity` retains issuer/subject, exact audience, proof-key
thumbprint, token issue/expiry times, verification time and a non-secret proof
digest. It contains no raw credentials, roles or grants. **It is evidence of this
one request, not a transferable bearer authorization object.** Resolve current
enrollment and scoped policy separately, including provider-specific revocation
freshness, before any lookup, delivery or effect.

The subsequent isolated [admission library](runtime-admission.md) supplies
current trusted-snapshot enrollment, correlated read/operation scopes and finite
internal Permit checkpoints. It does not authenticate that snapshot's provenance
or replace the future transport/provider/resource-owner obligations.

## Replay ownership and restart

Expose `nonce` only through the selected protected server challenge flow.
Each verifier instance creates a fresh unpredictable nonce epoch. A replacement
instance rejects proofs from the previous epoch, even if they were never
admitted. Graceful cutover must route each request to its epoch owner or require
a fresh proof; do not copy a nonce to an independent replay cache. A multi-process
or multi-replica shared-epoch deployment is **unsupported** by this primitive.

Admission atomically retains `(proof-key thumbprint, proof ID)` until the proof
is no longer valid, across access-token renewals. The default cap is 4096 live
proofs (a caller may reduce it); full capacity explicitly refuses `resource-limit`
without evicting still-valid entries. A clock rollback after admission refuses.
The owner samples time before verification and again at atomic replay admission,
rechecking trust, token and proof validity there. Request completion order does
not determine clock order: an older concurrent request can finish after a newer
one without a false rollback. A credential that expires during verification
cannot enter the replay/admission fence.
Application retries use a fresh proof and retain the original operation ID and
content binding; this verifier does not allocate operation IDs. DPoP does not
sign arbitrary request bodies.

This is a global single-owner bound, not a claim of fair per-principal quotas or
protection against repeated denied-request CPU load. Resident integration must
add its pre-verification request/rate budgets and isolate principals before
advertising a public service. Failures are value-free `IdentityError.code`
categories; callers must surface them explicitly with safe retry policy.

## Session longevity and remaining boundaries

There is no Gateway human-session age, presence timer, dead-man default or
universal five-minute token cap. Provider-permitted credentials may be renewed
silently for as long as the underlying provider/OS session permits. Proof renewal
and reconnect do not impose interaction. This verifier neither acquires nor
renews credentials, so provider `usable`, `challenge-required`, denial/revocation
and `temporarily-unavailable` outcomes remain an adapter obligation; no provider
interaction or revocation propagation proof is claimed here.

Tests use real synthetic issuer/proof keys, negative signatures and bindings,
atomic replay/capacity checks, many-day token/proof renewal without a presence
timer and a fresh-process nonce-epoch boundary. They do not establish encrypted
Gateway/connector transport, scoped queries, enrollment or a live deployment.
