# Vault Service

- **Slug:** `vault-service`
- **Repo:** copilot-extensions
- **Branch(es):** planning PR, then independently admitted per-slice PRs to `dev`
- **Created:** 2026-10-24
- **Status:** Draft
- **Vision:** [Vault service](../../../visions/vault/README.md), all Features and Behaviors
- **Umbrella issue:** #8729

## Guiding Intent

Make the reviewed Vault vision realizable by composing existing credential,
coordination, Fleet and presentation capabilities. Vault owns credential
lifecycle and durable authentication transactions; Manager remains stateless.
The standard Gateway-backed Fleet supplies a central container, while local
Vault remains independently installable and optionally federated.

**This contribution is initial architecture and effort authoring only.**
The proposed implementation sequence is not execution or deployment consent.
The effort stays Draft until the operator accepts the architecture and admits
implementation owners and slices. No login, browser/native interaction,
credential mutation or service installation is authorized by this document.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `patient-dial` | Initial architecture and plan authoring/PR stewardship only | Planning PR and #8729 |
| Implementation owners | Unassigned; admission pending, no execution claim | Future explicit assignment through #8729 |

## Coordination

- **Topology:** one planning PR, then bounded per-slice PRs; source branches
  target `dev` and follow normal review and CI.
- **Host (owns PRs):** `patient-dial` owns only this planning publication.
  Implementation PR ownership must be assigned before work starts.
- **Delegates:** none. Existing credential, Fleet, installer, service and
  presentation owners retain custody; a plan is not a transfer.
- **Handoff:** operator review accepts or revises the proposal, then names
  implementation owners and their validation scope in #8729 and this effort.
  No planning worktree is bound to the entire future implementation campaign.
- **Tracker:** #8729 remains open across vision and planning merges.
  Planning completion never means the service is implemented or this effort Done.

## Context

The canonical vision landed in [PR #8734](https://github.com/ThomasMichon/copilot-extensions/pull/8734),
merge `022feaa541463ceea79a9bc5a98eafe862edde1d`. It governs the proposal,
not the reverse.

[Initial architecture](architecture.md) separates verified public building
blocks from proposed composition. `agent-vault` supplies local custody and
extension seams; `fleet-contracts` and `fleet-vault-adapter` supply bounded
credential-pull shapes, not the new Vault authority. Gateway has distinct
authenticated credential and outbound-service profiles; neither its
ephemeral delivery state nor a connection is a durable Vault transaction.
Manager's generic progress action currently owns a CLI child, so its Cancel/
unmount behavior cannot be treated as cancellation of durable service work.

No `services/vault` implementation is established by this effort.
[Validation](validation.md) maps every vision item to an acceptance obligation;
[migration](migration.md) preserves existing adopters and service ownership.

## Request

The latest operator request, verbatim and public-safe:

> Right. Manager is intended to be a stateless UX surface, not the owner of the flow or the credentials. It should surface the request, show necessary information, and provide a way to launch a browser to perform interactive flows, or be able to launch the correct WAM-backed flow, such as flows through GCM or Windows-native Entra. But the Vault service itself should own the credential and transaction management.
>
> Your overall description sounds good; we should build out the vision first, and then use pieces of what we have to help drive initial architecture and effort build-out.

**Earlier deployment intent (summary, not a verbatim quotation):**
Gateway-backed Fleet's standard profile installs a central Vault container;
machine-local Vault installs independently without Fleet or central access and
may optionally federate through explicit trust. Existing provider/OS stores
may retain physical custody under Vault's logical credential and transaction
authority. These requirements are already captured in the reviewed vision and
#8729. Planning does not authorize vendor login, native interaction or cutover.

## Plan

**Agent-recommended proposal:** the phase decomposition, technical boundaries
and validation design below are recommendations derived from the reviewed
intent, not additional operator instructions. All implementation is unchecked
and awaits explicit owner/slice admission. The proposed first vertical slice
is a useful start, not a narrowing of the complete effort.

### P0 - Accept architecture and admit implementation

- [x] Establish the canonical vision through merged PR #8734.
- [ ] Review this initial architecture/plan through normal PR review and
  operator design review; resolve [D1-D6](architecture.md#open-decisions-and-risks).
- [ ] Assign implementation owners, first slice and qualification permissions
  through #8729; preserve other owners' source and runtime custody.

### P1 - Contracts and first local vertical slice

- [ ] Define typed service/provider/consumer contracts, access bindings,
  protected custody and delivery-or-use semantics; qualify reuse before
  extraction ([architecture](architecture.md#contracts-and-ownership)).
- [ ] Build the proposed minimal slice: headless intake, durable request,
  fake-provider explicit interaction, protected synthetic credential adoption,
  consumer-readiness acknowledgement and reconnectable CLI observation.
  Prove dismissal is not cancellation and no discovery starts interaction.

### P2 - Complete credential lifecycle and durable recovery

- [ ] Implement supported-provider acquire, bounded cache, scoped delivery or
  authorized use, renew, expiry and revoke; provider denial remains authoritative.
- [ ] Qualify coalescing, per-consumer outcomes, attempt fencing, bounded
  retry/polling, service restart, uncertain replies and replacement handover;
  migrate no production queue merely because these tests pass.

### P3 - Standalone and central service construction

- [ ] Construct independently installable local service/CLI and container
  distributions with protected durable state, attributable readiness,
  platform-native supervision and state-compatible rollback.
- [ ] Qualify Windows, Linux/WSL and macOS local behavior without Fleet,
  Gateway, Manager or Copilot; qualify the container without desktop assumptions.
  Follow the existing installer and graceful-cutover owners, not new loops.

### P4 - Browser and actual native provider qualification

- [ ] Adapt supported browser authorization-code/PKCE and device-code flows
  with explicit user actions, bound callback/attempt identity and correct
  requester, consumer/custody and interaction-host placement.
- [ ] Implement and separately qualify actual WAM-backed GCM and
  Windows-native Entra paths where applicable, including nonexportable
  authorized use. Real platform/account tests require explicit permission;
  simulations cannot satisfy this phase ([native gates](validation.md#qualification-levels)).

### P5 - Stateless Manager and CLI presentation

- [ ] Contribute Vault request summary/view actions and progress using existing
  presentation primitives; do not add another modal/protocol solely for Vault.
- [ ] Separate client/subscription dismissal from explicit Vault cancellation,
  eliminate credential-bearing output, and prove client replacement preserves
  service-owned work ([presentation](architecture.md#presentation-and-cancellation)).

### P6 - Gateway-backed Fleet standard deployment

- [ ] Add the central Vault container to the Gateway-backed standard integrated
  profile through existing declared slots, protected outbound service routing
  and selected external deployment-manager lifecycle.
- [ ] Prove complete installed readiness, scoped consumer access and
  rollback/recovery without moving container ownership to Manager or relying
  on Fleet membership as credential permission. Preserve standalone use.

### P7 - Optional explicitly trusted federation

- [ ] Implement scoped local/central and peer federation with one authoritative
  credential/transaction owner, explicit custody, bounded offline use and
  revocation freshness; no implicit replication or fallback ownership.
- [ ] Qualify partitions, trust withdrawal, stale callbacks, concurrent owners,
  recovery and consumer restrictions before admitting any federated adopter.

### P8 - Adopter migration and complete acceptance

- [ ] Execute [the migration plan](migration.md) only for explicitly admitted
  adopters, preserving pending requests, credentials, history and native
  execution authority through reversible cutover.
- [ ] Resolve every [validation obligation](validation.md), update actual
  reality/operator docs, and record source, release, installed and live
  outcomes separately. Close or transfer remaining tracked obligations before
  declaring the effort Done; no phase or PR alone satisfies that gate.

## Validation Plan

All runtime acceptance below is **pending**, not evidence of an implementation.
The [validation matrix](validation.md) is the detailed, agent-recommended plan.

- [ ] V1: typed bindings, complete credential lifecycle and protected delivery/use.
- [ ] V2: durable coordination, coalesced-consumer cancellation and recovery.
- [ ] V3: real browser/PKCE and device-code qualification under explicit consent.
- [ ] V4: actual Windows WAM-backed GCM and native Entra qualification.
- [ ] V5: stateless Manager/CLI, UI teardown and no secret-bearing presentation.
- [ ] V6: independent local and central container construction, cutover/rollback.
- [ ] V7: Gateway/Fleet standard-profile installed readiness and scoped access.
- [ ] V8: trusted federation, offline bounds, expiry and revocation continuity.
- [ ] V9: adopter migration, protected history and reversible ownership transfer.
- [ ] V10: complete vision coverage, portable platform results and separate
  source/release/installed/live completion records.

## Proposal

[Architecture](architecture.md), [validation](validation.md) and
[migration](migration.md) are the proposed initial design, not as-is service
documentation. D1-D6 are tracked under #8729 with named validation obligations.
The immediate next gate is operator design review and explicit implementation
admission; publication alone does not start P1.

## Journal

### 2026-10-24 - Reviewed intent and initial proposal

- Canonical Vault vision merged in PR #8734 at
  `022feaa541463ceea79a9bc5a98eafe862edde1d`; read back before deriving this plan.
- Captured the latest Request verbatim and separately labeled the earlier
  deployment summary. Proposed composition preserves Vault credential and
  transaction authority, stateless Manager and all deployment modes.
- `patient-dial` owns initial planning publication only. The effort is Draft;
  implementation, native interaction, migration and rollout remain unadmitted.
