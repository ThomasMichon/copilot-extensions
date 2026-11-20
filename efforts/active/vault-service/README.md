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

The reviewed [finite copy-transaction cases](copy-transaction-contracts.md)
retain complete #13077 relocation/fencing, #13083 selected-service copy
capability and #13085 uncertain overwrite-recovery acceptance under P2.
#13730 delivered complete planned receiving; current whole source/receiver/
custody readback and three separate audited judgments preceded acknowledged
NOT_PLANNED tracking retirements. All eighteen technical rows remain
unchecked. OPEN #8729 carries the planned work; the three retired sources
remain historical provenance, not pending proposal or receiving gates.
Existing CLI/service,
cache/journal, credential/provider and recovery custodians retain their work;
the relay implementer receives no new assignment. Preserve P0 design,
applicable owner/slice admission and the current R1-R4 priority. The three
source conditions and technical acceptance remain intact. Accepted tracking
does not appoint an implementer, resolve the defects or grant custody.
Planning authorizes no credential operation,
copy, rename, cache mutation, service restart, implementation or rollout.

The reviewed finite #11816 generation/freshness lane stays with the existing
`agent-vault` credential read/rotation, cache and consumer custodians under P2,
with direct P11816/V11816/G11816 below. It does not assign that correction to
the federated-relay implementer, change the current R1-R4 priority or grant
credential/runtime authority. Preserve the existing P0 design and applicable
owner/slice admission gates; recording finite requirements does not require
completing the unrelated relay/browser/native/migration portfolio.
#13590 delivered the planned receiving; complete current receiving/source/
custody readback and a separate judgment preceded #11816's acknowledged
NOT_PLANNED tracking retirement. All six technical rows remain unchecked.
This accepted tracking is not a generation fix, new implementation
appointment or resource release; the earlier proposal remains history.

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

#### Finite credential generation and freshness from #11816

Agent-recommended owner-side correctness receiving, outside the separately
admitted relay implementation. This is proposed source/fixture work, not
permission to read, rotate or cache an actual credential.

- [ ] **P11816-1:** Audit EVERY consumer of credential generation/freshness
  fences and both the live-read and password-rotation allocation paths.
  Retain the reported old-value/equal-generation observation under
  `time.time_ns()`, without interpreting it as lock bypass, credential
  disclosure or backend data loss. Nanosecond units prove neither unique
  ticks nor monotonic wall-clock movement.
- [ ] **P11816-2:** Select and document the production semantics needed by
  those consumers so an old credential remains distinguishable from a newer
  mutation. If strict generation order is required, allocate independently
  of wall-clock resolution, including persistent-cache and restart boundaries.
  Preserve the selected owner/domain rather than imposing an unexplained
  global counter or replacing one ambient assumption with another.
- [ ] **P11816-3:** Preserve existing read/rotation serialization, bounded
  offline-cache and ordinary consumer behavior through the smallest
  attributable correction. An advancing synthetic clock may remove an
  unrelated test assumption; it is not a production allocation repair.
  Existing credential, cache, source and mutation custody remains unchanged.

##### Local Validation for #11816

- [ ] **V11816-1:** With synthetic storage and one constant clock tick,
  exercise actual `get_with_generation` then `_set_password` and compare
  the old returned generation with the current credential generation.
  Cover a backwards clock and each selected production freshness consumer;
  assert the chosen old/new distinction without mocking past allocation
  or changing the clock merely to make the control pass.
- [ ] **V11816-2:** Qualify persistent-cache and restart boundaries wherever
  the selected ordering/freshness contract requires them. Preserve existing
  read/rotation locking and offline-cache tests, with attributable failed/
  positive controls and secret-free synthetic results. No real secret read,
  rotation, cache mutation, provider/native probe, rollout or new resource
  operation follows from this planned receiving.

#### Finite copy-transaction contracts

Agent-recommended P2 correctness receiving, outside the separately admitted
relay implementation. The [complete case detail](copy-transaction-contracts.md)
is part of this canonical Plan; these are its only technical state rows.

- [ ] **P13077-1:** Restore both outside-group destination forms with existing move safety ([detail](copy-transaction-contracts.md#p13077-1---destination-and-move-safety)).
- [ ] **P13077-2:** Invalidate only fenced values; preserve pending journal and post-ceiling writes ([detail](copy-transaction-contracts.md#p13077-2---generation-bounded-invalidation)).
- [ ] **P13077-3:** Capture before revocation and apply floor expiry during lookup ([detail](copy-transaction-contracts.md#p13077-3---capture-order-and-lookup-expiry)).
- [ ] **V13077-1:** Exercise actual move and copy destination/scope behavior ([detail](copy-transaction-contracts.md#v13077-1---destination-regressions)).
- [ ] **V13077-2:** Prove fenced-value removal without journal or newer-write loss ([detail](copy-transaction-contracts.md#v13077-2---preservation-regressions)).
- [ ] **V13077-3:** Cover capture/revoke concurrency and expired-floor lookup ([detail](copy-transaction-contracts.md#v13077-3---ordering-and-expiry-regressions)).
- [ ] **P13083-1:** Advertise actual running-service copy support before caller migration ([detail](copy-transaction-contracts.md#p13083-1---provider-first-support)).
- [ ] **P13083-2:** Check the selected peer before mutation and document its support window ([detail](copy-transaction-contracts.md#p13083-2---selected-peer-preflight)).
- [ ] **V13083-1:** Exercise current/prior peers and useful pre-effect incompatibility ([detail](copy-transaction-contracts.md#v13083-1---mixed-version-boundary)).
- [ ] **V13083-2:** Qualify supported rollback and retained ordinary operations ([detail](copy-transaction-contracts.md#v13083-2---rollback-and-unknown-outcomes)).
- [ ] **P13085-1:** Keep no-overwrite defaults; separately realize the deferred explicit overwrite ([detail](copy-transaction-contracts.md#p13085-1---explicit-overwrite-scope)).
- [ ] **P13085-2:** Reconcile uncertain renames by re-listing before restore decisions ([detail](copy-transaction-contracts.md#p13085-2---uncertain-rename-reconciliation)).
- [ ] **P13085-3:** Name every surviving staged or backup entry on error ([detail](copy-transaction-contracts.md#p13085-3---recoverable-error-reporting)).
- [ ] **V13085-1:** Reproduce a committed backup rename followed by timeout/failure ([detail](copy-transaction-contracts.md#v13085-1---committed-backup-timeout)).
- [ ] **V13085-2:** Cover failed final rename AND failed restore with named survivors ([detail](copy-transaction-contracts.md#v13085-2---failed-final-rename-and-restore)).

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

- [ ] **G13077:** Resolve every P13077/V13077 and continuing Coordination clause at its original source/regression tier, or explicitly transfer every unresolved clause to an accepted named tracked objective with actual custody preserved. Both destination forms, generation-bounded invalidation, pending-journal/newer-write preservation, pre-revocation capture and lookup expiry remain required; planning is not a cache or relocation fix.
- [ ] **G13083:** Resolve every P13083/V13083 and continuing Coordination clause at the actual CLI/service contract boundary, or explicitly transfer every unresolved clause to an accepted named tracked objective with actual custody preserved. Running-peer capability, pre-mutation selection, useful refusal, mixed-version/rollback and declared support-window obligations remain; planning is not negotiated copy support.
- [ ] **G13085:** Resolve every P13085/V13085 and continuing Coordination clause at its original source/regression tier, or explicitly transfer every unresolved clause to an accepted named tracked objective with actual custody preserved. Both uncertain-rename failures, re-list-before-restore and all surviving names remain required; planning grants no overwrite or cleanup authority.

- [ ] **G11816:** Resolve every P11816/V11816 and continuing Coordination
  clause: all relevant consumers and allocation paths, selected production
  old/new distinction, constant/backwards-clock controls, applicable cache/
  restart proof and unchanged locking/offline behavior. Preserve existing
  owner/admission and credential/resource boundaries. Documentary
  consolidation is neither a generation fix nor evidence of disclosure,
  lock bypass, live rotation or whole-service acceptance.

- [ ] Qualify the [federated-relay matrix](federated-relay.md#validation):
  actual service startup/channel/readiness/demand/result, exact grants,
  standalone compatibility, disconnect/reconnect, uncertain delivery and
  independently packaged installation; no source-only end-to-end claim.
- [ ] Map the applicable [L1-L4 caller-loss cases](../source-managed-runtime-installs/update-service-lifetimes.md#validation-and-completion)
  into the existing proposal's validation without using real credentials,
  changing a live service or bypassing architecture/owner admission.

Full parent runtime acceptance below is **pending**; the separately admitted
relay source slice and its isolated qualification are recorded in the journal.
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

### 2026-11-20 - Proposed finite copy-transaction receiving

[Complete source crosswalk, preserved custody and prior receiving follow-through](../backlog-reconciliation/journal/2026/11.20-vault-copy-and-existing-keepers.md).
The three cases join existing P2 through eighteen unchecked technical rows;
the Goal, relay priority, P0/owner admission and every incumbent state remain.
This is planned tracking, not an implementation, credential operation or
source disposition.

### 2026-11-20 - Proposed finite generation/freshness receiving

The complete qualified #11816 original is retained directly in the existing
P2 credential-lifecycle owner, local and actual global Validation and
continuing Coordination. All six technical rows remain unchecked. The
[campaign record](../backlog-reconciliation/journal/2026/11.20-source-claim-and-credential-generation-receiving.md)
preserves the original conditional ordering requirement, synthetic tier and
actual credential/cache/consumer custodians. The relay implementer receives
no new assignment; current priorities and P0/owner admission remain.
No secret, cache, native, runtime or resource operation occurred.

### 2026-11-16 - Closed-wheel dependency-profile source qualification

The native closed-wheel receiver now joins admitted Vault extras to the explicit
or recorded desired dependency profile. Unselected optional inputs fail before
publication; selection is rechecked and persisted under the existing transaction
lock. Completed-generation readiness additionally joins the frozen extras to
that desired profile without adding a payload-tree scan.

The generic native adapter forwards an optional ordinary health-callable name
to the existing candidate engine. Vault uses its existing selected dependency
health checks before completion; base callers retain module-only health. This
does not construct a relay, read credentials or activate a service. Whole-file
strict checking also required annotations in the touched retained-construction
and source-target helpers; no checker suppression or admission level changed.

Fresh Windows closed-wheel relay and combined SSH/relay construction passed,
including explicit selection, unselected refusal, preserved update desire,
actual installed dependency imports and readiness. Both profiles' provision
and update paths passed with no ambient Python commands: construction and its
preflight use the supplied wheel interpreter. The new condition first
reproduced a real preflight failure before its correction. A separately built
wheel with a missing required Gateway module failed before completion/publication.
The final relay positive/negative packet passed two cases in 261.72 seconds
(294.48 including preparation); the separate combined case passed in 171.57
seconds (178.95 including preparation). These are not one aggregate run, and
neither item nor group deadlines were increased. The generic callback
refusal and affected receiver controls passed 21 cases, and profile/snapshot
regressions passed 47 with two platform skips after the preflight correction.
Before that correction, sync/native recipe controls passed 140 with one skip,
and strict checking passed 23 physical files across the 18 selected projects
and configured Linux/Windows targets.

This is source qualification, not accepted publication or installed adoption.
Required CI and review remain pending. R2-R4, independent deployment lifetime,
real provider/file renewal and complete consuming journeys remain open. No
live profile, installation, credential acquisition or rollout occurred.

### 2026-11-16 - Native optional-profile source accepted

PR #12840 merged to `dev` at `2026-11-16T09:50:16Z` as
`29a22b5620765248ac413bbf19a401cb17128a46`, from exact head
`41fd117825a9114648d90317fc9a9369ded5e077`. Copilot review `5917937017`
approved that head without active issues; required CI `43842338316` passed.
The managed merge used the authorized additional code-owner exception, not
a CI or findings exception. The umbrella and parent remain open.

The existing installer now selects explicit relay and combined SSH/relay
dependency profiles, retaining desired state and immutable slots. Standalone
Fleet Manager remains a first-party application artifact. Dependency health
does not select policy, read keys, acquire credentials or start serving work.
Qualification also corrected native Windows health-import asset lookup and
bounded completion-driven source progress without increasing deadlines.

The source packet passed 71 tests with two POSIX-only skips on Windows.
Separate native selections qualified installed synthetic TLS consumers,
base/SSH preservation, failed candidates and retained-source reuse. Source
identity/progress passed 180 tests; eight native progress/quiet-window
controls and the separate long-path integrity check passed. The closed-wheel
suite passed 63 tests with one skip. Strict analysis covered 25 changed
Python files across Linux/Windows targets. CI's full dependency-age audit
passed all 616 declarations; exact unit source bindings and standalone
release intent were corrected before the green run, without changing unit
nodes, coverage, effect restrictions or budgets.

This accepts part of R4's source construction only. Native closed-wheel
non-base association, native consumer selection, installed service/adopter
lifetime, actual renewal, R2/R3 and the full parent stay open. No installed
runtime, credentials, enrollment, tunnel or shared service changed.

### 2026-11-16 - Source-lease consumer join accepted

PR #12818 merged to `dev` at `2026-11-16T06:47:50Z` as
`d123d0b93d1b4d3cd3886343bb2692174a0692eb`, from exact head
`02533b1e636ffcf5b19a4de43ced7d62cabd5707`. Copilot review `5915977280`
approved without active issues; original and metadata-triggered required CI
passed. The managed merge used the authorized additional code-owner
exception, not a findings or CI exception. The umbrella remains open.

The explicit normal-fetch profile now carries custodian-issued source
authorization through Gateway selection into Bridge's finite acquire port
and existing native account-file adapter. Serving-peer negotiation precedes
consumer capability reads. Gateway and custodian installation pins remain
independent; unknown token expiry is not a lease or native execution authority.
Old callers retain their meanings and unknown POSTs have no replay/downgrade.

The exact published head passed 183 Fleet and 46 Bridge regressions, with
20 POSIX-only skips on Windows. The actual HTTP/TLS/PoP/channel/custodian path
uses synthetic credentials; this run does not claim POSIX file effects or
native CLI reload. All nine changed Python files passed strict Linux/Windows
analysis. Lint, install/docs, dependency-age and required CI gates passed.

Only this R2 consumer source contract is accepted. Native optional-profile
construction/distribution, installed adoption, actual provider renewal,
deliberate interaction, proper cache/provider evolution and remaining R2-R4
and parent acceptance stay open. No live credentials, login, enrollment,
tunnel, installed runtime or shared service changed.

### 2026-11-16 - Initial normal-retrieval source accepted

PR #12807 merged to `dev` at `2026-11-16T05:43:43Z` as
`2759dce666e29bd4dd1b6ca6827ac1755c795827`, from exact head
`7a4c969440f4bd7959ebb600a40ea97f768773e8`. Copilot review
`5915476463` approved that head without active issues; required CI and
its metadata-triggered rerun passed. The managed merge used the authorized
exception to the additional code-owner vote, not a CI or findings exception.

The opt-in SDK Gateway owner now composes current authorized Controller offers,
the shared bounded selector and the existing account custodian. Normal provider
retrieval and refresh are explicit, with unknown credential expiry kept separate
from source leases. The strict available-only profile and legacy lease exchange
retain their meanings. The existing vision and proposal now explicitly cover
the independent cache, local/central broker role and standard provider drivers.

The exact published source passed 168 focused real TLS/channel/custodian and
Gateway regressions. All seven changed Python files passed whole-file strict
analysis for Linux and Windows with the Python 3.10 target retained; source,
install, documentation and required CI gates passed. Credentials were synthetic.

This is the initial R2 source flow, not full R2-R4 or parent acceptance. Continue
the actual consumer/source-lease composition, qualified cache/provider contract,
deliberate interaction and native optional-profile construction/adoption.
Durable transactions and every remaining Plan/Validation item stay open.
No live credential, installed runtime, enrollment or shared service changed.

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

### 2026-11-15 - First running federated source flow

Implemented the optional channel in the existing Vault service lifetime,
using the existing account custodian and exact channel-derived caller grants.
The connector consumes an expiring local conduit observation while retaining
the canonical Gateway TLS/Host/proof identity. Native configuration and cheap
status are separate from portable vault settings and credential acquisition.

The isolated source flow exercised actual Vault generation startup, real TLS
Gateway admission, Controller-service advertisement, authorized synthetic
credential delivery, denied recipient/capability, conduit withdrawal,
replacement and shutdown. The composed Gateway packet passed 130 tests,
including caller/source contracts and revocation. A revoked offer now reports
the stable unavailable result even before its old channel finishes closing.

The contained Windows generation/request-settlement packet passed 119 tests
with one platform skip and settled cleanup. The source work also corrected
fixture client-lifetime and delayed receipt-cleanup problems without weakening
production process-identity custody. These contained results do not qualify an
installed optional profile or service survival after production caller-Job loss.

The normal native `base`/`ssh` profiles do not yet select the optional relay
dependency closure. R2-R4, attended durable transactions, independent installed
distribution, real provider adoption and complete parent acceptance remain open.
No live credential, enrollment, installation, tunnel or shared service changed.

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
