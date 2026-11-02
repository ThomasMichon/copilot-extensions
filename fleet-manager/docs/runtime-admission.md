# Current enrollment and scoped admission

`fleet_manager.runtime_admission` is an isolated internal library alongside
the [identity verifier](runtime-authentication.md), implementing the next bounded
part of the reviewed [authentication interface](../../efforts/active/fleet-control-v2/runtime-authentication.md).
It does not install an issuer, acquire credentials, enroll principals, expose a
Gateway route, delegate to a backend or change any existing standalone mode.
The [resident Gateway](gateway-runtime.md) has its own existing static
index/credential enrollment API; this library does not replace or wire it.
An owner-agreed current-snapshot adapter remains necessary before advertising
these observation/operation permits on that serving plane.
The separate [service Gateway SDK](gateway-service-profile.md#in-process-deployment-owners)
binds its original retained verified caller to local operation admission.
That opt-in bridge still requires independent current operation policy and
effect-owner custody; it does not adopt the legacy serving plane or a live fleet.

## Trusted owner and currentness

One serving owner constructs `AdmissionAuthority(snapshot=..., clock=...)`.
The required callback returns an immutable `AuthoritySnapshot` on **every**
admission and execution/emission revalidation. This is an in-process trust
contract, not a wire DTO: constructing these dataclasses authenticates nothing.
The adapter must independently authenticate its policy/enrollment source and
establish the asserted current issuer-key/provider-revocation freshness.
Never deserialize caller-provided snapshots, identities or permits into authority.
The selected transport must already be encrypted and server-authenticated.

The snapshot binds authority, fleet, issuer, audience, policy revision,
valid-from/until and a distinct identity-trust validity bound. Enrollments map
verified subjects plus explicitly requested role/installation to stable
principal/account namespace, kind and enrollment generation. Token role text,
display names, health, reconnect and installation discovery grant nothing.
`None` from the callback explicitly refuses `authority-unavailable`; exceptions
propagate. There is no cached-allow fallback. Unknown, revoked, stale, conflicting
and unavailable are distinct value-free refusal categories.

Revisions cannot go backwards or name conflicting snapshots in one owner.
Enrollment, read-branch and operation-grant order is canonicalized, so a
permutation alone is not a same-revision policy conflict. Changed grant content
still conflicts; normalization never joins or widens branches.
Freshness renewal changes the revision even if grants are unchanged. The source
owner still owns durable monotonic revisions and enrollment generations across
deletion, restart and failover; this library is not their durable store.
Authority/fleet/issuer/audience replacement requires a new owner and invalidates
all old permits. The trusted integer clock is sampled under its lock before and
after callback/decision work. Expiry at the final checkpoint and clock rollback
refuse; completion order cannot fabricate time order.

`AuthoritySnapshot.identity_profile` defaults to the original `jwt-pop-v1`.
An adopter may separately select `enrolled-pop-v1` to consume an actual verified
`EnrolledIdentity` of kind `caller` from the existing enrolled-key serving
boundary. Both Gateway and operation policy must explicitly admit that profile.
An enrolled service identity cannot become an operator, and default operation
policy does not silently accept another identity profile. Profile replacement,
like issuer/audience replacement, requires a new admission owner. No token
rewriting, identity reconstruction, provider login or new verifier is supplied.

## Supported requests and exact branches

`ObservationRead(fleet, scopes=None)` means the enrolled principal's authorized
view, **not all resources**. Explicit empty scopes and empty grants deny.
Each `ObservationScope` is one exact origin/project/entity/field branch;
fields are the existing current-state field names. A requested branch must fit
one granted branch. Branches are never flattened into independent sets whose
Cartesian product would widen access. Aliases, prefixes and wildcards are not
grant syntax. The library does no query, count, hydration or storage lookup.
Adapters must use the effective branches before retrieval and must not expose
the store's internal global cursor, unrelated row metadata or unselected fields.
Broad query/pagination/subscriptions and protected wire cursors remain unimplemented.

Immutable `OperationIntent` values support their original three commands and
the fixed [`runner.converge`](arc-owner.md) and
[`manager.converge`](komodo-owner.md) operations. The verified
principal/account and snapshot authority must match the
original intent before operation-ID or result lookup. Target, command and
generation require an exact `OperationGrant`; enrollment generations and
operation target generations remain separate. The original canonical intent
and request digest are never rewritten. This authorizes admission only:
resource parameters, current source policy, routing/installation/offer binding,
backend delegation, accepted-work fences and effect-side policy still belong to
their existing owners. A permit is not permission to skip those checks.

Both fixed convergence commands additionally require the fourth
`OperationGrant` field, `request_digest`, to equal the complete original
intent's canonical digest. Command/target/generation-only grants refuse.
The runner request binds its prepared proposal and selected deployment;
the manager request binds its complete selected deployment through the single
`deployment` parameter. Changing an image, resolved scope, duration,
operation ID or selected configuration cannot reuse the grant.
The original three commands retain their
existing grant shape; supplying a digest for them is unsupported. The current
policy owner must independently admit that exact deployment. A preparation
review does not create such an enrollment/grant.

The serving adapter calls the real verifier once per request, then passes that
`VerifiedIdentity` directly to `authorize(..., role=..., installation=...)`.
Evidence is consumed once per owner, including a denied admission. Its bounded
90-second transfer window is request freshness, not human presence. DPoP does
not sign the body; the library computes the canonical read/intent digest itself,
while TLS and the adapter protect and associate the original request bytes.
A verification timestamp in the owner's future refuses `invalid-identity`;
evidence at or beyond its transfer-window expiry refuses `stale-identity`.
Both checks precede the snapshot callback and evidence consumption.
Consumption also precedes the trusted snapshot callback: an exception propagates
without restoring already-attempted request evidence. This deliberate one-use
contract does not retry an already verified proof after an owner fault. Recovery
uses a fresh proof and verification result with the original operation ID and
content, not a human check-in, cached allow or evidence rollback.

## Permit checkpoints, renewal and bounds

A trusted in-process embedding can supply `checkpoint=...` to `authorize`.
This is a short I/O-free scope/lifetime check, not a policy or identity
authenticator, and must return `None` or raise. It runs before and after
admission and remains attached to every renewed handle's revalidation and
completion. Its refusal consumes a retained handle without refunding proof
evidence. Cleanup via `discard` never calls it or turns failure into authority.
The service Gateway uses this seam for its original retained caller/route and
local request custody; wire requests cannot supply a callback.

The immutable `Permit` retains actor/enrollment, authority/fleet, action,
canonical digest, exact effective resources, issuing policy revision and finite
validity. Only the exact object retained by its `AdmissionAuthority` is usable.
Copying/serializing/reconstructing its fields grants no authority, and another
owner/restart cannot accept it. No signing/encryption credential is hidden here.

Call `revalidate(permit, original_request)` immediately before the bound lookup,
effect admission or emission and retain its returned handle. Revalidation
accepts a reconstructed supported request with the same canonical action/digest;
it does not depend on retaining the request's Python object instance.
The Permit itself must still be the exact owner-retained object. Revalidation
consumes the old handle, including on failure. It rereads current policy and
identity freshness; changed enrollment, revocation, request content, scope or
expiry refuses. Unrelated policy updates can revalidate without expanding an
existing view or resetting its expiry. It is not an atomic transaction with an
external backend: the future adapter must fence a concurrent policy update at
its real execution/emission boundary, not simply check once and queue forever.

`complete(permit, original_request)` applies that same fresh checkpoint but
consumes the handle without retaining a replacement. The
[isolated observation reader](scoped-observation-reader.md) uses it at point or
bounded point-set snapshot/conditional-refresh completion, including after a
storage or serialization failure. Both authorize all branches before lookup
and use one finite permit for the complete selection. An unchanged refresh
still completes fresh authorization; it adds no pagination, replay or wire grant.
Proof evidence stays consumed. Existing
`revalidate` callers retain their unchanged replacement-handle semantics.

`discard(permit)` withdraws an exact still-retained local handle after a caller
refuses, cancels or fails a request. It returns false for an already consumed,
copied or foreign handle and never refunds consumed proof evidence. This is
capability cleanup, not authorization, completion evidence or a policy/fence
callback: it remains usable when the current policy source itself is unavailable.
Malformed non-Permit input refuses. A discarded handle cannot subsequently
revalidate; another attempt needs fresh identity evidence and authorization.

An authority may explicitly bind a reentrant `fence` at construction. All its
admission and checkpoint calls then acquire that fence before its internal lock,
including direct callers outside a reader, with a one-second acquisition bound.
The scoped reader requires the exact same configured fence. Unfenced existing
authorities keep their original synchronization semantics.

Permits last at most 30 seconds and never outlive policy, identity trust or
the access token. Revalidation cannot extend them. Silent authorized renewal
uses a fresh proof/identity and keeps the original operation ID/content.
There is no maximum human-session age, presence prompt, forced sign-out,
universal access-token lifetime cap or enabled dead-man switch. Provider/OS
session longevity remains the presence authority, with real provider challenges,
expiry and revocation enforced by the selected adapter.

Snapshots permit at most 64 enrollments and 256 total grants. Read requests
permit at most 256 exact branches. An owner retains at most 4096 live permits
(configurable downward) and 4096 consumed proof evidence digests for the bounded
request window. Capacity refuses without evicting a live fence. These are
single-owner bounds, not per-principal fairness or pre-verification rate limits.
Expiry cleanup scans these bounded maps under the owner lock, deleting expired
entries in place rather than allocating replacement maps of all live handles.
It is still a bounded linear scan, not an expiry-index or throughput guarantee.
Effective read branches reuse the request/enrollment's canonical immutable tuple;
field subset checks avoid temporary sets without joining branches.
The fresh-process integration regression uses only production modules with
synthetic signed credentials and a real SQLite store, proving separate views
and refusal before foreign-row retrieval without importing test helpers.
The verifier's independent nonce/replay fence remains required. Multi-owner
shared-epoch service admission, enrollment persistence, provider outcomes,
publication grants, delegation and resident integration are not implemented.
