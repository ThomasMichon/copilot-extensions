# Explicit release authentication

`fleet_manager.release_auth.authenticate_release(manifest_bytes, proof, policy,
*, authority, installation, role, now)` authenticates release declarations
under an explicitly supplied trusted policy. **Current implementation: pinned
Ed25519 keys only.** Selecting GitHub attestation explicitly refuses as not
implemented; it never tries pinned keys instead. Supporting both methods remains
the [reviewed parent plan](../../efforts/active/fleet-control-v2/release-authentication.md),
not a capability delivered by this first cryptographic slice.

This is an API, not an installer, signing tool, key enrollment command or CLI
activation path. The default planner/import paths remain standard-library-only.
It consumes proof from the selected release authority; it does not publish a
parallel provenance identity, rename artifacts or replace promotion attestations.
Product assembly must reuse the existing release lock/artifact identities and
the [artifact-admission boundary](../../visions/plugin-services/runtime-assembly/README.md).
Independent fixture signatures prove this consumer's cryptographic behavior,
not that a production release publisher currently supplies that proof format.
The maintained `cryptography` implementation is imported lazily after selecting
pinned-key verification. Missing support explicitly raises `PlanError`.
The explicit `release-verification` package extra supplies
`cryptography>=46,<47`; development tests require the same range. The lifecycle
owner agreed the optional capability boundary and the operator approved the
exact dependency/API seam before shared metadata changed. Default runtime
dependencies remain empty; installing the extra does not select policy or keys.

## Signed bytes

The manifest is **exact bytes**, not an already-decoded object: at most 1 MiB,
strict duplicate-free JSON satisfying the existing `fleet-manager.release`
schema, equal byte-for-byte to `planning.canonical(manifest)`. This encoding is
UTF-8 JSON with sorted object keys, no insignificant whitespace, ASCII escaping
and Python's existing bounded canonical serializer. All schema-defined numeric
manifest fields are integers. A trailing newline, alternative escaping or a
duplicate key refuses; there is no attempt to reinterpret a signature against
different bytes.

The strict schema-v1 `fleet-manager.release-proof` has `method: pinned-key`,
`payload` and `signature`. The payload is schema-v1
`fleet-manager.release-signature`, with exactly:

- `key_id`: the expected bounded identifier of a configured public key.
- `authority`: bounded release authority, equal to the trusted policy.
- `release_digest`: SHA-256 of the exact selected canonical manifest bytes.
- `sequence`: integer 1 through `2**63 - 1`.
- `issued_at`, `expires_at`: integer epoch seconds in that same bounded range;
  issue time is inclusive and expiry is exclusive.

The Ed25519 signature signs exactly the ASCII domain separator
`fleet-manager.release-signature.v1` followed by one NUL byte, followed by
`planning.canonical(payload)`. The domain fixes this signature purpose/schema;
the proof envelope is not a substitute for those signed bytes. Public keys
and signatures are canonical standard padded Base64 of exactly 32 and 64 bytes
respectively. URL-safe, unpadded and noncanonical pad-bit encodings refuse.
The API accepts only public verification material, never a private signing key.

## Trusted policy and refusal

The strict schema-v1 `fleet-manager.release-trust-policy` declares `id`,
`revision`, `trust_revision`, `method`, `authority`, `installation`, `role`,
`valid_from`, `valid_until`, `minimum_sequence` and `keys`. IDs/revisions use the
existing bounded identifier contract; the caller's expected scope must match
the policy exactly. `now` is an explicit bounded integer epoch second.
The caller must obtain it from its trusted clock, not from an untrusted proof
or saved report; a caller-supplied historical time cannot prove current validity.

The policy is trusted **because the caller supplies authenticated local
configuration**, not because this module authenticates that configuration.
Passing an attacker's policy/public key authenticates nothing about a legitimate
distribution. There is no ambient key discovery, policy inference, online root
refresh, login or network lookup. The policy validity window bounds the age of
the supplied trust/revocation snapshot; it cannot prove instantaneous offline
revocation.

`keys` contains 1-256 unique key identities. Each entry has exactly `id`,
`public_key`, nonempty unique `roles` drawn from client/Gateway/node role names,
`valid_from`, `valid_until` and boolean `revoked`. The selected key must exist,
be unrevoked, authorize the selected role and contain the entire signed lifetime.
The policy and signature must both be currently valid. An expired/revoked key,
wrong identity/scope, tampered manifest/signature, unknown method or sequence
below the configured floor is an error, not an integrity-only success.
Unknown supplied fields and input values are not echoed in diagnostics.

The sequence floor is supplied trusted rollback policy, **not** a persisted
high-water mark. This stateless module permits re-verification of a valid current
release. The lifecycle owner must detect conflicting content at the same
sequence, advance durable rollback state, and enforce supported rollback and
commit-forward boundaries. A report never advances that state.

## Evidence, not authority

The `fleet-manager.release-authentication` result names method, policy/revision,
trust revision, policy digest, authority/installation/role, manifest digest,
signing-key identity/fingerprint, signed sequence/lifetime and verification time.
`valid_until` is the earlier signed or policy expiry. `authenticity_verified`
is true only after actual cryptographic verification; actual artifact integrity,
activation authorization, live readiness and mutations are all explicitly false.

Consumers cannot reuse a saved report as a bearer grant. They must freeze owned
immutable artifact/manifest staging, authenticate those same bytes again under
current policy, perform actual role-selected artifact integrity preflight,
enforce compatibility/freshness/rollback, fence the old writer and separately
authorize activation. Authenticating component digests in a manifest does not
prove that any corresponding artifact exists, matches or is safe to execute.

The independent positive fixtures are Ed25519 signatures produced by Node's
cryptographic signer, not mocked verifier results; only public material is
retained. The actual-artifact fixture binds the same authenticated manifest to
real selected files for each role and rejects changed artifact bytes separately;
authentication alone still succeeds for an unchanged manifest and is not file
integrity. Installed tests deny Python socket entry points during real local
authentication plus artifact preflight, and exercise actual missing crypto
support with site packages disabled. This is not OS-level network isolation or first-touch machine
installation evidence. Authentic GitHub bundles/roots, network-denied maintained
GitHub verification, native Linux proof and live release/signing lanes remain
separate validation gates; no signing workflow or live trust root is created.
