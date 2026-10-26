# Continuity-backed first-touch and human-session boundary

Back to [Gateway and SSH Fleet Onboarding](README.md).
Coordination remains with #6681, #6236, #6240/#6424, #5789/#6443/#6804 and
the separately selected [peer-approval contract](peer-approval.md).

**Agent-recommended contract amendment, not implemented setup, credential
issuance or live enrollment.** This extends the existing onboarding effort,
not a new installer, account/enrollment registry, storage owner or control loop.
It realizes the Installer's
[harness-selected continuity](../../../visions/installer/README.md#harness-selected-continuity-bootstrap),
the fleet's
[continuity-backed bootstrap](../../../visions/machine-fleet/README.md#continuity-backed-machine-bootstrap)
and the Gateway's
[separate human/machine modes](../../../visions/machine-fleet/gateway/README.md#human-sessions-and-machine-enrollment).

## Requested portable outcome

A harness README supplies the standard upstream Manager setup plus selection
of its harness/worktree configuration context. That context declares where the
user's portable continuity material is expected. The user has already
established the authenticated path to that source.

The selected source supplies Gateway discovery and actual delegated bootstrap
authority, allowing the current machine to obtain scoped Gateway credentials
before SSH exists. An already authorized direct-SSH provisioning path can reach
the same owners; it is not required for managed outbound-first machines.
The input selects a portable harness context, not an assumed local path or an
unrelated repository default. This proposal publishes no new CLI flag.

Cloud-sync, private Git and other protected user-state sources remain provider
choices. Account, container, unlock, credential and enrollment decisions are
not inferred from a folder name or copied public configuration. A source that
already delegates the needed bootstrap authority is the approval path, not a
reason to require another arbitrary human check-in.

## Ownership and trust

| Boundary | Retained owner and required meaning |
|----------|-------------------------------------|
| Standard setup and harness selection | Existing upstream installer/adoption owner; the selected harness supplies context and policy, not a copied installer |
| Continuity access | Selected provider plus existing credential/Vault owner; exact user/account/container and actual available access |
| Gateway discovery | Trusted selected source; endpoint and transport/trust references are configuration, not enrollment permission |
| Bootstrap delegation | Existing credential/enrollment policy owner; explicit user, harness, destination, machine and capability scope |
| Credential issuance and local custody | Existing provider and target-local owners; no secret issuer or cache writer hidden in a UI |
| Current enrollment and grants | Existing Gateway/policy publication authority; slow sync cannot revive a revoked or superseded grant |
| SSH/profile/peer admission | Existing setup, service, credential and agent-ssh owners; preserve selected directional and peer-approval policy |

Portable continuity may carry encrypted vault material, credential references
or an appropriately protected delegated grant through the existing custody
contract. It does not make machine-bound caches, local wrapping keys, process
locks or live authority databases portable. Never distribute an unlock key
beside an encrypted vault, copy a machine-bound protected cache, or put secrets
in public source, setup arguments, logs or receipts.

Prefer independently revocable machine credentials rather than copying a
shared fleet-administration secret. Exact issuance/key-custody mechanics need
their owner's implementation review. Knowing a Gateway address, possessing an
unrelated provider token or declaring a machine name cannot enroll that machine.

## Bootstrap modes

Both modes below are **proposals for the same owning setup/enrollment flow**.
They are not new services or evidence that an existing running Gateway accepts
them.

### Source-delegated first touch

The selected continuity provider returns trusted discovery and an actual
bootstrap grant or provider-owned reference capable of authorizing enrollment.
The existing enrollment owner validates that delegation and provisions only its
allowed machine/capability scope. Existing source authentication supplies the
human-approval boundary when that policy delegates it; do not add a mandatory
second ceremony or periodic human-presence rule.

Consume normal provider renewal and unlock behavior. An actual challenge,
revocation, unavailable source or missing required delegation is explicit;
none authorizes another account, weaker verifier or an unconfigured issuer.

### Confirmation from an authenticated device

Where policy requires new-device confirmation, an already admitted device
approves the exact pending device and requested scope. A short displayed code
is a human correlation aid, not the request secret, a Gateway credential or
approval authority. Bound its lifetime/attempts and one-time redemption, bind
the unguessable pending request to its actual requester, and recover lost
replies through that original operation rather than repeating enrollment.

The bootstrap strategy must provide an authenticated way to create or exchange
that request: for example, a scoped bootstrap credential for the private
Gateway or a declared authenticated alternate exchange. A read-only source
containing discovery alone is not a writable approval mailbox or an
authenticated registration path. Do not open an anonymous endpoint or execute
synchronized data merely to escape the circular dependency.

The existing `explicit-peer-approval` SSH policy is unchanged. Source access or
a display code does not approve its public-key fingerprint, and unavailable
peers cannot trigger its separately confirmed recovery operation. Reuse that
service-owned decision contract where applicable instead of adding a second
peer-approval store.

## Ordered setup and recovery

1. Resolve the selected harness context through the standard adoption boundary.
   Preserve the standalone no-harness path and completed core installation
   when an optional repository/continuity stage cannot finish.
2. Resolve and authenticate the declared continuity source through its owner.
   Retain selected source/account/container provenance; do not probe unrelated
   accounts or substitute another source after failure.
3. Obtain the initial Gateway reach/trust configuration and the required
   bootstrap delegation or authenticated confirmation path. Edge admission and
   application enrollment remain separate checks.
4. Ask the existing enrollment/credential authority to provision the current
   machine's explicitly allowed identity and grants. Distinguish accepted
   approval, issued credentials and authenticated connection.
5. Establish outbound Gateway access without assuming inbound SSH.
   Only then apply selected SSH/profile/service capabilities through their
   existing admitted lifecycle and policy owners.

The original operation owns retries and uncertain results. Re-running setup
must not duplicate identities, rotate keys, widen grants or blindly repeat an
unknown effect. Delayed/conflicting source copies cannot roll back enrollment
or current grants. Retain explicit stage outcomes and current revocation
through the existing operation and policy authorities.

## Ordinary human UX is separate

The selected identity adapter can admit normal provider-authenticated human
sessions for page loads and application requests. A server-side session cookie
is one candidate, with secure/HttpOnly/SameSite handling, appropriate CSRF
protection, exact identity provenance and closed direct-backend paths.
OIDC-based adapters retain their normal state, nonce, redirect and token checks.

This is not bearer fallback in `jwt-pop-v1` or `enrolled-pop-v1`, and is not
passing a provider cookie into those verifiers. An explicit human-session
adapter maps its verified principal into the same current route/capability
policy. Machine/service profiles and any explicitly selected stronger
interactive profile retain their original requirements. Normal human SSO does
not require browser-key persistence or a Service Worker.

## Implementation sequence and compatibility

### Selected executable slice

**Operator-selected direction, summarized:** implement the missing first-touch
enrollment operation and its normal setup consumer within the existing
Fleet/Manager authorities. Use independently revocable public-key machine
enrollment rather than require a new external application-token issuer.
The bounded source allocation is recorded in #6681 comment6408998175.
This selects implementation work, not live credential access or deployment.

**Agent-recommended realization, proposed for review before runtime changes:**

1. Add an explicit opt-in public-key machine-authentication profile to the
   existing Gateway service authority. Keep `jwt-pop-v1` and `enrolled-pop-v1`
   unchanged, including their defaults, independent issuer requirements and
   proof checks. The new profile authenticates a configured principal through
   its currently enrolled public key; it is not a self-issued application
   token disguised as either existing profile, a bearer fallback or human SSO.
   Use maintained cryptographic operations, authenticated encrypted transport,
   exact principal/resource/method/target and request-content binding, bounded
   challenges/proofs, atomic replay protection and explicit capacity refusal.
2. Implement the actual first-touch receiver in the existing Gateway serving
   owner. A selected protected source conveys a high-entropy delegated grant,
   not mere discovery. Current owner-authenticated configuration binds that
   grant to its source/account/container, harness, destination, machine,
   principal, permitted capabilities and expiry. Validate that delegation
   before enrollment or sensitive lookup; a request cannot select broader
   grants, another source or its own trust. The machine generates its unique
   signing key locally and proves possession over the exact enrollment intent.
   No raw grant or private key enters source policy, logs or operation receipts.
3. Extend the existing operation/receipt journal for the real enrollment
   operation, not `workspace.allocate` under a new label. It retains the
   original operation, exact public-key/scope binding and accepted generations.
   Concurrent/repeated requests cannot allocate another identity or silently
   replace the key. A lost reply or process restart inspects that operation and
   its actual publication outcome; an uncertain effect is not blindly retried.
   Required journal changes need an explicit compatible migration/refusal
   contract, without breaking unrelated existing operations or silently
   changing an old command's meaning.
4. Implement the missing enrollment producer under the same current-policy
   authority. Today's `ServiceAuthority` source callback only reads complete
   policy; it is not a publication API. Compose configured desired grants and
   journal-owned admitted public-key bindings through that authority rather
   than mutate an adopter's desired repository or introduce another enrollment
   database. Persist accepted revision/generation floors in the existing owner
   so delayed source copies and restarts cannot revive a revoked or superseded
   enrollment. Configuration, admission, publication and authenticated
   connection retain distinct outcomes and one writer per owned resource.
5. Consume the implemented receiver through the normal upstream Manager and
   engine adoption path. Extend explicit harness/worktree context selection
   through the existing repository-setup boundary; do not create a private
   installer or make a repository script contain the reusable enrollment
   engine. The trusted selected context declares its continuity provider and
   source references. Begin with an explicitly selected already-authenticated
   local continuity projection, suitable for a user-managed sync provider;
   this is not a claim of a new cloud-login or sync implementation.
   Keep source provenance and the local key/operation state separate. Only an
   actual authenticated outbound Gateway connection permits advancing to
   separately selected SSH/lifecycle work.

Publish support from the actual serving/runtime generation, expand the receiver
first, then migrate the caller conditionally. No new setup syntax is advertised
as runnable by this proposal. An incapable receiver, provider or journal reports
the unsupported requirement before effects and retains completed core work.
Absent selection preserves existing setup, token-authenticated Gateway and
trusted direct-SSH behavior. Rollback cannot reinterpret new credentials as
old tokens or discard accepted enrollment/current-policy floors.

Focused qualification must exercise the actual setup parser/process and TLS
receiver, not just a descriptor or injected success callback. It must include
wrong source/account/container/harness/destination/machine/scope, discovery-only
and expired/revoked grants, forged or content-mismatched proofs, replay and
capacity, concurrent first touch, crash/lost-reply recovery with the same key,
stale-source/restart rollback refusal, private local custody and mixed-version
controls. Separate installed source-denying, native platform/provider and live
adoption results; a synthetic grant is not production enrollment. Existing
selected peer approval and ordinary human-session validation remain open.

### Delivery order

- Review this shared intent/contract amendment before implementing new runtime
  semantics. Retained setup, identity, credential, operation and SSH owners
  retain their authorities, with the bounded allocation above.
- Extend the existing installer/adoption contract for explicit harness context
  and continuity-provider selection without forking it. Do not advertise a
  runnable new argument until the owning executable implements it.
- Expand the receiving enrollment and human-session adapters first. Discover
  selected contract support from the actual runtime/peer, not payload versions;
  preserve strict parsing and truthful unsupported-policy refusals.
- Then compose source selection, delegated first touch or confirmed pairing,
  Gateway connection and selected SSH effects through the same setup owners.
  Preserve standalone, existing direct-SSH and configured peer-admission modes.
- Qualify isolated synthetic sources and real installed consumer boundaries
  before separately approved provider access, credential delivery and adoption.
  No code, release, installation or live success follows from this proposal.

## Validation obligations

All items remain **unqualified implementation obligations**:

- [ ] A bare selected Windows, Linux or macOS target can begin with the standard
  setup plus harness context, without prior SSH or Gateway credentials; unsupported
  platform/provider combinations report the actual limitation.
- [ ] Already-authenticated source delegation supplies only its permitted
  enrollment scope, without a redundant confirmation. Reject wrong account,
  container, harness, Gateway, principal or machine; public discovery alone
  and absent/expired/revoked grants cannot enroll.
- [ ] Selected confirmation rejects guessed, expired, reused, wrong-device and
  changed-scope codes; a source lacking a request-exchange capability cannot
  silently become a mailbox or anonymous API.
- [ ] Existing peer approval still requires its exact authenticated decision;
  stale/self/unauthorized approvals and missing-peer recovery bypass refuse.
- [ ] Direct trusted-SSH and Gateway-first targets converge through the same
  setup/credential owners without creating a second writer or live registry.
- [ ] Duplicate/lost replies, restarts, delayed/conflicting sync and revoked
  source material preserve the original operation, machine identity and current
  grant floors without duplicate issuance or blind effects.
- [ ] Normal human navigation, assets, application calls and long-lived streams
  preserve current authorization, expiry/logout exposure, CSRF and backend
  isolation without mandatory browser keys; machine proof checks remain intact.
- [ ] New/new, mixed-version and compatible rollback consumers refuse unsupported
  required semantics; old selected modes retain their behavior.

This slice does not complete #6681, #6236 or their implementation, publication,
installed-role, provider/adoption and preserving-migration gates.
