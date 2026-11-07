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
- **Native observation prerequisite:** #10925 (fixture-scoped desktop evidence)
- **Resident configuration correction:** #10951 (qualified caller context)

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

Consumer qualification must observe the fixture's own native effects, not demand
that unrelated desktop activity stop. The generated-CMD witness therefore needs
fixture-scoped observation with verified console-host attribution. This is a
source-qualification prerequisite, not a new runtime incoming-readiness gate.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| battered-sprocket | Coordinator, implementation integration and PR owner | Managed source worktree |
| lopsided-gasket | Existing handover implementation and focused qualification | Managed implementation worktree |
| patient-spanner | Incoming/retirement architecture and acceptance amendment | Managed documentation worktree |
| soldered-ratchet | Fixture-scoped observer contract and SDK qualification prerequisite | Managed source worktree |
| squeaky-crank | Bounded #10951 resident configuration-context implementation | Managed source worktree; coordinator owns publication and rollout |
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
- soldered-ratchet owns the prospective #10925 SDK observation slice, separately
  from lopsided-gasket's MCP implementation. Review this contract before SDK
  implementation; preserve both branches' journals and existing resource owners
  when integrating. No duplicate observer framework or implementation writer.
- squeaky-crank owns only the #10951 configuration-context correction, tests and
  local commit. It does not own publication, release, rollout, observer effects,
  incoming-installer convergence or session-handover implementation.

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
The later observer-scope request is captured there separately; it does not
replace the fresh-execution and session-reattachment objective.

## Scope Approvals

- The operator requested a source-backed MCP retirement/update fix and selected
  reattachment when explicitly offered reattachment versus old-host draining.
- Existing live clients and state-preservation processes must not be force-killed.
  Their retained code cannot be live-patched to manufacture new protocol support.
- This reviewed plan does not itself grant rollout, elevation, service-restart,
  configuration-policy changes or destructive cleanup authority. Apply any
  separately established target-specific authorization at its existing boundary.
- The operator requested that the observer not watch the whole desktop.
  The scoped consumer contract below is agent-recommended implementation of that
  request, not permission to weaken source admission or discard unknown events.
- Observer planning and source review grant no native effects. Any existing
  bounded visible-control authorization retains its original target, count,
  activation/restoration/retirement limits and unused-or-consumed state.

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

### Resident configuration-context correction

- [ ] Correct #10951: transport the caller's configuration root and bridge
  binding to the resident consumer, independently resolve the qualified owner,
  and preserve direct/resident preference parity without process-global cwd or
  environment changes. Refuse missing/mismatched context and isolate concurrent
  callers; unsupported peers retain a pre-effect direct fallback.

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

- [ ] Land the reviewed fixture-scoped observer contract below before its SDK
  implementation; retain #10925 as its discrete coordination issue.
- [ ] Implement and qualify that SDK-owned observation scope, then enroll the
  exact generated-CMD consumer through source-bound native admission.
- [ ] Run focused protocol and real-process regression controls on supported
  Windows and POSIX boundaries, plus practical clean-room install qualification.
- [ ] Update owning reality docs, contract declarations and per-plugin
  changefiles; land implementation through review and required CI.
- [ ] Verify promotion, then perform separately authorized installed-consumer
  acceptance. Record old-client preservation separately from actual reattachment.
- [ ] Resolve every plan and validation gate, or transfer an explicitly blocked
  legacy adoption item to its named tracked owner, before closing this effort.

### Fixture-scoped native observation contract

The following design is **agent-recommended and prospective**. It closes the
observation gap in the existing native launch-validation pattern; it does not
change the installer vision, make prepared-application qualification an incoming
gate, or claim that a scoped observer already exists.

- Use the existing native process-ownership, lifecycle and desktop-observer
  owners. Add a distinct, versioned consumer scope rather than repurposing the
  SDK self-probe's literal case roster. Preserve the existing self-test profile;
  its whole-desktop evidence is not scoped-consumer evidence.
- Acquire window/focus evidence through fixture-bound native subscriptions and
  queries. The consumer mode must not install a desktop-wide event hook, enumerate
  unrelated desktop windows, or collect a global stream and filter it into a
  passing result. Do not collect unrelated titles, screenshots or focus history.
- Bind fixture roots and descendants by native identity and birth, not process
  names, titles, age or caller-asserted membership. Establish observation coverage
  before relevant launch effects. Resolve descendant discovery/startup ordering,
  early exit and identity-reuse races through the existing launch/lifecycle owner;
  unproven startup coverage cannot qualify a zero-window result.
- Include console and Default Terminal hosts only through a verified relationship
  to the fixture. A shared host does not make all its windows fixture-owned:
  constrain observations to the verified process/thread/window boundary.
  Unsupported or ambiguous host attribution remains unqualified.
- Record only fixture-owned visibility and foreground transitions. A minimal
  pinned original-foreground endpoint may be retained solely for an authorized
  calibration restoration; that is not permission to monitor unrelated activity.
- Preserve raw scoped evidence, source bindings, epoch/identity checks, bounded
  event delivery, gaps, overflow, callbacks and cleanup outcomes. Missing identity,
  incomplete coverage or observer failure must not become an ignored event or a
  manufactured zero. Source-bound expected cases and rosters remain enforced.
- Keep calibration, workload observation and restoration/retirement evidence
  distinct. A scoped profile needs its own validated evidence contract, not relaxed
  checks on the existing whole-desktop packet. Native controls remain opt-in and
  outside the required pure-unit inventory. If qualification needs an effect beyond
  current authorization, stop for that exact additional approval.
- Qualify the actual generated completed-slot CMD from a windowless parent across
  two cycles. Observe the PowerShell resolver and configured descendants/hosts;
  flags, a helper-only proxy or a quiet interval after startup do not substitute
  for the changed launch chain.

## Validation Plan

- [ ] First-slice incoming publication succeeds with a live old keeper,
  unfinished handover, unavailable outgoing census/provenance or occupied old
  control home; verify every applicable current selection and actual fresh
  execution of the incoming generation through its owned namespace.
- [x] #10951 source configuration-consumer controls cover a neutral daemon cwd,
  direct/resident parity, missing/mismatched context, concurrent repository
  separation and mixed-version pre-effect fallback. Source qualification is not
  released or installed-consumer acceptance.
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
- [ ] Scoped acquisition refuses desktop-wide subscriptions/enumeration in the
  consumer mode. Unrelated activity cannot change its verdict or enter its
  recorded window/focus evidence; retain the old SDK self-profile's contract.
- [ ] Positive owned-window and owned-focus evidence is detected, while forged,
  stale, reused, exited or missing bindings and extra cases cannot produce a pass.
  Cover dynamic descendants and shared/ambiguous Default Terminal host boundaries.
- [ ] Coverage begins before relevant effects and remains complete through exit;
  startup races, lost callbacks, delayed delivery, overflow and unconfirmed cleanup
  remain unqualified. Process flags or sampled post-start quietness are not proof.
- [ ] The actual MCP generated-CMD two-cycle case passes with scoped evidence and
  verified owned cleanup under its existing consent/deadline. Calibration count,
  foreground restoration and control retirement are reported independently;
  no additional native effect is inferred from this plan.

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

### 2026-11-05 - Fixture-scoped observation request

- Captured the operator's request to stop observing the whole desktop. Source
  inspection found unfiltered WinEvent hooks and whole-desktop enumeration in
  the SDK self-probe, not an existing scoped application-consumer API.
- Claimed #10925 and proposed a separate SDK-owned qualification slice before
  implementation. It preserves the literal self-test roster and makes identity,
  acquisition scope, startup coverage and console-host ownership explicit.
- This amendment is prospective only. No observer code or native effect is
  claimed; review, source qualification and actual generated-CMD acceptance
  remain open. Incoming publication, full handover and installed acceptance
  retain their original completion gates.

### 2026-11-06 - Qualified resident configuration slice

- Claimed #10951 as a bounded correction within Phase 2. The resident data
  directory is not the caller's repository. The correction carries a versioned
  caller-root/bridge selection while the native consumer independently reads its
  existing qualified configuration owner, cell matching and provenance.
- Added authenticated data-plane capability discovery before bridge operations;
  unsupported peers take the existing direct path before upstream effects.
  Missing/mismatched context is refused, directory-marketplace discovery uses
  the caller selection, and warm entries are separated by root/resolved source.
  No global cwd/environment mutation, copied authority or new process owner.
- Source qualification passed 200 focused tests, with 9 existing platform
  skips, through the bounded runner; cleanup was verified. The packet
  includes the real configuration consumer, concurrent repository separation,
  call/attach loading and actual CLI-parser fallback. Lint, install-contract,
  module-size, version and generated-source consistency checks passed.
- This closes the configuration-consumer source-control item only.
  squeaky-crank owns the local commit; the coordinator retains publication,
  review, release and authorized installed-consumer qualification. No rollout,
  observer effect or native calibration occurred. #10951's delivery, incoming
  publication, full handover and the whole-effort gate remain open.
