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
- **Independent SDK observation work:** [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925); not an MCP prerequisite
- **Resident configuration correction:** #10951 (qualified caller context)
- **Separate prepared qualification:** #10742 under campaign #7028; not the ordinary-installer slice's publication gate

## Goal

Current operator reaffirmation:

> Seems like there are issues with MCP we need to deal with, to fix version-over-version compat and instll success

**Interpretation:** This reaffirms the unchanged Guiding Intent below, including
fresh physical-generation execution and preserved session reattachment. It does
not replace the original Request or waive release and installed-consumer gates.

### Selected originating-owner design

> explicit-launcher-handoff

The controlled launcher supplies the actual originating client process's native
PID and birth at its launch boundary. Ancestor guessing and discovery through
another plugin's session files are not the selected design. This source design
choice does not itself qualify automatic caller adoption or installed rollout.

### Application-state ownership boundary

> Install shouldn't be messing with a live DB or state file; this is one area where we do have to figure out lock management with the outgoing version, during boot of the incoming service versions

This supplements, rather than replaces, the request above. Installation stages
software; it does not rewrite or migrate live application DB/state files.
Incoming boot must coordinate safe compatible access, quiescence or ownership
transfer with the outgoing state owner before making state changes. This is
necessary incoming readiness, distinct from optional predecessor cleanup.

### Requested boot and recovery sequence

> For SQLite, I assume we need the existing version to release its lock on the DB and finish pending transactions. When starting the new version of a service, we will need to:
> 1. Check for existing service version
> 2. Negotiate handoff with existing service
> 3. Upon failure to negotiate, force-terminate existing service
> 4. Check health of existing DB, perform repair/update if needed, take ownership
> 5. In case of failure, rename existing DB to backup, create new DB in "state recovery mode".

The forced fallback is an outgoing-service lifecycle policy, not permission to
terminate separate client/session keepers or an arbitrary process tree.
Ownership and uncertain accepted-operation outcomes remain explicit.

### Durable state authority

> Per my rules: all DBs fundamentally hold recoverable state that should be recomputable from file-system-based state, usually persisted in worktree/session folders, access over SSH, or Git. Only known "violation" is agent-dispatch Tasks, for which we probably should have an agent design a fallback-recover mechanism.

DBs are intended to be rebuildable projections, not the sole authority.
Dispatch Tasks is the operator-identified known exception; its filesystem
fallback requires a separate design before DB replacement can be treated as
lossless recovery. This intent does not claim that every existing DB's
reconstruction path has already been qualified.

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

MCP source qualification, release and acceptance have no desktop-quiet,
observer, focus/window-calibration, Terminal-host attribution or debugger
prerequisite. The application-specific observation conditions formerly proposed
here are **withdrawn, not passed**. Generic SDK observation work is independently
deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925);
it cannot delay MCP. Functional stdio, runtime-selection, exit and exact-owned
cleanup evidence remains required, as do source admission, review, required CI
and authorized installed-consumer acceptance. No global validation rule is waived.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| battered-sprocket | Coordinator, implementation integration and PR owner | Managed source worktree |
| lopsided-gasket | Existing handover implementation and focused qualification | Managed implementation worktree |
| patient-spanner | Incoming/retirement architecture and acceptance amendment | Managed documentation worktree |
| squeaky-crank | Bounded #10951 resident configuration-context implementation | Managed source worktree; coordinator owns publication and rollout |
| stubborn-latch | #11524 Worktrees incoming-monitor publication correction | Coordinator-controlled source worktree; separate from MCP handover implementation |
| Session-path evidence | Read-only client, host and upstream-state analysis | Coordinator delegation |
| Activation evidence | Read-only installer, selection and retirement analysis | Coordinator delegation |
| Keeper evidence | Read-only existing lifecycle/transport primitive analysis | Coordinator delegation |

## Coordination

- **Topology:** Independently qualified incoming-publication slice first, then
  full handover; documentation and implementation PRs have non-overlapping file
  ownership. No duplicate implementation writers.
- **Host (owns PRs):** battered-sprocket coordinates implementation;
  patient-spanner owns the merged documentation amendment.
- **Delegates:** Evidence roles are read-only. Lopsided-gasket retains
  exclusive implementation ownership; its effort-file edits are reconciled
  with the merged amendment without discarding its journal. The coordinator
  retains independent review, publication, release and acceptance.
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
- stubborn-latch owns the #11524 Worktrees installer/monitor correction under
  the coordinator. Lopsided-gasket's MCP handover implementation and retained
  source changes remain independently owned; this correction does not replace
  that writer or settle its remaining work.

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
The later observer-scope request and superseding withdrawal of MCP observation
gates are captured there separately; neither replaces the fresh-execution and
session-reattachment objective.

## Scope Approvals

- The operator explicitly approved publishing the bounded ordinary-installer
  correction while tracking prepared-application qualification separately.
  That approval does not close the prepared matrix, full reattachment, release
  or legacy-adoption gates, nor authorize a public-feed fallback or trust changes.
- The operator requested a source-backed MCP retirement/update fix and selected
  reattachment when explicitly offered reattachment versus old-host draining.
- The operator approved coordinator custody of the retained implementation:
  reconcile its preserved journal and prototype against current Dev, then
  continue through normal tests and review. This grants no work discard,
  production effects, process termination, elevation or review/CI bypass.
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
owned-process cleanup remain this effort's requirements. Debugger conditions
are also withdrawn; no original calibration approval is transferred or consumed.

## Plan

The decomposition and implementation choices below are **agent-recommended**.
Land and qualify incoming publication as the first implementation slice.
Complete custody/protocol realization and adopted-wrapper reattachment remain
separate required work, not prerequisites for that slice. Every Plan and
Validation Plan item must still be resolved or explicitly transferred to a
named tracked objective before the whole effort is Done.

### Phase 1 - Reviewed contract

- [x] Resolve concrete client/upstream state ownership and reuse existing
  lifecycle/transport primitives rather than inventing a parallel supervisor.
- [x] Reconcile client-bound stdio conduits and shared custody against the
  [service process-count contract](../../../visions/plugin-services/README.md#process-count-scales-with-services-not-sessions).
  Heavy backends and custody services must be bounded by installation and
  identity-compatible pooling, not new per-client daemons or supervisor trees.
- [x] Specify the versioned handover, admission and outcome contract, including
  the legacy-client adoption boundary and unsupported/rollback behavior.
- [x] Land this prospective effort through normal automated review before code
  (#10600). Handover-specific design obligations gate that implementation, not
  an independently qualified incoming-publication correction.

### Phase 2 - Incoming publication and fresh-call convergence

- [x] **Bounded source slice merged:** #10754 landed at
  `6dec373a753716d4e780d9ed03079c263dd61b55` after required current-head CI and
  an accepted owner-authored review with no open findings. The completion
  items below retain promoted-release and authorized installed-consumer gates.
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
- [x] **Corrective source merged:** #11407 restores native-proven dead-handle
  startup and batches installer dependency preparation. Final-head automated
  approval and required CI passed.
- [x] **Corrective release verified:** Main
  `06aac8c5f6e596652a4a497c4af25a39f44e3e66` promotes qualified Dev
  `fe117fd8609ffa22bc5e787be3fbdf39903c9a12`, containing #11407 and #11153.
  MCP was released as `0.2.207-dev1`; the later `0.2.208-dev1` release retains
  the checked acceptance paths.
- [x] **Bounded literal installed call:** one configured read-only status call
  through the installed MCP208 command succeeded in 8.37 seconds, with a live
  current control/data listener observed afterward and measured legacy
  native incarnations preserved. This is not full constructor, reattachment or
  whole-host qualification.
- [ ] **Constructor correction #11455:** publish and release native readiness
  validation independent of caller repository preferences, then verify actual
  installed constructor publication and a clean measured update.
- [ ] **Clean measured sweep:** resolve actual incoming completion and update
  performance; a selected runtime or a successful isolated tool call does not
  make a failed full update successful.
  - [ ] **Nested-library version coverage #11704:** release the transitive
    consumer-map correction and affected-consumer patch changefiles, then verify
    ordinary reconciliation against the newly versioned payloads. Coordinator
    retains review, release and installed acceptance; source guards alone do
    not complete the measured sweep.
    - [x] **Source correction merged:** #11709 landed at
      `4176aaea9a1d3dde5f3e6f924cc5542e7fce9483` after current-head approval
      and required CI. Promotion and installed reconciliation remain open.
- [x] **Worktrees incoming-monitor source correction:** #11673 merged the
  #11524 correction at `2e575be1a26c789f1b5b1912df626165fb9c7158`
  after current-head approval, required CI and Windows/Linux native acceptance.
- [x] **Bounded Worktrees correction delivery:** #11673 was promoted to Main
  `48556f2680f5dbbdbdf5ada65867a5feb5d85de5`; authorized Windows payload and
  runtime phases installed `1.25.53-dev1`. Independent serving, native identity,
  physical-runtime and selection checks passed. This is not a normal full-update,
  outgoing-cleanup or MCP-session-reattachment result.
- [x] **Optional installer backstop source merged:** #11550 delivers the #11534
  termination-free, addressed-runtime correction; explicit lifecycle-owner
  cleanup remains separate.
- [x] **Backstop release verified:** Main
  `2f22e1803fa7f6e5c85efee839c50881245374ec` contains the reviewed collector,
  helpers and initializers; both MCP archives retain the matching collector.
- [x] **Bounded installed backstop proof:** the targeted Windows MCP
  `0.2.217-dev1` runtime phase and fresh literal bridge passed; all 18 observed
  MCP-tree native births remained unchanged. This is not full-host or
  adopted-session reattachment acceptance.

### Resident configuration-context correction

- [x] **Source merged:** #10951 correction landed through #10969 at
  `4cecd3fe3f677041e0beb0118397b2deb18b3227`: transport the caller's configuration
  root and bridge binding to the resident consumer, independently resolve the
  qualified owner, and preserve direct/resident preference parity without
  process-global cwd or environment changes. Refuse missing/mismatched context
  and isolate concurrent callers; unsupported peers retain a pre-effect direct fallback.
- [ ] **Release open:** verify promotion of the source correction; a source
  merge is not a released or running provider.
- [ ] **Authorized primary-consumer acceptance open:** verify installed
  direct/resident parity against the actual primary configuration owner.

Old clients that omit the versioned caller-root context are not transparently
compatible with the updated resident protocol. Update the affected client or
use its direct `--no-serve` path where supported. New-client fallback to an old
provider occurs before application effects; it is not transparent legacy
handover. Preserve held sessions and the separate pre-feature adoption hold.

### Phase 3 - Session-preserving handover and independent retirement

- [x] **Provider-first source merged:** #11911 landed at
  `70ecc34fc56bb71fa9a7a279ed7de7bfd266ce87` after current-head automated
  approval and required CI. Internal custody/backend/conduit providers are
  expanded; ordinary caller selection and installed adoption are unchanged.
- [ ] **Separate warm-reuse follow-up under #10486:** qualify broader
  HTTP/dynamic-auth/routed/CLI execution and per-target context/credential
  custody before expanding sharing. Preserve actual stdio cwd semantics.
  The bounded slice's explicit direct fallback and cold-start cost are
  operator-selected limitations, not additional incoming gates.
- [ ] Retain a client-owned, installation-independent stdio lifetime boundary;
  replacement payloads and shared runtime slots must not invalidate it.
  The internal explicit-owner path and hosted packet are now qualified on
  disposable hosted Windows. Next, supply originating-owner acquisition for
  eligible ordinary callers without weakening native lifetime admission.
  - [x] **Explicit launcher CLI handoff:** accept the paired native owner on
    `bridge` and `forward`, preserve unproven callers' existing selection, and
    qualify the real CLI through the hosted lifetime packet. #12036 merged at
    `b2bda964dc42593bc94a2cb37cde9e3520f68962`; the
    [source-pinned CLI packet](journal/2026/11.10-explicit-owner-cli.md#qualification-and-merge-follow-up)
    passed. Selected-runtime discovery, controlled production launcher capture
    and automatic adoption remain separate work.
  - [x] **Selected-runtime discovery provider:** report owner-handoff support
    from the actual physical selected executable without MCP startup, and
    qualify both selected and stale-slot queries across replacement. #12052
    merged at `59a739fce374514aaec89b65957c17a5bf6b2b29`; its
    [source-pinned query qualification](journal/2026/11.10-explicit-owner-cli.md#discovery-provider-qualification-and-merge)
    passed. Caller discovery/capture integration and rollback adoption remain
    open. #12306 corrects the live Windows declaration to retain explicit-pair
    support without advertising the optional receiver on Windows; promoted
    release and installed adoption of that correction remain separate.
  - [ ] **Windows controlled caller:** expand and qualify the optional
    physical receiver before emitting Windows caller overlays. Capture the
    actual native client's PID/birth from its creation handle before execution;
    do not reuse the Linux launcher's own identity or the observer-only native
    admission callback. Qualify private handoff lifetime, delayed MCP start
    after launcher exit, original stdio, rollback and exact cleanup without a
    new per-client supervisor. Native handle retention is an explicit
    qualification obligation, not a documented client guarantee. A
    [bounded native retention proof and realization sequence](journal/2026/11.10-explicit-owner-cli.md#windows-native-retention-proof-and-next-realization)
    establish feasibility, not caller adoption.
    - [x] **Windows provider-only receiver:** #12316 qualifies direct selected
      physical entry, native pre-effect fallback and owner refusal, physical
      replacement/rollback and exact cleanup. Capability advertisement,
      bootstrap, section transfer and controlled caller capture remain open;
      see the [source qualification](journal/2026/11.10-explicit-owner-cli.md#windows-provider-only-receiver-qualification-and-merge).
    - [x] **Windows section provider:** #12319 qualifies bounded versioned
      native-owner transfer, actual creation-handle identity, protected
      principal boundary, read-only retention, strict binding and owner-death
      refusal; see the
      [provider controls](journal/2026/11.10-explicit-owner-cli.md#windows-native-owner-section-provider).
      #12330 adds the opt-in, in-process pre-execution seam to the existing
      native creation/wait owner, including actual native refusal and packaged
      companion controls.
    - [x] **Windows independent section snapshot:** #12345 qualifies the
      in-process native publisher and stdlib-only pinned reader through
      publisher-process exit, protected principal boundary, read-only
      inheritance, malformed/owner refusal and exact cleanup, including actual
      wheel/source-distribution preservation. See the
      [source result](journal/2026/11.10-explicit-owner-cli.md#windows-independent-publication-and-snapshot-source-controls).
      Production caller selection and actual-client/installed acceptance
      remain open.
    - [x] **Windows anonymous actual-start bootstrap:** qualify the embedded
      native snapshot before installed-provider discovery, physical receiver
      selection and untouched stdio/context. Only pre-creation failure may
      fall back; accepted-child exits and wait errors must never replay.
      Controlled source packages are not actual-client or installed acceptance;
      advertisement and production caller selection remain disabled.
      #12350 merged at `ce3a3c1a3db4e5012154d2b0f069fb1e311a3cde`
      after current-head approval, required CI and Windows/Linux source
      controls. Actual native-client creation-bound consumption is next.
    - [ ] **Windows native-client consumption source:** qualify actual offline
      native Copilot through the existing suspended-creation owner and
      anonymous bootstrap after publisher exit. Require the exact native
      PID/birth, physical receiver, real upstream/client round trip, ordinary
      context/settlement parity and owned cleanup. Distinguish client-owned
      abrupt MCP settlement from graceful upstream EOF; neither this packet
      nor its source merge is installed or productive caller adoption.
  - [x] **Explicit Linux exec caller:** capture the exec owner's native pair
    and pass a sealed, session-only overlay for explicitly named configured
    entries, with actual-start discovery bound to a physical optional receiver.
    Preserve probe-only self-provisioning inhibition, cold-context fallback
    before effects and strict existing paired CLI semantics. Qualify actual
    Copilot consumption against an ordinary same-environment baseline,
    separately from the synthetic native-exec fixture. Preserve
    ordinary selection for unsupported entries and leave Windows capture,
    default caller adoption and installed acceptance open.
    #12192 merged at `5a9594df3a03a5294db2bcaa6182dbf8cba93fcd` after
    current-head approval, required CI and the exact-source Linux/native
    configuration-consumption packet. This closes the bounded source producer,
    not trace-sensitive checkpoint adoption or the parent lifetime boundary.
    All 18 affected non-changefile blobs are present byte-for-byte on Main
    `d6a55e39950de6cd40204f8c9ff581dd7b547308` (MCP `0.2.241-dev1`);
    this is released source, not installed or productive adoption.
    #12273 subsequently merged the #12272 standalone-Python portability fix
    at `60221d0d568282a347f702250b570a0ad205bdff`: real Linux exec succeeds
    with normal or omitted Python memfd/sealing bindings, retaining owner
    identity and mandatory seals. Native libc refusal, seal failure/readback
    and descriptor cleanup remain enforced. The isolated candidate helper also
    passes on the actual missing-binding interpreter. Main
    `2c7828b3cb61dd3df0d39c34b20edb999cd3a122` releases the exact correction
    as MCP `0.2.252-dev1`; an ordinary update delivered it without a package
    patch. The installed, contained Linux baseline now completes an actual
    native tool invocation through custody with one initialization, one
    invocation and verified owned cleanup. #12272 is closed. This is not a
    subsequent installed generation handover, production lifetime or default
    adoption result.
  - [ ] **Trace-sensitive checkpoint adoption:** qualify the private binding
    across physical replacement and rollback when launch diagnostics differ,
    without silently changing legacy identities. Conditional optional-receiver
    admission must preserve ordinary execution against unsupported running
    keepers before startup and require the selected mode through the complete
    attach path. This is separate from request-context negotiation and actual
    configuration consumption.
    Provider expansion accepts an explicitly selected trace-bookkeeping mode
    and advertises it from the actual backend; legacy custodians and bindings
    remain unchanged. #12222 merged the provider expansion at
    `938a59d60efc3f263245b241784d3c5e31869c01` after exact-head approval and
    required CI. Release and optional-launcher adoption remain required before
    removing that guard.
    #12224 merged conditional custodian selection at
    `8418036f427ca65b7977c4a598512cc418112f91` after current-head approval,
    required CI and trace-varying physical source qualification on Windows and
    Linux. This qualifies the internal connector/custodian path, not the
    optional launcher's old-running-custodian boundary or installed adoption.
    #12226 merged running-keeper enforcement at
    `912d19227b259d7f5902362d362f45df9b445c45` after current-head approval,
    required CI and mixed-version socket controls. The live keeper's ping
    declares enforcement separately from backend support, and its acknowledgment
    confirms the selected binding mode. Post-attach failures are terminal, not
    pre-effect fallback signals. Conditional optional-receiver selection now
    probes that running provider before startup, passes the strict requirement
    through cold/existing attach and independently checks backend support.
    #12233 merged that optional-receiver boundary and the tightly coupled
    native constructor comparison correction at
    `6ae73d5d3883312a2fe30c33dd53706f5e31618a` after current-head approval,
    required CI and Windows/Linux source qualification. Promoted release and
    installed adoption remain open; the source packets do not establish a
    joined installed-client handover.
    #12239 subsequently merged the
    [joined optional-receiver source packet](journal/2026/11.10-explicit-owner-cli.md#joined-optional-receiver-source-composition).
    Its controlled caller does not qualify actual Copilot handover or installed
    acceptance.
    #12242 now qualifies the joined actual offline Copilot protocol path through
    three private physical handovers and post-retirement exchanges. See the
    [native-client source result](journal/2026/11.10-explicit-owner-cli.md#actual-native-client-source-result).
    Installed adoption and production lifetime remain open; the source tool
    qualification follows below.
    #12247 qualifies four actual native tool executions and result consumption
    with a bounded loopback canned provider, initially and after all three
    predecessor exits. #12249 subsequently qualifies three accepted native
    tool calls while each predecessor is actually quiescing, followed by fresh
    incoming traffic. #12252 adds an accepted invocation whose real result
    transport fails during quiescence: custody remains uncertain/fenced, the
    affected conduit exits, the native client reports an error without replay,
    and outcome observers stay active through shutdown. This qualifies the
    bounded negative source case, not recovery of the lost result or continued
    use of that failed MCP session. Installed adoption and production lifetime
    remain open. See the
    [tool-consumption slice](journal/2026/11.10-explicit-owner-cli.md#actual-native-tool-consumption-prepared).
    Main `0bf2f14e6a035bf91ed441be02dc81c64f3b5432` releases MCP
    `0.2.250-dev1`: all 78 package tree entries match the qualified source,
    except the independently checked fallback-version stamp; the constructor
    correction also matches. This verifies released implementation bytes, not
    installation, the latest test-only PR's promotion or running acceptance.
  - [x] **Explicit-owner source merged:** #11935 landed at
    `c0eab0d952a37607f6cf7030ac1a77f377f98a74` after current-head automated
    approval, required CI and resolution of addressed findings. The source
    controls do not establish native caller-Job survival or installed adoption.
  - [x] **Released controller and native lifetime packet:** Main
    `6d99b59489c8c4a667e3713fab2a5f0c1345830c` advertises the reviewed packet.
    [Hosted run 42858317478](https://github.com/ThomasMichon/copilot-extensions/actions/runs/42858317478)
    qualifies source `c0eab0d952a37607f6cf7030ac1a77f377f98a74`: actual caller-Job
    closure, conduit survival, restrictive-outer refusal, interposed-parent exit
    and exact owner-death settlement pass. This is not ordinary or installed
    caller adoption.
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

- [x] **Deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925):**
  generic SDK observation design and qualification, independently of MCP.
  This transfers tracking; it does not claim that SDK work passed.
- [x] **WITHDRAWN, NOT PASSED:** MCP-specific observer enrollment, focus/window
  calibration, Terminal-host attribution and debugger qualification conditions.
  None is an MCP source, release or acceptance prerequisite.
- [ ] Run focused protocol and real-process regression controls on supported
  Windows and POSIX boundaries, plus practical clean-room install qualification.
  - [x] **Internal provider source packet:** 127 focused tests, 265 Windows
    tests with two existing platform skips, and both native Linux cases pass
    with verified cleanup. Physical replacement, rollback, same-slot process
    adoption and original pipes/upstreams are covered; production lifetime,
    default caller adoption and installed acceptance remain open.
- [ ] Update owning reality docs, contract declarations and per-plugin
  changefiles; land implementation through review and required CI.
- [ ] Verify promotion, then perform separately authorized installed-consumer
  acceptance. Record old-client preservation separately from actual reattachment.
- [ ] Resolve every plan and validation gate, or transfer an explicitly blocked
  legacy adoption item to its named tracked owner, before closing this effort.

### Withdrawn MCP observation conditions and independent SDK deferral

- [x] **Deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925):**
  generic SDK acquisition, native identity/coverage, source-bound admission and
  observer qualification. Its implementation and validation remain independently
  owned; this effort does not reproduce that plan or claim its completion.
- [x] **WITHDRAWN, NOT PASSED:** the application-specific generated-CMD
  desktop/window/focus, Terminal-host, calibration and debugger conditions.
  Functional two-cycle stdio/runtime-selection/exit and exact-owned cleanup
  remain applicable without any desktop-observation prerequisite.

Git and the append-only journal preserve the earlier proposal as history, not
as an active MCP contract. The unused calibration approval remains held; this
withdrawal authorizes no GUI effect or change to the SDK's own admission rules.

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
  enforced; do not require takeover of an unknown foreign listener. Verify that
  installation does not mutate live application DB/state, and that incoming
  boot establishes compatible access or an outgoing-owner handoff before state
  changes or migration, without forcibly closing live handles.
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
- [ ] Trace-varying physical keeper/backend generations retain compatible
  checkpoint identity through update and rollback, with no upstream
  reinitialization or replay. Constant-environment composed controls do not
  qualify this case; until qualified, optional adoption refuses before effects.
  - [x] **Internal conditional custody source:** two Windows cases and two
  Linux cases qualify trace-varying physical replacement, capable rollback
  and same-slot process replacement with original upstream state preserved.
  The optional receiver's old-running-custodian boundary subsequently passed
  mixed-version source controls through #12233. These separate source packets
  are not joined installed-consumer acceptance.
  - [x] **Joined optional receiver source:** #12239 qualifies the actual
    bootstrap and selected physical receiver with a controlled native Linux
    caller through replacement, rollback and same-slot replacement, retaining
    original caller/conduit identities and upstream state until EOF. Actual
    Copilot handover and installed acceptance remain open.
  - [x] **Joined actual native-client source:** #12242 qualifies actual offline
    Linux Copilot through the optional receiver across replacement, rollback
    and same-slot replacement, including post-retirement ping/reply traffic,
    original client/conduit/keeper/upstream native identities and exact cleanup.
    Contained shutdown is SIGTERM, not graceful upstream EOF. This does not
    qualify installed/default adoption, model-driven tool calls, accepted
    mutations or production caller lifetime.
  - [x] **Actual native tool consumption source:** #12247 adds four unique
    synthetic tool actions generated by a credential-free loopback provider.
    Actual Copilot consumes the real results and returns ACP completions across
    all three post-retirement handovers. Exact-source native execution and
    retained-identity cleanup pass; in-flight/unknown outcomes and installed
    adoption remain open.
  - [x] **Actual accepted/in-flight native source:** #12249 requires a
    copied-backend witness that actual quiescence has started with accepted
    work and settlers still pending before each held call is released.
    Seven actual tool results and three in-flight transitions pass on the
    corrected exact source, with single initialization and exact cleanup.
    This does not qualify unknown delivery outcomes or installed adoption.
  - [x] **Actual unknown native-delivery source:** #12252 qualifies one
    upstream-accepted call with lost result delivery during real quiescence.
    The real keeper records uncertain/fenced state with the accepted nonce;
    the native client reports an error, the affected conduit exits, and the
    invocation occurs exactly once through cleanup. Provider and ACP outcome
    observers remain active through bounded client shutdown. Exact EOF and one
    initialization are verified. This is terminal no-replay qualification,
    not result recovery, session resumability or installed acceptance.
- [ ] Failed candidate health, changed owner/route/selection and an old
  generation's late cleanup cannot corrupt or retire the current generation.
- [ ] EOF/disconnect and verified owner death settle exact client resources;
  an interposed parent exit does not destroy a still-owned live session.
  - [x] **Internal owner source controls:** 47 Windows tests with two skips and
    48 Linux tests with one hosted-native skip pass with verified cleanup,
    including actual owner-death exit while another process retains stdin.
  - [x] **Hosted explicit-owner lifetime:** the exact native case passed without
    skips in 71.816 seconds, within an 89.078-second verified packet. Original
    pipes/upstream survive caller-Job loss and physical backend replacement;
    owner death settles the conduit/upstream with stdin still held open.
    Ordinary caller adoption and installed-consumer acceptance remain open.
- [ ] Actual old central retirement and client-preserver survival are verified
  independently. No process-name sweep, whole-root reset or manufactured receipt.
- [ ] Held clients survive installer cutover beyond its grace, including the
  legacy forced-cutover/no-reap combination. Automatic `--force` preserves
  automatic inhibition and never authorizes state-holder termination.
- [ ] Actual consumer evidence distinguishes source qualification, released
  payload, selected runtime and the generation that handles a tool call.
- [x] **Backstop source controls:** default live-instance preservation, exact
  runtime scope, interleaved cursors and mixed/rolled-back helper refusal passed;
  this is not installed MCP constructor or session-handover acceptance.
- [x] **Deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925):**
  SDK fixture-scoped acquisition and self-profile compatibility validation;
  not an MCP result or prerequisite.
- [x] **Deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925):**
  SDK owned-window/focus, native-identity, descendant and host-attribution
  validation; no pass is claimed here.
- [x] **Deferred to [#10925](https://github.com/ThomasMichon/copilot-extensions/issues/10925):**
  SDK observation coverage, event delivery and observer-cleanup qualification;
  independently tracked, not passed by this effort.
- [x] **WITHDRAWN, NOT PASSED:** MCP-specific scoped desktop evidence,
  focus/window calibration, foreground restoration, Terminal-host attribution
  and debugger conditions. The actual generated-CMD functional two-cycle and
  owned-cleanup controls remain required without those conditions.

## Proposal

Prospective requirements and validation obligations only. The recommended
owner-composed design is specified in [handover-contract.md](handover-contract.md).
It separates stable connection custody from replaceable policy/decorator code;
retaining an entire old pipeline behind a new relay is not accepted as new
backend execution. This plan does not assert that reattachment or legacy live
migration already works.

## Journal

- [2026-11-10 - Explicit originating-owner CLI handoff](journal/2026/11.10-explicit-owner-cli.md)

### 2026-11-10 - Explicit originating-session lifetime prepared

- [Pinned owner boundary and pending native caller-Job qualification](journal/2026/11.10%20explicit%20session%20owner.md).

### 2026-11-09 - Retained reattachment source reconciled

- [Preserved source history and next implementation boundary](journal/2026/11.09%20retained%20reattachment%20source.md).

### 2026-11-09 - Incoming-monitor correction resumed

- Promotion run `42613130492` qualified the immutable candidate from Dev
  `2e575be1a26c789f1b5b1912df626165fb9c7158` and landed Main
  `48556f2680f5dbbdbdf5ada65867a5feb5d85de5`. Both corrected Worktrees
  files exactly match the reviewed source in that generated release snapshot.
- Authorized Worktrees-only Windows payload and runtime phases completed in
  41.48 and 81.49 seconds respectively. The new `1.25.53-dev1` monitor
  independently reports ready and published after installer exit; its native
  birth, route, physical runtime, manifest, current version, last-known-good
  selection and fresh command version agree. The imported correction bytes
  match the reviewed release.
- Outgoing recovery and retirement remained explicitly unconfirmed without
  vetoing the healthy incoming monitor. Installer-instance reconciliation also
  reported deferred native observation; no cleanup success or manual retirement
  is claimed. Both phase requests completed and no update remained active.
  Normal full-update performance and all MCP-wide publication, preservation and
  adopted-wrapper reattachment gates remain open.
- #11673 merged at `2e575be1a26c789f1b5b1912df626165fb9c7158`
  after genuine current-head Copilot approval, zero unresolved findings and
  green source and native CI. The live-lock and legacy-protocol installer
  cases passed on both Windows and Linux; absent/stale-lock and
  construct-then-reuse cases remain unqualified. The next stretch is formal
  release promotion and authorized installed acceptance, not whole-effort
  completion. No deployment, clean full-update performance or session
  reattachment is claimed by this source merge.
- Claimed #11524 as stubborn-latch, retaining the separate MCP handover owner.
- The source correction separates outgoing native observations from incoming
  election. It archives exact unresolved recovery bytes and retains prior code
  without recovery or termination; structural route CAS and actual incoming
  preparation, promotion and serving checks remain mandatory.
- Focused source qualification passed 39 installer, actual-cutover and
  retention cases, including unavailable outgoing birth, uncertain/corrupt
  recovery, incoming identity mismatches and archive I/O failure. Broader
  affected-file qualification, review, release and installed acceptance remain
  open; this is not a clean full-update or MCP-session-handover result.
- Final local contract qualification subsequently passed 345 tests across the
  installer, actual-cutover, retirement and cancellation files, with verified
  bounded-owner cleanup. Five cases from two expensive native installer
  scenarios were deselected and remain unqualified; the earlier all-file
  300-second timeout is not counted as a pass. Lint, install-contract,
  unchanged module-size and whitespace guards pass. Review, release, actual
  normal-update performance and installed consumer acceptance remain open.
- Review of #11673 identified six blocking edge cases. Regression-first
  corrections preserve previous-only startup with raw-active CAS, defer failed
  outgoing reuse probes, retain exact dev-release obligations even without a
  known outgoing slot, and use bounded atomic reference groups plus write-ahead
  archives that survive changed observations. Current local affected-file
  qualification passes 362 tests; the same five native cases remain
  unqualified. The shared ZDD cutover suite passes 49 cases, including
  unchanged legacy healing. Required CI, renewed review, release and installed
  acceptance remain open; no new deployment or whole-objective completion is
  claimed.
- The intervening old-head review also exposed malformed archive envelopes,
  unreadable breadcrumb cleanup and normalized legacy-route comparisons.
  Added regression-first corrections, including orphan references after loss
  of the archive directory. Final affected-file qualification passes 369
  tests with the same five native cases unqualified; the shared cutover and
  refusal files pass 64 tests. These are source results only, not current-head
  review acceptance, required CI, release or installed completion.
- Further review exposed retry ordering, prepared-claim reference repair, GC
  between inventory and fencing, and predecessor exit after qualification.
  Added direct installer retry and actual-GC/selection regressions. Write-ahead
  intent now precedes the deletion fence and inventory capture, interrupted
  inventory resumes under that fence, and archived references recover before
  ordinary retirement reservation and prepared-claim reconciliation. Qualified
  cleanup also preserves raw-active selection. The complete affected selection
  passes 376 tests with verified bounded-owner cleanup; the same five native
  cases remain unqualified. Review, required CI, release, normal full-update
  performance and session-preserving acceptance remain open.
- Current-head review confirmed those seven prior findings resolved, then
  identified multi-archive recovery clearing a shared fence too early and
  cleanup retry re-retaining already-collected slots. New failure-first
  controls cover GC between archive commits and deletion failure after
  reference release, with and without a dev claim. Recovery now publishes
  combined references under one fence, and terminal cleanup intent precedes
  their removal. CI also exposed stale generated ZDD admission copies and
  three exact cold-start controls: regenerated the copies and restored the
  explicit retired-predecessor native-exit checks. The combined affected and
  legacy-custody selection passes 487 tests, with the same five native cases
  still unqualified and bounded cleanup verified. The admitted source
  fingerprint is refreshed without changing nodes, source inventory or budget.
  Renewed current-head review and CI remain required; no installed or
  whole-effort acceptance is claimed.
- Source CI and the Linux native acceptance job then passed. Windows reached
  the later native transport controls and exposed six obsolete expectations
  of eager passive reaping. Updated only that assertion to require explicit
  deferred ownership and zero reaper effects; native readiness, publication,
  grace, refusal and rollback assertions remain intact. The exact Windows CI
  selection reproduced six failures, then passed all eight cases with verified
  cleanup under unchanged limits. The latest review reports no open findings
  but remains `COMMENTED`, not a current-head approval. Renewed CI and the
  separately required approval remain open.
- Added a failure-first case to the existing installer-custody controls:
  outgoing native-identity observation is unavailable, but the healthy incoming
  runtime must publish with retirement pending and both slots preserved.
  The current source fails this case before publication; bounded test cleanup
  was verified. No installed correction is claimed.
- Conditional route publication already separates the raw predecessor route
  from native identity of the incoming writer. The remaining implementation
  must preserve uncertain recovery records and conflicting-work protection,
  rather than merely suppressing the first outgoing-observation exception.

### 2026-11-09 - Nested-library consumer correction merged

- [Source milestone, validation scope and remaining delivery gates](journal/2026/11.09%20nested-library%20consumer%20correction.md).

### 2026-11-08 - Termination-free installer source merged

- [Source qualification, merge and remaining delivery gates](journal/2026/11.08%20termination-free%20installer.md).

### 2026-11-04 - Kickoff

- Claimed #10486, resolved the source repository's effort adoption and captured
  the operator's explicit reattachment selection.
- Started bounded read-only evidence on client state, activation and reusable
  session/lifecycle primitives. No implementation or live migration performed.
- Source evidence found no existing generic MCP-session migration broker.
  Added a prospective quiescent-handover contract and explicit multi-client
  process-count validation in response to review.

### 2026-11-04 - Implementation admission

- Plan PR #10600 merged to `dev` with current-head automated approval and required
  checks. Phase 1 records the reviewed ownership and protocol design, not an
  implementation witness.
- lopsided-gasket takes the exclusive implementation role. Stable connection
  custody will own transport state; incoming backend objects must own subsequent
  pipeline execution. Live coroutines cannot be serialized, so accepted calls
  finish under their existing epoch before a data-only checkpoint.
- Publication, promotion, authorized installed acceptance and the pre-feature
  legacy adoption hold remain coordinator-owned completion gates.

### 2026-11-04 - Source implementation milestone

- Implemented the MCP-owned shared raw-connection keeper and independently
  replaceable execution attachments. Incoming code constructs new session,
  decorator and routing objects from versioned JSON data; no old pipeline is
  retained behind a new relay. Local CLI responders remain current-backend
  execution rather than keeper-owned policy.
- Preserved explicit portable interpreter-binding provenance across runtime
  slots, without treating literal interpreter-path changes as equivalent.
  Routed target credentials and negotiated state remain separately bound.
- Integrated guarded incoming publication on both native installers through
  actual nested construction slots. Publication and command/deployment state do
  not depend on old-process census or reset-control. The current IPC namespace
  is distinct from pre-feature listeners, including configured socket bases.
- Protected MCP conduits, keepers, foreground calls, central session hosts and
  descendants from generic version retirement. Installer census is diagnostic
  check-only; central generations retire through their own drained owner.
  Automatic slot collection is
  held: generic best-effort PID discovery cannot establish that launch-owned
  code is no longer needed for lazy imports. This deliberately retains old code
  on disk; qualified idle collection remains an explicit maintenance gate.
- Real Windows source-process cases cover a pending mutation, simultaneous
  fresh admission, startup and transition notifications, a server request,
  cancellation, unchanged upstream PID/one initialization, new pipeline code,
  old central exit, compatible provider rollback, backend-loss uncertainty
  without replay and verified conduit-death cleanup. One-client and multi-client
  heavy-process counts remain installation/generation bounded.
- The final execution/configuration/transport packet passed **317 tests** with
  **11 platform skips**. The native construction/retirement/home packet passed
  **250 tests**, with **24 skips** and **one explicitly deselected qualification
  case** after its direct failure. Every bounded runner reported verified owned
  process cleanup. No production operation or publication was performed.
- The final slot-retention adjustment was separately checked through the native
  PowerShell warning/encoding boundary: both supported host cases pass while a
  fixture collection tripwire remains untouched.
- That unresolved case is
  `test_reap_versions.py::test_real_tree_exit_spares_original_parent`: native
  ownership census reports **Cannot attribute candidate process ownership**.
  Its proof was not weakened or marked xfail. This is a coordinator-owned
  qualification blocker under #10486, not an accepted clean native-retirement
  result.
- Independent source review, Linux/macOS qualification, complete native
  HTTP/routed-target witnesses, hardened clean-room installation, installed
  literal-command/Copilot acceptance, launch/performance qualification, release,
  rollout and legacy adoption remain open. The process witnesses use distinct
  source generations through the normal module entry, not a released installed
  global command. The source milestone does not close this effort.

### 2026-11-04 - Bounded supervision checkpoint

- lopsided-gasket pauses at the coordinator's review boundary. The source
  candidate is preserved locally, uncommitted and unpublished. No owned test
  command remains running; completed bounded runs reported verified cleanup.
- The latest installer policy is diagnostic-only census and retained predecessor
  slots. Its final deltas require metadata refresh and bounded revalidation
  before a commit can be treated as qualified.
- One source gap remains explicit: fresh attachment still needs physical
  generation admission when optional global configuration discovery skips
  activation, or an activation opt-out leaves a capable old backend advertised.
  Capability support alone must not select that old execution graph for a fresh
  current caller. No generation-stamp-only test may substitute for execution proof.
- Native fixture isolation needs owner review before further helper/test runs:
  the runner redirects standard profile/state variables, but does not scrub every
  manager-root override. Intended redirection and process cleanup are not proof
  that every independently resolved resource stayed private. No causal ownership
  conclusion follows from timing alone.
- Resume with the same implementation actor after coordinator review. Preserve
  the full reattachment contract, source exclusivity, native-census qualification
  blocker, retained-slot hold and all release/consumer/legacy adoption gates.

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
- #10660 merged with automated approval and required checks at
  `0fecf29e819de94c3adea8c731be6d9ce762b383`. Reconciled that canonical amendment
  with the implementation journal without discarding prior progress.

### 2026-11-05 - Private incoming-publication qualification

- Committed the coupled canonical test-environment isolation prerequisite:
  preparation children and native helpers receive private explicit state roots,
  including inherited manager-root overrides, without relocating supervisor
  custody. Three poisoned-parent-root controls passed.
- Preserved the larger handover candidate separately while assembling the first
  incoming-only slice. Its historical packets above are not qualification of
  this slice or evidence that the independent-review findings are resolved.
- The ordinary Windows native installer witness passed with two genuine
  installations of different Pipeline code but unchanged package versions.
  A fresh literal PATH command executes incoming code in a different physical
  slot; the held old client retains its upstream PID and one initialization.
  No manually published endpoint or fabricated installed-slot layout is used.
  Retirement tripwires remain untouched and the old slot remains available.
- This proves incoming publication and existing-client preservation only.
  Adopted-wrapper reattachment, caller-identity and flow-control review fixes,
  complete platform/native qualification, release, rollout and legacy adoption
  remain open. No production operation or publication was performed.
- The incoming integration/native-construction packet passed **299 tests**
  with **30 platform skips**, including both retained-slot controls. Final
  server/admission/isolation controls passed **49 tests** with **one skip**.
  The ordinary installer witness passed again against the final production
  source; one warm-pool fixture still using the legacy namespace was corrected
  and its complete server packet rerun. Every runner verified owned cleanup.
- The Windows fresh literal bridge observation was approximately **0.35 seconds**
  with the incoming shared backend already ready. This is one native observation,
  not a cross-platform performance qualification. No new per-client daemon or
  synchronous helper is added to an already-current attachment.
- Refreshed declarations through their canonical owners, passed the install
  contract and touched-Python `F`/`E9` checks, and extracted incoming ping
  metadata to keep the resident server within its source-module cap.
  Coordinator review/publication remains the next gate. The managed-application
  owner in #10054 must reconcile the current-handle namespace interface before
  publication; its construction, rollback and reuse ownership is not superseded.

### 2026-11-05 - Incoming caller-authority and cold-admission corrections

- Independent incoming-slice inspection found that ready backend generation
  did not bind caller authority, and optional acceleration failure could fall
  through to a physically unselected cold interpreter.
- Added private caller-derived configuration/environment/working-directory
  equivalence before installed shared execution. Unsupported or different
  contexts use current direct execution without rewriting backend environment.
  Added typed physical-selection refusal across cold bridge/call/stub/catalog
  execution and explicit unknown-outcome handling that forbids mutation replay.
- The focused caller-context, selection, no-replay, server and isolation packet
  passed **78 tests**, with **one platform skip** and verified owned cleanup.
  The final complete incoming native packet passed **82 tests**, with **one
  platform skip**. Both ordinary-installer variants prove actual caller-B
  bridge/tool output against installer/backend-A context, and paused old
  interpreters refuse warm, unavailable-accelerator fallback and no-serve calls
  without stale upstream mutations. The source materialized-stub guard and
  no-credential-transfer controls are included in that same green packet.
- The companion constructor/application/retention packet passed **264 tests**
  with **30 platform skips**, including both retained-slot controls. Every
  runner verified owned cleanup. A larger combined selection exceeded the
  existing 300-second group budget; the two complete packets were separated
  without raising limits. A new stub fixture's missing explicit JSON input was
  corrected before the final complete native packet was rerun successfully.
- Production source did not change between the two final green packets.
  Independent review, namespace coordination and publication remain
  coordinator-owned gates; full handover, native census and other-platform
  qualification remain open.
- No managed-application adapter or namespace revision is part of these fixes.
  The existing startup transaction and guards remain owned by #10054; its
  inactive first touch, explicit incumbent, reuse, independent roots/handles,
  failed-publication retry, reverse replacement, missing-route and occupied
  endpoint matrix is not relabeled qualified for this namespace.

### 2026-11-05 - Installed namespace contract reconciliation

- Reproduced a concrete configured-handle regression: prepared `source_setup`
  observed a sibling namespace rather than the explicitly configured incumbent
  and incorrectly recorded `wanted=false`. The focused source test failed
  before correction.
- A real private ordinary-install fixture separately reproduced an inactive
  configured catalog auto-starting a resident before first touch. This violated
  the application's retained on-demand contract; the native case failed before
  the publisher correction.
- Committed `12c3ec7cc705206d7036cad468672035e8f08a8b`: configured handles remain
  exact, separate default legacy state supplies intent without adoption, and
  catalog readiness alone does not start an inactive resident.
- Against that exact production source, both ordinary native variants passed
  (**2 tests**) and the final callback/handle/authority packet passed
  **143 tests**, with **14 platform skips**. All owned cleanup was verified;
  source lint, install contract and module caps passed.
- Authentic prepared-application qualification is blocked before MCP execution:
  the existing governed builder refused because the private fixture has no
  affirmatively trusted governed package feed. No trust entry was added and no
  public-toolchain exception or installed global runtime was substituted.
  The opt-in preparation prerequisite requires an explicitly pinned approved
  commit and retains this real refusal rather than manufacturing wheel inputs,
  completion receipts or an application layout.
- The complete installed legacy/prepared application matrix remains open.
  Resume with owner-supplied admitted prepared inputs or an approved governed
  build context, then exercise the actual adapters, both replacement directions,
  reuse, failed-publication recovery and mixed consumers. The prior 15-phase
  report is not relabeled as qualification of this namespace.

### 2026-11-05 - Ordinary installer scope and exact-handle intent

- The operator separated the publishable ordinary-installer correction from
  the still-open prepared application matrix, now tracked by #10742 under
  #7028. The missing governed build context is a test-environment input refusal
  for that separate qualification, not an ordinary-installer release veto.
  Actual ordinary incoming authentication, build, health and admission failures
  remain completion gates. Neither #10742 nor #10486 is closed by this slice.
- Reproduced the exact configured-sidecar regression before correction:
  shared serving intent was false, but the publisher's duplicate file scan
  treated the same current endpoint as legacy intent and attempted startup.
- Removed that duplicate scan. The publisher now uses the existing shared
  serving-intent owner, including its exclusion of the current handle from
  separate legacy hints. Exact configured handles, explicit socket semantics,
  the default current namespace and the caller/physical-admission guards remain.
- The focused intent/callback/authority packet passed **78 tests**, with
  **one platform skip** and verified cleanup.
- Final ordinary native qualification passed both complete variants in two
  disjoint bounded selections (**one pass each**); the complete callback,
  exact-handle, authority and isolation packet passed **185 tests** with
  **15 platform skips**. All runners verified owned cleanup. A combined
  selection exceeded the unchanged 300-second group limit and is not credited
  as a pass; no timing or custody limit was raised.
- The configured native variant verifies that the dead exact sidecar remains
  untouched and no resident activates, then continues its existing default-
  namespace generation witness. This is not configured-handle migration or
  prepared-matrix qualification. Production source was unchanged between all
  final green selections; only this journal result followed them.
- Coordinator review/reconciliation/publication remain the next ordinary-slice
  gates. Prepared qualification stays unqualified under #10742/#7028, and full
  adopted-session reattachment and whole-effort completion stay open.

### 2026-11-05 - Reconcile the tested ordinary slice onto current Dev

- Replayed the owned commits onto Dev
  `5320b55f0dc6a10d8eab7eeab99cf2903a45cb60`, preserving the merged #10660
  architecture, every implementation journal entry, the ordinary-slice approval
  and separate unqualified #10742 scope. No source prototype stash was applied.
- Resolved the isolation scrub-list conflict by retaining both upstream test-
  venv isolation and the installation/context scrubbing. Regenerated only
  derived footprint hashes from the combined source through their canonical
  owner; no contract recipe, upstream bootstrap code or release version changed.
- The combined runner exposed a real API integration failure before pytest:
  upstream makes the cache-root override optional, while the preparation wrapper
  still assumed it was a path. The bounded job failed before correction.
  Preparation now uses the owning root resolver and scratch owner, and native
  fixtures use the runner's explicit retained-cache input instead of a checkout-
  local cache. The focused isolation/admission packet passed **19 tests** with
  verified cleanup.
- A single guarded managed checkpoint sync succeeded on the same Dev pin;
  no additional base chasing or source-host adoption occurred. Both complete
  ordinary native variants then passed (**one test each**), and the full
  callback/authority packet passed **185 tests**, with **15 platform skips**.
- The companion constructor packet exposed two test assertions still expecting
  a sibling namespace for an explicit configured handle. Corrected only that
  expectation to the approved exact-handle contract; both shell adapters passed
  the focused four-case control and the entire constructor/retention packet
  passed **266 tests**, with **30 platform skips**. All owned cleanup was verified.
- All final packets used the same reconciled production source. The final
  constructor expectation and this journal are test/documentation-only changes;
  they do not change the qualified runtime. Prepared qualification under #10742,
  full handover under #10486, release and installed acceptance remain open.

### 2026-11-05 - PR source admission and runtime review corrections

- PR #10754 exposed stale unit source admission for the changed isolation
  helper and a dependency-audit integration gap after preparation moved into
  its invoked helper. Extended only the bounded actual preparation route;
  missing, repointed, uninvoked and changed source evidence still revokes
  attribution. The original parser reported **548 declarations/5 violations**;
  the corrected complete live guard reports **548/0**, retaining the seven-day
  policy. The full source-attribution suite passed **159 tests**, with **4 skips**.
- Rebound only the two changed admitted source entries through the existing
  protected source reader and admission contract. Membership remains **4146
  nodes**, **595 sources**, unchanged coverage targets and the **300-second**
  production packet budget. Existing environment, collector and PR-reuse
  contract controls passed **243 tests**; no actor/process suites were admitted.
- Corrected non-object/invalid warm-call replies to unknown outcomes without
  cold replay; preserved data-home resolution independently of installation
  roots; threaded exact requested handles through the installed startup owner;
  and retained configuration home in manual cutover. Owner-declared process
  bookkeeping is separated from caller authority, explicit references remain
  significant, and private control tokens are not accidentally inherited.
- The final Windows native variants each passed, proving exact override startup
  without a default resident, independent default data-home failure, compatible
  installed forwarding/pool reuse, distinct caller credentials and no stale
  mutation replay. The complete callback/transport packet passed **211 tests**
  with **15 skips**; constructor controls passed **267 tests** with **30 skips**.
  Owned cleanup was verified. Temporary private diagnostic instrumentation was
  removed before final qualification.
- Complete unit qualification is not claimed on Windows: the unchanged packet
  reached an existing symlink control requiring unavailable native privilege
  and had platform skips. No elevation, skipped-node waiver or admission
  weakening was used. The parent must run the entire unchanged packet in its
  supported Linux venue or CI. The corrected real POSIX cutover/configuration
  fixture likewise remains pending that venue; Windows skips are not proof.
- #10742 prepared qualification and #10486 full handover remain open. No public
  push, review-thread mutation, production rollout or source-host adoption was
  performed by this implementation slice.

### 2026-11-05 - Complete Linux source qualification

- The coordinator ran the unchanged complete unit packet on Linux: **4146
  admitted nodes** passed, with **595 source bindings**, original coverage
  targets and the **300-second** whole-packet budget retained. Execution,
  coverage and packet verification completed in **181.575 seconds**; the
  bounded supervisor verified owned cleanup. The failed Windows attempt is not
  credited as a pass.
- The corrected real POSIX cutover fixture passed, verifying fixed-handle
  routing, actual predecessor exit and lookup of the original configuration
  home. Its initial fixture address exceeded the native Unix-socket length
  limit before startup; an exact bind probe confirmed that cause. The existing
  canonical short scratch root resolved the venue failure without a source
  change, timeout increase or custody waiver. Both reports were retained.
- Independent correction review found no additional High/Medium issues.
  Source qualification does not satisfy the still-pending current-head review,
  required CI, promotion or authorized installed-consumer acceptance gates.
  Prepared application qualification under #10742 and full adopted-session
  handover under #10486 remain open.

### 2026-11-05 - Post-publication Dev reconciliation

- Reconciled the actual PR conflict with current Dev. Both conflicts concerned
  producer digests only; the non-derived footprint contracts were identical.
  The canonical footprint owner regenerated digests over the combined source,
  retaining the updated upstream daemon asset and contract revision.
- Preserved all earlier journals, six review corrections and current upstream
  unit membership, coverage targets and budget. The combined manifest admits
  **4145 nodes**, **595 sources** and **300 seconds**; only the two previously
  corrected source bindings differ from Dev. Protected source verification
  passed. The earlier complete Linux packet describes its original source pin,
  not qualification of this new integrated pin.
- Both ordinary native installation variants passed. The callback/client packet
  passed **211 tests** with **15 skips**, and focused unit-admission/dependency
  controls passed **402 tests** with **4 skips**. Owned cleanup was verified.
  A missing coverage collector caused an initial collection failure; restoring
  it through the existing CI tooling owner resolved that test-environment issue.
- Install-contract, F/E9 lint, module-cap and whitespace checks passed. The
  complete dependency guard admitted **549 declarations/0 violations**, with
  the seven-day policy unchanged. No new MCP production behavior was authored.
  Current-head publication, review, CI and complete Linux qualification remain
  coordinator-owned; #10742 and full handover #10486 remain open.

### 2026-11-05 - Reconciled Linux packet qualification

- The coordinator qualified the complete reconciled Linux packet: all **4145
  admitted nodes** passed, retaining **595 sources**, coverage targets and the
  **300-second** budget. Execution, coverage and source/packet verification
  completed in **181.116 seconds**, with verified owned cleanup.
- MCP code/tests are unchanged from the previously qualified correction;
  the native installer and real POSIX cutover witnesses remain applicable.
  Publication uses the owning private PR's documented exact-head-leased
  intentional-rebase flow. Current-head review, required CI, promotion and
  authorized installed-consumer acceptance still remain open, alongside
  #10742 and full handover #10486.

### 2026-11-05 - Further review corrections and containment qualification hold

- Contradictory warm-call success/refusal and result-bearing refusal replies now
  retain unknown outcomes without cold mutation replay. Actual-wire controls
  preserve one warm write and zero cold executions; ordinary pre-effect context
  refusal still permits direct execution.
- Native readiness, reuse and guarded publication qualify the authenticated
  process's installation root as well as native birth, physical generation and
  exact data ownership. Both ordinary native variants passed. The discovered
  variant constructed the same physical version in a second installation,
  refused publication and preserved the first route and held upstream.
  The complete callback/client packet passed **225 tests**, with **15 skips**.
- Dependency-source attribution rejects local helper shadowing and module-level
  callee replacement while retaining legacy direct preparation. Preparation
  retains UV configuration through the existing dependency owner's reader,
  precedence merge and private writer before detaching user/service roots.
  Explicit reinstall controls validate the private environment and original
  install arguments. Native discovery controls now follow the owning inventory
  while retaining exact selection and the unchanged 25-file grouping cap.
- Final dependency controls passed **170 tests/4 skips**, runner controls
  **196 tests/3 skips**, and unit-admission/collector controls **243 tests**.
  The combined packet is **not green**: containment had **88 passes/2 skips/1
  failure**, with immediate file deletion reporting WinError 32 after contained
  return. The exact single retry reproduced that failure. Both supervisors
  verified final cleanup; this does not prove the inner return boundary.
  No containment policy change, ad hoc process kill, assertion waiver or flake
  classification was substituted. That owner-boundary diagnosis remains open.
- Only the two changed unit source bindings were refreshed through the protected
  reader and admission contract; **4145 nodes**, **595 sources**, coverage targets
  and the **300-second** budget remain intact. Install contract, F/E9 lint,
  module caps and whitespace passed; the dependency guard remains **549/0**.
  The manual cutover function is unchanged, including its configuration-home
  argument. Earlier Linux qualification describes earlier pins; current complete
  qualification and review remain coordinator-owned. #10742 and #10486 stay open.

### 2026-11-05 - Native Job exit qualification hold resolved

- Targeted native evidence identified an owned file-holding descendant in the
  inner Job. Immediately after Job termination, accounting reported zero while
  its native process handle remained unsignalled and delete-access-open still
  reported WinError 32. Assigned exit status was not actual native exit.
  No external holder was attributed and no flake waiver was used.
- Windows containment now reuses procutil's existing stable-member pin/native
  wait barrier within the original teardown budget. Unconfirmed native exit,
  unclosed handles or retained witnesses preserve the same Job owner on the
  error; successful settlement flags require completed retirement. No new
  controller, timing allowance, file-deletion retry or production remedy was
  introduced.
- Both original failed reports and fixture files were preserved. The unchanged
  file-holder and cancellation cases passed after the source correction.
  Final validation passed **841 tests/9 skips**, including **92 containment
  tests/2 skips**, **170 dependency tests/4 skips**, **196 runner tests/3 skips**,
  **243 admission controls** and **140 native qualification/SDK controls**.
  Owned cleanup was verified; the former combined-packet hold is resolved.
- Only the containment source binding changed in the admitted unit manifest.
  **4145 nodes**, **595 sources**, coverage targets and the **300-second** budget
  remain unchanged. Module caps, F/E9 lint, install contract, whitespace and
  dependency guard **549/0** passed. The prior five corrections are preserved;
  current full Linux qualification, review and publication remain coordinator
  gates. Prepared qualification #10742 and full handover #10486 remain open.

### 2026-11-05 - Pure-unit native-retirement fixture integration

- Complete Linux qualification exposed a borrowed owner fixture still mocking
  the retired kernel/count boundary. Its real retirement import attempted an
  ineligible dynamic-library effect inside the pure-unit packet.
- Updated only that fixture to fake the current native-retirement boundary,
  asserting native witness closure, the unchanged five-second budget and forced
  nonpositive qualification. Production teardown and unit effect policy remain
  unchanged. Focused admitted-owner, contract and PR-source controls passed
  **149 tests**, with verified cleanup.
- Refreshed only the borrowed fixture's admitted source binding; **4145 nodes**,
  **595 sources**, coverage targets and the **300-second** budget remain intact.
  Current complete Linux qualification and publication remain coordinator gates;
  #10742 and #10486 remain open.

### 2026-11-05 - Canonical generated Windows runtime resolution

- CI's strict runtime-resolution guard exposed a generated CMD interpreter
  shortcut. Removed both hand-built slot paths; every launch now uses the
  co-deployed canonical marker resolver noninteractively in read-only mode.
  Environment-based path handling preserves quoted profile paths without
  weakening completion/reparse checks or adding guard annotations.
- A mismatched completion marker executed before the correction and now refuses
  before module execution. Two focused generated-launch controls passed,
  preserving selected nested environment, literal arguments and stdin.
  Both required ordinary native installer variants passed on final regenerated
  source, preserving the original held-client PID/single initialization and
  fresh physical-code witnesses.
- Regenerated only MCP's human-output declaration and producer footprint through
  their existing owners. Strict runtime-resolution, install-contract, F/E9,
  module-cap and whitespace checks passed. Protected verification found no
  changed unit source bindings; **4145 nodes**, **595 sources**, coverage targets
  and the **300-second** budget remain unchanged.
- No complete Linux packet was rerun or relabeled; the coordinator's result
  remains bound to its original source pin. Review, publication and rollout
  remain coordinator gates. Prepared qualification #10742 and full handover
  #10486 remain open.

### 2026-11-05 - Final source qualification and reaper documentation correction

- The coordinator qualified the complete Linux packet after the canonical CMD
  correction: **4145 admitted nodes** passed, with **595 source bindings**,
  unchanged coverage targets and the **300-second** budget. Execution,
  coverage and packet verification completed in **259.815 seconds**, with
  verified owned cleanup. Both final ordinary Windows installer variants and
  the two marker/stdin controls passed; the previously failing strict
  runtime-resolution guard was independently rerun successfully.
- Corrected the remaining review finding in the README and incoming-publication
  documentation: the environment variable is a legacy installer opt-out.
  It does not inhibit the explicit reaper; non-terminating inspection requires
  `--check-only`. This correction changes no executable or test inputs.
- Current-head review, required CI, promotion and authorized installed-consumer
  acceptance remain open. Prepared qualification #10742 and full adopted-session
  handover #10486 remain separate required work, not completed by this packet.

### 2026-11-05 - Current Dev reconciliation and bounded branch reachability

- Reconciled the published change with current Dev through the owned PR flow.
  Four conflicts concerned producer digests only; non-derived footprint
  contracts were identical and the canonical owner regenerated combined-source
  digests. Upstream attached-session drain protection, executing-writer changes,
  primitive caching, documentation and all prior journals were retained.
- The review overview had a concrete source-attribution defect: traversal could
  continue after a selected terminating branch and credit an uncalled helper.
  Six controls failed before the correction. The bounded existing traversal now
  propagates return/raise termination through recognized `if`/`with` blocks,
  while retaining continuation for a nonmatching or partially terminating
  branch. No general control-flow rewrite or dependency exemption was added.
- Dependency controls passed **178 tests/4 skips** and the complete dependency
  audit remains **549/0**. Integrated MCP callback, configuration and payload
  controls passed **276 tests/22 skips**. Existing private-environment/reinstall
  and PR-source controls passed **83 tests**; all supervisors verified cleanup.
  Strict runtime-resolution, install contract, F/E9, module caps and whitespace
  passed.
- The two carried findings remain corrected: private preparation validates its
  environment without discarding install arguments, and same-version foreign
  installation ownership refuses before reuse or publication. Those
  implementations were not changed to address stale comments.
- Current Dev's **4152 nodes**, **595 sources**, coverage targets and original
  **300-second** unit budget are preserved, including newly admitted upstream
  controls. Only the changed dependency-audit binding was refreshed after this
  correction. Complete Linux qualification, provider review and publication
  remain coordinator gates; #10742 and full handover #10486 remain open.

### 2026-11-05 - Exact Dev metadata reconciliation and CI qualification gate

- Reconciled the published branch onto the approved current Dev pin. Replay
  conflicts concerned only producer digests; non-derived footprint contracts
  remained identical and canonical regeneration retained all upstream and
  authored source. Upstream nested-peer environment validation was preserved.
  Minimal existing frontdoor/configuration controls passed **39 tests/7 skips**,
  with verified cleanup. Strict runtime-resolution, install contract, module
  caps and whitespace passed.
- At published source `bc9b65d90991cd4342e3dcb7541e215c284cd934`, both local Linux
  attempts **failed whole-packet qualification**, despite all **4152 assertions**
  passing in **182.92** and **192.09 seconds**. The first reached final
  verification at **272.330 seconds**; final verification exceeded the original
  **300-second** whole bound. Both supervisors verified cleanup, not successful
  packet qualification.
- The **29.906-second** profile, including **20.269 seconds** of source-path/hash
  checks on the mounted filesystem, is diagnostic only. It is not a full pass.
  No path checks, coverage, nodes, profile or deadlines were weakened; no
  600-second native profile was substituted.
- Both alternative native-venue requests refused before allocation; no resource
  was created, blanket fallback enabled or adoption policy changed. Public
  #10827 tracks the routed allocation's project-binstub assumption.
- Required native Linux CI must qualify the complete current **4152-node**,
  **595-source**, **300-second** packet before merge. Earlier qualified source
  pins remain distinct. Current review, publication, CI, merge and rollout
  remain coordinator gates; #10742 and full handover #10486 remain open.

### 2026-11-05 - Windows reference casing and matched-branch reachability

- Current review exposed two concrete defects. On Windows, a mixed-case explicit
  environment reference could resolve a real credential while omitting it from
  the sharing digest. Reference matching now follows case-insensitive Windows
  lookup only; POSIX matching remains case-sensitive. Explicit references remain
  significant and unreferenced control bookkeeping remains excluded.
- The matched dependency branch still scanned its body directly after outer
  reachability analysis. It now uses the same bounded traversal inside that
  branch. Early return/raise cannot credit a later local source; a partially
  terminating branch can retain a genuinely reachable source.
- Before-fix controls reproduced two casing failures and three unreachable-source
  failures. Final context/no-replay controls passed **29 tests**; dependency
  controls passed **182 tests/4 skips**, with the complete dependency guard at
  **549/0**. Both ordinary native Windows variants passed, retaining positive
  installed pooling, credential isolation, held clients and fresh physical-code
  execution. All supervisors verified cleanup.
- Canonical footprint regeneration and the single changed dependency-audit unit
  binding preserve **4152 nodes**, **595 sources**, coverage targets and the
  **300-second** budget. F/E9, strict runtime-resolution, install contract,
  module caps and whitespace passed. No rebase, local complete-packet rerun,
  publication, production effect or qualification waiver was introduced.
- Required native Linux CI still gates merge; local whole-budget failures and
  diagnostic-only profiling remain disclosed separately. Review, release and
  installed-consumer acceptance remain coordinator-owned. #10742 and full
  handover #10486 remain open.

### 2026-11-05 - Expired Job termination and caller-unset default home

- Current review exposed a termination omission after whole-bound expiry.
  Expired close now requests termination through the exact owned original or
  retained Job handle before reporting unconfirmed retirement. It does not
  begin another verification interval, wait beyond the deadline, discard
  witnesses or publish positive settlement flags. Normal native teardown
  retains its existing five-second bound.
- An unset or empty unreferenced home now compares as the canonical resolved
  default added during daemon startup. Nonempty overrides and explicit
  configuration references remain significant; no credential key or custom
  configuration home is blindly removed.
- Four controls failed before these fixes. Final context/no-replay controls
  passed **31 tests**; expired-owner, native file-holder and cancellation
  controls passed **13 tests**, with **149 pure owner/PR-source controls**
  passing. Both ordinary native variants now run with caller `AGENT_MCP_HOME`
  unset under private default profile roots, including the configured catalog.
  Both passed actual pool reuse, credential isolation, held-client continuity
  and fresh physical-code witnesses. All supervisors verified cleanup.
- Canonical footprint regeneration and the single changed containment unit
  binding retain **4152 nodes**, **595 sources**, coverage targets and the
  **300-second** budget. F/E9, strict runtime-resolution, install contract,
  module caps, whitespace and dependency guard **549/0** passed.
- No local whole-packet rerun, rebase, publication, production effect or budget
  waiver was used. Mandatory native Linux CI, current review, release and
  installed-consumer acceptance remain coordinator gates. #10742 and full
  handover #10486 remain open.

### 2026-11-05 - Source-identical history collapse and single-conflict rebase

- Preserved the published source and raw Git tree before collapsing only the
  owned private PR history against its original merge-base. The collapsed
  commit retained the identical tree. Rebase onto the approved current Dev pin
  required one producer-digest resolution; the non-derived footprint contract
  remained identical and canonical regeneration retained all source.
- Authored runtime/parser implementations are unchanged. Upstream installer,
  Source parsing and lease/interpreter changes were preserved; minimal existing
  controls passed **146 tests/3 skips**, with verified cleanup. Strict runtime
  resolution, install contract, F/E9, module caps and whitespace passed.
  The current dependency guard reports **552 declarations/0 violations**.
- Current Dev's **4160 nodes**, **596 sources**, coverage targets and original
  **300-second** unit budget are preserved. No new local whole-packet run or
  qualification claim was made. Required native Linux CI and current-head
  review still gate merge; prior-head green checks are not new-head proof.
  Publication, release and consumer acceptance remain coordinator-owned;
  #10742 and full handover #10486 remain open.

### 2026-11-05 - Preliminary-accounting cleanup and visibility consent gate

- Two controls reproduced preliminary accounting failure skipping owned cleanup
  before and after expiry. Unavailable accounting now marks the attempt unclean
  but continues exact owned retirement or the expired termination request under
  the original bound. It cannot publish positive settlement flags; unconfirmed
  native witnesses remain with the same Job owner. Canonical SDK accounting
  structure reuse removes the duplicate native structure without changing limits.
- Final containment and pure-owner controls passed **246 tests/2 skips**, with
  verified cleanup. Strict runtime-resolution, install contract, F/E9, module
  caps, whitespace and dependency guard **552/0** passed. Only the changed
  containment unit binding was refreshed; **4160 nodes**, **596 sources**,
  coverage targets and the **300-second** budget remain unchanged.
- Actual generated-CMD two-cycle visibility qualification remains open. The
  qualified SDK observer requires explicit consent for one owned visible
  calibration control, foreground activation/restoration and retirement.
  No such control or observer was launched without that consent. Its existing
  SDK case roster was not widened or relabeled as consumer qualification.
- The previous argument/stdin and hidden-helper tests are not claimed as that
  visibility proof. Current native Linux CI, review, visibility qualification,
  release and consumer acceptance remain coordinator gates; #10742 and full
  handover #10486 remain open.

### 2026-11-06 - MCP functional qualification independent of desktop focus

- The operator clarified that MCP acceptance must not depend on desktop quiet,
  focus/window observers, terminal-host attribution, calibration or debugger
  attachment. This supersedes the preceding journal's MCP visibility gate;
  separate SDK visibility work under #10925 is not MCP qualification. No visible
  calibration or focus observer was executed, and the unused approval remains
  held. The canonical headless implementation is unchanged; no desktop-visibility
  proof is claimed.
- Extended the existing actual generated-CMD control to execute the completed
  slot's canonical resolver twice with distinct stdin and quoted arguments.
  Selected physical interpreter, stdout/stderr and zero/nonzero exit propagation
  are checked on each invocation. The canonical caller-owned native executor
  settles each owned process tree within the original 20-second bound, including
  its five-second cleanup reserve. The invalid-marker control still refuses
  before module execution. Both explicitly selected native cases passed with
  verified outer cleanup; no native actor entered promotion-unit inventory.
- The initial preparation attempt failed before tests when the running cached
  interpreter prevented its own environment rebuild. The independent existing
  bootstrap then completed canonical preparation and the functional packet.
  The failed attempt is retained as a failure, not qualification.
- F/E9, strict runtime-resolution, headless source guard, install contract, module
  caps, documentation consistency, whitespace and dependency guard **552/0**
  passed. Canonical source verification retains **4160 nodes**, **596 sources**,
  coverage targets and the
  original **300-second** unit budget. The MCP footprint remains revision **36**
  with unchanged producer digests; no source binding needed refresh.
- Current-head native Linux CI, review, publication, release and installed
  consumer acceptance remain coordinator-owned. No local whole-packet repeat,
  runtime rollout or handover claim was made. The prior preliminary-accounting
  cleanup fix and its **246 tests/2 skips** qualification are preserved;
  #10742, full handover #10486 and pre-feature adoption holds remain open.

### 2026-11-06 - Caller-root contract reconciliation and unmasked native controls

- Rebased the actual conflicting PR onto merged caller-context correction
  `4cecd3fe3f677041e0beb0118397b2deb18b3227`, after preserving its published
  predecessor and tree. Resolved the three runtime modules, generated footprint
  and two effort documents without discarding either implementation or any
  historical journal. Upstream #10951 participation and source-owner contracts
  remain intact; separate SDK observation planning retains its existing owner.
- Async capability negotiation now receives the already-enriched request,
  preserving physical generation, installation root and execution challenge.
  Both attach clients retain those fields alongside the versioned caller-root
  context. The resident advertises both capabilities and resolves configuration
  for the validated root before equivalence checking; pool identity retains
  caller-root/configuration-source isolation.
- Client challenge construction uses the same explicit root as the resident,
  including relative configuration references. Credential and cwd significance,
  pre-effect legacy-provider fallback and unknown-outcome no-replay remain
  unchanged. Existing context/dependency helper owners absorb the small
  responsibilities that otherwise exceeded module caps; no ceiling, budget or
  policy was widened.
- Final focused controls passed **74 tests**, plus **11** preparation-owner
  controls, with verified cleanup. After native serialization was released, the
  existing generated-CMD controls and both ordinary-installer variants passed
  **4 tests**, with verified cleanup. The ordinary witness now removes both
  retirement/cutover opt-outs; its held upstream PID, single initialization and
  incoming physical Pipeline-code assertions still pass. No calibration,
  observer, debugger attachment or production mutation was performed.
- Both ordinary installer source paths retain no legacy cutover/reaper/reset or
  slot-GC fallthrough; two focused source guards cover that boundary. This does
  not attribute individual victims from aggregate historical cleanup output or
  qualify the full POSIX ordinary-install matrix.
- Fresh upstream inventory is **4234 nodes**, **598 sources**, unchanged coverage
  targets and the original **300-second** unit budget. Canonical verification
  required no eligible binding refresh or native-actor admission. MCP footprint
  revision **36** retains its identical non-derived contract with canonical
  producer regeneration. F/E9, strict runtime-resolution, headless source guard,
  install contract, module caps, documentation consistency and dependency guard
  **567/0** passed.
- Current-head native Linux unit CI and review remain required before merge;
  no whole local unit packet or Windows-native substitute was run. Publication,
  release and consumer acceptance remain coordinator-owned. #10742, full
  handover #10486 and pre-feature adoption holds remain open.

### 2026-11-06 - Prospective MCP desktop gates withdrawn

- Corrected the active metadata, guiding intent, coordination, Plan and
  Validation Plan: MCP source, release and acceptance do not depend on observer,
  focus/window-calibration, Terminal-host or debugger qualification. The
  application-specific conditions are **withdrawn, not passed**. Generic SDK
  observation items are explicitly deferred to the existing public #10925;
  no SDK plan or implementation is duplicated here.
- Historical journal text is retained verbatim. Earlier descriptions of an MCP
  observation prerequisite are history, not surviving prospective gates.
  The unused calibration approval remains held; no effects are authorized.
- Marked #10951's source correction **merged** through #10969 at
  `4cecd3fe3f677041e0beb0118397b2deb18b3227`. Promotion/release and authorized
  installed primary-consumer acceptance remain open. Old clients lacking the
  caller-root protocol need an updated client or supported direct `--no-serve`
  path; this is not transparent legacy handover.
- This is documentation-only after the qualified code reconciliation. The
  **74** pure, **11** preparation and **4** native results are unchanged;
  no native or whole-unit packet was repeated. Source admission remains
  **4234/598/300**, with dependency guard **567/0** and all other source, review,
  required-CI, lifetime and authorized-acceptance gates intact. #10486, #10742
  and pre-feature adoption holds remain open.

### 2026-11-06 - Secondary preparation preserves the private environment

- The secondary agent-bridge install in worktree-manager preparation omitted
  the private environment already supplied to the primary install. It now
  receives the same preparation environment. All three runner `run_uv_install`
  call sites were audited; primary and source-only preparation already passed
  it. Resolver/source policy and installation arguments are unchanged.
- Extended the existing two-install regression for both success and secondary
  failure. Both install calls must carry private home/config/service roots,
  containment markers and scrubbed installation-participant context. Both cases
  failed before the fix on the missing environment; the final focused
  preparation packet passed **11 tests** with verified cleanup.
- Canonical admission verification retains **4234 nodes**, **598 sources**,
  coverage targets and the **300-second** unit budget, with no eligible binding
  refresh or actor admission needed. F/E9, module caps, strict runtime-resolution,
  headless source guard, install contract and dependency guard **567/0** passed.
- Tracked MCP runtime, tests and shared-library bytes are unchanged from the
  published predecessor. No native or whole-unit packet was repeated; previous
  native results remain prior-head evidence, not a new current-head CI claim.
  MCP desktop-gate withdrawal, historical journals and existing holds remain
  intact. Current-head CI/review, publication and acceptance remain
  coordinator-owned; full handover and prepared qualification remain open.

### 2026-11-06 - Bounded fallbacks and preparation authentication capture

- The operator selected landing the bounded fresh-code fix with explicit direct
  fallbacks, tracking broader warm reuse separately. Contract and Performance
  text now state the HTTP/dynamic-auth/routed/CLI limits and cold
  authentication/startup/initialization costs. Actual inherited stdio cwd
  remains significant; no nonexistent `ServerSpec.cwd` feature was invented.
  The warm follow-up is tracked under #10486, not as a new incoming gate.
- An offline, synthetic selected-UV experiment proved that home isolation lost
  the default credential-store location; the existing explicit
  `UV_CREDENTIALS_DIR` input restored lookup without restoring HOME.
  Preparation now captures the selected tool's `uv auth dir` metadata before
  isolation, delegating executable and environment qualification to the existing
  dependency owner. Only the validated absolute location is retained for
  preparation; capture never reads or copies credential contents.
- Unknown, unsupported, malformed, failed or oversized metadata refuses before
  installation. The owned metadata bound is **20 seconds**, including its
  five-second cleanup reserve; stream limits are **32 KiB/16 KiB**.
  Unconfirmed cleanup retains native custody and the preparation sandbox.
  Explicit directory inputs must agree with qualified metadata. No global
  installer floor, automatic tool upgrade, feed/trust fallback or broker was added.
- Actors receive their own private auth directory, including host-state opt-ins.
  Configured resolver/keyring settings remain with their existing owner, but
  external keyring state is unqualified and not claimed restored.
  Final focused qualification passed **36 tests** with verified cleanup,
  including actual selected UV **0.11.18** default/explicit synthetic stores,
  actor exclusion, errors and existing preparation/isolation controls.
- Canonical installer libraries and their mirrors are unchanged; capture remains
  in the preparation owner. Only that owner's existing admitted source binding
  changed. The **4234/598/300** inventory/coverage/budget, MCP footprint and
  runtime pipeline are unchanged; dependency guard **567/0** and owning
  source/doc/contract guards passed. No full unit or MCP-native packet was
  repeated; prior MCP-native results remain prior-head evidence.
- Current-head review/CI, publication, release and installed acceptance remain
  coordinator-owned. Full session handover, prepared qualification and pre-feature
  adoption holds remain open; this slice is not whole-effort completion.

### 2026-11-06 - Windows host-state actor aliases remain private

- The existing default/explicit auth controls reproduced duplicate lowercase
  and mixed-case host-directory keys in the Windows host-state actor branch.
  That branch now removes only aliases of each always-private variable before
  assigning its canonical private value, matching the ordinary isolation branch.
  POSIX case semantics and other host-state settings are unchanged.
- Both controls failed before the fix. Final affected pure auth/environment
  controls passed **23 tests** with verified cleanup; the native UV case was
  deliberately not run. Input mappings remain unchanged.
- F/E9, module caps and canonical binding verification passed. Only the admitted
  preparation-owner digest changed; **4234/598/300** remains intact. No native,
  full-unit or MCP packet was repeated, and no new gate or actor was admitted.

### 2026-11-06 - Actual-base reconciliation and netrc preparation input

- Preserved the published source/lease and rebased onto actual Dev
  `af9afa408013c9d5378feb4eb5be6887447f174a`. The only conflict was the runner's
  primary installation call: retained private `env` together with upstream's
  non-editable Manager installation, cache-shape fingerprint and group planner.
  No upstream netrc correction was present; no recovery ref or stash was altered.
- Selected UV **0.11.18** against an owned synthetic loopback index proved
  default netrc authentication succeeded before home isolation but failed during
  preparation. Explicit `NETRC` already survived preparation, but leaked into
  actors. Both new native controls failed before the correction.
- Preparation now retains only the qualified netrc location: explicit absolute
  `NETRC`, otherwise the documented home `.netrc` using original POSIX `HOME`
  or Windows `USERPROFILE`, matching the existing native-feed owner. Unknown or
  unqualified home/file locations refuse with an explicit-input requirement;
  no credential contents are read/copied and no OS fallback is guessed.
- Actor netrc locations remain private in ordinary and host-state modes,
  including Windows case aliases. Final affected qualification passed
  **45 tests** with verified cleanup, including actual default/explicit UV
  resolution, unauthenticated actor requests, private home disambiguation and
  rebase/preparation controls. The POSIX home rule is source/document-qualified
  and covered by pure controls; this is not a full POSIX installed qualification.
- Current upstream **4525 nodes**, **621 sources**, coverage targets and the
  original **300-second** unit budget are preserved. Only the changed
  preparation-owner digest needed refresh; the canonical MCP footprint needed
  no regeneration. Source/doc/contract guards and dependency guard **567/0**
  passed. No full unit or MCP-native packet, publication or production operation
  was performed. Current-head CI/review and release/installed acceptance remain
  coordinator-owned; full handover, prepared qualification and adoption holds
  remain open.

### 2026-11-06 - Preserve merged SDK lifetime source; ordinary witness blocked

- Final fetch completed normally and exposed actual conflicts with merged SDK
  source `407f5d7c7d0cca8081cb2c3572e9797436bf39b4`. Reconciled the two MCP
  effort documents and producer-only footprint conflicts without altering either
  historical journal. Upstream SDK code/tests/docs and the canonical MCP
  caller-lifetime archive remain exact; optional pre-resume admission and expired
  Job unconfirmed-custody behavior were not replaced by older code.
- The MCP footprint's non-derived revision **36** contract was identical;
  canonical owner regeneration covers the actual merged scripts/source.
  Auth/netrc/preparation checks passed **45 tests** against the new SDK, with
  verified cleanup. Current **4525/621/300** admission and dependency guard
  **567/0** remain qualified; owning source/doc/contract guards passed.
- The archive/native-owner closure change justified the existing private MCP
  functional controls, not a whole-unit rerun. Initial probes omitted Windows
  platform and diagnostic metadata from the outer synthetic environment:
  missing `OS` selected a POSIX interpreter path; missing `COMPUTERNAME` later
  failed manifest writing. Those failed packets remain failures. The generated
  CMD fixture now checks platform metadata and its actual private CMD file;
  ordinary fixtures use a generic diagnostic machine name.
- The two generated-CMD controls passed after correcting the platform input.
  With the diagnostic input supplied, both ordinary variants completed incoming
  installation/publication but failed their retained warm-reuse assertion:
  two successful account-a calls reported different upstream PIDs.
  This is **unqualified**, not silently credited as direct-fallback acceptance.
  Context equivalence versus fixture-environment differences is unresolved;
  no assertion, challenge, deadline or lifetime rule was weakened, and no
  further native retry was attempted.
- Cleanup was verified for each failed packet. All work remains private/source
  only, without GUI/observer/debugger or production operations. The coordinator
  retains this concrete ordinary-witness diagnosis/acceptance decision together
  with publication, current-head CI/review and release/installed verification.

### 2026-11-06: Formal ordinary-installer validation; historical mismatch unresolved

- At source revision `2e1a5c5535`, new isolated Windows formal cases passed for
  explicit-only and installer-discovered selection in 124.47s and 196.04s,
  respectively, each within its own original 300s total bound including
  verified cleanup. Instrumentation was disabled; every original PID reuse,
  physical-code, stale refusal, endpoint identity and held-client assertion
  remained intact. Earlier instrumented diagnostics and negative controls
  remain separate evidence, not retroactively qualified formal runs.
- The earlier warm PID mismatch was **not reproduced; its cause remains
  unknown**, not fixed. Preserve that finding under #10486; these passing
  cases do not establish its historical cause or waive a future regression.
- Subsequent source reconciliation retains upstream lifecycle ownership and
  admission inventory. New-source validation, current review/CI, release and
  installed verification remain separate gates; prepared-application acceptance
  and complete session-preserving handover remain open.

### 2026-11-06: Review corrections for ordinary caller context and readiness replies

- Two focused regressions expose installer-only root bookkeeping in the
  semantic caller digest and uncaught malformed readiness JSON. The corrections
  ignore an unreferenced installation-root field only for semantic comparison,
  preserve physical inheritance and explicit references, and translate only
  readiness JSON/UTF-8 decode failures into pre-application unavailability.
  Application uncertainty and no-replay behavior remain unchanged.
- The ordinary installer witness now leaves the root field unset for literal
  callers while retaining every installer child's explicit root and all
  original assertions. New-source formal validation and current review/CI
  remain pending. Neither finding establishes the cause of the historical
  warm PID mismatch; that finding remains not reproduced with unknown cause.

### 2026-11-06: Bounded incoming source merged; historical milestone

- [Source merge, qualification scope and then-open delivery gates](journal/2026/11.06%20bounded%20incoming%20source%20merged.md).

### 2026-11-07: Corrective serving recovery and update transaction work

- #11333 reproduced a retained endpoint without an active route or native
  listener. The admission guard had treated endpoint presence alone as a live
  owner. Startup now consults the native listener owner first and retains the
  original handle until guarded incoming publication. Live foreign owners,
  unavailable native inspection and listeners appearing during readiness still
  refuse without incumbent termination.
- Two dead-handle controls failed before correction. Thirty focused startup
  controls and a private ordinary Windows install plus literal tool call passed
  afterward, with verified cleanup under the existing bounds. This source
  result does not substitute for release, installed acceptance or the complete
  adopted-wrapper reattachment gate.
- Retained phase timings also exposed repeated local-library UV transactions
  before root package installation. Dispatch and Machines now resolve their
  UV workspace closure once, retaining reinstall/refresh inputs, governed
  dependency policy and their non-UV fallback. An already-enabled supervisor
  task is left enabled without a redundant enable mutation; disabled-task
  permission failures remain failures.
- Current review/CI, promotion, owned installed repair and a clean measured
  sweep remain the corrective slice's completion gates. Cold-update performance
  is not declared qualified merely because source regression controls passed.

### 2026-11-07: Corrective source and release; bounded literal acceptance

- #11407 merged after final-head automated approval and all required CI.
  Its coupled bytecode guard now expects five dependency-policy command sites
  after batching removed one redundant preinstall site; all bytecode-disable
  assertions remain enforced. Only that test's admitted digest changed.
- Promotion run 42426784779 passed coverage, real Windows Resume, artifact
  attestation, immutable candidate reuse, publication readback and normal merge.
  Qualified Dev `fe117fd8609ffa22bc5e787be3fbdf39903c9a12` and released Main
  `06aac8c5f6e596652a4a497c4af25a39f44e3e66` include the correction. The checked
  MCP and Machines paths and Dispatch supervisor match reviewed source;
  Dispatch's installer differs only by the expected payload-library path rewrite.
- A later installed MCP208 command completed one real read-only status call
  in 8.37 seconds without replay. Its current native owner held both listeners
  afterward; all measured pre-existing MCP incarnations retained their
  births. The later host absence is consistent with the documented idle-exiting
  accelerator, but this evidence does not establish that particular exit cause.
- A separate normal Windows update still failed incoming construction and took
  more than five minutes. Its native bridge-readiness adapter required caller
  repository context from an installer payload directory. Selected MCP208
  code and older deployment-source metadata remain distinct; no metadata was
  manually rewritten. Constructor acceptance and the measured sweep stay open.

### 2026-11-07: Native constructor readiness correction under #11455

- Three focused controls reproduced a valid native declaration being rejected
  by repository-context discovery and genuine native declaration/overlay errors
  being masked by that same refusal.
- Installation readiness now reads, merges and validates native declarations
  and machine-local overlays without selecting caller repository preferences.
  Actual bridge/tool configuration still uses the strict repository-qualified
  composer; no native validation, authentication, source or publication guard
  is bypassed.
- Readiness, configuration and application-callback qualification passed
  109 tests with two existing platform skips and verified bounded cleanup.
  This includes the actual installed-callback readiness entry, not just a mock
  successful result. Install contracts and F/E9 lint passed.
- Canonical producer refresh retains footprint revision 36. The unchanged
  4,660-node, 659-source unit admission and 300-second budget verify; none of
  the changed source paths belongs to that packet, so no unrelated digest,
  membership or coverage target was changed.
- Review, required CI, promotion and actual installed constructor/sweep
  acceptance remain open. The effort remains Active; full adopted-wrapper
  reattachment, historical warm-reuse uncertainty and prepared qualification
  are not closed by these corrective slices.

### 2026-11-09: Nested-library version coverage blocker #11704

- Logger and Machines release payloads included changed nested ZDD metadata
  without distinct consumer versions. Replacing only the current ZDD manifest
  with its prior released bytes reproduced the earlier completed content hash;
  this is changed nested input, not corrupt completion metadata or permission
  to rewrite immutable completed slots.
- The bump-consumer map now expands canonical sibling dependencies with the
  materializer's shared UV parser, deterministic cycle-safe traversal and
  fail-closed invalid-manifest handling. Both indirect ZDD consumers appear
  in the actual map; ordinary patch changefiles request distinct releases.
- Integration also identified Remote Driver, Budget Guidance and Chat as
  previously omitted indirect consumers. Patch changefiles cover all five
  missed payloads, not only the two observed installer failures; no additional
  capability activation is requested.
- Seven focused source controls failed before the correction and passed after
  it, covering shared/nested dependencies, consumer forms, cycles, aliases and
  invalid manifests. This closes a bounded release-accounting gap toward
  authoritative runtime replacement, not installer admission or lifecycle work.
- Related bump/changefile/accumulation guards pass 88 tests; two native symlink
  controls explicitly skip when the platform denies symlink creation. The
  source-only nested symlink-refusal control passes without native privileges.
- Review, promotion and actual installed reconciliation remain coordinator-owned.
  #3654's broader pointer work and #6369's source/stamp ownership stay separate.
  No MCP, global/full-update or session-reattachment gate is marked complete.

### 2026-11-09: Changefile-only consumer coverage follow-up

- Current-head review of #11709 found that a library-only bump could still
  bypass consumer coverage because no canonical source path changed. The
  presence guard now expands new or changed library bump intents through the
  transitive map, retaining diff-owned consumer coverage and rejecting
  unreadable or malformed changed intents.
- Unchanged baseline entries, note-only edits and removed intents remain
  unrelated to the current PR. Promotion/version allocation and immutable-slot
  admission are unchanged. Review, required CI, release and installed acceptance
  remain coordinator-owned; MCP, full-update and reattachment gates remain open.
- The failure-first selection reproduced six failures. Related source guards
  and selected admitted release decisions now pass 108 tests with two explicit
  native-symlink capability skips and verified cleanup. Admission/source checks
  retain 4,668 nodes, 663 sources and the 300-second budget without fingerprint
  changes; this selected evidence is not the full required CI packet.
