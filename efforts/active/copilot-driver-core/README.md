# Copilot Driver Core Refactor

- **Slug:** `copilot-driver-core`
- **Repo:** copilot-extensions
- **Branch(es):** serial functional PRs from one source worktree
- **Created:** 2026-11-05
- **Status:** Draft
- **Vision:** [Copilot Driver](../../../visions/plugins/agent-remote-driver/README.md)
- **Umbrella issue:** #10765
- **Sub-issues:** Existing #10636, #5930, #5898, #8669 and #5579 retain their scopes.

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

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Refactor driver | Owns the implementation, tests, serial PRs and closeout | This effort and #10765 |
| Existing component owners | Retain receiving acceptance, custody, startup, retention and permissions | #10636, #5930, #5898, #8669, #5579, #4417, #9364, #8375, #6009, #10052 |

## Coordination

- **Topology:** tightly coupled source work with one owner, one checkout and
  dependency-ordered functional outcomes.
- **Host (owns PRs):** Refactor driver.
- **Delegates:** none at proposal time.
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

## Plan

### Phase 1 - Review the implementation boundary

- [x] Reconcile the merged vision, existing source owners and actual launch seams.
- [x] Record source scope, package recommendation, lifecycle contract and
  dependency-ordered consumer acceptance in [implementation.md](implementation.md).
- [ ] Land this proposal after review and required CI before changing runtime code.

### Phase 2 - Standalone headless core and real Bridge consumption

- [ ] Extract the canonical Host, protocol, lifecycle and receiving support
  into the independent core package; leave only bounded compatibility/client adapters.
- [ ] Produce the ordinary app and compact receiving artifact through existing
  build/install owners, with selected-runtime support discovery.
- [ ] Prove direct headless launch, ACP/HTTP-TCP access, reservation/process/native
  identity, prompt acceptance and reconnection without a target coordination suite.
- [ ] Migrate Bridge creation and resume through that core while preserving
  policy, workspace, preference, cancellation and retained-host custody.
- [ ] Publish the first complete functional flow and steward its PR to merge.

### Phase 3 - Common headed broker and all caller convergence

- [ ] Move canonical mux creation, prerequisite composition and pane execution
  into the same core; preserve platform behavior and literal prompt/argument transport.
- [ ] Connect the launch-injected, scoped trusted extension to Session Host.
  Headed mode uses the common broker, not a second conversation or Bridge-only inbox.
- [ ] Migrate Manager launch-session and Worktrees embody/copilot, plus existing
  local/remote adapters, to the selected core entrypoints.
- [ ] Prove fresh/resume/reuse, detached/attached/new-window and no-mux behavior
  through the real callers without duplicate creation or seed replay.
- [ ] Publish and merge the complete headed/caller flow without regressing headless use.

### Phase 4 - Lifetime, receiving parity and completion

- [ ] Prove CLI persistence until user closure or explicit authorized retirement,
  including survival after caller cleanup and package replacement.
- [ ] Prove caller-stopped ACP and configured idle grace with no pending tasks,
  agents or schedules; unknown state is not an empty pending set.
- [ ] Qualify ordinary/hoisted receiving, supported skew/rollback, endpoint and
  process identity, exact cleanup and measured cold/warm readiness.
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

### 2026-11-05 - Main refactor preparation

Continued #10765 after the completed vision slice. Confirmed no newer core
implementation claim; existing portable mux source #10748 is merged and its
remaining acceptance stays with #10636. Mapped the real Host package, Manager
launcher, Worktrees creation seam and current receiving artifact. The old
reapable flag and textual sub-agent recovery do not by themselves establish
complete pending tasks/schedules; native structured-state qualification is
an explicit implementation gate. No runtime, installed session or fleet changed.
