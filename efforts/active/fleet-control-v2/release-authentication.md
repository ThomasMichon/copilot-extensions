# Release authentication - next reviewed slice

Back to [the canonical effort](README.md).
**Agent-recommended implementation plan; no verifier or live trust change is
delivered by this document.** Integrity preflight remains unsigned.

## Operator decision

Asked which release trust policy to support first, with choices of offline pinned
signing keys, GitHub Actions identity/artifact attestations, or support for both
with an explicitly selected policy per installation, the operator selected:

> both

This selects supported artifact-distribution verification methods. It does not
authorize creating a signing key, enrolling a trust root, changing a release
workflow/token, publishing a signed artifact, or deploying a product.
Gateway principal/JWT/JWKS and connector-proof authentication remain owned by
#5789; they are not artifact signing credentials.

The operator later asked to avoid anything requiring special GitHub
authentication; a new test-manifest signing workflow was not authorized.
Offline pinned-key verification therefore proceeds first. GitHub-positive proof
is deferred unless an authentic canonical-manifest bundle can be supplied through
an already-authorized lane without new permissions/authentication. Unrelated
attested artifacts do not satisfy that gate; supporting both remains planned,
not silently declared implemented.

## Recommended boundary

Support **two verification methods**, each behind the same explicit product
release admission contract:

- **Pinned key:** verify the selected release manifest with a maintained
  cryptographic implementation and a caller-configured public signing-key policy.
  Recommend Ed25519 for the first method, not an in-house implementation.
- **GitHub attestation:** use the maintained GitHub attestation verifier with
  explicit repository, issuer, signer-workflow and source identity restrictions.
  Do not treat a bundle's own certificate/root, unverified JSON, an owner-wide
  default or workflow-controlled predicate fields as trusted policy.

Each installation explicitly chooses one required method. Supporting both does
not mean accepting whichever proof happens to pass or falling back when a proof
is absent, invalid, revoked, incompatible or unverifiable. A later requirement
for both proofs on the same release is a distinct policy, not this selection's
implicit meaning. Default installation must not silently choose a trust root.

Keep ordinary client planning/inspection/preflight dependency-free. Verification
dependencies belong to an explicitly selected product verification capability,
not to a session hook or an automatically provisioned CLI. Coordinate package
metadata/build changes with #6424 before editing its shared surfaces.

## Manifest-to-artifact chain

Both methods must authenticate the **same selected release content** and bind the
existing per-role artifact digests, protocol and schema declarations. Specify
the bounded, unambiguous signed-byte encoding before implementation; reject
duplicate keys, signature/envelope confusion and altered manifest content.
Caller-selected policy/trust revision must be attributable in the result.

For pinned keys, require the expected key identity and explicit key scope/
validity/revocation policy from trusted configuration. An untrusted manifest
cannot supply or broaden its own key. Public keys are not secret, but private
signing keys never belong to the product verification API.

For GitHub attestations, bind the signed subject digest to the actual selected
manifest bytes and enforce the configured source and signer identity using
authenticated certificate/verification evidence. Do not merely match claimed
provenance fields. Reusable workflows must match the real signer, not only their
caller. Missing verifier support or trust material is an explicit failure.

Verification can use an explicitly supplied local attestation bundle and trusted
root material; an online lookup is not inherently required. Prove the actual
offline mode with network denied rather than equating `--bundle` with no
network. Any online fetch must be explicit, bounded and separately permitted;
no ambient login, root refresh or registry discovery is inferred.

Authentication is not installation/admission authority. The lifecycle owner
must freeze verified bytes in owned immutable staging, recheck those bytes and
role compatibility, enforce release freshness/rollback policy, exclude the old
writer and perform activation separately. A saved verification report is not a
reusable bearer grant. Offline revocation and trust-root rotation have explicit
freshness limitations; do not claim instantaneous revocation.

## Validation and implementation order

1. Review this plan and agree interface/package ownership under #6240/#6424.
   Retain the actual offline baseline and full vertical gate; this plan adds no
   service readiness or deployment claim.
2. Implement bounded policy/proof input and explicit method dispatch with no
   automatic fallback. Reject missing/unknown policy, wrong scope/trust revision,
   altered manifest and contradictory proof identities.
3. Prove real cryptographic pinned-key verification with independently generated
   fixture signatures: valid, tampered, wrong key, disallowed/revoked/expired key,
   ambiguous encoding and replay/rollback-policy refusal. Parser-only or mocked
   verifier tests do not prove signature verification.
4. Prove real GitHub attestation verification with maintained verifier/trusted
   roots and authentic fixture bundles: correct subject, wrong digest, wrong
   repository/issuer/workflow/source, unsupported predicate, untrusted root and
   tampered signature. A fake verifier's JSON is insufficient. A live signing
   lane requires separate workflow/publication authorization.
5. Exercise the same selected release and actual artifact bytes through both
   explicitly selected methods, installed outside the source checkout and with
   no Copilot cache. Verify offline operation with network denied where supported;
   record unavailable native/clean-room/live lanes rather than manufacturing
   credentials or a repository to make them available.
6. Integrate authenticated immutable staging with the separately owned client
   lifecycle, then the independently assembled Gateway/node lifecycle after
   agreement with #5789. Do not report this verifier as a service installer or
   settle the full parent effort from it.

## Existing verifier reference

The installed `gh attestation verify --help` describes subject verification,
repository/signer identity enforcement, SLSA provenance-v1 default, local
`--bundle`, `--custom-trusted-root`, `--cert-oidc-issuer`,
`--cert-identity`, `--signer-workflow`, `--signer-digest`,
`--source-digest` and JSON verified-result output.
[The command reference](https://cli.github.com/manual/gh_attestation_verify)
is the external authority for supported flags; inspect the chosen installed
version before relying on it. Help/source evidence is not a successful
cryptographic test, no-network proof or permission to execute a signing workflow.
