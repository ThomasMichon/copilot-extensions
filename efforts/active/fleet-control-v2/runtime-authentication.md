# Runtime identity and authorization interface proposal

Back to [Fleet Control V2](README.md). Coordination: #5789 / #6443, with
#5862 observation and #3761 / #352 source/client ownership retained.
**Agent-recommended interface proposal with operator-required presence behavior
below, not implemented authentication or enrollment.**

This refines the existing [machine-fleet vision](../../../visions/machine-fleet/README.md)'s
`authenticated-scoped-and-revocable`, `capability-scoped-credential-access`,
`resumable-observation` and independent-role boundaries. No new standing intent
or mandatory identity vendor is needed. Runtime fleet identity is distinct
from [release authentication](release-authentication.md): a signed product
artifact is not a principal, and an authenticated principal cannot select a
release or activate a role merely by connecting.

## Concrete gap and recommendation

The merged operation records name actors without authenticating them.
`fleet_contracts` registration/offer checks validate shapes and correlations,
not principals. The [observation store](../../../fleet-manager/docs/current-observations.md)
requires a trusted caller and exposes internal positions, not authorized wire
cursors. The [index/credential slice](gateway-index-slice.md) needs authenticated
connector, service, worker and credential-provider identities with independent
grants. Existing relay/vault prototypes are not that complete interface.
This bounded source inventory does not claim that no unpublished prototype exists.

**Recommendation:** one Gateway-owned authentication/admission boundary, consumed
by every client and role. Separate credential verification, current enrollment,
policy authorization and resource-owner enforcement. Return a bounded immutable
decision, not a boolean or caller-provided role dictionary. Do not add an auth
daemon, competing account registry, implicit credential issuer or per-UX policy
engine.

For cross-machine HTTP, propose a versioned `jwt-pop-v1` profile: encrypted,
server-authenticated transport, configured-issuer signed access JWTs and
DPoP sender-constraining under [RFC 9449](https://www.rfc-editor.org/rfc/rfc9449).
Use maintained JOSE/proof libraries and the verification practices in
[RFC 8725](https://www.rfc-editor.org/rfc/rfc8725), not handwritten crypto.
The adopter explicitly selects an issuer/provider and trust references; this
proposal does not choose a tenant, require special GitHub auth, mint a key,
request consent or configure that provider. Missing support is `auth-needed`
or `unsupported-profile`, not a reason to accept a legacy bearer token.
Shared bearer-only secrets are not the recommended fleet identity contract.
An mTLS-bound profile is a possible separately reviewed alternative; it is not
silently negotiated as fallback. DPoP is recommended for a common HTTP client/
service proof boundary without requiring every UX client to manage TLS certificates.

Native machine-local use can explicitly select an `os-peer-v1` profile, mapping
verified OS peer identity to the same policy model. It requires a protected
socket/pipe and actual OS identity evidence, not a PID, loopback address or
forwarded username. A forwarded remote connection cannot inherit local-user
authority. Local mode is not an outage fallback for an explicitly selected
remote Gateway.

## Identity, authority and owner separation

### Separate human-session and bootstrap proposals

The [continuity/onboarding amendment](../gateway-ssh-fleet-onboarding/continuity-bootstrap.md)
distinguishes ordinary human UX sessions, machine bootstrap and normal admitted
machine/service requests. The `jwt-pop-v1` recommendation above remains the
selected machine proof contract, not a universal requirement that a browser
retain an independently enrolled signing key.

A future explicitly selected human-session adapter verifies its provider/
session and identity provenance at the Gateway or admitted edge, then consumes
the same current resource policy. It must preserve CSRF protection and closed
backend paths; a caller cookie or forwarded username is not a machine proof.
No existing profile is reinterpreted, weakened or silently replaced.

Similarly, authenticated continuity access is useful bootstrap evidence only
where it supplies real delegated enrollment authority or an authenticated
confirmation path. The existing enrollment owner commits that decision before
ordinary machine authorization; a discovered endpoint, public configuration or
short display code cannot self-enroll. Required peer approval and separate
recovery policy remain enforced. Receiving adapters must expose support before
new consumers select these semantics; this is a proposal, not an added API or
implemented verifier.

| Identity / owner | Can be granted | Does not acquire merely by connecting |
| --- | --- | --- |
| Operator or automation client | Selected current views, named commands and status | Fleet administration, host capacity or credential-provider access |
| Machine/container connector | Its admitted origin, fixed service offers and receipt delivery | Another origin, arbitrary execution or trusted generation replacement |
| Service/index worker | Its installation, admitted job and canonical corpus/source scope | Every source in a corpus, deployment or another account's credential |
| Credential provider/controller | Fulfilment of matching requests for declared provider/account/audience policy | Global observation, arbitrary token delivery or Gateway administration |
| Fleet policy/enrollment owner | Explicit trust, grant and binding changes | Driver/provider capacity, resource retirement or source agency migration |
| Existing service/effect owner | Its own backend checks, fences and accepted-work responsibility | Blind acceptance of a Gateway-supplied role or task-completion claim |

The stable principal key is the verified `(issuer, subject)` mapping owned by
fleet policy, not a display name, token ID or connection ID. Key rotation and
token renewal must not silently rename the actor or change its operation-ID
namespace. An external account reference is a separate policy binding.
Role labels select possible capabilities; only explicit grants authorize them.

Enrollment is durable policy; a connection is ephemeral evidence. Keep separate:
principal/enrollment generation, producer incarnation, connector generation,
service installation/offer generation, allocation fence, source revision and
observation position. No single counter substitutes for all of them.
Machines, WSL environments and containers remain distinct logical origins.

## Operator-required presence and unattended continuity

**Default: run as long as possible.** A live user does not periodically prove
presence to the Gateway. Use the adopted identity/platform provider's own session
longevity as the presence authority: Entra/Windows sessions are the requested
case, while the portable interface preserves the equivalent contract for other
selected providers. This is not permission to treat an arbitrary OS-session
claim as remote authentication; verified identity and scoped enrollment still
apply.

Reuse valid credentials and attempt provider-permitted silent refresh/renewal
for as long as that underlying session supports it. Access-token expiry, a new
proof, a short view/delegation lease or a transport reconnect does not itself
require a human check-in. No Gateway-added maximum human-session age, periodic
step-up, forced sign-out or refresh flow that deliberately requests interaction
is enabled by default. Authentication timestamps are provenance, not an
independent Gateway presence countdown.

When the underlying token provider or protected key actually requires a
presence/interaction challenge, expose `challenge-required` and pause the
affected new authorization/delivery until its owner resolves that challenge.
Do not bypass the challenge, extend an expired credential, reuse a revoked one
or silently select another account. Ordinary network/provider unavailability is
`temporarily-unavailable`, not a fabricated sign-out or presence challenge;
bounded silent retry/recovery remains available.

The credential/session owner supplies `acquire_or_renew` with explicit
principal/account/audience/resource context. Its outcomes distinguish a usable
credential, actual challenge, denial/revocation and temporary unavailability.
It does not launch an unattended sign-in UI. Repeated requests for the same
blocked provider/account coalesce around the actual challenge rather than
continually prompting the user. Windows lock, sleep, UI inactivity, a closed
client window or an observation heartbeat timeout is not a new presence rule:
follow the provider's real session/key-access decision.

Automatic authorization revalidation and proof/lease renewal below are
**machine operations, not human presence renewal**. The adopted connector,
provider and service lifecycle owners maintain them; accepted work does not
depend on a continuously foregrounded operator client. Finite resource/job
grants and actual credential validity remain enforced and are not silently
extended by a live user session.

Explicit revocation can cut access short. An optional dead-man switch is
**off by default**, separately opted into by the user for a visible principal/
scope, interval and intentional check-in condition. Do not silently enable it
from idle detection or equate an automatic heartbeat with the chosen human
check-in. Expiry applies the declared scoped revocation/admission rule; disabling
or changing it is an explicit user/policy action. Neither revocation nor switch
expiry proves an already accepted external effect stopped, releases a resource,
or cancels work without its existing owner contract.

## Proposed boundary types and methods

These are logical interfaces for the existing product, **not published Python
symbols or final endpoint URLs**. Wire requests cannot construct the trusted
internal handles below.

| Interface | Inputs and bounded result | Required refusal |
| --- | --- | --- |
| `verify_identity` | Credential/proof plus trusted transport, audience and route facts -> `VerifiedIdentity` | Invalid signature, issuer, audience, type, time, key binding or replay |
| `resolve_enrollment` | Verified identity plus requested role/installation -> `EnrolledPrincipal` | Unknown, revoked, stale or mismatched binding; no automatic enrollment |
| `authorize` | Enrolled principal, named action, typed resource selection and canonical request digest -> `Permit` or typed denial | Missing current policy or any requested authority outside the grant |
| `revalidate` | Permit plus current policy/enrollment and execution/emit checkpoint -> current permit or denial | Expiry, revocation, changed scope or generation; no cached-allow fallback |
| `authorize_delegation` | Permit, exact registered destination and request binding -> bounded audience-specific delegation | No delegator grant, stale offer, wider scope or unsupported backend enforcement |
| `open_view` / `renew_view` | Current permit and canonical selection -> server-owned view lease and protected cursor | Changed view/grant, expired lease or unsupported scoped query |
| `authorize_credential_request` | Enrolled worker, admitted job and exact credential need -> provider-routing decision | Unbound account/source/recipient or policy/provider capability mismatch |
| `authorize_credential_reply` | Current provider subscription and correlated request/reply metadata -> delivery decision | Unsolicited, wrong-recipient, stale, expired or conflicting reply |

`VerifiedIdentity` retains verified issuer/subject, proof-key reference,
authentication profile, audience, validity and non-secret evidence reference.
It contains no raw access token, private key, refresh token or resource credential.

`EnrolledPrincipal` adds the canonical principal/account namespace, fleet,
principal kind, admitted role/installation and current enrollment generation.
All values are resolved from current policy and verified evidence. A claimed
role, target or account in a request is compared with that binding, not promoted
into authority.

`Permit` binds the principal, action, exact effective resource/field grants,
request digest, policy revision, enrollment/generation constraints and validity.
It is an internal trusted result; serializing its fields does not make a
delegation credential. The future DTO slice must use immutable bounded records,
strict versions/keys/types and value-free diagnostics, as existing contracts do.

Resource selections are operation-typed: fleet/origin/project/entity and field
mask for observations; target/service/installation/offer generation for routes;
corpus/canonical source families for index operations; and
provider/account/audience/permission set/job/recipient for credentials.
There is no arbitrary URL, method, shell, header map or general-purpose
`execute`/wildcard-admin field. Unsupported actions refuse.

Omitted read filters mean the authorized view, not every resource. Empty allowed
sets mean no access. Resolve aliases through their owning registry; do not turn
a string prefix into a grant. Query, hydration, similarity seeds, counts and
subscription metadata are scoped before lookup/aggregation, never by removing
forbidden rows after an unrestricted search.

## Verification profile and bootstrap

The initial network profile requires a configured issuer, audience, algorithm
allowlist and attributable key set. Verify access-token signatures and key type,
`iss`, `sub`, `aud`, token type, `exp`, `iat`, applicable `nbf`, and the sender-key
binding. A decoded claim is not evidence. Release-signing roots and runtime
identity keys are separate trust domains, even if they use the same algorithm.

Resolve keys only from configured trust references. Token-supplied key URLs,
issuer discovery, unknown algorithms, `none`, symmetric/asymmetric key confusion
and an unknown `kid` must not trigger an arbitrary fetch or fallback. Key refresh
is bounded and separate from request-driven discovery. A future implementation
must select/test a narrow asymmetric algorithm/key-type allowlist.

For DPoP, verify the proof type/signature/public key, token-key thumbprint,
method and externally validated target URI, access-token hash, proof timestamp,
unique proof ID and server nonce. URI normalization and trusted proxy
termination are server configuration, not caller-controlled forwarded headers.
DPoP does not bind query/body bytes by itself: transport protects the original
request, and admission computes its canonical content binding for idempotency
and subsequent delegation. Do not claim a DPoP proof signs an arbitrary body.

Reject a replay before admission. Restart/cutover must retain the proof fence or
change a nonce epoch so pre-restart proofs cannot become fresh again. Full replay
capacity refuses/backpressures new admission rather than evicting still-valid
proofs and accepting their reuse. A healthy socket is not renewed authorization.
An application retry presents fresh proof while retaining its original operation
ID/content binding; replay protection is not a reason to mint a new command.

First enrollment and trust installation require explicit owner-approved inputs
through the existing installer/configuration authority. A connector's first
`hello`, discovered machine, image, public key or local health response cannot
authorize itself. Replacing a binding compares the previous admitted generation;
uncertain ownership is blocked, not repaired from a timestamp.
Any issuer-specific login/exchange/renewal is a selected provider adapter's
responsibility, with attended requirements exposed before unattended use.
Do not introduce a mandatory external IdP or hide a second issuer service in a UI.

## Selected Windows/Entra application adapter

Operator, verbatim:

> Yes, Windows/Entra. Though we only need this to go through the API or any open ports on the Linux machines that aren't its management/SSH flow

This selects one adopter's application identity provider, not a mandatory
vendor for fleet-manager. It covers exposed fleet APIs and independently exposed
non-management Linux application listeners. Existing SSH and substrate management
authentication remain unchanged and independently protected. Private index/model
listeners do not become public or anonymous to avoid this requirement.

**Agent-recommended implementation amendment; review before provider code:**

1. For an Entra-issued API credential path, establish an approved fleet API
   resource/audience, Windows public-client registration, exact tenant/account
   selection and API permissions. Treat those
   as non-secret adopter configuration, never built-in identifiers. Choosing
   the provider does not authorize directory application creation, consent or
   permission grants. Tokens for unrelated Microsoft/source APIs and ID tokens
   are not fleet access credentials. These are requirements of that OAuth path,
   not an operator instruction to provision new directory applications for
   every Windows-backed enrollment design. Any alternative must establish real
   verified platform identity and reviewed key custody/enrollment; a PID,
   forwarded username or self-asserted account is not sufficient.
2. Reuse maintained MSAL/Windows broker acquisition and protected key custody.
   Silent renewal continues while the actual provider session permits it;
   background roles never initiate unattended sign-in UI. Preserve distinct
   usable, actual challenge, denial/revocation and temporary-unavailability
   outcomes, with no local presence timer or alternate-account fallback.
3. Prove the actual broker credential/proof format for the selected registered
   API before choosing the serving profile. MSAL Python's documented
   `PopAuthScheme` binds method, URL and resource nonce through the broker.
   Microsoft distinguishes legacy Signed HTTP Request (SHR) from RFC 9449 DPoP;
   neither the word "PoP" nor a successfully acquired token proves compatibility
   with the current ES256/P-256 `at+jwt`/`dpop+jwt` prerequisite.
4. Have the retained authentication owner review and supply any needed explicit
   provider profile through the common immutable identity/admission boundary.
   Use maintained signature/proof validation, configured tenant/issuer/audience/
   client/permission checks and current trust; do not broaden `jwt-pop-v1`,
   relabel a legacy proof or add bearer-only outage fallback. Stable actor
   mapping must use verified provider identifiers, not email/display names.
5. Keep enrollment/grants and backend delegation separate from user identity.
   The Gateway authenticates the exposed application edge; a fixed private
   service independently validates narrowed execution authority. It does not
   receive the user's broad source token or gain management/SSH authority.
6. Inventory listeners at deployment: only approved authenticated application
   endpoints are externally reachable. Keep index/model/backend sockets private
   unless their service supplies the reviewed selected boundary. Existing
   management endpoints retain their own configured protection; exclusion
   from this adapter is not an anonymous-management exception.

Provider evidence sources:
[MSAL Python public API and broker configuration](https://msal-python.readthedocs.io/en/latest/),
[MSAL Python PopAuthScheme](https://github.com/AzureAD/microsoft-authentication-library-for-python/blob/d73407a8137f409c40d018716bf98c5d9e71bf5d/msal/auth_scheme.py),
[Microsoft's PoP variants and Windows broker guidance](https://learn.microsoft.com/en-us/entra/msal/dotnet/advanced/proof-of-possession-tokens),
and [resource ownership of Entra access tokens](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens).
These establish provider/API requirements, not a live interoperability result.

### Additional validation gates

- Prove silent acquisition/renewal for the approved API on a real Windows
  broker; verify exact account/tenant/audience and provider key custody.
- Exercise actual selected-token/proof server interoperability over verified
  TLS, including nonce/restart/replay, wrong resource/tenant/client and expired
  or revoked enrollment. Synthetic cryptographic tests remain unit evidence only.
- Prove challenge/unavailability handling without unattended UI, forced
  presence checks, source-token substitution or fallback.
- Prove Linux listener reachability matches the approved exposure inventory
  and that management/SSH behavior and existing authentication are unchanged.

## Binding the existing consumers

### Origin push and current views

Authorize `observation.publish` for the exact admitted origin, installation,
producer and project before calling `ObservationStore.apply`. The trusted
adapter derives/checks `OriginBinding`; a submitted generation is not a CAS
replacement request. Binding changes require the separate enrollment authority.
Map opaque transport installation identities to product identities explicitly
and bijectively where required; never normalize away separators or collisions.

Exporter sequence, complete source publication barriers, removals and replay
gaps remain the source adapter's responsibility. Authentication does not turn
partial point observations into a complete roster. A view lease cannot enlarge
origin publication authority or mutate an existing agency/claim owner.

Authorize query and subscription selections by principal, fleet, project,
entity and fields. The store's internal global positions and typed cursors must
not be sent directly to scoped clients. A wire cursor is an opaque protected
reference bound to store identity, principal/view, effective grant generation,
selection and expiry; base64-encoding an integer is insufficient.
Reopening a view reauthorizes before any lookup or replay. Scope loss invalidates
that view; a newly narrower scope requires a new authorized snapshot.
Unrelated policy revisions may require internal revalidation, not disclosure of
foreign policy activity. The current store's global-head page invalidations also
cannot be forwarded unchanged as scoped wire behavior: the adapter needs a
bounded scoped snapshot/page boundary that preserves consistency without leaking
other origins' write activity. This remains an observation integration obligation,
not a capability already supplied by the local storage primitive.

Subscription attachment and renewal are authenticated requests. The server
binds the resulting lease to the exact channel/view and checks it before
emission. DPoP authentication of an HTTP attachment is not authentication of
every later WebSocket frame. Reauthentication needs its own bound request,
not a client-supplied identity object on the stream. Expiry/revocation closes
or pauses delivery explicitly; queued frames cannot bypass the new decision.
Heartbeats, cursor errors and presence/count metadata cannot reveal other views.

### Commands, receipts and fixed-service execution

Bind the verified actor/account namespace before operation-ID or result lookup.
Compare requested `OperationIntent.principal/account/authority` with that
binding; reject a mismatch rather than rewriting the intent and retaining
the old digest. Refreshing a token does not allocate a new operation ID.
Admission permission, target acceptance, effect outcome and obligation settlement
remain distinct.

The Gateway cannot forward its own broad credential or a serialized `Permit`
as a backend grant. Delegation requires an explicitly supported audience-bound,
sender-constrained credential/envelope from the selected auth authority. It
binds the original actor, Gateway delegator, exact destination/offer generation,
action, narrowed resources, canonical request digest and short validity.
The initial profile permits one Gateway-to-named-destination delegation hop,
not recursive redelegation or an arbitrary cross-Gateway chain.
The connector/service independently verifies the delegator, current local
admission, request binding and its own backend policy before retrieval/effects.
If that boundary cannot enforce a requested scope, advertise unsupported and
refuse; an unscoped adapter plus Gateway result filtering is not sufficient.

Credential, query and lifecycle grants are separate. Offer validity or backend
health does not grant source access. Allocation/effect-side fences remain
mandatory; expiry of a Gateway permit cannot prove a disconnected effect stopped.

### Credential request and reply

Authorize the enrolled worker's admitted job, configured source/account,
audience/permission set and recipient before selecting an eligible provider.
The caller cannot nominate an arbitrary provider, executable or token action.
Only an authorized, current provider subscription can answer the matching
request under its provider/account/audience policy.
Before initial delivery as well as redelivery, revalidate both the provider and
the requester's current job/recipient grants. An earlier admitted request does
not authorize releasing a queued credential after the requester loses access.

Bind request ID/content digest, requester/job, provider subscription generation,
recipient/key reference and expiry through reply verification and delivery.
A recipient encryption key must belong to the admitted recipient, not merely
be supplied in request text. Prefer a maintained recipient-bound encryption
envelope so routing does not require Gateway plaintext custody.
Any broader external token needs explicit provider policy; surrounding a broad
credential with a source-scoped request does not cryptographically downscope it.
An incapable provider returns unsupported/denied, not a mislabeled credential.

Persist only approved correlation/receipt metadata, never secret reply bodies.
Lost delivery acknowledgements, restart and provider disconnect have explicit
pending/unavailable/UNKNOWN outcomes. Reauthorize before bounded redelivery;
neither an old reply nor an observation event can execute or issue work again.
Provider renewal/revocation of external credentials remains distinct from fleet
identity revocation. Neither implicitly kills already accepted work.

### Resource-specific credential continuity

**Operator clarification, provider-neutral summary:** the fleet application
resource requests only its own required permissions. Separately governed source
resources can impose shorter renewal or interaction requirements; the relay and
redundant authorized producers address that independent availability problem.
The application must not add frequent human-presence prompts or forced periodic
sign-out. An adopter's acceptable interaction cadence is not a guaranteed
provider allowance or an application-imposed session deadline.

Keep the fleet-control identity session independent of each downstream
provider/account/resource credential. Access-token expiry normally triggers
silent renewal, not interactive sign-in. A source-resource challenge blocks the
affected credential need; it does not invalidate unrelated fleet-control access
or another resource's valid credentials. Fleet admission still enforces its own
current identity, enrollment, policy and revocation evidence.

A minimal application resource is not an exemption from tenant-wide or
resource-specific policy. The provider and tenant administrator retain authority
over sign-in frequency, device compliance, risk and revocation. Neither the
Gateway nor a relay producer may lengthen those allowances.

Redundancy selects only producers already authorized for the exact same
provider/account/resource/permission and recipient context. One producer's
unavailability or challenge need not prevent another independently eligible
producer from fulfilling that need. Recheck current eligibility and request
authority before delivery; do not silently select a different account, reuse a
revoked credential, broaden permissions, or use retries across producers to
circumvent a resource-wide denial or policy challenge. If no eligible producer
can supply a valid credential, expose one correlated resource-scoped blocked
outcome instead of repeated unattended sign-in or fleet-wide sign-out.

## Freshness, bounds and failure semantics

**Recommended initial implementation budgets, to prove before advertising support:**
access credentials at most 8 KiB; proof at most 4 KiB; no more than two
authentication credential/proof objects per request; clock skew at most
30 seconds; proof age at most 60 seconds; delegation lifetime at most
30 seconds and never beyond its parent; automatic view renewal at most every
30 seconds and always before credential/permit expiry. These are not deployed
settings and do not impose a human-presence lifetime.

Access-token and provider-session lifetimes come from the selected issuer/
provider. There is no extra universal five-minute access-token cap or
Gateway-imposed user-session countdown. Short derived grants, proofs and view
leases renew silently while the provider session and current grants permit it.
An expired token is replaced through normal authorized renewal, never stretched
locally; an actual provider challenge is honored rather than manufactured by
lease churn.

Durable fleet enrollment/policy changes take effect at the next admission or
known-revocation checkpoint. Local policy, provider identity revocation,
delegated backend permits and external resource-token revocation have different
authorities and propagation bounds. A signed JWT alone cannot promise immediate
issuer revocation. Each selected adapter must state/prove its revocation lease,
refresh/timeout and unavailable behavior; unsupported freshness cannot be
advertised as current.

Require current online authorization for enrollment/trust changes, new durable
command acceptance and credential release. Bound read/view/delegation leases
explicitly, with no extension from heartbeat or reconnect. When a required
policy/enrollment source is unavailable or too old, block new admission and
delivery rather than extend a cached allow. Target owners separately govern
already accepted/offline work; auth failure is not an automatic cancellation,
retirement, release or local fallback.

Cap token parsing, key refresh, nonce/replay state, per-principal/global requests,
view leases and credential pending state. Charge denied as well as successful
requests; bounded unknown-key/issuer failures must not amplify discovery or
exhaust another principal's allocation. Implementations must publish/test exact
caps, overflow outcomes and fair isolation, not assert that this proposal has
already chosen or measured every service limit.

Failures distinguish authentication required/invalid/expired, proof failure,
stale enrollment/generation, forbidden scope, unavailable authority, unsupported
profile/scope, resnapshot required and resource limit. Public responses disclose
only the authorized caller's safe category/retry action; internal diagnostics
retain bounded non-secret reason/correlation IDs, not tokens or foreign inventory.
There is no success-shaped empty query, silent provider/account switch, automatic
sign-in, auth downgrade or retry of an uncertain command effect.

## Implementation and validation gate

1. Review this proposal under the existing owners. Agree exact DTO/package
   placement, selected profile/library support and exclusive source edits.
   No current runtime is replaced or declared absent by this document.
2. Implement bounded verification/admission records and a real maintained-library
   verifier with synthetic issuer/proof keys. Prove wrong issuer/audience/type,
   algorithm/key substitution, stolen token without the proof key, replay,
   expired/not-yet-valid token, stale nonce/clock and restart/cutover fences.
   Cross many token/proof/view renewals with a long-lived provider session and
   verify zero Gateway-added presence prompts or forced sign-outs. Then inject
   the real challenge outcome, revocation, temporary outage and an explicitly
   opted-in dead-man expiry; prove distinct outcomes and safe resumption.
   The default has no dead-man check-in, including during lock/sleep/idle/reconnect.
3. Exercise the actual observation/operation contracts through admission:
   cross-origin/project/entity/field denial, content-bound actor/account lookup,
   generation transition, same-revision availability and snapshot/stream races.
   Verify no unauthorized lookup, count, hydration, emission or global-position
   disclosure occurs; test two principals with different scopes and unrelated
   writes/policy changes during scoped pagination and replay.
4. Prove separate real Gateway/connector/backend/client/provider processes with
   encrypted authenticated transport, explicit backend delegation, expiry/
   revocation, key rotation, issuer outage, slow consumers and credential
   correlation/recipient protection. Neither mocked crypto nor a health probe
   establishes this boundary.
   Include two independently governed source resources and redundant authorized
   producers: a source-specific challenge or shorter lifetime must not sign out
   fleet control, widen another producer's eligibility, hide a resource-wide
   denial, or permit expired/revoked delivery. Cache reuse is not renewal proof;
   observe an actually renewed provider credential without interactive APIs.
5. Run fresh Windows/Linux role/client and container installation in the existing
   clean-room/lifecycle lanes, then the separately authorized live lane.
   Test rejected trust changes preserving valid configuration, owned key custody,
   restart with pending work and provider separation. No key, enrollment, tenant
   permission, live lease or deployment is authorized by proposal review.

The shared interface is proposed, not delivered; all applicable full effort
Plan/Validation gates remain open. Existing native/SSH and local standalone use
remain unchanged. The next runtime slice consumes reviewed interfaces and
retained owner scopes, not silence as permission to build another auth stack.
