# Picker Launch Handoff - Mini-effort

- **Parent:** [Lean Session Lifecycle](README.md), Phase 3
- **Tracking:** #5579; no duplicate implementation owner or umbrella issue
- **Created:** 2026-11-01
- **Status:** Draft; planning only, implementation and rollout not started
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

## Scope Approvals

This request authorizes creating the focused plan. It does not authorize
terminating retained launchers, migrating live sessions, updating installed
runtimes, changing Job policy, or lifting a deployment hold. Plan approval is
not rollout authority. The parent effort's existing implementation owner and
the #8669 installation-independent-session owner remain unchanged.

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

- [ ] Confirm the current cold/resident/provider-session and project-binstub
  consumers; agree the boundary with the existing #5579 and #8669 owners.
- [ ] Specify the smallest structured result and shell consumption contract:
  selected installation/runtime, literal argv/environment, cwd, cancellation,
  seed/attempt/receipt identity, exit status and cleanup custody.
- [ ] Review the plan before runtime edits; identify the terminal/profile and
  same-window/new-window behavior without introducing an implicit preference.

### Slice 2 - Remove the two Python forwarding roles

- [ ] Implement the shell-owned preparation-to-attachment handoff for ordinary
  local Windows creation and resume, including existing-session joins.
- [ ] Retain terminal input/output, Ctrl+C, post-exit versus detach behavior,
  installation-independent pane shells, exact process ownership and reaping.
- [ ] Add focused lifecycle regressions, update launch documentation and
  required changefiles, and land through the normal source-review gate.
- [ ] Record whether direct terminal-to-PSMux attachment is qualified; if not,
  keep the shell-hosted result and record the precise remaining constraint.

### Slice 3 - Qualify the persistent shape

- [ ] Prove the native process-lifetime contract in an isolated Windows terminal
  with one and several sessions; finish all test-owned resources explicitly.
- [ ] Record release availability separately from a separately authorized
  installed rollout and running-state proof; do not migrate existing sessions.
- [ ] Journal the bounded outcome in the parent and retain its unrelated open
  Plan/Validation items and #5579's remaining launcher-depth acceptance.

## Validation Plan

- [ ] After creation and resume/attach are ready, actual process observations
  show zero persistent Python workers or interpreter trampolines serving the
  two forwarding roles; transient preparation/helpers are classified separately.
- [ ] Native console/PTY tests prove stdin, rendering, resize, Ctrl+C, detach,
  rejoin, child exit and terminal close; no session loss or unowned child tree.
- [ ] Literal argument tests cover spaces, quotes, Unicode, empty values,
  metacharacters and `--`; preserve selected runtime, cwd and profile behavior.
- [ ] Cancellation, failed launch, missing/malformed/reused handoff, seed and
  receipt failure cannot report success or launch a second session.
- [ ] Existing post-exit, launch-attempt, native-incarnation and Job/reaping
  tests pass; no-mux, remote, new-window and POSIX behavior is preserved.
- [ ] Fresh constructed-runtime and repeated/concurrent launch tests exercise
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
