# Vault Service

- **Slug:** `vault-service`
- **Repo:** copilot-extensions
- **Branch(es):** planning PR, then independently admitted per-slice PRs to `dev`
- **Created:** 2026-10-24
- **Status:** Active
- **Vision:** [Vault service](../../../visions/vault/README.md), all Features and Behaviors
- **Umbrella issue:** #8729

## Goal

Deliver the explicitly selected [federated-relay vertical
slice](federated-relay.md) in the existing `agent-vault` service: consume a
Controller-owned local Gateway conduit, establish the authenticated outbound
service channel, advertise auth-request readiness and return authorized provider
results to an actual bound consumer. Preserve independent local Vault operation
and all remaining credential/transaction, interaction and deployment gates.
The slice is not complete at a protocol declaration, library, connection or PR.

Make the complete reviewed Vault credential/transaction and independent
deployment vision realizable through the unchanged Guiding Intent below.
The [current lifetime amendment](../source-managed-runtime-installs/update-service-lifetimes.md)
requires accepted setup/start and the service tree to outlive updater
cancellation. Outside the bounded slice this remains a proposal: architecture
review, owner/slice admission, credential and deployment gates are not waived.

## Guiding Intent

Make the reviewed Vault vision realizable by composing existing credential,
coordination, Fleet and presentation capabilities. Vault owns credential
lifecycle and durable authentication transactions; Manager remains stateless.
The standard Gateway-backed Fleet supplies a central container, while local
Vault remains independently installable and optionally federated.

The initial contribution was architecture and effort authoring only, with
implementation-owner admission pending. The 2026-11-14 direct
implementation assignment now selects only the bounded federated-relay slice
below for source implementation and isolated qualification after its plan clears
normal review. It does not activate a deployed service, approve real credential
enrollment or admit the remaining platform/UI/migration campaign by implication.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| `patient-dial` | Initial architecture and plan authoring/PR stewardship only | Planning PR and #8729 |
| Federated-relay implementer | Existing `agent-vault` service/channel/provider composition and isolated consumer qualification only | Scoped claim in #8729 and implementation PRs |
| Implementation owners | Unassigned outside the bounded federated-relay slice | Future explicit assignment through #8729 |

## Coordination

<!-- backlog-reconciliation:7556:qualified-sixteen:3979:Coordination -->

The separate proposed finite #3979 headless/provider-miss family retains the actual
local Vault/provider credential custodian and optional standalone,
transport, broker, browser and native-interaction owners. `patient-dial`
still owns planning publication ONLY; P0 design/operator review and
implementation-owner admission for that family remain pending. A plan neither grants
unlock permission nor transfers master material, credentials or resources.

The inherited #3980/#3981/#5918/#5940/#5959 lanes retain their separate
machine/SSH-routing, endpoint/provenance/cell, installer/runtime/singleton,
transport and adopter custodians. This documentary intake assigns nobody
and moves no claim. P0 operator design review and implementation admission
remain design/execution gates, not prerequisites to recording these
unchecked requirements.

- **Topology:** one planning PR, then bounded per-slice PRs; source branches
  target `dev` and follow normal review and CI.
- **Host (owns PRs):** `patient-dial` owned the initial planning publication;
  the federated-relay implementer owns its admitted source PRs. Other
  implementation PR ownership must be assigned before work starts.
- **Delegates:** none. Existing credential, Fleet, installer, service and
  presentation owners retain custody; a plan is not a transfer.
- **Handoff:** operator review accepts or revises the proposal, then names
  implementation owners and their validation scope in #8729 and this effort.
  No planning worktree is bound to the entire future implementation campaign.
- **Tracker:** #8729 remains open across vision and planning merges.
  Planning completion never means the service is implemented or this effort Done.

### Admitted federated-relay slice

The 2026-11-14 source assignment and scoped issue claim admit the implementer
above for [the bounded relay plan](federated-relay.md), not the entire P0-P8
campaign or another contributor's native execution/custody work. Reuse
`agent-vault`'s lifecycle and the existing Gateway channel/account custodian;
the deployment Controller retains tunnel ownership. No source PR or isolated
test grants live enrollment, interactive authentication, installation or rollout.

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

The initial operator request, verbatim and public-safe:

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

**2026-11-14 implementation assignment (public-safe summary):** build the auth
relay in the existing `agent-vault` service as an explicitly selected
federated-relay mode. The deployment Controller exposes its protected Gateway
route locally; Vault consumes that conduit and announces auth-request readiness.
Drive through an actual service/provider/consumer flow, not a connection-only
or source-library milestone. Private adopter details are omitted rather than
quoted into the public effort.

## Plan

- [ ] Complete [R1-R4 federated-relay delivery](federated-relay.md#delivery)
  through the real service/provider/consumer boundary; retain complete parent
  transaction, platform, presentation and deployment acceptance separately.
- [ ] Reflect independent accepted setup/start ownership in the proposed local
  and central service lifecycle; retain P0 and every original implementation
  admission gate rather than treating this alignment as permission to execute.

**Agent-recommended proposal:** the phase decomposition, technical boundaries
and validation design below are recommendations derived from the reviewed
intent, not additional operator instructions. Implementation outside the
separately admitted R1-R4 relay slice still awaits explicit owner/slice admission.
The original proposed local transaction slice and the admitted relay slice do
not narrow the complete effort.

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

<!-- backlog-reconciliation:7556:qualified-sixteen:3979:Plan -->

### Proposed finite headless provider-miss transaction contract

_Agent-recommended P1/local transaction clarification. This finite family
remains proposed outside the separately admitted federated-relay slice;
no runtime, interaction, credential or implementation admission follows
from retaining it._

- [ ] **U3979-P1:** Keep provider-available, interactive controlling-terminal
  SSH and fully headless non-WSL SSH/provider-miss branches distinct.
  Provider-first and real inline TTY unlock are bounded credit;
  unavailable/unlocked daemon cases are not the reachable locked/no-provider
  miss.
- [ ] **U3979-P2:** Preserve authorized NONINTERACTIVE unlock OR a mechanism
  reaching the operator for the original headless case. Headlessness is not
  consent; broker/provider integration stays optional for standalone use
  and the actual Vault custodian retains verification.
- [ ] **U3979-P3:** Make provider miss/error/needs-unlock actionable at the
  originating CLI rather than silently returning failure after a boolean
  helper. Typed daemon results are credit, not proof the CLI conveyed
  explanation or reached the operator. Preserve useful next action without
  exposing or relocating master material.
- [ ] **U3979-P4:** Preserve narrowly owned local/provider/browser/native
  transactions, redacted input and honest unavailable/refused controls.
  Preserve existing interactive unlock and cooldown behavior while
  correcting the headless provider-miss branch.
  No mandatory broker, new executor/credential grant or whole central/
  federation/platform portfolio prerequisite is introduced for this
  finite branch.

- [ ] **U3979-P5:** Resolve authorized tunnel targets (#3980) from existing
  machine/SSH routing authority, not another registry. Fan out only after
  verified source Vault unlock, retaining source/target authorization and
  per-target success, denied, unknown, unreachable and partial outcomes.
  Discovery, enrollment or an open port never authorizes master-material
  broadcast.
- [ ] **U3979-P6:** Preserve intentional origin-Vault selection over general
  SSH reverse tunnels (#3981), correct database/provider-group storage
  context, local/origin coexistence and selected-origin failure without
  substitution of an unrelated local Vault. Keep unconfigured standalone use.
- [ ] **U3979-P7:** For explicitly cell-scoped operations (#5918), carry and
  validate attributable same-cell peer identity OR report unavailable.
  Newest/unscoped advertisements, load order and missing identity are not
  authority. Preserve ordinary unconfigured legacy WSL discovery and
  evidenced deliberate recovery/management override.
- [ ] **U3979-P8:** Acquire/reconcile cell-scoped process ownership before
  socket removal, bind or endpoint/PID publication (#5940); preserve a
  matching live incumbent. Revalidate exact owner, generation and
  process-birth identity before retirement/cleanup, including contention,
  PID reuse and mixed native-IPC/TCP outcomes. Preserve cold start,
  explicit recovery and warm-secret handoff without exposing secret state.
- [ ] **U3979-P9:** Carry bounded, secret-safe literal built-in and extension
  transport failures through actual final command consumers (#5959).
  Preserve later successful alternate transport and distinguish genuine
  absence from denied, malformed, timed-out or failed routes. Generic
  not-running output is not the cause; unknown effects permit no replay.

## Validation Plan

- [ ] Qualify the [federated-relay matrix](federated-relay.md#validation):
  actual service startup/channel/readiness/demand/result, exact grants,
  standalone compatibility, disconnect/reconnect, uncertain delivery and
  independently packaged installation; no source-only end-to-end claim.
- [ ] Map the applicable [L1-L4 caller-loss cases](../source-managed-runtime-installs/update-service-lifetimes.md#validation-and-completion)
  into the existing proposal's validation without using real credentials,
  changing a live service or bypassing architecture/owner admission.

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

<!-- backlog-reconciliation:7556:qualified-sixteen:3979:Validation-Plan -->

- [ ] **U3979-V1:** Resolve U3979-P1 through P4 at the original supported
  headless/provider/terminal consumer tier: authorized provider success,
  controlling-terminal success, reachable locked fully headless miss and
  unavailable/unlocked controls through the actual CLI/daemon boundary.
  Qualify the selected original OR alternative and actionable
  needs-unlock/error/next-action counterpart, with no secret-bearing
  presentation. Boolean false/exit failure or invisible-dialog avoidance
  alone is not the result; neither is a daemon-only mock.
  Cover existing interactive unlock and cooldown controls without
  weakening their behavior to make the headless case pass.
- [ ] **U3979-V2:** Keep this family's P0 design and implementation-owner admission,
  optional standalone/broker/transport and credential/native custody
  intact. Separate source/fixture/native/installed results without making
  every unrelated parent item a finite-case prerequisite. No real unlock,
  operator ceremony, credential operation or policy selection is performed
  by this planning retention.

- [ ] **U3979-V3:** Prove authorized dynamic target selection and fan-out
  only after successful source unlock, including per-target success,
  denied, unknown, unreachable and partial outcomes plus standalone use.
  Plan with synthetic routing/descriptors; no live enumeration, SSH or unlock.
- [ ] **U3979-V4:** Prove reverse-tunnel origin selection, local/origin
  coexistence, selected-origin failure, correct storage context and
  unconfigured standalone behavior.
- [ ] **U3979-V5:** Cover explicit same-cell, unrelated-cell, unattributed
  legacy, missing/corrupt record, unconfigured legacy and explicit-override
  cases. Missing identity is not a pass.
- [ ] **U3979-V6:** Prove custody precedes endpoint mutation under concurrent
  acquisition, matching live incumbent, PID reuse, stale/partial/foreign
  identity and mixed IPC/TCP binding; old cleanup cannot erase a successor.
  Keep contained/source and separately authorized installed/native proof
  distinct.
- [ ] **U3979-V7:** Through actual final command consumers, cover successful
  fallback and exhausted absent/denied/malformed/timed-out/failed routes,
  bounded redaction and locked/scoped refusals. Installer diagnostics or
  mocks bypassing the consumer do not satisfy this gate.

## Proposal

[Architecture](architecture.md), [validation](validation.md) and
[migration](migration.md) are the proposed initial design, not as-is service
documentation. D1-D6 are tracked under #8729 with named validation obligations.
The admitted relay slice has its own reviewed source and isolated-consumer
qualification sequence. Outside it, operator design review and explicit
implementation admission remain the next gate; publication does not start P1.

## Journal

### 2026-11-15 - Local transaction and admitted relay scopes distinguished

The effort is Active for the separately admitted federated-relay slice.
The finite U3979 family remains proposed under its own P0 design and
implementation-owner admission; retaining its unchecked contract does not
grant the relay implementer that other work or resume the whole campaign.
The independent relay Goal, owner and Plan/Validation remain unchanged.

### 2026-11-15 - Inherited tunnel, cell and transport lanes proposed

Added ten unchecked U3979 Plan/global Validation rows and five supporting
detailed-matrix cases for the inherited #3980/#3981/#5918/#5940/#5959 union.
Existing headless OR acceptance, V1-V10, qualification levels, owners and
P0/D1-D6 gates remain unchanged. Documentary review is not implementation,
credential/native interaction, source retirement or resource transfer.

<!-- backlog-reconciliation:7556:qualified-sixteen:3979:Journal -->

### 2026-10-29 - Proposed finite provider-miss acceptance

Added direct unchecked #3979 Plan, actual global Validation and continuing
custody after whole current contract and complete fixed-ROOT qualification.
Provider-first/controlling-terminal improvements remain credit; fully
headless provider miss still retains authorized unlock OR operator reach
and actionable failure at the originating consumer. The effort remains
Draft with all first gates and current planning-only ownership unchanged.
No implementation, credential/native interaction, transfer or source
disposition occurred; review, actual delivery and current readback remain
outstanding.

### 2026-10-24 - Reviewed intent and initial proposal

- Canonical Vault vision merged in PR #8734 at
  `022feaa541463ceea79a9bc5a98eafe862edde1d`; read back before deriving this plan.
- Captured the latest Request verbatim and separately labeled the earlier
  deployment summary. Proposed composition preserves Vault credential and
  transaction authority, stateless Manager and all deployment modes.
- `patient-dial` owns initial planning publication only. The effort is Draft;
  implementation, native interaction, migration and rollout remain unadmitted.
