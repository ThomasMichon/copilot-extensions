# Explicit RS256 application-token and enrolled-proof service profile

Back to [Fleet Control V2](README.md#phase-2a---gatewayindex-vertical-slice).
Related: #5789, #6443 and #6804. This **agent-recommended source
prerequisite** was reviewed in #12327 before implementation. It does not complete
the selected provider adapter, protected enrollment or the parent fleet.

This realizes a source prerequisite of the Gateway vision's
[authenticated role channels](../../../visions/machine-fleet/gateway/README.md#authenticated-outbound-role-channels)
and [separate connector enrollment](../../../visions/machine-fleet/gateway/README.md#human-sessions-and-machine-enrollment).
It follows the existing [composition and contract-evolution patterns](../../../docs/patterns/README.md):
identity verification, service enrollment and resource authorization remain
separate, with no new service owner or implicit fallback.

## Goal and scope

Support an explicitly selected original RS256 application access token together
with an independently approved P-256 request-proof binding at the existing
Gateway service boundary. Preserve the actual issuer, audience and user or
application authority, without requiring the issuer to mint a different token
format merely to fit the narrow existing profile.

The current `jwt-pop-v1` and `enrolled-pop-v1` service profiles validate their
documented ES256 token form. The separate `user-bearer-v1` RS256 SDK profile
is caller-only; it must not become an implicit service or proof fallback.
The existing connector already treats its supplied access token as opaque
bytes when constructing the request proof. That is a useful composition seam,
not evidence that the current server accepts a different issuer token.

The implemented profile is distinct and opt-in,
`enrolled-rs256-pop-v1`. A name and successful parser are not runtime support
or actual provider interoperability. Existing profiles, configuration versions
and unselected callers retain their behavior.

## Ownership and exclusions

The bounded contributor owns this proposal and its subsequent isolated common
library/service-policy source PRs. Coordinate actual overlapping files with the
retained #5789 authentication and #6443 Gateway owners before editing them.
Normal review remains required; no separate ACK-only permission loop is added.

#6804 retains actual provider acquisition, protected keys, approved enrollment,
silent renewal, challenges/revocation and real provider/server interoperability.
Adopters retain their existing listener, caller, credentials, source capability,
resource grants and rollout. No source merge transfers those responsibilities.

Do not add an issuer, enrollment database, credential pump, daemon, provider
login, directory application, consent or role grant. Do not import a broker's
private key, rewrite/re-sign tokens, substitute unrelated resource/ID/source
tokens, introduce bearer-only service admission or silently select machine-key
authority. A source-authorization lease, channel generation or policy revision
is not an original-execution permit or monotonic execution-authority publisher.

## Proposed verification contract

Reuse maintained RS256 verification and the existing common request-proof and
replay machinery rather than a second JOSE implementation.

1. Select the profile explicitly against the executing SDK's declared support.
   An older or incompatible provider refuses before admission; package versions
   and configuration labels alone are not capability evidence. Expand the
   provider before selecting the new profile in an independently updated host.
2. Verify the original compact token's real RSA signature with bounded public
   RSA JWKS, exact issuer and resource audience, required issuance/expiration
   and applicable not-before times, current trust and supported header/algorithm
   semantics. Reject unselected algorithms, token-selected key URLs, private
   keys and unsupported proof/token forms without fallback.
3. Extract subject/client identity only from explicitly selected signed claim
   names. Apply bounded required signed-claim constraints, including declared
   scalar/list shapes where selected. Configuration must not rewrite a token
   or promote an unverified claim, username or request field into identity.
4. Require the complete independently approved current
   subject/client/kind/resource/principal/proof-key enrollment. The original
   issuer identity plus the real request proof must both match that enrollment.
   A presented token, proof key or provider callback cannot approve its own
   mapping. Service and caller kinds stay separate; an enrollment for one is
   not an enrollment for the other.
5. Verify the actual P-256 proof's signature, token hash, canonical HTTPS
   method/target, nonce, time and replay fence through the existing common
   engine. If the original token carries a supported confirmation binding,
   it must agree; absence must never be reported as issuer-bound DPoP.
   This is independently enrolled proof, not a claim about a provider's broker
   PoP or legacy signed-request interoperability.
6. Bound identity and accepted channels by token, enrollment and trust expiry.
   Revalidate current service/route/caller grants at existing request and
   response checkpoints. Withdrawal or material identity/enrollment changes
   invalidate old handles and delayed results without reauthorizing an effect.
   Preserve nonce/replay state for equivalent publications; an invalid candidate
   cannot extend the prior publication's lifetime.

Code-registered provider callbacks remain pure public-trust preparation.
They cannot acquire credentials or return authenticated identities, verifiers,
permits or enrollment approvals. Source authentication, public-key refresh,
durable accepted-revision custody and protected proof-key provisioning remain
with their existing owners. No additional human-presence timer is introduced.

## Implementation sequence

- [x] Review this source contract and reconcile it to the common authentication,
  service-profile and Gateway vision/pattern boundaries.
- [x] Add the explicitly selected verifier and policy integration, reusing the
  existing RS256 parsing/validation and common proof engine. Keep current
  profile refusal tests and compatibility behavior intact.
- [x] Prove the existing actual service-admission/channel/finite-consumer path
  with original synthetic RS256 tokens and real request proofs over contained
  TLS. Verify source-handler bindings without adding credential acquisition.
- [x] Publish exact callable/configuration support, performance and validation
  limits; pass normal review and required CI before merging implementation.
- [x] Return the resulting contract to Gateway/adopter integration. Actual
  provider interoperability, protected enrollment, same-owner listener hosting,
  installed selection and rollout remain separate, not completed by this leaf.

The source implementation is `enrolled_identity.EnrolledRS256IdentityVerifier`
and service-policy version `6`, selected as `enrolled-rs256-pop-v1`.
The [exact SDK/configuration contract](../../../fleet-manager/docs/gateway-enrolled-identity.md#explicit-rs256-application-token-profile)
documents signed-claim selection, required scalar/list constraints and executing
SDK support discovery. Implementation merged in #12335 after current-head
Copilot approval and required green CI; its source contract was returned to
#6804. This bounded source prerequisite is complete. All provider, enrollment,
hosting and live-adoption gates remain separate; no new registry or CLI was
added.

The canonical contained Fleet runner passed 446 focused cryptographic,
policy, downstream-admission and real TLS cases. Two additional bounded
request-cost cases retained all 25 samples per profile as diagnostics, not
production latency budgets. Python lint, install contract and documentation
checks passed. The PR records the exact test selection, measurements and
explicit reasons for not running installer clean-room or real-provider/live
adoption lanes. No installer/provisioning or installed runtime was changed.

## Validation and stopping boundary

Actor-free cryptographic tests use synthetic RSA issuer and P-256 proof keys,
explicit public trust, approved fixture enrollments and controlled clocks.
Cover supported original tokens; wrong issuer/audience/client/subject/resource/
kind/key/required-claim shape; missing, wrong-key or replayed proof; token-hash,
method/target/nonce mismatches; expiry; policy withdrawal and key replacement.
Include a copied token with the wrong proof key and an unapproved presented key.
Retain existing ES256 profiles and RS256 caller-only refusals as controls.

The contained real TLS service-path test must use the actual verifier and
connector, not mock past token/proof authentication. Cover channel attachment,
bounded response delivery, expired/withdrawn enrollment, delayed-result fencing,
disconnect and a new legitimate reconnect without replaying an uncertain
request. Synthetic source credentials are not actual provider acquisition.
Keep effectful transport tests outside required actor-free unit CI and use
the existing focused runner, bounds and cleanup owner.

Record unit, simple integration, clean-room and available live-venue tiers
honestly for the implementation, including explicit reasons for skipped tiers.
Measure added verification/policy work on the real consumer path; no network
discovery or credential acquisition may enter the pure verifier.

Source completion means reviewed, tested opt-in verification and actual
contained service-path interoperability with unchanged existing profiles.
It does not prove a real identity provider, protected key custody/enrollment,
installed host compatibility, listener exposure, source availability, execution
authority or live recovery. Keep #6804, #6443 and all parent aggregate gates open.
