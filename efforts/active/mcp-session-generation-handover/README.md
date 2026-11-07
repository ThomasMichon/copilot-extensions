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

MCP lifecycle qualification is independent of terminal-host focus and desktop
observation. Observer, host-attribution and calibration requirements were an
agent-introduced coupling and are withdrawn from this effort. They do not gate
incoming publication, handover, source acceptance, release or installed MCP
acceptance.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| battered-sprocket | Coordinator, implementation integration and PR owner | Managed source worktree |
| lopsided-gasket | Existing handover implementation and focused qualification | Managed implementation worktree |
| patient-spanner | Incoming/retirement architecture and acceptance amendment | Managed documentation worktree |
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
- squeaky-crank owns only the #10951 configuration-context correction, tests and
  local commit. It does not own publication, release, rollout, observer effects,
  incoming-installer convergence or session-handover implementation.
- Generic SDK observation work belongs to the independent #10925 objective, not
  an MCP implementation slice. Historical journals remain here unchanged; SDK
  progress cannot block this effort's independent implementation or completion.

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
The later observer-scope request and explicit withdrawal of its MCP coupling are
captured there separately. Neither changes the fresh-execution and
session-reattachment objective.

## Scope Approvals

- The operator requested a source-backed MCP retirement/update fix and selected
  reattachment when explicitly offered reattachment versus old-host draining.
- Existing live clients and state-preservation processes must not be force-killed.
  Their retained code cannot be live-patched to manufacture new protocol support.
- This reviewed plan does not itself grant rollout, elevation, service-restart,
  configuration-policy changes or destructive cleanup authority. Apply any
  separately established target-specific authorization at its existing boundary.
- The operator explicitly disconnected MCP from terminal-host focus. Generic SDK
  observation work is separately owned; its plan and qualification are not MCP
  gates. Earlier application-bound calibration approval does not transfer to SDK
  work or authorize any new effect.

### Withdrawn observer scope

The generated-CMD desktop/focus witness, terminal-host attribution and associated
observer/calibration prerequisites are **WITHDRAWN**, not implemented or validated
acceptance. The original source commits and historical journals remain evidence
of what was attempted, not active MCP requirements. Generic SDK requirements
have one canonical owner:
[Fixture-Scoped Native Observation](../fixture-scoped-native-observation/README.md)
(#10925). Functional generation selection, protocol/session preservation and
owned-process cleanup remain this effort's requirements.

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

- [x] Correct #10951: transport the caller's configuration root and bridge
  binding to the resident consumer, independently resolve the qualified owner,
  and preserve direct/resident preference parity without process-global cwd or
  environment changes. Refuse missing/mismatched context and isolate concurrent
  callers; updated callers retain a pre-effect direct fallback with unsupported
  old providers. Source correction #10969 is merged, not released or installed
  acceptance, and does not close #10951/#10486.
- [ ] Verify release/promotion of the merged resident configuration-context
  correction; a source merge does not make the selected runtime live.
- [ ] Perform separately authorized installed primary-consumer acceptance of
  that correction. This remains open independently of release and SDK observation.

The context correction is not transparent migration of legacy clients. Legacy
old callers reaching a new provider without context receive an explicit
rejection and need an updated client or `--no-serve`.

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

- [x] Deferred to `#10925`: SDK-owned observation contract, source foundation and
  independent qualification. This resolves ownership only; it is not MCP
  validation and does not wait for the SDK objective to finish.
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
- [x] Deferred to `#10925`: Generic SDK scoped-acquisition, identity/admission,
  coverage and cleanup validation. Application-specific generated-CMD observation
  and calibration conditions are withdrawn scope, not completed validation.

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

### 2026-11-05 - Scoped observer contract admitted

- Contract PR #10930 merged after current-head automated approval and required
  CI. The source worktree was pulled forward onto the merged contract before
  implementation.
- The next slice is the SDK-owned scoped acquisition/evidence implementation and
  exact MCP consumer enrollment under #10925. No observer code or native
  qualification is credited by the contract merge.
- The effort remains Active. Incoming publication, full adopted-wrapper handover,
  release, authorized installed acceptance and bounded native-effect approvals
  retain their independent gates.

### 2026-11-06 - Root-scoped SDK source foundation

- Extended the existing atomic Windows launch owner with opt-in admission between
  suspended Job-bound creation and resume. Missing deadlines, breakaway,
  refusal, expired admission and replacement acknowledgements fail before resume;
  original exact-root/Job cleanup and default behavior remain intact.
- Added native process-filtered acquisition, retained handle-derived identity
  and birth, an initial owned-thread query and a pump barrier. The separate
  source/epoch-bound schema retains raw root events, violations, gaps, overflow
  and cleanup failures. Unknown unhook/handle cleanup retains exact custody.
  No desktop-wide hook, enumeration, foreground capture, title or screenshot is
  used in this scope.
- Enrolled only the literal completed-slot MCP case through the existing SDK
  source owner. This is source integrity and case binding, not native execution
  consent, provenance or full consumer acceptance. Unadmitted descendants and
  every unverified console/Terminal host remain unsupported; packets explicitly
  withhold full consumer qualification.
- The bounded focused selection passed 314 synthetic controls, without skips;
  its supervisor confirmed cleanup. This includes portable pump threads, not
  native desktop acquisition. The import-isolated selection also preserves the
  original SDK self-profile's admission, packet and observer controls.
- Python F/E9 lint, install-contract, documentation consistency and scoped module
  caps passed. Refreshed only four changed source bindings through the existing
  unit admission contract; retained all 4,175 nodes, 597 sources, 251 targets and
  the 300-second budget. The new threaded fixture stays outside that packet.
- Documentation-impact review updates the library API and testing catalog;
  shared-library and all consuming distributions receive changefile metadata.
  Dependencies and their minimum-age policy are unchanged.
- Recorded the pending minimal opaque-graph/debug-event and terminal-association
  amendment above. No debugger, new controller, native GUI action or calibration
  was implemented or executed. Fresh required native Linux CI, reviewed
  instrumentation decisions, actual MCP assembly, two-cycle native observation
  and separately authorized calibration remain open. The effort remains Active;
  incoming publication, session handover, legacy adoption, release and installed
  acceptance gates are not closed by this source foundation.

### 2026-11-06 - Settle observer custody after post-admission launch failure

- Added a lexical context to the existing scoped observer, surrounding the launch
  itself. Successful admission followed by native resume or later launch failure
  now reaches observer cleanup even when no process/Job values return.
  The original native owner still controls root/Job and stream settlement.
- Context exit propagates the same original exception and preserves its root/Job
  custody and cause. Independent observer cleanup uncertainty is attached with
  the live observer and its evidence. Without a body exception, cleanup uncertainty
  raises a structured `ScopedCleanupError`; it is not reported as closed success.
- Close attempts are one-shot. Repeated context/explicit cleanup reports the
  cached result without replaying unknown native closes, including a process pin
  retained by failed admission. Closing without `finish()` remains unqualified.
- Integrated synthetic controls cover original resume failure, independent Job
  settlement failure, confirmed and unconfirmed hook/pin cleanup, exact exception
  identity, retained custody and no retry. Targeted controls passed, followed by
  the complete six-file selection: 323 passed, no skips, supervised cleanup
  confirmed. Private JUnit, supervisor and exact-command receipts are retained
  for source review; these are not native desktop qualification.
- The required unit roster, source/coverage inventory and 300-second budget are
  unchanged. Neither changed Python file belongs to its admitted source closure,
  so no unrelated source digest or test admission was refreshed. Final-head guard
  qualification is recorded separately rather than inferred from the foundation's
  earlier guard run.
- No new controller, debugger, native GUI action or calibration was introduced
  or executed. Full consumer/terminal coverage, native MCP acceptance, required
  CI, review, release and the whole effort remain open.

### 2026-11-06 - Withdraw observer coupling and transfer SDK ownership

- Captured the operator's correction: MCP must be disconnected from terminal-host
  focus. The observer prerequisite was agent-introduced, not part of the original
  generation-selection/session-preservation objective.
- Withdrew application-specific observer, host/focus and calibration gates from
  active MCP scope. This is a scope correction, not successful implementation or
  validation. MCP functional/protocol/current-generation/cleanup work remains
  independently required.
- Transferred generic SDK requirements to
  [Fixture-Scoped Native Observation](../fixture-scoped-native-observation/README.md),
  the independent #10925 objective. It is not a child completion gate for MCP.
  Earlier journals above remain verbatim history; no source work was discarded.
- The SDK enrolls its own dedicated managed-root case rather than an MCP case.
  No earlier MCP-bound calibration approval transfers. SDK debugger/new-graph
  work, SDK native execution and SDK calibration remain held.

### 2026-11-06 - Resident context source merged; release and acceptance open

- #10969 merged the resident configuration-context source correction after
  current-head automated review and required CI. Marked its source Plan item
  resolved; #10951 and the #10486 provider/handover objective remain open.
- Updated callers fall back directly before effects with unsupported old
  providers. Legacy old callers without context receive explicit rejection from
  a new provider and require an updated client or `--no-serve`; no transparent
  old-client migration is claimed.
- Source completion remains pending release and separately authorized installed
  primary-consumer acceptance, with distinct unchecked Plan items. It is not
  live deployment or whole-effort completion and does not wait for SDK
  observer/focus/host qualification.
