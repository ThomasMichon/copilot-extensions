# Native Mux Launch Contract

_This proposal was prepared with GitHub Copilot._

- **Slug:** `native-mux-launch-contract`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed proposal and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Done
- **Vision:** closes [session-hosting](../../../../visions/session-hosting/README.md) host-owned presentation and exact initial context; preserves [architecture invariants](../../../../docs/patterns/README.md)
- **Umbrella issue:** #7962

## Guiding Intent

An interactive `agent-worktrees copilot` invocation must keep terminal input
with its attached mux until that client exits. Reattaching by the mux's native
command must not lose worktree status. Resuming a conversation must execute in
the selected worktree rather than restore an unrelated anchor directory.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Fix driver | Own the bounded attach, connected-session status and directory repairs through review and source acceptance | Managed source worktree `soldered-bearing` |
| Connected-status implementer | Own bounded connected-mux discovery/routing and focused regressions; no launch, lifecycle, Git publication or effort writes | Fix driver's in-session coding delegate |
| Existing native recovery owner | Retain restart-recovery implementation and activation | #4564; `steady-resistor` |
| Existing session-host owners | Retain provider selection, remote identity and graceful cutover | #352, #5961 and #4496 |

## Coordination

- **Topology:** reviewed proposal first, then one cohesive source repair.
- **Host (owns PRs):** Fix driver.
- **Delegates:** Connected-status implementer owns status-specific
  Manager/engine helpers and tests; Fix driver owns terminal/directory launch,
  shared-loop integration, documentation, effort and publication.
- **Scope:** existing native CLI and Manager mux boundaries, not reboot
  recovery, arbitrary foreign mux adoption, provider replacement or rollout.
- **Handoff:** preserve this complete objective, source workspace, pending
  review and remaining validation; a merged slice is not parent completion.

## Context

- The Windows `copilot` terminal handoff currently calls `os.execvp`, while
  the Manager's Windows foreground launcher waits for its child. A Windows
  overlay is not the POSIX process-replacement/terminal-custody contract.
- Direct embodiment creates its mux without going through the Manager's
  normal launch-script mapping registration. Existing attachment observation
  sees registered mappings, not every native attachment.
- Copilot advertises `-C <directory>` as an explicit working-directory
  selection. A mux's pane starting directory and a resumed conversation's
  recorded workspace are different evidence.
- The reporter identified the wrong directory through Copilot session
  metadata or its displayed workspace, not an independently observed shell
  `pwd`. The repair must test actual execution and distinguish historical
  metadata from current process context.
- Existing native restart, provider attachment identity and daemon cutover
  owners remain unchanged. No installed payload, live session or global
  terminal/feed configuration is patched by this effort.

## Request

The operator reported (verbatim, with no private identifiers):

> Bugs in `agent-worktrees copilot`:
> 1. Although it seemingly rendered the mux session in the terminal, I could not interact with it; key presses fell through to the backing console. I had to use `psmux attach ____` to get back to the session manually
> 2. Manually re-attaching to the mux session failed to ensure the Worktree Manager mux daemon pushed sttaus to it. We should ensure that the Mux daemon pushes status to all muxes with active connections
> 3. The CWD of copilot in that session was somehow set to the anchor repo root, and not the worktree folder.

The operator confirmed `native-mux-launch-contract` and clarified that the
directory observation came from "Copilot session metadata or displayed
workspace." The implementation and validation decomposition below is
**agent-recommended**, not additional operator scope.

## Plan

### Phase 1 - Reviewed contract and bounded reproduction

- [x] Review and merge this proposal before implementation.
- [x] Establish fail-first terminal-custody, manual-attachment status and
  resumed-directory regressions at their existing owning boundaries.

### Phase 2 - Existing-owner repairs

- [x] Keep the Windows interactive attach caller waiting with inherited
  terminal streams; preserve POSIX replacement, detach and exit-code behavior.
- [x] Ensure every connected, attributable worktree mux receives status through
  existing Manager/engine ownership, including native manual reattachment.
  Preserve incarnation fences and standalone operation when Manager is absent.
- [x] Pin the selected execution directory through native Copilot launch/resume
  without rewriting session history, dropping the conversation, or accepting a
  foreign worktree identity.
- [x] Update authoritative launch/status documentation and changefiles for all
  affected shipping owners.

### Phase 3 - Source acceptance and conclusion

- [x] Exercise focused tests and native Windows consumer boundaries; record
  exact evidence and any unavailable platform or interactive tier.
- [x] Land the repair through genuine current-head review and required CI.
- [x] Resolve every Plan and Validation Plan item, journal the result and
  archive this effort. Release and productive rollout remain explicitly
  separate, and no updater is authorized by this source-fix request.

## Validation Plan

- [x] Terminal tests prove the Windows parent cannot return before its attach
  client finishes, inherits rather than pipes input/output, propagates client
  failure, and preserves POSIX `exec` behavior.
- [x] Connected-session controls include multiple sessions, manual reattach,
  daemon restart, stale mapping/incarnation refusal and disconnected sessions.
  Unknown attachment probes must not become confirmed zero or claim ownership.
- [x] Deferred to `#7962`: Resume tests use an anchor-origin conversation and a distinct selected
  worktree; prove the actual child's directory, not only launch JSON or a title.
- [x] Native Windows checks exercise the real process boundary in contained,
  disposable state. A synthetic mux client does not prove full human keyboard
  interaction; any unavailable real terminal tier is stated explicitly.
- [x] Deferred to `#7962`: POSIX behavior is exercised where available, or remains an explicit
  native-CI obligation without representing Windows compatibility as POSIX proof.
- [x] Run touched-code lint, install/resource contracts and the relevant
  focused regression portfolios without adding a broad promotion matrix.
- [x] Review final documentation and graceful-cutover impact; verify actual
  source merge before reporting completion. No running-state claim follows
  from source tests or a merge.

## Proposal

Repair the existing foreground attachment and status-routing contracts through
their current owners. Preserve the session-host process boundary and reuse
native directory selection instead of editing Copilot's stored transcript.
Qualify actual native execution separately from metadata and wrapper output.

Documentation impact: the CLI reference describes foreground attachment,
directory selection and historical metadata; the Manager README describes
discovery, incarnation fencing, optional-provider behavior and on-demand
startup. This effort and its index retain progress and unearned acceptance.
The changefile requests patch releases for both shipping owners; no version
is hand-edited.

Graceful cutover impact: the existing mux daemon gains a discovery request,
not another service or presentation owner. Its route, drain, retirement and
installer-driven cutover remain unchanged. New status applies carry the
mapping incarnation; startup uses the selected owner's existing ensure
operation with bounded headless custody. A reachable older peer is not
restarted or updated to force acceptance. No productive rollout is performed.

## Journal

### 2026-10-22 - Report and scope confirmation

- Confirmed the effort slug and the reporter's directory-observation source.
- Checked current open mux/provider PRs and adjacent attachment/restart issues.
  Existing recovery and provider owners retain their distinct work.
- Traced the Windows overlay call, direct embodiment and Manager mapping seams.
  No executable source or installed runtime has been changed.

### 2026-10-22 - Reviewed plan and native launch regressions

- Proposal #7965 merged after current-head Copilot approval and successful
  required CI. Implementation began only after pulling that reviewed plan
  forward.
- The terminal/directory regressions failed first: nine failures distinguish
  the Windows overlay and missing/foreign native-directory selection.
- The first native PowerShell forwarding check exposed a second real defect:
  an attached short `-C` value containing a drive colon was split at the
  dot-sourced setup boundary. Normalized setup now receives a named directory
  parameter and constructs native arguments after its own parameter parsing.
- The complete focused launch portfolio passes 178 cases, including the real
  Windows child lifetime/input control and normalized PowerShell execution
  with a spaced selected directory. The executable stand-ins do not constitute
  human terminal interaction or native Copilot conversation-resume acceptance.
- A bounded status implementer owns only connected-mux discovery/routing and
  its focused regressions; the fix driver retains launch integration, review
  and completion. No running agent, mux, service or installed payload is patched.

### 2026-10-22 - Integrated connected-session repair

- Connected-session discovery now joins unambiguously attributable native
  worktree muxes to the existing status pipeline. Healthy mappings avoid
  repeated roster/probe work; unresolved incarnations have a completion-based
  retry cadence and bounded fair selection.
- Review corrected permanent refusal of legitimate fresh incarnations:
  native confirmation and an atomic registry identity/revision comparison
  preserve tombstones and concurrent replacements without trapping a new
  incarnation behind an old teardown.
- Positive attributable demand can start an already-selected dormant Manager
  through its declared command and existing ensure lifecycle. Absent Manager
  remains standalone; unsupported peers defer visibly without restart or a
  direct-writer bypass. An explicit legacy sink-root override is preserved;
  conflicting automatic startup is refused rather than silently retargeted.
- Independent final engine/status validation passes 222 cases under the
  ordinary runner limits. The complete Manager discovery/daemon/attachment
  group passes 134 cases under its unchanged 120-second containment, including
  the native startup case that had previously failed in a larger worker run.
  The launch portfolio separately passes 178 cases.
- A read-only real PSMux enumeration confirmed the required native identity,
  attachment and original-directory format fields on every observed session.
  It did not create, attach, stop or modify any user session.
- Full Docker clean-room execution is unavailable because the installed Docker
  client has no running engine endpoint. Disposable Windows process/IPC/setup
  controls were exercised instead. Human keyboard/real Copilot resume
  acceptance and native POSIX execution remain with #7962; this source-only
  task does not authorize productive rollout or claim those outcomes.

### 2026-10-22 - Current-base reconciliation and final source checks

- Reconciled the current contribution base while preserving unrelated effort
  rows and the new `get git-remote` documentation. Range comparison showed
  only documentation-context differences in the replayed repair.
- The combined native portfolio exposed a wake-test lifecycle race: startup
  could already set the event, the loop could consume it before the final
  assertion, and an exhausted two-value supersession iterator could prevent
  bounded retirement. The fixture now isolates the real request's wake,
  captures its actual reply-time transition and retains superseded state
  through drain. No production wake behavior, deadline or test selection was
  weakened. An intermediate attempt hit the unchanged 300-second bound;
  the corrected complete portfolio passes 403 cases in 82.98 seconds.
- Checked the startup helper's POSIX custody contract rather than assuming a
  Windows Job covers other platforms. POSIX startup has its own process group;
  timeout cleanup retires that group while its leader is still owned, before
  reaping. The focused client portfolio passes 18 cases, including the
  platform-parameter cleanup control. This is not native POSIX qualification.
- All changed Python passes focused lint. Installer closure, source-size and
  headless-launch checks pass; no ceiling or deadline was raised.

### 2026-10-22 - Review corrections

- Addressed all five findings from the first source review: absolute PSMux
  executables retain native target syntax; repeated interrupts terminate and
  reap the foreground attach client; POSIX Manager startup uses the shared
  unreaped process-group cleanup primitive; standalone status admission
  persists and revalidates native incarnation across restart; and Manager
  recovery compares resolved executable identity rather than command spelling.
- Standalone admission suppresses changed and unknown incarnations before
  presentation, including detached observations. Legacy path registrations
  require original-directory proof, and failed identity persistence does not
  become a successful admission. Fresh Manager ownership and standalone
  fallback retain their existing separate contracts.
- Normalized setup recognizes absolute shell executables and refuses a second
  internal execution-directory override. The complete launch/session consumer
  portfolio passes 239 cases. The four engine status/foreground modules pass
  252 cases and the complete focused Manager portfolio passes 138 cases.
  These are source and contained native-boundary checks, not productive
  deployment, human keyboard or real Copilot resume acceptance.
- Documentation impact was rechecked against the final review correction:
  the Manager reference now states restart-safe standalone admission and
  executable equivalence. Graceful-cutover ownership and rollout remain
  unchanged; deferred native qualification remains with #7962.
- Reconciled the latest contribution base, preserving the unrelated active
  effort rows. Range comparison confirms unchanged executable repair patches.
  The combined six-module launch, session and status portfolio passes all
  491 cases in 112.33 seconds under the ordinary unchanged containment.
  Touched-code lint, install closure, module-size and headless-launch checks
  pass. All five review findings have concrete fix replies; fresh review and
  required CI still gate the source merge.

### 2026-10-22 - Status visibility and lifecycle review

- Corrected the next review's three new defects: confirmed native
  disappearance retires only the matching revision/incarnation; newly
  discovered sessions initialize visible session-scoped status formats through
  the existing presentation writers; and custom shell prefixes are no longer
  parsed as native Copilot directory flags. Unknown probes and concurrent or
  same-name replacement identities retain their existing protections.
- New format controls cover both tmux and PSMux, initial admission and failed
  initialization retries. The custom PowerShell `-C copilot` launch shape is
  exercised through an actual disposable native child, not just launch JSON.
  The repeated absolute-PSMux review item was already fixed by the shared
  executable classifier; it was not treated as a new defect.
- Independent integrated validation passes 504 engine launch/session/status
  cases in 87.14 seconds and 155 Manager discovery, daemon, attachment and
  native IPC cases in 18.58 seconds, within the unchanged containment limits.
  The status implementer's fail-first controls distinguished confirmed absence
  and initially unconfigured presentation before the fixes.
- Retained the additional POSIX direct-embodiment/manual-attachment witness
  reported on #7962 as an acceptance input, not independent daemon diagnosis
  or proof of native POSIX qualification. Source acceptance still does not
  authorize rollout or claim human keyboard/real Copilot resume acceptance.
- Documentation impact: launch-template prefix compatibility, initial
  presentation and guarded mapping retirement are now stated in their
  existing references. No new service, global mux setting, restart owner or
  graceful-cutover path was introduced.

### 2026-10-22 - Bash command-string consumer correction

- The next current-head review resolved all inline findings but identified
  a remaining summary-level defect: Bash `-c` assigns trailing arguments to
  positional parameters rather than automatically forwarding them to Copilot.
  A matching launch-array assertion was therefore not execution proof.
- Simple direct Copilot command strings now explicitly forward their native
  arguments and preserve the command-name slot. Existing direct forwarding
  forms retain their configured slot; opaque compound command strings fail
  with an actionable launcher-script requirement instead of claiming directory
  selection they cannot guarantee. Script arguments are not parsed as shell
  options.
- The new controls failed first in six cases. An actual installed local Git
  Bash child then confirmed selected-directory and resume-argument forwarding.
  This is the native Bash consumer boundary on Windows, not native POSIX
  platform or real Copilot conversation qualification.
- The complete launch portfolio passes 101 cases in 34.98 seconds, including
  native PowerShell and local Git Bash controls. Touched-code lint, whitespace
  and module ceilings pass; no test deadline or source ceiling changed.
- Follow-up review exposed an existing command-name slot in a simple Bash
  template becoming a spurious Copilot positional argument. Slot insertion
  now happens only when absent for every supported command-string form.
  Fail-first controls reproduced six failures, including actual native
  `--` and named slots. The full launch portfolio then passes 107 cases in
  57.44 seconds; both resume and directory flags reach the native child
  without either slot becoming an argument.

### 2026-10-23 - Render-independent retirement

- The review's remaining lifecycle observation exposed a coupled gap:
  confirmed absence was retired only on a new status apply. Unchanged renders
  can be coalesced while stale live mappings keep the daemon in demand.
  The existing attached-client observer is the independent owner for this
  reconciliation, within its unchanged bounded selection and retry cadence.
- The repository's existing tmux absence contract distinguishes exact
  missing-server/missing-socket diagnostics from arbitrary failed probes.
  That contract does not qualify PSMux-specific errors or generic
  "no sessions" text. Permission failures, malformed or partial output,
  timeouts and replacement incarnations remain unknown, not permission to
  retire an owner.
- Assigned the bounded observer/discovery repair and fail-first controls to
  the existing connected-status implementer. No extra service, probing loop,
  presenter or recovery owner is introduced; final source review remains open.
- The observer now replaces the selected discovered mapping's attachment probe
  with identity-aware enumeration and uses the same guarded tombstone helper.
  Its two-probe/two-second cycle and completion-based 20-second cadence remain
  unchanged. Fail-first controls produced 22 failures; the final independent
  four-module Manager portfolio passes 187 cases in 35.32 seconds under the
  unchanged 120-second containment.
- Controls preserve unknown, malformed, timed-out and same-name replacement
  evidence, concurrent mapping revision/incarnation changes and zero-client
  presence. Qualified tmux terminal absence is exercised without a render
  request. This source proof does not qualify additional PSMux diagnostics or
  native POSIX execution, which remain with #7962.

### 2026-10-23 - Strict native identity and final integration

- Closed the next review's summary-level malformed-identity gap. Both process
  owners enforce the registry's existing `$<decimal>:<decimal>` grammar;
  standalone admission, persistence and restart reuse reject malformed
  identities visibly. Manager probe parsing retains malformed evidence as
  unknown, never confirmed absence. Valid zero values remain accepted;
  no age, lease or freshness requirement was added.
- The new controls failed first in 15 engine and six Manager cases.
  Current-base integration then exposed an old recreation fixture using
  bare numeric epochs and an exhausted finite self-retirement iterator in
  the real-wire wake fixture. Fixtures now use valid native identities and
  retain supersession through drain. The elected runtime remains current
  for the real hook request; election-based retirement ends the run.
  No production lifetime, wake assertion, timeout or test selection changed.
- Independently verified all 535 launch/session/status cases in 116.66 seconds
  and all 200 Manager discovery/daemon/attachment/native IPC cases in
  37.76 seconds, within the original containment. The backed base rebase
  preserved peers' archived effort rows; range comparison showed unchanged
  executable repair commits before this final identity correction.

### 2026-10-23 - PowerShell command-source argument boundaries

- The next review correctly distinguished a single constructed argv element
  from a PowerShell `-Command` source boundary. Direct command templates now
  serialize the fully assembled native invocation as PowerShell literals
  after preference/permission arguments are appended. Spaces, apostrophes
  and literal variable syntax cannot split or expand the selected directory
  or forwarded arguments. Opaque compound commands require a launcher script;
  normalized setup and ACP retain their existing owners.
- Replaced the lossy `echo %*` proxy with a native Python child that records
  its individual argv elements as JSON. Four fail-first controls reproduced
  the defect. The actual staged launch wrapper and both `-C`/`-Command` forms
  now preserve exact directory, resume and prompt arguments, including paths
  with spaces, apostrophes and literal substitution syntax.
- The normalized PowerShell fixture now uses the same exact-child-argv proof
  while retaining its actual child-directory assertion. The complete launch
  portfolio passes 111 cases in 31.54 seconds, with unchanged test limits.

### 2026-10-23 - Literal option-value preservation

- Closed the next review's summary-level argument-handling defect:
  directory normalization now respects the native CLI's required-value
  options and normalized setup's own value parameters. A prompt, agent or
  path value that resembles `-C` remains data, not another directory flag.
  Missing required values fail explicitly rather than consuming the injected
  directory; the end-of-options tail is retained.
- Grounded the option arity in the installed native CLI's `--help`, without
  adding a runtime help invocation or configurable parser. Native child
  controls now include literal `-C` and `-C=/foreign` prompt values as well
  as exact directory/resume boundaries.
- The expanded first combined run exposed a native-child timeout whose
  `subprocess.run` cleanup waited on inherited descendant pipes. The fixture
  now uses the existing caller-owned process helper with the same 20-second
  deadline, so descendant cleanup is owned instead of relying on leader-only
  termination. All 138 launch cases pass in 49.43 seconds under the unchanged
  suite containment; no failed run was represented as passing evidence.
- A further exact-native control reproduced the normalized PowerShell
  parameter collision for a literal `-C` prompt value. The setup boundary now
  uses the equivalent long-option inline form for flag-shaped native values,
  leaving its own parameters intact. Both `--prompt` and `-p` retain the
  literal value while Copilot receives the selected directory separately.
  All 140 launch cases pass in 49.05 seconds.

### 2026-10-23 - Normalized native argument ownership

- Follow-up review identified directory flags after a forwarded literal tail
  in both normalized shells. Both scripts now place selected-directory flags
  before native arguments. Bash stops setup-parameter parsing at the literal
  tail, so a later setup-shaped value cannot replace the selected directory.
- Exact PowerShell execution also demonstrated that raw literal-tail tokens
  still collide with script parameter binding. The owned launcher now carries
  native argv in a checked JSON argument envelope, separate from setup fields.
  This supersedes inline option-value normalization and preserves the original
  native tokens, including short options and literal values. Invalid or mixed
  envelopes fail before setup effects; configured launch templates cannot
  override the internal envelope.
- Existing seed-record/id/runtime fields and recovery remain setup-owned.
  The legacy direct setup call remains available when no envelope is supplied;
  ACP remains on its existing transport. No service or installed state changes.
- Five exact native PowerShell/Bash directory-and-tail controls pass. The
  combined launch and launch-seed consumer portfolios pass 276 cases in
  111.15 seconds under ordinary containment, including malformed/null/mixed
  envelope refusal. This remains source acceptance, not productive rollout.
- The integrated eight-module launch/seed/status portfolio passes 704 cases
  in 168.62 seconds. Its real-wire wake fixture now excludes unrelated project
  activation I/O from the unchanged one-second best-effort RPC budget;
  actual transport, decision and wake assertions remain in place.
- Traced post-build resume and seed consumers before source acceptance.
  A cycle-free, standard-library-only native-argument helper now composes
  later arguments inside the normalized envelope or quoted PowerShell
  invocation rather than after it. Resolve, embody, headless launch and
  handoff preview paths use the same helper; no runtime/registry import was
  added to the headless process path.
- Generated flags and later resume/seed additions precede literal tails while
  required option values remain data. Exact native PowerShell/Bash controls
  now exercise the real late-addition helpers with spaces, apostrophes and
  literal variable syntax, preserving conversation and seed together.
  The final eight-module launch/resume/resolve/handoff consumer portfolio
  passes 612 cases in 115.98 seconds. Lint, module ceilings, install closure
  and headless-launch contracts pass without raising a limit.

### 2026-10-23 - Bash native value ownership

- Closed the next review's Bash parser defect: required native option/value
  pairs are consumed together before setup-parameter interpretation. Literal
  directory, recovery or executable-override names remain prompt data rather
  than replacing setup state. Missing native values fail before launch.
- The bootstrap-independent shell table is checked against the shared native
  option contract, preventing duplicated arity knowledge from drifting.
  No Python or other runtime dependency was added to Bash setup.
- Exact native controls first reproduced four failures, including an actual
  selected-directory override. The corrected full launch portfolio passes
  158 cases in 65.52 seconds with unchanged containment and child deadlines.
- Audited the same value/option distinction in launch-mode selection:
  literal `--acp`, `--experimental` and `--allow-all` values could previously
  suppress directory selection or generated flags. Three fail-first cases
  reproduced it. The builder now uses actual native option tokens for mode,
  default flags and preference deduplication, excluding setup values and
  Bash command-name slots. Genuine ACP transport remains unchanged.
  All 162 launch cases pass in 66.44 seconds.

### 2026-10-23 - Source accepted and effort concluded

- Source repair #8031 actually merged to `dev` as
  `5fbce0760c22c22d3970898f4ec5ca56a1e1f115`. The exact published head
  received a current-head Copilot Comment with no open inline findings;
  all review threads were resolved and required CI was green.
- Investigated the final nonspecific late-composition concern with a separate
  read-only code check: every late resume/seed consumer, the deferred
  split-process path, both normalized Bash tail cases and 81 adversarial
  PowerShell quote combinations were checked without a reproducible defect.
  The repository's owner-authored clean-Comment policy satisfied source review.
  Managed head-pinned merge used the authorized additional CODEOWNER-vote exception,
  not a CI bypass or an override of a concrete unresolved finding.
- Re-read and resolved the complete Plan and Validation Plan. Durable launch
  and status truth is in the CLI and Manager references; this record archives
  the bounded source repair rather than becoming runtime documentation.
- Human keyboard/mux acceptance, real Copilot conversation/CWD acceptance,
  native POSIX qualification, any additional PSMux absence qualification,
  release observation and an explicitly authorized rollout remain with #7962.
  The normalized Bash exact chained resume-plus-seed subprocess case is also
  retained there as a coverage opportunity; the individual native boundaries
  and chained pure-code probe passed. No installed payload, service or user
  mux was changed, and no updater is authorized by this conclusion.
- Pulled forward the provider-confirmed merged head onto current `dev`,
  retaining its complete local backup. This effort is Done for source
  delivery and is archived on the completion date.
