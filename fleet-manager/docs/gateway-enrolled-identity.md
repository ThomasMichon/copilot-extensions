# Opt-in enrolled application-token authentication

The [service Gateway SDK](gateway-service-profile.md) supports the explicit
`enrolled-pop-v1` profile in complete service-policy **version `3`**. Versions
`1` and `2` retain their existing `jwt-pop-v1` schema, digests, strict string
audience and issuer `cnf.jkt` requirement. No default selects enrollment.
The foreground CLI does not load identity providers; version `3` requires the
same code-registered `IdentityProvider` on `ServiceAuthority`, `build_app` or
`start` as version `2`.

This slice serves the Gateway vision's
[`portable-protected-ingress`](../../visions/machine-fleet/gateway/README.md#portable-protected-ingress)
and `configuration-is-authority-registration-is-observation`: independent
application authentication, enrolled connectors and explicit resource grants
remain separate from edge transport and deployment. It adds no issuer,
installer, database, network discovery, provider dependency or lifecycle owner.

## Complete authenticated policy

Version `3` has exactly `schema`, `version`, `manifest`, `identity`, `bindings`,
`callers` and `enrollment`. The first three policy collections retain the
[service profile's schemas](gateway-service-profile.md#bootstrap-and-coherent-live-policy).
`identity` has exactly:

```json
{
  "provider": "example-provider",
  "profile": "enrolled-pop-v1",
  "configuration": {"snapshot": "selected"}
}
```

The registered pure `prepare(configuration_bytes)` callback returns only
`IdentityTrust(issuer, audience, jwks_bytes, trust_valid_until)`. It receives
canonical bounded configuration, performs no I/O or clock sampling, and cannot
return enrollments, identities or a replacement verifier. The common maintained
`joserfc` engine validates the pinned HTTPS issuer, ES256/P-256 public signing
keys and bounded trust snapshot. Configuration cannot import code or choose a
different verifier.

For example, an embedding owner can register an already authenticated public
snapshot without changing the callback API:

```python
from fleet_manager.gateway_identity_provider import (
    IdentityProvider, IdentityProviderError, IdentityTrust,
)
from fleet_manager.gateway_service_server import start

public_trust = IdentityTrust(
    issuer="https://issuer.example",
    audience="example-gateway",
    jwks=selected_public_jwks_bytes,
    trust_valid_until=selected_trust_expiry,
)

def prepare(configuration_bytes: bytes) -> IdentityTrust:
    if configuration_bytes != b'{"snapshot":"selected"}':
        raise IdentityProviderError()
    return public_trust

provider = IdentityProvider(name="example-provider", prepare=prepare)
serving = await start(
    selected_listener, selected_policy_path, identity_provider=provider,
)
```

The owner supplies the public JWKS bytes, integer trust expiry, listener and
complete policy path. The policy's `identity.provider` must equal the
registered name. This example runs inside the embedding's async lifecycle;
registration alone performs no enrollment, acquisition or serving activation.
Any refreshed public snapshot must still arrive through authenticated
owner-controlled publication, not remote work inside `prepare`.

The separate `enrollment` envelope has exactly `allowed_audiences` and `mappings`:

```json
{
  "allowed_audiences": ["example-gateway", "another-resource"],
  "mappings": [
    {
      "subject": "issuer-application-subject",
      "client_id": "registered-application-client",
      "principal": "service-a",
      "kind": "service",
      "resource": "example-gateway",
      "key_source": "service-a",
      "expires_at": 1900000600
    }
  ]
}
```

All seven mapping fields are required; unknown fields refuse. This example
needs an existing manifest slot with principal/key source `service-a`, that
declared key source's exact resolved public `bindings` entry, and prepared
trust audience `example-gateway`. Caller mappings instead use kind `caller`
and must match an explicit caller principal/key-source grant. There are no
implicit caller grants, cross-principal key aliases or service-to-caller
conversions, even when both principals have the same string.

Each mapping selects the exact issuer `sub` **and** `client_id`, admission kind
and expected resource, then supplies the canonical Fleet principal and current
resolved key source. Issuer selection comes only from prepared trust. Subjects
and client IDs are case-sensitive nonempty visible-ASCII strings of at most
256 characters; principals and key-source names use existing bounded Fleet
identifiers. Expiry is a nonnegative integer epoch second below `2**53`, never
a boolean. Duplicate selection tuples refuse even if their output agrees.
Multiple intentionally distinct clients or subjects require separate explicit
mappings; no subject guessing or `azp`/username/role fallback exists.

The signed token's `client_id` is required and matched literally: it is not
inferred from the provider registration name or from another claim. The
admission kind is the policy mapping's **`kind`**, not `principal_kind`.
Token claims named `principal_kind`, `kind` or `principal` do not select the
canonical principal or grant a kind. `ServiceAuthority` derives the requested
kind from the actual service/caller admission path and requires that exact
mapping; producer-specific labels cannot convert one into the other.

`mappings` allows zero through 128 records. An empty list withdraws all
enrollments. Individual revocation removes the mapping in a new complete
publication; expired mappings cannot authenticate. `allowed_audiences` has
one through 16 distinct nonempty visible-ASCII strings, each at most 512
characters, including the prepared expected resource. Each mapping's `resource`
must equal that expected resource; the allowlist authorizes only additional
token audience advertisements, never admission to a different resource.
The complete document retains the existing 1 MiB, depth-12 and strict
duplicate-JSON/key bounds.

The authenticated embedding source owner alone publishes these complete
bindings. Provider trust is not enrollment. A presented token, its claims,
an attached public proof key or a successful signature **never enrolls itself**.
Source authentication, first-administrator bootstrap, secure publication,
token issuance/renewal, credential custody and key acquisition/revocation
freshness are external prerequisites, not implemented by parsing this JSON.
Use public snapshots and references; never put private proof keys or tokens
in source policy.

## Original token plus independent proof

The server verifies the **original issuer-signed application access token**,
not a rewritten or re-signed token. It requires exactly the existing ES256
`at+jwt` header, pinned issuer, bounded issuer subject, `iat`, `exp` and optional
`nbf` date constraints, and exact `client_id`. The token's `aud` may be a
bounded string or a nonempty unique array of at most 16 bounded strings.
The expected resource must appear and **every** advertised resource must be
explicitly allowlisted. Empty, duplicate, malformed or unapproved audiences
refuse; matching one element is insufficient.

`cnf` may be absent. If present, it must be exactly `{"jkt": ...}` matching the
independently enrolled resolved key thumbprint. Null, malformed and conflicting
bindings refuse; the server never injects a fake `cnf`.

The same common cryptographic engine used by `jwt-pop-v1` verifies the
independent P-256 `dpop+jwt` proof signature, actual HTTP method, canonical
HTTPS target, hash of the exact original token, current server nonce,
timestamp and unique proof ID. Token/proof byte bounds, clock skew, proof age,
atomic replay/capacity bounds and value-free refusal categories are unchanged.
There is no bearer fallback, remote constructor I/O or provider-owned
request-verification callback.

The frozen `EnrolledIdentity` carries canonical subject, expected audience,
proof thumbprint, bounded lifetime, proof evidence, `profile="enrolled-pop-v1"`
and the verified admission `kind`. Identity expiry is the earlier of token
expiry and individual enrollment expiry. Current trust expiry separately
bounds every authority operation. `ServiceAuthority` supplies `service` or
`caller` from the actual admission path, never from a token or request label.
It still requires the actual slot/caller principal, resolved key, method and
current resource grant. Exact retained handles are revalidated before effects
and responses; an authenticated identity alone grants neither.

For a standalone trusted verifier embedding, `EnrolledIdentityVerifier` takes
the common verifier's trust arguments plus immutable `allowed_audiences` and
`enrollments` tuples of frozen `EnrolledBinding` records. Its `verify` additionally
requires explicit `kind="service"` or `"caller"`. This lower-level API does not
establish policy provenance, TLS, source custody or resource authorization;
the service-policy path resolves real key thumbprints and performs those
current-grant checks.

## Explicit RS256 application-token profile

The SDK also supports `enrolled-rs256-pop-v1` in complete service-policy
**version `6`**. This is separate from version `3`'s ES256 profile and from
caller-only `user-bearer-v1`; none is an implicit fallback for another.
Version `5` user-caller composition may embed a version `6` remote `service`
policy without changing its independent caller authentication.

Version `6` has exactly the version `3` root fields plus `token_claims`.
`identity` uses the same code-registered pure public-trust provider but selects
`"profile": "enrolled-rs256-pop-v1"`. The callback still returns only
`IdentityTrust`; its JWKS now contains bounded public RSA signing keys. The
maintained RSA verification path is shared with the bearer verifier, not a
second JOSE implementation. Keys require unique `kid`, 2048-4096-bit modulus,
bounded exponent and supported signing metadata; private keys and key URLs
refuse. No discovery, network refresh or credential acquisition occurs here.

For example, the additional root field is:

```json
{
  "token_claims": {
    "subject_claim": "object_id",
    "client_claim": "app_id",
    "required_claims": [
      {"claim": "roles", "values": ["gateway-client"], "shape": "list"},
      {"claim": "realm", "values": ["realm-a"], "shape": "scalar"}
    ]
  }
}
```

Both selectors and the `required_claims` list are mandatory. Selectors name
exact signed claims, never a request field, provider name or username fallback.
The selected subject and client match the existing enrollment's `subject` and
`client_id` fields respectively; enrollment, kind, expected resource, canonical
principal and exact resolved P-256 key remain independently approved.
Claim names use the existing bounded claim-name syntax. There are at most 16
distinct required claims; each has exactly `claim`, `values` and `shape`.
`values` contains 1-64 distinct visible-ASCII strings of at most 256 characters.
`shape` is exactly `scalar` or `list`, with no coercion. A matching claim must
contain at least one approved value; lists must be nonempty, bounded, unique
string lists. This is an allow-any value constraint, not an all-values or
wildcard expression. An explicitly empty required-claims list adds no such
constraints; it does not waive enrollment or either signature.

The original compact token requires `alg=RS256`, nonempty `kid` and optional
`typ=JWT`. Only optional validated `x5t`/`x5t#S256` metadata is accepted beyond
those headers; unsupported critical headers, embedded keys, key URLs and
other algorithms/types refuse. Exact issuer, allowlisted resource audiences,
required `iat`/`exp`, optional `nbf` and clock fences remain mandatory. A
present `cnf.jkt` must agree with the independently approved key; its absence
does not make the token issuer-bound DPoP. The original bytes are never
rewritten, re-signed or replaced before proof-token-hash verification.

`EnrolledRS256IdentityVerifier` is the lower-level callable, taking the same
arguments as `EnrolledIdentityVerifier` plus `subject_claim`, `client_claim`
and an immutable tuple of `bearer_identity.ClaimRequirement` records.
It returns `EnrolledIdentity` with `profile="enrolled-rs256-pop-v1"` and the
verified admission kind. Downstream `AuthoritySnapshot.identity_profile` must
select that exact profile too; verification still grants no operation permit.
Pure callbacks cannot return identities, verifiers, grants or enrollment.

Before selecting the policy in an independently installed host, inspect that
**executing** SDK's `gateway_service_context.SERVICE_IDENTITY_PROFILES` for
the exact profile and qualify `parse_context` with synthetic public trust and
the host's actual registered provider. Missing declaration or refusal means
unsupported: expand the provider first, retain the previous profile during
the upgrade/rollback window and do not infer support from a package version
or configuration label. No existing host is migrated by this source change.

The normalized signed-claim policy participates in the existing trust epoch
alongside issuer trust and enrollment. Equivalent requirement/value ordering
preserves nonce/replay custody; changed selectors, requirements or bindings
invalidate old handles and delayed results. Actual token issuance, provider
interoperability, protected proof-key provisioning, authenticated publication,
durable revision custody and deployment remain external responsibilities.

## Publication and limits

The trust epoch includes normalized provider/profile/configuration/prepared
trust and the complete normalized enrollment: subject, client, canonical
principal, kind, resource, key-source name/provider/reference, resolved proof
thumbprint, expiry and allowed audiences. Equivalent collection ordering keeps
the verifier, nonce, replay cache and exact current handles. Material mapping
or resolved-key changes replace the verifier and invalidate old handles,
candidates and challenges. Resource-grant changes continue to use the existing
slot/caller fingerprints. Accepted revision rules cover the entire document.

### Atomic key replacement, not overlapping rotation

Each exact subject/client/kind/resource selection resolves to one `key_source`,
and admission also requires the actual slot or caller's single configured key
source for its principal/resource. There is no list of simultaneously accepted
old/new keys for one such mapping and grant. Approved atomic replacement of
the resolved key changes the trust epoch: old keys, handles, pending candidates
and existing channels lose authority before a replacement connector can be
admitted with the new key and a fresh challenge.

The replacement must reconnect and pass normal admission; this is not
dual-key serving overlap, seamless rotation or an availability guarantee.
Coordinating key distribution, publication, channel withdrawal and reconnect
belongs to the external lifecycle owner and requires deployment-specific
qualification. Adding overlapping key acceptance would require a separate
explicit contract, not another JWK or implicit grace period in this schema.

Invalid or partial candidates retain only the prior bounded authority: they
do not publish partial mappings, clear replay state or extend any trust,
identity or enrollment expiry. Enrollment expiry also withdraws an existing
service channel and bounds an already-admitted caller response.
Revision/nonce/replay custody is **process-local**. Durable rollback prevention,
restart revision custody, authenticated refresh and any shared-replica policy
remain with the source/lifecycle owner. A new process has a new nonce epoch;
this slice does not implement a durable enrollment database.

This is a separate application-token-plus-enrolled-key profile, **not a claim
of RFC 9449 issuer-bound access tokens**. It does not assert automatic token,
browser or vendor compatibility. A provider must issue this exact signed
application-token shape, particularly `client_id`, issuer subject, audience,
algorithm and type, and the authenticated publisher must configure its exact
mapping. Synthetic real-crypto and loopback-TLS tests are not provider
qualification, real enrollment or production delivery.
