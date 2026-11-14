# Copilot Driver Core Refactor

- **Slug:** `copilot-driver-core`
- **Repo:** copilot-extensions
- **Branch(es):** serial functional PRs from one source worktree
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [Copilot Driver](../../../visions/plugins/agent-remote-driver/README.md)
- **Umbrella issue:** #10765
- **Sub-issues:** Existing #10636, #5930, #5898, #8669 and #5579 retain their scopes.

## Goal

Operator root goal, verbatim:

> Factor out session-host, mux logic, and agent-bridge's internal extension into a forward-deployable core, so that a machine or container lacking suite service can still have Copilot launched via ACP or Mux in an ACP-controllable Copilot session that can be picked up by a local or calling agent-bridge. This core should be uses by agent-worktrees and agent-bridge, and it should not depend on those services being installed into the target space.

Source: operator direction of 2026-11-14 00:27Z, relayed through existing
coordination. The complete intake reference is retained privately by the
effort owner, not published with transport or session identifiers.

### Continuing acceptance obligations

Complete the standalone core, all named caller migrations, mode-appropriate
lifetime and receiving acceptance, with the required source publication.
For recovery, treat abrupt transport loss and observer, process or host restart
as the baseline rather than assuming a clean terminal event. Clean SDK observer
detach/drain is an optional improvement for this proposed crash path.

Preserve current authority, exact owned-scope settlement, the durable
conversation and uncertain work without seed, last-prompt or action replay.
Restored observation is not quiescence or completed work. This changes the
proposed recovery boundary, not current support, activation or effect authority.

## Guiding Intent

Implement the reviewed standalone Copilot Driver architecture, not another
launcher facade. Move Session Host and canonical Copilot mux execution into
one independently installable, quickly hoistable core. Manager launch-session,
Worktrees embody/copilot and Bridge create/resume must consume it. Headless
and headed execution expose the same broker, preserve native conversation
identity and obey their distinct lifetime policies.

The completion gate is the complete caller-convergence and receiving flow,
with real consumer validation and required source publication. A package,
an extraction-only PR or one migrated caller is not completion.

## Outcome acceptance and finalization

The root outcome requires evidence from a built or copied core artifact, not
only imports from this source checkout:

- A target with only the core and its declared Copilot/mux prerequisites can
  launch in both ACP and mux modes without installed or running Bridge,
  Worktrees, Manager or Dispatch services.
- A local or calling Bridge can discover and control that existing session
  through the common broker, retaining its exact native conversation and
  original input without a second session, seed replay or effectful fallback.
- Actual Worktrees and Bridge callers select that same core. Receiving,
  mode-appropriate lifetime, ownership, supported skew/rollback and exact
  cleanup pass their existing acceptance gates.
- Evidence names the artifact, executing provider and consumer boundary.
  Controlled SDK/model-free cases, native Copilot execution and installed
  adoption are reported separately; unavailable authority or capability is
  an explicit open gate, never a substituted pass.

Finalization requires this complete functional flow, every continuing Plan and
Validation Plan item resolved or explicitly transferred to a named tracked
objective, required source review/CI/publication completed, and the canonical
effort marked Done and archived. A package, source leaf, review or merged PR
alone does not satisfy this criterion. Deployment remains separately authorized.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Refactor driver | Owns the implementation, tests, serial PRs and closeout | This effort and #10765 |
| Receiver implementation contributor | Owns the auth receiver and bounded POSIX foreground-custody follow-on; renewal stays disabled | Implementation #11781; follow-on #11913; proof prestage #11758 |
| Existing component owners | Retain receiving acceptance, custody, startup, retention and permissions | #10636, #5930, #5898, #8669, #5579, #4417, #9364, #8375, #6009, #10052 |

## Coordination

- **Topology:** tightly coupled source work with one owner, one checkout and
  dependency-ordered functional outcomes.
- **Host (owns PRs):** Refactor driver.
- **Delegates:** source-auth receiver/enforcement implementation and its callable
  contract move to the contributor owning #11781 (proof prestage #11758).
  The refactor driver releases
  that build claim, reviews the implementation, and owns native qualification
  before positive renewal can be enabled. Qualification infrastructure is not
  a prerequisite for the receiver contributor to start.
  The released source scope includes receiver-owned modules and auth-specific
  changes in `headed/acp-adapter.mjs`, `headed/extension.mjs`,
  `headed/backend.py`, and the coupled `headed/launcher.py`
  `run_headed`/`_PaneChild` lifecycle, plus minimal receiver-owned containment
  support and focused tests/docs. Generic launcher behavior, unrelated Host
  changes, raw Host write observation, Worktrees seed/handoff/native fixtures
  and global process/Job policy are not transferred.
- **POSIX containment follow-on:** the receiver contributor owns a minimal
  additive asynchronous foreground-custodian adapter reusing the existing
  Procutil guardian, whole-session census, PTY and lifetime-pipe custody,
  plus thin POSIX `AuthPaneChild` integration. Declare and review the callable
  boundary before consumer use: retained native identity, asynchronous wait
  and exact retirement/settlement, without blocking the event loop. This does
  not transfer generic/global process policy, finite caller-owned stream
  changes, daemon reconciliation or another contributor's source. Preserve
  unsupported refusal until the provider and independent native owner-death,
  descendant, PTY and cancellation controls qualify; source publication is
  not productive activation. Canonical derived-archive regeneration required
  by the new provider uses its existing generator and current source, not
  manual archive edits or another contributor's unpublished finite/daemon code.
- **Crash recovery (proposed):** the original lifecycle owner retains durable
  transition/admission ownership. The receiver contributor owns a bounded
  candidate/status and decision-reducer leaf through that owner's store
  interface; qualified platform providers retain their existing custody and
  settlement scope. No platform, receiver, generic process-policy or updater
  ownership transfers through this proposal.
- **Handoff:** continue this canonical effort across contexts. Preserve its
  parent completion gate, exact current slice, effects and one next action.
- Consume the source delivered in #10692/#10748; do not supersede its separate
  native/installed acceptance or claim its live resources. Coordinate actual
  overlapping changes through those existing issues before touching them.

## Context

The [finite vision effort](../../2026/11/05%20copilot-driver-core-vision/README.md)
landed the architecture in #10768 and archived it in #10775. This effort owns
realization. The [implementation map](implementation.md) records the current
source seams, proposed app boundary and validation obligations.

Apply the existing [application installation contract](../../../docs/patterns/fast-application-install-and-cutover.md),
[service contract evolution](../../../docs/patterns/service-contract-evolution.md)
and [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement).
Incoming readiness and complete current selection define installation success;
old recovery and keeper retirement are separate. Preserve automatic/manual
classification, configuration, work, live keepers and independently owned
connectivity. No second installer, supervisor or global broker is proposed.

## Request

Current execution request, verbatim:

> Now continue with the main refactor effort

The governing architectural request and caller/lifetime requirements are
preserved in the [reviewed vision effort](../../2026/11/05%20copilot-driver-core-vision/README.md#request).
They require a compact redistributable, common Session Host broker, scoped
launch-time extension injection/trust, all named caller migrations, CLI
persistence until closure, and caller-stopped or pending-work-aware idle ACP
retirement. A possible `agent-driver` rename is optional.

Recovery direction, public-safe summary: preserve uncertainty across abrupt
loss and restart; do not make a clean SDK detach certificate a prerequisite
for the proposed crash path. Clean-path negative evidence and existing refusal
behavior remain valid. The agent-recommended realization and qualification
sequence are in [implementation.md](implementation.md).

## Scope Approvals

This request advances the completed vision into source implementation, normal
review/publication and bounded validation using owned, isolated fixtures.
Preserve the existing namespace during extraction unless a concrete
implementation need changes that recommendation. Naming is not the objective.

No shared runtime/fleet rollout, production session migration or shutdown,
global settings rewrite, credential export, forced claim, new cloud resource
or takeover of another owner's work is authorized here. New effect scope
must use its actual owner. Local isolated native controls are distinct from
installed or external-venue adoption; unavailable lanes remain explicit.
Review of this proposal precedes implementation and is not deployment consent.
The crash-recovery amendment adds no observer, scheduler, agent, login,
credential mutation or live process-retirement authority. Its first
receiver-owned leaf is actor-free source/state testing with effects unwired;
native qualification and activation need their existing separate admission.

## Plan

### Next outcome - portable consumer acceptance

- [ ] Prove the smallest admitted standalone artifact flow for ACP and mux,
  then Bridge pickup, with suite services absent from the target. Reuse
  existing owned fixtures; preserve separate admission for native effects.
- [ ] Correlate that flow with the actual Worktrees/Bridge caller paths and
  coordinate any launch-caller changes with their existing owner, rather
  than duplicate the mux-launch implementation.
- [ ] Publish the first complete functional outcome through normal gates and
  update this root acceptance record, retaining all remaining obligations.

The source-complete actor-free recovery-intent adapter supports original-owner
persistence but does not prove this outcome. Its bounded source acceptance is
recorded in the journal, not counted as root acceptance. Select the portable
consumer flow before adding further optional recovery-data layers.
The completed source items below remain source milestones, not native
or installed qualification. Container-auth pumping and mux-launch custody
remain with their existing owners.

### Phase 1 - Review the implementation boundary

- [x] Reconcile the merged vision, existing source owners and actual launch seams.
- [x] Record source scope, package recommendation, lifecycle contract and
  dependency-ordered consumer acceptance in [implementation.md](implementation.md).
- [x] Land this proposal after review and required CI before changing runtime code.

### Phase 2 - Standalone headless core and real Bridge consumption

- [x] Extract the canonical Host, protocol, lifecycle and receiving support
  into the independent core package; leave only bounded compatibility/client adapters.
- [x] Produce the ordinary app and compact receiving artifact through existing
  build/install owners, with selected-runtime support discovery.
- [x] Prove direct headless launch, ACP WebSocket/TCP access, reservation/process/native
  identity, prompt acceptance and reconnection without a target coordination suite.
- [x] Migrate Bridge creation and resume through that core while preserving
  policy, workspace, preference, cancellation and retained-host custody.
- [x] Publish the first complete functional flow and steward its PR to merge.

### Phase 3 - Common headed broker and all caller convergence

- [x] Move canonical mux creation, prerequisite composition and pane execution
  into the same core; preserve platform behavior and literal prompt/argument transport.
- [x] Connect the launch-injected, scoped trusted extension to Session Host.
  Headed mode uses the common broker, not a second conversation or Bridge-only inbox.
- [x] Migrate Manager launch-session and Worktrees embody/copilot, plus existing
  local/remote adapters, to the selected core entrypoints.
- [ ] Prove fresh/resume/reuse, detached/attached/new-window and no-mux behavior
  through the real callers without duplicate creation or seed replay.
- [ ] Publish and merge the complete headed/caller flow without regressing headless use.

### Phase 4 - Lifetime, receiving parity and completion

- [ ] Prove CLI persistence until user closure or explicit authorized retirement,
  including survival after caller cleanup and package replacement.
- [ ] Prove caller-stopped ACP and configured idle grace with no pending tasks,
  agents or schedules; unknown state is not an empty pending set.
- [ ] Review and qualify uncertainty-preserving crash recovery through the
  original lifecycle owner, without treating clean SDK detach as a prerequisite
  or relaxing scope settlement, authorization and no-replay gates; begin with
  the [original-owner admission binding](implementation.md#original-owner-recovery-admission-binding).
- [ ] Qualify ordinary/hoisted receiving, supported skew/rollback, endpoint and
  process identity, exact cleanup and measured cold/warm readiness.
- [ ] Establish independent POSIX owner-death custody and native Linux/pod
  qualification before opt-in auth-wait composition; ordinary non-auth execution
  remains a separate supported path.
- [ ] Remove duplicated launch implementations; preserve supported adapters and
  retained-session recovery without effectful fallback replay.
- [ ] Update reality docs, packaging, capability declarations, changefiles and
  focused tests; pass required source gates, review and CI at final heads.
- [ ] Resolve every Plan/Validation item or explicitly transfer an independently
  owned adoption gate, then mark Done, archive and finalize the source workspace.

## Validation Plan

- [ ] Core works from its built/copied artifact without source checkout or
  installed Bridge/Worktrees/Manager; extension assets and notices are complete.
- [ ] Each named caller actually invokes the same core; negative controls
  forbid legacy internal spawning and mux creation on the selected new path.
- [ ] Both modes expose the broker and truthful stage distinctions: reservation,
  process/mux, native binding, input readiness, prompt acceptance and restoration.
- [ ] Exact native identity, multiline/quoted seed, permissions and preference
  selections survive new/resume/reuse without staging or delivering a duplicate.
- [ ] Structured pending-work and schedule evidence controls ACP idle expiry;
  work arrival during grace cancels expiry, and CLI foreground idle never expires it.
- [ ] Caller loss, all fixture-owned cleanup Jobs, concurrent creation, takeover,
  stale identities, interrupted delivery, unsupported peers and rollback retain custody.
- [ ] Crash recovery reconciles one durable transition after observer or host
  restart, independently settles the original scope before seedless same-session
  restoration, and retains unknown external effects for their rightful owners.
- [ ] Before advertising POSIX auth-wait, prove coordinator-death and source-expiry
  cleanup, exact descendant custody and same-session recovery without replay in
  a native Linux/pod lane; a private process group alone does not qualify.
- [ ] Unit contracts, a real-process consumer check, clean first-touch installation,
  Windows-native and available POSIX/external lanes are recorded separately.
- [ ] Test containment/leases, no-window observations and exact fixture cleanup
  pass; no production prompt or retained session is used as a fixture.
- [ ] Changed source passes relevant lint/type/install/headless/contract and
  unit-portfolio gates, final-head review and required CI without widening them.

## Proposal

The detailed source map, migration boundary and evidence requirements live in
[implementation.md](implementation.md). Its refinements are agent recommendations,
not extra operator authority. Runtime implementation has not started before
this proposal's review gate.

## Journal

- [2026-11-12 - Private mux creation merged; selected caller source migration complete](journal/2026/11.12%20private%20mux%20creation%20convergence.md)
- [2026-11-12 - Recordless normalized caller converges on the core](journal/2026/11.12%20recordless%20caller%20core%20acceptance.md)
- [2026-11-12 - Original-owner recovery store reviewed and merged](journal/2026/11.12%20original-owner%20recovery%20store.md)
- [2026-11-12 - Recovery contract reviewed; original-owner store next](journal/2026/11.12%20recovery%20contract%20acceptance.md)
- [2026-11-11 - Portable mux receiving converges on the common core](journal/2026/11.11%20portable%20mux%20core%20acceptance.md)
- [2026-11-11 - Uncertainty-preserving crash recovery proposed](journal/2026/11.11%20uncertainty-preserving%20recovery.md)
- [2026-11-11 - Refactor source history](journal/2026/11.11%20refactor%20source%20history.md)
- [2026-11-10 - Refactor source history](journal/2026/11.10%20refactor%20source%20history.md)
- [2026-11-09 - Refactor source history](journal/2026/11.09%20refactor%20source%20history.md)
- [2026-11-08 - Refactor source history](journal/2026/11.08%20refactor%20source%20history.md)
- [2026-11-07 - Refactor source history](journal/2026/11.07%20refactor%20source%20history.md)
- [2026-11-06 - Refactor source history](journal/2026/11.06%20refactor%20source%20history.md)
- [2026-11-05 - Refactor source history](journal/2026/11.05%20refactor%20source%20history.md)
