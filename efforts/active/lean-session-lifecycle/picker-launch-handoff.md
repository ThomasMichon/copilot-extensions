# Picker Launch Handoff - Mini-effort

- **Parent:** [Lean Session Lifecycle](README.md), Phase 3
- **Tracking:** #5579; no duplicate implementation owner or umbrella issue
- **Created:** 2026-11-01
- **Status:** Active; native source qualification complete, publication pending
- **Vision:** `visions/plugin-services/README.md` - transient callbacks and
  removal of session-scaled wait-only forwarding processes

## Guiding Intent

After an ordinary Windows Picker launch or resume reaches its mux attachment,
neither the Worktrees nor Manager Python forwarding layer should remain alive
just to wait. A terminal-owned PowerShell launch host is acceptable; direct
terminal-to-PSMux attachment is the preferred simplification if qualified.
This is the bounded launch slice of the parent effort, not a second campaign.

## Request

The operator's 2026-11-01 request, verbatim:

> So, we expect the Python part of the Manager launch to be gone; ideally only the mux-pane launched process should be live in the terminal window. Let's figure out what changed here. After Picker launches or resumes a worktree, I expect Picker's python process to be *gone*. We should see:
> wt.exe -> launch_session.ps1 -> psmux.exe -> mux_session.ps1 -> copilot.exe
> I'd love it if we could achieve
> wt.exe -> psmux.exe -> mux_session.ps1 -> copilot.exe
> (mux_session is a placeholder; I forget what the actual process is named)

The subsequent planning request, verbatim:

> Great. Make a mini-effort to tackle this.

After the reviewed plan merged in #9913, the operator authorized execution:

> Great. Drive implementing these fixes.

## Scope Approvals

The final request authorizes source implementation and isolated qualification
of this reviewed mini-effort. The earlier request authorized the focused plan.
Neither authorizes
terminating retained launchers, migrating live sessions, updating installed
runtimes, weakening Job policy, or lifting a deployment hold. Implementation
approval is not rollout authority. The #5579 driver owns this bounded Phase 3
continuation; the #8669 installation-independent-session owner is unchanged.

## Context

- `agent_worktrees.manager_launch_cli._exec_worktree_manager` waits on
  Manager on Windows; the original forwarding seam dates to #484.
- `worktree_manager.relocated_launch._run_relocated_mux_launch` waits on
  `pwsh -File launch-session.ps1`. This behavior entered the relocated launch
  path in #2429 and was extracted without changing that wait in #5232.
- The outer Windows launcher now owns kill-on-close Job custody. Simply
  dropping its wait or exiting Python can kill the attachment descendants;
  it is not a valid handoff.
- `launch-session.ps1` attaches to PSMux, then performs return-path checks.
  The pane side is `pane-launch.ps1` invoking `pane-wrapper.ps1`, not a script
  literally named `mux_session.ps1`. An attachment client and its session
  server are separate process branches, so the requested chains express
  lifetime intent rather than a required literal ancestry.
- The resident Picker fast path also retains forwarding waits. Cover cold,
  resident and provider-session entry, not only the ordinary Manager entry.
- #8669 owns copied pane shells, current-installation lifecycle handlers and
  uninstall/replacement survival. Reuse that contract without taking over its
  seed-execution work. #5579 also retains broader command-shim and measured
  launcher-depth acceptance; this mini-effort does not close it by itself.

## Proposal

**Agent-recommended implementation direction:** make the terminal's shell host
own a two-phase handoff. Python produces the prepared selection/launch result
and exits; the shell consumes that result and performs launch/attachment using
the existing owner. Transfer cleanup custody explicitly before retiring the
Python forwarding owner. Do not introduce a detached unowned attachment, another
resident helper, arbitrary shell evaluation, or a Task/service/UAC fallback.

Start with the shell-hosted target. Evaluate direct PSMux attachment only after
the required path passes: the optimization must preserve attach-return cleanup,
terminal restoration and exit reporting, not hide those duties in another
wait-only process. Keep POSIX process replacement and standalone behavior.

## Plan

### Slice 1 - Agree the finite handoff

- [x] Confirm the current cold/resident/provider-session and project-binstub
  consumers; agree the boundary with the existing #5579 and #8669 owners.
- [x] Specify the smallest structured result and shell consumption contract:
  selected installation/runtime, literal argv/environment, cwd, cancellation,
  seed/attempt/receipt identity, exit status and cleanup custody.
- [x] Review the plan before runtime edits; identify the terminal/profile and
  same-window/new-window behavior without introducing an implicit preference.

### Slice 2 - Remove the two Python forwarding roles

- [x] Implement the shell-owned preparation-to-attachment handoff for ordinary
  local Windows creation and resume, including existing-session joins.
- [x] Retain terminal input/output, Ctrl+C, post-exit versus detach behavior,
  installation-independent pane shells, exact process ownership and reaping.
- [ ] Add focused lifecycle regressions, update launch documentation and
  required changefiles, and land through the normal source-review gate.
- [x] Record whether direct terminal-to-PSMux attachment is qualified; if not,
  keep the shell-hosted result and record the precise remaining constraint.

### Slice 3 - Qualify the persistent shape

- [x] Prove the native process-lifetime contract in an isolated Windows terminal
  with one and several sessions; finish all test-owned resources explicitly.
- [ ] Record release availability separately from a separately authorized
  installed rollout and running-state proof; do not migrate existing sessions.
- [ ] Journal the bounded outcome in the parent and retain its unrelated open
  Plan/Validation items and #5579's remaining launcher-depth acceptance.

## Validation Plan

- [x] After creation and resume/attach are ready, actual process observations
  show zero persistent Python workers or interpreter trampolines serving the
  two forwarding roles; transient preparation/helpers are classified separately.
- [x] Native console/PTY tests prove stdin, rendering, resize, Ctrl+C, detach,
  rejoin, child exit and terminal close; no session loss or unowned child tree.
- [x] Literal argument tests cover spaces, quotes, Unicode, empty values,
  metacharacters and `--`; preserve selected runtime, cwd and profile behavior.
- [x] Cancellation, failed launch, missing/malformed/reused handoff, seed and
  receipt failure cannot report success or launch a second session.
- [x] Existing post-exit, launch-attempt, native-incarnation and Job/reaping
  tests pass; no-mux, remote, new-window and POSIX behavior is preserved.
- [x] Fresh constructed-runtime and repeated/concurrent launch tests exercise
  real binstubs and shell boundaries, not only mocked Python composition.
- [ ] The shared Manager mux daemon remains a singleton after bounded cutover;
  per-session PSMux servers and actual Copilot processes remain permitted.
- [ ] Source tests, native evidence, promotion and authorized installed proof
  remain distinct; skipped lanes have explicit reasons and tracked follow-up.

## Journal

### 2026-11-01 - Focused proposal

- Reused #5579 and the parent Phase 3 instead of opening a competing campaign.
- Traced both Windows wait sites and their history; this is a longstanding
  launch-lifetime gap, not evidence of duplicate Manager mux daemons.
- Captured the operator's required Python lifetime and preferred shorter chain.
  No runtime edits, live-session changes or deployment were performed.

### 2026-11-01 - Source execution admitted

- The proposal merged in #9913 after a clean approval and required CI.
  The operator then explicitly requested implementation.
- Implementing the outer PowerShell return channel and finite Manager producer.
  Python-owned preparation Jobs settle before the shell invokes the ordinary
  launcher; existing pane scripts and their custody remain unchanged.
- Initial generated-launcher controls pass in the isolated native ARM Python
  environment. The earlier x64 test environment was retained after Windows
  Restart Manager identified its denied DLL's holder as the OS emulation cache;
  no service was stopped or reconfigured. Full native handoff acceptance remains
  open and is not inferred from those compatibility controls.

### 2026-11-01 - Native source boundary qualified

- The finite channel is shared through both forwarding layers. A capable
  Manager publishes version-1 JSON; Python and its cleanup Jobs settle before
  the terminal's PowerShell host consumes it. The host deletes the channel
  before attachment, preserves literal argv, restores scoped environment/cwd
  and records the actual return through a finite callback.
- Native ConPTY controls exercise the actual Worktrees forwarder and copied
  user/attributable entry scripts, generated retained Manager CMD/PowerShell
  entries and a standalone Manager entry creating its own channel. Native
  handles pin every preparation descendant, including interpreter trampolines;
  none remains at the selected attachment boundary.
- A separate real PSMux control runs two simultaneous fixture sessions, delivers
  input and Ctrl+C, resizes, detaches/rejoins the same live pane and independently
  exits each child. Fixture Jobs, retained handles, isolated data roots and
  named-session cleanup own all test resources. The production launcher's
  refusal path additionally preserves exit 3, its journal observation and the
  original diagnostics without running preparation effects.
- Engine discovery, runtime-selection responses and the successful selected
  command are synthetic. These tests are not a full real-engine/Copilot launch,
  live installed inventory or a Manager-daemon singleton claim. Those live
  measurements remain with parent Phase 4 / #5579; copied-shell and lifecycle
  behavior owned by #8669 is not taken over here.
- Focused results: 134 launch/receipt/compatibility unit cases; 98 resident,
  outcome and earlier native cases; 20 provider-session cases; 10 entry/terminal
  cases plus the production-refusal case; one real concurrent mux lifecycle
  case; two existing real-pane producer cases; and the earlier three generated
  installed-slot and three selected-payload controls passed. These packets
  overlap and are not an additive coverage count.
- Existing post-exit/reaping controls pass (three cases), as do native-hosting
  identity controls (26 cases; two exact-held-file cases require Linux procfs
  and are explicitly skipped on Windows) and four launcher packaging/UTF-8
  controls. The 13-plugin installation-contract check remains clean.
- Native checks exposed and fixed nested-array PowerShell splats and a
  malformed non-object request. Existing receipt fixtures were updated to
  answer hosting admission before asserting the actual resolver behavior.
  Independent pre-submit review found no high-confidence runtime defect.
- Direct terminal-to-PSMux replacement is not qualified: the existing launcher
  still owns attach-return, detach/exit and post-exit bookkeeping. The reviewed
  shell-hosted target is retained rather than deleting those duties.
- No installed runtime was updated, no live session was migrated or killed,
  and no deployment hold was lifted. Source publication, promotion and a
  separately authorized installed rollout remain distinct gates.
- A CMD entry without PowerShell 7 retains its previous Python route. The
  shell-host capability check uses CMD's built-in PATH expansion rather than
  adding a discovery process or a new mandatory dependency. Native missing-host
  and CMD handoff/selected-slot controls pass.

### 2026-11-01 - Published review corrections

- #9936's first review identified three concrete gaps: the shared wrapper
  cleanup roster omitted the new helper; a standalone CMD replacement fixture
  omitted its companion PS1; and a caller-defined `ERRORLEVEL` could shadow the
  terminal-host result. All three are corrected before accepting the review.
- Deployment/uninstall tests now derive owned files from the actual manifest,
  exercising removal of the helper rather than repeating an incomplete tuple.
  The CMD replacement cases include their companion PS1 and a deliberately
  shadowing `ERRORLEVEL=0`, covering both retained and constructed runtimes and
  real nonzero return.
- The two deployment/uninstall controls and four native CMD replacement/exit
  cases pass. Review and required CI must still clear on the updated head.
