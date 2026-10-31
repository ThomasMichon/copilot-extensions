# Vault Service - Initial Architecture Proposal

Back to [the effort](README.md). Authority comes from the
[reviewed Vault vision](../../../visions/vault/README.md) and #8729.
**All composition, contracts and sequencing proposed here are
agent-recommended, not implemented or independently operator-approved.**

## Source-grounded reuse assessment

These are bounded source observations, not installed-readiness claims.

| Existing public owner/reference | Observed capability and limit | Proposed treatment |
|---------------------------------|-------------------------------|--------------------|
| [agent-vault architecture](../../../plugins/agent-vault/docs/architecture.md), [extensions source](../../../plugins/agent-vault/src/agent_vault/extensions.py) | Local KeePass-backed daemon, protected cache and extension hooks; extensions are permissive/fail-open, not an authenticated transaction contract | Reuse custody capabilities through an admitted adapter; preserve standalone consumers and do not inherit fail-open authorization |
| [GCM delegation source](../../../plugins/agent-vault/src/agent_vault/gcm.py) | Allowlisted local Git helper delegation, with noninteractive refusal when prompting is disabled | Adapt provider invocation and no-prompt behavior; existing helper presence is not proof of WAM or native Entra support |
| [fleet-contracts](../../../libs/fleet-contracts/README.md), [credential records](../../../libs/fleet-contracts/src/fleet_contracts/records.py) | Bounded credential parameters/results and offer identity; parsing does not authenticate, persist or authorize | Reuse bounded typed-envelope conventions; evolve explicitly for lifecycle, device/session and use-only capabilities |
| [fleet-vault-adapter](../../../libs/fleet-vault-adapter/README.md), [route source](../../../libs/fleet-vault-adapter/src/fleet_vault_adapter/adapter.py) | Fixed-backend `credential.get` pull; no minting, cache, store, push or authorization | Preserve compatibility only where qualified; not the new Vault service or a full lifecycle API |
| [Gateway runtime](../../../fleet-manager/docs/gateway-runtime.md), [credential broker source](../../../fleet-manager/src/fleet_manager/gateway_broker.py) | Enrolled/scoped credential policy and bounded ephemeral controller delivery | Reuse security and policy integration; ephemeral delivery is not Vault's durable transaction ledger |
| [Gateway service profile](../../../fleet-manager/docs/gateway-service-profile.md), [manifests](../../../fleet-manager/docs/gateway-manifests.md) | Separate opt-in authenticated outbound service roles, namespace/caller admission and live policy | Compose Vault as its own declared service; do not silently turn legacy credential routes into lifecycle authority |
| [role deployment plans](../../../fleet-manager/docs/role-deployment-plans.md) | Pure role planning with external deployment-manager ownership; a projected Stack is not an installed service | Extend standard-profile composition through the existing effect owner; no Vault/Manager container scheduler |
| [Manager progress screen](../../../worktree-manager/src/worktree_manager/pivot_progress.py), [action streaming](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/tasks.py), [pivot manifest](../../../worktree-manager/src/worktree_manager/production_picker/picker_tui/pivot_manifest.py) | Generic contributed actions/progress; cancellation and runtime close kill the locally owned CLI child | Adapt presentation to short client commands/subscriptions; never place durable Vault execution in that child |
| Candidate coordination capabilities | Durable queue, deduplication, conditional updates, attempt generations, expiry, presence and consumer-driven outcomes are extraction candidates, not an asserted public implementation | Review source provenance, licensing, ownership and contract compatibility before reuse; extract generic capabilities without adopting an unrelated service wholesale |

The new home is proposed as `services/vault`, with independently usable
service/CLI packaging and thin optional integrations. That path names intended
source organization only: no runtime is created by this proposal. Backend
independence is retained; the new service is not a blanket rewrite of
`agent-vault`, the Gateway or Manager.

## Contracts and ownership

### Logical authority versus physical custody

Vault owns credential acquisition, bounded cache policy, authorized delivery or
use, renewal, expiry and revocation management, plus durable authentication
transactions. Providers retain issuer/account policy and may refuse renewal,
require user interaction, expire access or revoke it independently. Vault
reconciles that evidence and stops authorizing invalid access.

OS/provider stores, existing local vaults and custody agents implement physical
custody under this logical authority after explicit integration/migration.
They do not become new competing transaction owners. Nonexportable native
credentials require an authorized-use capability, not token export disguised
as universal compatibility. Manager never receives credential material.

### Proposed typed seams

Contract names here are conceptual, not wire/API commitments:

| Seam | Responsibility | Required boundary |
|------|----------------|-------------------|
| Consumer access request | Identify consumer, requested capability and binding; retain request identity across uncertain delivery | Authenticated consumer and explicit account/tenant/resource/audience/scopes; membership alone is insufficient |
| Credential lifecycle provider | Acquire, locate/protect cache, deliver or use, renew, expire and revoke within provider capability | Explicit provider allowlist, issuer authority, protected handles and capability-honest unsupported outcomes |
| Transaction coordination | Durable acceptance, per-consumer attachment, interaction intent, provider progress and outcome reconciliation | Vault is the sole durable owner; no transaction execution lifetime attached to a CLI or UX |
| Interaction host | Execute one typed, admitted browser/native capability in a named user session | Explicit user intent, bound provider/attempt/callback; no arbitrary request-selected executable, URL or environment |
| Observation and user action | Non-secret request summaries, information, subscription/recovery, explicit launch/cancel requests | Read-only discovery has no ceremony effects; a submitted action is not completed authentication |
| Consumer readiness | Confirm actual correctly bound adoption/use at the intended consumer | Provider success and approval cannot stand in for readiness |
| Federation | Route to an explicitly trusted owning Vault and admitted custody/interaction agents | No second durable owner, implicit mirror or credential redistribution |

Gateway authenticates/enrolls and authorizes the service route through its
existing owner. Vault also enforces credential-specific consumer/provider
policy at the operation boundary. The two layers are complementary, not
competing issuers or interchangeable permission checks.

### Three distinct execution locations

The **requesting device**, **credential custody/consumption device** and
**attended interaction host** are separate choices. A headless consumer may
request access held in a Windows-native store while the user acts in that
Windows session; a central container remains the logical transaction owner
without assuming desktop, WAM or logged-in account access.

Binding includes consumer and Vault authority, provider, device and native
user/session identity, selected account and tenant, resource/audience/scopes,
and the interaction attempt and callback destination. Provider adapters resolve
allowed native actions; callers do not supply command recipes.
Callback destinations and PKCE material remain provider-owned and bound to
the admitted attempt. Late, duplicated, mismatched or superseded callbacks
cannot complete a different request or revive revoked access.

Provider intermediates, refresh material, authentication codes and PKCE
verifiers remain in protected provider/service custody, never the ordinary
transaction journal, UX, logs or session environment. Explicitly designated
public device-code instructions may be shown to the intended user; that
exception does not make access tokens or callback secrets displayable.

## Durable transaction and credential model

Recommend a transactional service-owned ledger on separately managed durable
storage, with secret material/handles protected by the selected custody owner.
Acceptance commits before acknowledgement. It retains request identity,
consumer attachments, access binding, attempt generation, effect uncertainty
and non-secret outcome history across crash/replacement.

Conditional updates and current attempt ownership fence stale pollers,
callbacks and successors. Equivalent requests may coalesce only when their
provider/access and execution bindings permit it. Each consumer still has its
own cancellation, adoption and readiness outcome. Presence can influence where
an interaction is offered; it is never permission, ownership transfer or a
reason to drop accepted work.

Vault owns bounded provider polling/retry and expiry. Retry of a communication
operation first reconciles acceptance and known effects; it does not relaunch
a ceremony under a new identity. User approval, provider authentication,
protected credential adoption and consumer readiness are separate facts.
Unknown or disconnected consumers stay pending/unknown.

Provider revocation/expiry invalidates the relevant serving eligibility and
derived caches according to current provider evidence. Revocation management
includes stopping local authorization and requesting issuer revocation where
supported; unsupported issuer operations are reported honestly, not fabricated.
No claim of instantaneous remote revocation during a partition is made.

## Presentation and cancellation

Reuse Manager's contributed summary/view/action and progress presentation, not
a Vault-specific modal or a second protocol for the same UX. CLI commands are
short service clients; an observation stream can reconnect to Vault's
authoritative snapshot/history. Closing one only ends local observation.
Presentation refresh is not durable authentication polling.

**Known integration trap:** `PivotProgressScreen` signals cancellation on
Cancel and unmount; its action runner kills the owned CLI child. Thus that
child must not own a Vault transaction or its native interaction process.
A done/exit frame means the client action ended or was accepted, not that
authentication or consumer readiness succeeded. Adapt generic presentation
semantics where necessary without changing unrelated actions' cancellation.

Use separate, clearly labeled **Dismiss/Close view** and **Cancel request**
intent. The former may close its local subscription without a service cancel.
The latter invokes Vault for the selected request, current attempt and consumer
scope. Cancelling one coalesced consumer must not cancel another's continuing
need or revoke an unrelated credential. Only Vault/provider authority decides
whether a now-unneeded interaction can be stopped; an uncertain stop remains
uncertain. Launching a browser/native window is a separate explicit user action.

## Placement, lifecycle and federation

- **Local:** independently installable service/CLI on Windows, Linux/WSL and
  macOS. Native capabilities differ explicitly, but durable lifecycle semantics
  do not. Local discovery, intake and cache/use need no Gateway, Fleet, Manager
  or Copilot. Existing platform service/installer owners supervise it.
- **Central:** the Gateway-backed standard integrated profile includes a Vault
  container and durable storage. Existing selected external deployment manager
  owns container creation, replacement and retirement. Gateway owns protected
  routing; Vault owns transactions/credentials; Manager owns neither lifecycle.
  The declaration is not readiness or permission to activate it.
- **Federation:** explicit local-to-central or peer trust identifies the owning
  Vault for each responsibility and admitted consumers/custody agents. Initially
  recommend owner routing rather than replicated writers. No credential copying
  is implied by connecting services or enrolling machines.

Disconnected local-owned work can continue only within provider validity,
existing grants and a declared revocation-freshness/offline budget. Work owned
elsewhere stays unresolved when its owner cannot be reached. Expired trust,
stale revocation evidence or uncertain ownership cannot trigger an automatic
ownership transfer or a broader provider fallback.

Installation composes the existing immutable-runtime, endpoint-discovery,
[service supervision](../../../docs/patterns/service-lifecycle-supervision.md)
and [graceful cutover](../../../docs/patterns/graceful-daemon-cutover.md)
contracts. Compatible handover is preferred; bounded cold recovery still
preserves one writer and reasonably durable accepted work. Candidate readiness,
activation, recovery, retirement and rollback are distinct. State-incompatible
rollback refuses rather than resetting a ledger or repeating native effects.
See [migration](migration.md) for the separately gated adopter transition.

## Open decisions and risks

All decisions are tracked under #8729; none authorizes implementation or
runtime effects. The proposed defaults guide review, not silent final choices.

| ID | Decision / proposed default | Required resolution and validation |
|----|-----------------------------|------------------------------------|
| D1 | Provider contract evolution versus compatibility adapter; retain existing independent backends | Owner review of actual consumer contracts, capability discovery and denial behavior; V1/V4/V9 before changing a consumer |
| D2 | Transactional local ledger, protected provider state and ownership-fenced recovery; concrete storage/retention not selected | Demonstrate crash, uncertain-effect and replacement recovery, bounded history and safe rollback; V2/V6/V9 |
| D3 | Typed interaction-host adapter for browser, WAM-backed GCM and native Entra; no generic remote command host | Select supported real platform/provider combinations and native session/callback authority; V3/V4, explicit account/interaction permission before execution |
| D4 | Existing Manager actions/progress with service-owned execution and separate dismissal/cancel | Qualify all teardown paths and terminal-frame meaning, coalesced consumers and unrelated action compatibility; V2/V5 |
| D5 | Separate Vault outbound-service role, standard-profile central container and independent local distribution | Fleet/deployment owners choose bootstrap credentials, source/artifact admission, storage and support matrix without circular dependency on an unavailable Vault; V6/V7 |
| D6 | Optional federation uses owner routing, no replicated durable writers | Define trust/transfer, offline and revocation-freshness budgets per provider; partition and trust-withdrawal proof V8/V9 before federation admission |

## Proposed first vertical slice

After P0 acceptance and owner admission, recommend P1 with synthetic credentials
and a fake provider in an isolated local service: headless request intake,
durable acceptance, later explicit interaction intent, provider outcome,
protected adoption, consumer readiness and CLI reconnect/dismiss/cancel.
This proves the ownership spine without account login or production migration.
P2-P8 remain mandatory: a fake provider, a local prototype or a successful
pull route is not native, central, federation or installed acceptance.
