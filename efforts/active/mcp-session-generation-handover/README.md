# MCP Session Generation Handover

- **Slug:** `mcp-session-generation-handover`
- **Repo:** copilot-extensions
- **Branch(es):** Independent reviewed source PRs targeting `dev`
- **Created:** 2026-11-04
- **Status:** Active
- **Vision:** [Authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement), [version-skew-safe service contracts](../../../docs/patterns/service-contract-evolution.md)
- **Umbrella issue:** #10486
- **Related issues:** #9716, #10377
- **Realization defects:** #10652 (incoming completion gate), #6389 (fresh serving convergence), #10653 (unsafe forced cutover)

## Guiding Intent

An update must make fresh `agent-mcp` invocations use the incoming runtime and
serving generation without terminating existing clients. A client-owned stdio
wrapper may retain its original code, but an adopted wrapper must reattach to
the replacement shared runtime without losing its MCP session. Leaving the old
heavy host serving existing sessions indefinitely is not that guarantee.

Incoming readiness and complete current selection define installation success.
Incumbent recovery, ownership metadata, process-census failures and predecessor
retirement must not veto installation. Configuration, credentials, repositories,
databases, sessions and uncertain effects survive. Retirement remains separately
owned and excludes client/session preservers.

Incoming installation completion is independent of whole-effort completion.
Authentic incoming readiness, every applicable current selector, command, route
and registration, and fresh calls executing the incoming physical generation
are required. Old-session handover, outgoing census/provenance, occupied old
control homes and cleanup cannot gate that result. Unprovable outgoing cleanup
is separately deferred, not incoming failure. Necessary durable-state
compatibility/migration and transfer or exclusion of conflicting work admission
remain readiness requirements; real incoming authenticity, build,
authentication, permission, I/O and health failures remain failures.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| battered-sprocket | Coordinator, implementation integration and PR owner | Managed source worktree |
| lopsided-gasket | Existing handover implementation and focused qualification | Managed implementation worktree |
| patient-spanner | Incoming/retirement architecture and acceptance amendment | Managed documentation worktree |
| Session-path evidence | Read-only client, host and upstream-state analysis | Coordinator delegation |
| Activation evidence | Read-only installer, selection and retirement analysis | Coordinator delegation |
| Keeper evidence | Read-only existing lifecycle/transport primitive analysis | Coordinator delegation |

## Coordination

- **Topology:** Independently qualified incoming-publication slice first, then
  full handover; documentation and implementation PRs have non-overlapping file
  ownership. No duplicate implementation writers.
- **Host (owns PRs):** battered-sprocket coordinates implementation;
  patient-spanner owns this documentation amendment.
- **Delegates:** Evidence roles are read-only. Lopsided-gasket retains
  implementation ownership; its effort-file edits wait for this amendment's
  merge and are then reconciled without discarding its journal.
- **Handoff:** Findings return to the coordinator. Any implementation delegation
  receives explicit file ownership and preserves this effort's completion gate.
- Preserve the existing installer, source-construction and census owners.
  This effort does not replace the installer or waive native ownership checks.

## Context

#10486 records the missing session-preserving handover boundary. The existing
thin forwarder can attach to a shared host, but its documented host-loss path
exits rather than reattaching. Initialization, upstream sessions, notifications
and accepted calls cannot simply be replayed as a new session.

An old host can remain active while newer code is selected on disk. Fresh
retirement can also refuse an uncertain process census before installation
metadata and serving state converge. Silencing that refusal, killing old
interpreters or rewriting a route is not a session-preservation solution.

The [install contract](../../../docs/install-contract.md#authoritative-replacement-and-completion)
and [service-contract evolution pattern](../../../docs/patterns/service-contract-evolution.md)
govern the implementation. Source review, promoted release and actual consumer
acceptance are distinct milestones.

## Request

The settled requirement is fresh-call generation selection **and** preserved
session reattachment, not merely an old-host drain. The operator's exact request
and reattachment selection are retained in
[inception-transcript.md](inception-transcript.md).

## Scope Approvals

- The operator requested a source-backed MCP retirement/update fix and selected
  reattachment when explicitly offered reattachment versus old-host draining.
- Existing live clients and state-preservation processes must not be force-killed.
  Their retained code cannot be live-patched to manufacture new protocol support.
- This reviewed plan does not itself grant rollout, elevation, service-restart,
  configuration-policy changes or destructive cleanup authority. Apply any
  separately established target-specific authorization at its existing boundary.

## Plan

The decomposition and implementation choices below are **agent-recommended**.
Land and qualify incoming publication as the first implementation slice.
Complete custody/protocol realization and adopted-wrapper reattachment remain
separate required work, not prerequisites for that slice. Every Plan and
Validation Plan item must still be resolved or explicitly transferred to a
named tracked objective before the whole effort is Done.

### Phase 1 - Reviewed contract

- [ ] Resolve concrete client/upstream state ownership and reuse existing
  lifecycle/transport primitives rather than inventing a parallel supervisor.
- [ ] Reconcile client-bound stdio conduits and shared custody against the
  [service process-count contract](../../../visions/plugin-services/README.md#process-count-scales-with-services-not-sessions).
  Heavy backends and custody services must be bounded by installation and
  identity-compatible pooling, not new per-client daemons or supervisor trees.
- [ ] Specify the versioned handover, admission and outcome contract, including
  the legacy-client adoption boundary and unsupported/rollback behavior.
- [x] Land this prospective effort through normal automated review before code
  (#10600). Handover-specific design obligations gate that implementation, not
  an independently qualified incoming-publication correction.

### Phase 2 - Incoming publication and fresh-call convergence

- [ ] Keep the literal portable `agent-mcp bridge` entry and resolve current
  software for every fresh invocation.
- [ ] Correct #10652: publish authentic incoming readiness, selectors, commands,
  routing and required registrations independently of outgoing census,
  provenance, control-home idleness, recovery and retirement. Old-state
  inspection failure is a deferred cleanup result, not an incoming veto.
- [ ] Qualify #6389 through the actual fresh literal command and incoming
  physical-generation admission, including an explicit bridge configuration
  absent from global discovery. An old capable endpoint or importable slot is
  not serving-currentness proof.
- [ ] Preserve automatic/manual inhibition, necessary durable-state migration
  and conflicting-work admission protection, and truthful actual incoming
  source, authentication, build, permission, I/O and health failures.
- [ ] Report incoming completion, outgoing cleanup and per-session handover
  independently. Completing this slice does not complete the handover feature
  or the effort.

### Phase 3 - Session-preserving handover and independent retirement

- [ ] Retain a client-owned, installation-independent stdio lifetime boundary;
  replacement payloads and shared runtime slots must not invalidate it.
- [ ] Reattach adopted wrappers to the incoming shared runtime while preserving
  initialization, upstream session identity, notifications, ordering and
  accepted/in-flight/unknown call outcomes without blind mutation replay.
- [ ] Preserve identity/credential isolation, bridge configuration, decorators,
  routed targets and protocol-era compatibility.
- [ ] Self-retire client preservers on disconnect/EOF or verified owning-session
  termination; do not mistake a wrapper parent's exit for session completion.
- [ ] Close superseded admission through the existing owner and retire old
  central generations only after their protected state is transferred or
  drained. Exclude client preservers and independently owned resources.
- [ ] Correct #10653: installer `--force` is not proof of safe drain or authority
  to terminate attached state. Any eligible independent central-only retirement
  revalidates native identity, installation, explicit role and then-current
  selection/registration at mutation time. Unknown cleanup stays deferred.
- [ ] Preserve pre-feature live clients without claiming that their old code
  acquired a new handover protocol. Name any remaining legacy adoption hold.

### Phase 4 - Qualification, release and acceptance

- [ ] Run focused protocol and real-process regression controls on supported
  Windows and POSIX boundaries, plus practical clean-room install qualification.
- [ ] Update owning reality docs, contract declarations and per-plugin
  changefiles; land implementation through review and required CI.
- [ ] Verify promotion, then perform separately authorized installed-consumer
  acceptance. Record old-client preservation separately from actual reattachment.
- [ ] Resolve every plan and validation gate, or transfer an explicitly blocked
  legacy adoption item to its named tracked owner, before closing this effort.

## Validation Plan

- [ ] First-slice incoming publication succeeds with a live old keeper,
  unfinished handover, unavailable outgoing census/provenance or occupied old
  control home; verify every applicable current selection and actual fresh
  execution of the incoming generation through its owned namespace.
- [ ] Pair with genuine incoming source-admission, authentication, build,
  permission, I/O and health failures that prevent incoming completion.
  Necessary durable-state compatibility and conflicting-work protection remain
  enforced; do not require takeover of an unknown foreign listener.
- [ ] The incoming-complete result leaves outgoing-deferred, handover-pending
  and whole-effort gates visibly distinct. No new selector or manifest alone
  manufactures success, and no old cleanup result reverses healthy incoming
  completion.
- [ ] A real client remains connected through an update, its original wrapper
  remains alive, and subsequent requests are handled by the incoming runtime.
- [ ] A simultaneous fresh literal `agent-mcp bridge` invocation reaches the
  incoming runtime, not an advertised stale host or obsolete command redirect.
- [ ] Compare one client with multiple simultaneous clients and an update in
  progress: heavy backend/custody process counts remain service/installation
  bounded, client identities stay isolated, and no per-client daemon or
  supervisor tree is introduced.
- [ ] Session initialization, upstream identity, notifications and accepted
  calls survive handover; side-effecting calls are not duplicated on ambiguous
  responses, failed handover or transport interruption.
- [ ] Exercise current/prior peers, supported rollback and a pre-feature peer
  lacking reattachment. Unsupported support is explicit; live legacy clients
  are preserved rather than forcibly migrated.
- [ ] Failed candidate health, changed owner/route/selection and an old
  generation's late cleanup cannot corrupt or retire the current generation.
- [ ] EOF/disconnect and verified owner death settle exact client resources;
  an interposed parent exit does not destroy a still-owned live session.
- [ ] Actual old central retirement and client-preserver survival are verified
  independently. No process-name sweep, whole-root reset or manufactured receipt.
- [ ] Held clients survive installer cutover beyond its grace, including the
  legacy forced-cutover/no-reap combination. Automatic `--force` preserves
  automatic inhibition and never authorizes state-holder termination.
- [ ] Actual consumer evidence distinguishes source qualification, released
  payload, selected runtime and the generation that handles a tool call.

## Proposal

Prospective requirements and validation obligations only. The recommended
owner-composed design is specified in [handover-contract.md](handover-contract.md).
It separates stable connection custody from replaceable policy/decorator code;
retaining an entire old pipeline behind a new relay is not accepted as new
backend execution. This plan does not assert that reattachment or legacy live
migration already works.

## Journal

### 2026-11-04 - Kickoff

- Claimed #10486, resolved the source repository's effort adoption and captured
  the operator's explicit reattachment selection.
- Started bounded read-only evidence on client state, activation and reusable
  session/lifecycle primitives. No implementation or live migration performed.
- Source evidence found no existing generic MCP-session migration broker.
  Added a prospective quiescent-handover contract and explicit multi-client
  process-count validation in response to review.

### 2026-11-05 - Reconcile the existing incoming-completion architecture

- The operator requested bug filing and effort correction against the existing
  north-star. #10652 and #10653 separate the concrete implementation defects;
  #6389 retains serving-convergence ownership and #10486 retains full reattachment.
- Made independently qualified incoming publication the first landable slice.
  Old cleanup is not an incoming gate; completing publication does not close
  handover or whole-effort validation. Historical evidence remains unchanged.
- #10600's reviewed plan is merged and implementation is in progress. This
  documentation amendment claims no runtime correction, release or rollout.
