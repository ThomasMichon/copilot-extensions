# Consumer telemetry sink adoption

- **Slug:** `consumer-telemetry-sink-adoption`
- **Repo:** copilot-extensions
- **Branch(es):** dependency-ordered contributions targeting `dev`
- **Created:** 2026-11-19
- **Status:** Draft
- **Vision:** [Portable Telemetry](../../../visions/telemetry/README.md), especially `harness-owned-sink-selection`, `continuous-executable-sink`, and `daemon-free-command-adoption`
- **Umbrella issue:** [#13545](https://github.com/ThomasMichon/copilot-extensions/issues/13545)
- **Sub-issues:** Split only when an implementation slice has an assigned owner.

## Goal

Deliver a complete, daemon-free path from a consuming repository's telemetry
configuration to its own receiver command. Real existing producers must use
that route with bounded delivery, correct origin, compatible existing sinks,
and receiver-confirmed evidence.

Sequence reviewed implementation and release, not just framework scaffolding.
HTTP and its service lifecycle remain optional follow-on capabilities.
No adopter backend or organization-specific event policy belongs in this effort.

## Guiding Intent

Extend the existing producer seam rather than build another event bus or SDK.
The adopter supplies a script or executable; ordinary tools and services remain
usable without that receiver or an observability service. A successful pipe
write is not proof of persistence or backend delivery.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Planning coordinator | Publish and reconcile this plan | #13545 and the planning PR |
| Portable delivery owner | Own an explicitly assigned implementation slice | Claim and progress on #13545 before execution |
| Existing producer owners | Retain telemetry reducers, measurement interfaces, and reserved call sites | #7935/#5900 and their existing coordination |
| Adopter implementer | Supply and qualify an independent receiver | The adopted command contract; no adopter-specific code is owned here |

## Coordination

- **Topology:** Independent, dependency-ordered PRs; one writer per slice.
- **Host (owns PRs):** Planning coordinator for this plan; the assigned portable
  delivery owner for each implementation PR.
- **Delegates:** None assigned by this planning contribution.
- **Handoff:** Each slice records its exact source, tests, current contract,
  merge/release state, and the next dependency. Do not equate opening a PR with
  completion.
- Preserve the [performance-telemetry campaign](../performance-telemetry/README.md)
  under #7935/#5900, its measured interfaces, reserved Manager/entry files,
  retained native evidence, and separate collector/rollout gates. Obtain the
  relevant owner's API/file-scope agreement before overlapping implementation.
- This effort adds configuration and command delivery through the existing
  owner. It does not claim new instrumentation, process-spawn provenance,
  update-telemetry, retained logging, or another campaign's acceptance.

## Context

The intent clarification landed in #13532. The vision already permits a
continuous receiver command and an optional discovered HTTP consumer.

Existing reusable boundaries:

- [process-telemetry](../../../libs/process-telemetry/README.md): callable sink
  slots, explicit bounded buffering, typed measurements, and optional OTLP
  adapters. Preserve those owners and public APIs.
- Bridge and Dispatch telemetry modules: content-reduced lifecycle records,
  existing factory/configuration discovery, and JSONL spools.
- [QoS/Engagement design](../../../docs/patterns/telemetry-qos-engagement.md):
  proposed normalized meanings, not proof that all hooks or schemas are installed.
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md)
  and [endpoint discovery](../../../docs/patterns/local-endpoint-discovery.md):
  compatibility and realization guidance, not a new receiver to implement here.
- [Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
  the existing install/update owner governs any later installed adoption; this
  effort does not introduce another installer or change its completion rules.

The existing performance effort retains the measurements/export work. The
separate worker-status effort concerns observation/query ergonomics, not this
adoption route.

## Request

Operator:

> Great. Sequence an effort to get the changes for telemetry in place.

Public-safe scope summary: implement the portable part of the accepted telemetry
direction through consuming-repository configuration, an adopter-owned command,
and real producer adoption. Keep command-only collection complete without a
daemon installer. Preserve optional HTTP, existing integrations, privacy,
origin binding, and bounded fail-open behavior.

## Scope Approvals

- This contribution creates the implementation plan and coordination record.
  It does not begin runtime implementation, installation, or rollout.
- Implementation needs an assigned owner and the reviewed contract/file scope
  below. Existing producer ownership and holds are not transferred by plan review.
- No shared service, remote collector, backend, credentials, or deployment target
  is selected here.
- Command-only acceptance is not conditional on an HTTP receiver, endpoint
  command, or service installer.

## Plan

The sequence and grouping below are **agent-recommended**, subject to review.
Keep tightly coupled configuration, runner, and first-consumer changes with one
owner. Publish useful complete slices early; do not merge an unused framework
and count it as delivery.

| Slice | Depends on | Observable result |
|-------|------------|-------------------|
| U0 - Contract and owner agreement | Accepted vision #13532 | One reviewed, implementable configuration/delivery contract and admitted API scope |
| U1 - Bounded command route with Bridge | U0 | A real existing Bridge transition reaches an independent receiver through normal configuration |
| U2 - Dispatch and coverage reconciliation | U1 | Dispatch uses the same route; actual participating/uncovered consumers are explicit |
| U3 - Native and compatibility qualification | U1 and U2 | Windows/Linux, mixed-version, origin-isolation, and failure witnesses pass |
| U4 - Release and adopter handoff | U3 | Reviewed source is merged and a release containing the required capability is consumable |

### U0 - Contract and owner agreement

Contract source:
[Consumer telemetry command v1](../../../docs/patterns/consumer-telemetry-command.md).
This is the reviewed-before-implementation specification, not shipped command
support. Its inventory and source reservation preserve #7935/#5900 ownership;
any overlapping implementation hunk still needs its owner's explicit agreement.

- [ ] Inventory actual producer schemas and consumers; distinguish existing
  hooks from proposed catalog entries or missing instrumentation.
- [ ] Reconcile API/file overlap with the performance and producer owners.
- [ ] Review one configuration owner and precedence model: harness selection,
  supported local override, explicit disable, invalid/missing configuration,
  and the compatibility boundary for explicitly unbound legacy consumers.
- [ ] Specify trusted command argv, bound harness working directory, source/
  route generation, record schema/framing, size/queue/process/time budgets,
  and truthful delivery outcomes. No shell-evaluated command string.
- [ ] Specify capability discovery from the selected runtime, not package
  equality; document provider-first expansion and both mixed-version directions.
- [ ] Put exact mechanics in a focused contract document before coding. Do
  not create another normative payload schema or configuration parser per plugin.

**Exit:** A reviewer and the relevant source owners can identify the first
consumer, exact behavior, compatibility path, budgets, and finite test witness.

### U1 - Bounded command route with Bridge

- [ ] Extend the existing sink slot/buffer and owning configuration seam.
  Disabled/unconfigured emission stays a cheap no-op.
- [ ] Run one caller-owned headless receiver per admitted producer route,
  reused for its records. Queue offers stay off blocking startup/pipe I/O;
  no child is launched for each event.
- [ ] Bind the receiver to the initiating harness rather than current working
  directory or a daemon's last caller. Propagate only admitted context.
- [ ] Preserve product exit status and ordinary output, including malformed
  configuration, unsupported peers, slow/non-reading children, and sink failure.
- [ ] Ship the real Bridge compatibility adapter and its ordinary producer
  path with the shared change. Do not replace lifecycle reducers or touch
  reserved launch/measurement call sites without their owner.
- [ ] Exercise the actual consumer boundary in an isolated native fixture:
  configuration -> real transition -> receiver process -> receiver readback.
  Direct calls to a new helper alone are not this exit criterion.

**Exit:** A neutral receiver obtains real source records without importing an
adopter package into Bridge or installing a daemon.

### U2 - Dispatch and coverage reconciliation

- [ ] Adopt the same contract through Dispatch's existing telemetry owner,
  preserving factory, legacy spool, and retained-spool compatibility.
- [ ] Demonstrate two interleaved admitted origins and service-owned activity
  without cross-routing or borrowing another session's identity.
- [ ] Reconcile all actual shared-seam consumers. For each, record adopted,
  explicitly inapplicable, or a separately tracked owner/gap. Do not infer
  suite-wide coverage from two examples or missing observations.
- [ ] Keep source IDs and observation/attempt identities stable where supplied;
  distinguish retry, replay, and a new occurrence. Do not synthesize missing
  trace context or use timestamp proximity as duplicate identity.

**Exit:** Both initial production owners work, and the coverage statement is
specific enough for an adopter to know what it receives.

### U3 - Native and compatibility qualification

- [ ] Run focused admitted unit controls for configuration, route selection,
  framing, disabled paths, compatibility, and bounded accounting.
- [ ] Run selected native Windows and Linux witnesses for real child lifetime,
  non-reading/crashing receivers, cancellation, finite exit drain, and cleanup.
- [ ] Prove old/new, new/old, and new/new behavior plus rollback. Unsupported
  selected policy must remain explicit rather than silently changing the sink.
- [ ] Qualify a fresh consuming repository with no telemetry daemon and no
  adopter packages installed in producer environments.
- [ ] Record real source/capability identity, receiver readback, losses,
  workload, and measured overhead. Do not turn unavailable evidence into zero.

**Exit:** The command route is compatible, bounded, and real on both platforms.
Native tests stay in their selected integration lane, not hidden in unit CI.

### U4 - Release and adopter handoff

- [ ] Land each owned PR through current-head review, required CI, actual
  merge, and managed worktree finalization once its obligations are settled or
  accepted by the continuing coordinator.
- [ ] Add changefiles for every changed runtime/payload owner when code is
  implemented; do not hand-edit release versions.
- [ ] Verify normal promotion publishes a consumable release with the admitted
  capability. A `dev` merge alone is not release or installed adoption.
- [ ] Publish exact capability/compatibility and receiver-example guidance.
  Adopters can then supply their own script/configuration through that contract.
- [ ] Record installed adoption only through its separately assigned owner.
  This plan selects no machine or live sink and grants no rollout authority.

**Exit:** An adopter can consume the released command-only capability without
inventing configuration or installing an HTTP service.

## Validation Plan

- [ ] Missing/disabled configuration creates no receiver, queue, scan, or
  telemetry-dependent failure; invalid selected configuration cannot retarget
  data through another harness or legacy destination.
- [ ] Literal trusted argv and the correct bound working directory survive
  spaces and platform argument rules; no shell expansion or raw payload enters argv.
- [ ] Receiver startup/write/queue/route/shutdown budgets are numerical and
  enforced; slow or failed receivers never block product-critical callbacks.
- [ ] One child serves a route's records; source shutdown leaves no owned
  descendants or unbounded retry worker.
- [ ] Real Bridge and Dispatch producer witnesses reach an independent
  receiver through their normal owning configuration path.
- [ ] Concurrent origins, background continuations, service-only work, and
  admitted remote boundaries preserve honest routing and attribution.
- [ ] Existing factory, spool, retained-spool, and explicitly unbound behavior
  remain compatible; unknown capabilities do not imply support.
- [ ] Model/adopter-specific fields are not added to generic producer schemas.
  Source privacy allowlists and actual trace context remain intact.
- [ ] Receiver acceptance, local persistence, and final backend delivery are
  reported separately; a successful callback or stdin write is not persistence.
- [ ] Windows/Linux native and fresh-repository evidence identify actual source,
  scope, losses, and unavailable measurements.
- [ ] Final source review/CI, release capability, and separately owned installed
  adoption are distinguishable completion facts.

## Proposal

Command-only adoption is the committed initial scope. An adopter may later add
HTTP with a command-returned endpoint, but that transport and its explicit
service installer/lifecycle need their own reviewed implementation slice.
They must not become a hidden dependency of the command route.

The generic suite owns the portable route; the adopter owns its catalog mapping,
audit/storage policy, receiver dependencies, backend credentials, and destination.
No organization-specific receiver or backend belongs in this public effort.

Completion means U0-U4 and the relevant validation checks are resolved with
evidence. HTTP is outside this initial scope, not an unchecked prerequisite.

## Journal

### 2026-11-19 - Sequencing proposal

- Created #13545 after checking for overlapping sink work.
- Reused the accepted Portable Telemetry direction from #13532.
- Preserved performance, producer, collector, update, and rollout ownership.
- Proposed the contract -> real Bridge route -> Dispatch/coverage -> native
  qualification -> release sequence. No runtime work has started.

### 2026-11-19 - U0 contract contribution

- Claimed only U0 on #13545 after checking current comments and open telemetry
  pull requests; the parent tracker and U1–U4 remain open.
- Specified one shared parser/runner home, root-bound selection, local override,
  structural Bridge projection, JSONL framing, finite process/queue/deadline
  bounds and selected-runtime support discovery.
- Inventoried actual lifecycle, diagnostics, hourly and performance consumers.
  No Manager, entry, measurement, collector or native reservation is transferred.
- Runtime support, receiver readback, native/compatibility qualification and
  release/installed adoption remain later acceptance, not U0 evidence.

### 2026-11-19 - U1 source implementation

- Claimed only U1 on #13545. The contract requires agreement for actually
  reserved overlapping hunks, not a blanket approval from historical campaign
  roles. Related EventLog refresh and native Job reservations received narrow
  coordination requests; their changed hunks remain untouched.
- Added one shared declaration parser, structural projection and command route
  on the existing `SinkSlot`/`BufferedSink` owner. Bridge admits exact roots from
  trusted caller-to-harness service configuration, independently of the target
  agent and cwd.
- The expanded Windows development packet passes 179 controls, including private
  restoration and remote-session compatibility. Its real configured,
  authenticated HTTP stop operations reached one independent receiver, whose
  file readback contained four session/conversation transitions. Literal argv,
  cwd after `chdir`, environment isolation, unlisted-caller suppression,
  non-reading-child failure and finite owned cleanup are explicit controls.
- Added a finite, contained Windows/Linux native CI packet with immutable
  candidate selection and receiver-readback properties. Current-head CI/review
  and merge remain required; source tests do not establish release or rollout.
- U2 Dispatch adoption, wider coverage/skew qualification and U4 release remain
  open. No receiver installation, live runtime update or deployment is included.
- Review regressions cover writable-database ownership transfer, nonblocking
  concurrent sequence ordering, late remote/merge-recovery session admission,
  immutable reused-session binding and candidate cleanup after success,
  construction failure or cancellation. The existing reserved producer hunks
  remain unchanged.
- Additional review controls suppress inherited command admission in elevated
  sub-daemons without changing unbound legacy mode, and retain Linux lifetime
  custody through the bounded stdin-EOF flush grace before forced retirement.
  Their native regression persists and reads back the receiver's final batch.

### 2026-11-20 - U2 Dispatch source adoption

- Claimed only U2 on #13545 in managed source worktree codename
  `clanking-compass`. U1 retains no shared-command source reservation.
  Current Dispatch work on assignment capture and placement retains its own
  expression; this slice does not change those integration hunks.
- Added the independent closed `dispatch.lifecycle.v1` projection and explicit
  coordinator-owned admission through the existing shared parser, bounded
  writer and native process owner. Bridge remains the default shared API and
  advertises only its own schema.
- Real ordinary configured HTTP mutations exercise interleaved stored-task
  origins and an explicit service-owned fence route, with independent
  receiver-file readback. Disabled/invalid/local opt-out, legacy compatibility,
  failure, nonreading receiver and new-source restart are finite controls.
- Reconciled actual shared-library imports in the command contract inventory:
  participating lifecycle owners, deliberately separate diagnostic/reporting
  readers and validation-only consumers, and explicitly unknown/unobserved
  instrumentation or installed generations.
- Current-head review, required CI, native evidence, merge and finalization
  remain publication obligations for this slice. U3/U4 and the umbrella stay
  open; no installed runtime, receiver, daemon or release was changed.
