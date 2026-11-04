# Picker Launch Handoff - Mini-effort

- **Parent:** [Lean Session Lifecycle](README.md), Phase 3
- **Tracking:** #5579; no duplicate implementation owner or umbrella issue
- **Created:** 2026-11-01
- **Status:** Active; implementation released, authorized installed qualification underway
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

After the original plan merged in #9913, the operator authorized source work:

> Great. Drive implementing these fixes.

## Scope Approvals

The final request authorizes source implementation and isolated qualification
of this reviewed mini-effort. The earlier request authorized the focused plan.
Neither authorizes
terminating retained launchers, migrating live sessions, updating installed
runtimes, changing Job policy, or lifting a deployment hold. Implementation
approval is not rollout authority. The #5579 driver owns this bounded Phase 3
continuation; the #8669 installation-independent-session owner is unchanged.

On 2026-11-02, the operator selected the admission choice, verbatim:

> probe

The selected form label was "Use a bounded selected-runtime capability query
(recommended)". This chooses the read-only selected-runtime query direction
below, including its transient startup work. It does not approve the alternative
constructor-metadata implementation, installed rollout, live-session changes
or a deployment-hold change. The admission amendment cleared source review in
#9943 before further protocol implementation.

For the acquisition prerequisite on 2026-11-03 (UTC), the operator selected:

> consumer

The selected form label was "Repair suite-side progress observation
(recommended)". The choice was between recognizing completed writes from the
already-owned Windows Job, preserving the existing inactivity allowance, total
budget and cleanup gates, and retaining output-only observation while pursuing
a separate native CLI producer contribution. This selects the consumer-side
source direction. It does not waive source review, native qualification,
bootstrap admission, existing update coordination or the separate rollout
and live-session limits.

The operator's 2026-11-02 selected-consumer update authorization, verbatim:

> You may perform an update as-needed to pull this in, and then resume your charter/effort

The original charter was resumed on 2026-11-04 after a winddown. Preserve
single-writer update custody and the existing installed-acceptance owner;
neither continuation authorizes fleet rollout or retained-session migration.

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

### Selected-runtime admission amendment

A response-only environment offer is not safe for an unsupported runtime:
that peer can ignore the channel yet pass it into a daemon or detached pane,
whose later commands inherit a deleted request. Filtering only new resident
helpers does not fix an older peer. Do not reinterpret an invalid active
request as permission to launch, or retain abandoned channel files to hide
the problem.

**Operator-selected direction; implementation details remain reviewable:**
establish positive support through a bounded read-only query against the actual
selected execution runtime before offering the channel. Independently selected
receivers in the forwarding chain also require support before receiving it.
Do not infer support from a newer payload, a package version string alone or
an unqualified capability claim.

Use an effect-free query for both current and supported legacy peers, never a
flag appended after arbitrary operator arguments or `--`. Reuse the existing
finite-process owner to bound output and lifetime and confirm descendant
cleanup before admitting preparation. No implicit installation, network
bootstrap, unowned timed-out child, new daemon, Task or UAC fallback is allowed.
Explicitly unsupported peers retain legacy execution without the channel;
malformed/unavailable queries and uncertain cleanup remain visible and do not
authorize handoff or automatic replay.

The accepted channel belongs only to the foreground preparation flow. Capture
and forward it deliberately rather than leaving it in the environment of
resident services, reusable workers, launched panes or non-handoff children.
Preserve native failure results, literal empty arguments and the original
caller state. Measure and report the query's real cold/warm startup work,
including actual process counts; do not carry forward a claim of zero
discovery work.

Constructor-owned capability metadata was the unselected alternative. Existing
runtime-selection facts may locate the query target, but cannot stand in for
the actual capability response. The implementation remains drafted until the
reviewed admission revision is implemented and qualified.

## Plan

### Slice 1 - Agree the finite handoff

- [x] Resolve and review the admission amendment in #9943 before further protocol
  implementation; preserve the existing no-rollout boundary.
- [x] Confirm the current cold/resident/provider-session and project-binstub
  consumers; agree the boundary with the existing #5579 and #8669 owners.
- [x] Specify the smallest structured result and shell consumption contract:
  selected installation/runtime, literal argv/environment, cwd, cancellation,
  seed/attempt/receipt identity, exit status and cleanup custody.
- [x] Review the plan before runtime edits; identify the terminal/profile and
  same-window/new-window behavior without introducing an implicit preference.

### Slice 2 - Remove the two Python forwarding roles

- [x] Implement selected-runtime support discovery and foreground-only channel
  propagation, preserving unsupported-peer legacy behavior.
- [x] Implement the shell-owned preparation-to-attachment handoff for ordinary
  local Windows creation and resume, including existing-session joins.
- [x] Retain terminal input/output, Ctrl+C, post-exit versus detach behavior,
  installation-independent pane shells, exact process ownership and reaping.
- [x] Add focused lifecycle regressions, update launch documentation and
  required changefiles, and land through the normal source-review gate.
- [x] Record whether direct terminal-to-PSMux attachment is qualified; if not,
  keep the shell-hosted result and record the precise remaining constraint.

### Slice 3 - Qualify the persistent shape

- [ ] Correct the flagged CMD resident-picker entry and repeat its actual
  terminal-lifetime controls; an empty-argument CMD test is not sufficient.
- [x] Prove the native process-lifetime contract in an isolated Windows terminal
  with one and several sessions; finish all test-owned resources explicitly.
- [x] Record release availability separately from a separately authorized
  installed rollout and running-state proof; do not migrate existing sessions.
- [x] Journal the bounded outcome in the parent and retain its unrelated open
  Plan/Validation items and #5579's remaining launcher-depth acceptance.

### Slice 4 - Qualify the acquisition prerequisite

- [x] Review the operator-selected consumer proposal below before implementation.
- [x] Add opt-in held-Job write activity to the existing finite stream owner,
  retaining original output behavior, explicit failures and the absolute budget.
- [x] Use it only for Windows installed-payload updates under strict Job custody;
  preserve installation activation recovery, retry admission and POSIX behavior.
- [x] Qualify useful silent writes, a later stall, read-only work, the absolute
  ceiling, output parity and exact owned-descendant cleanup with isolated tests.
- [ ] Land through normal review/release, then qualify a supported bootstrap
  and normal updater before claiming installed adoption or returning to live
  launch/singleton measurements.

The following is the **reviewed implementation proposal** from #10178.
Sample strictly increasing completed write-byte
accounting from the already-held native containment Job, not a PID lookup,
filesystem scan or private Copilot staging convention. The optional activity
callback extends the existing finite observer; it does not create another
watcher, thread, daemon, Job or timer heartbeat. Default output-only callers
remain unchanged.

A positive native write delta may renew inactivity. CPU use, reads, a timer and
an unchanged counter may not. Missing/closed ownership, failed native accounting
or a decreasing counter is an explicit operation failure followed by the same
strict settlement path, never a successful fallback. Accounting is activity
evidence only: it does not validate copy content, publication or service health.

Keep the current quiet allowance and acquisition target budget, reserving their
existing cleanup/activation-settlement portions. Supply an absolute deadline
to the finite observer so continued writes cannot extend that target budget.
Do not change source admission, candidate validation, descendant custody,
activation preservation or the conditions licensing a second attempt.
Synchronize dependent source closures and document the optional library seam.

Native controls use private synthetic writers under the real owner, not a live
service or package stage. Unit tests cover counter/deadline/error decisions;
the Windows lane proves actual silent file writes, bounded stalls and retained
native exits. The installed-acceptance owner has no already-qualified path that
adopts a newer Worktrees consumer before the failing acquisition; bootstrap
qualification therefore remains an explicit gate, not an assumed workaround.

## Validation Plan

- [x] Actual selected-runtime support, not latest-payload metadata, gates the
  channel. Both mixed-version directions preserve legacy operation without
  leaking a request into later sessions.
- [x] Queries are effect-free for current/legacy peers, bounded and owned;
  missing/malformed/unavailable results, timeout and failed cleanup cannot
  admit handoff or silently trigger installation, retry or a second session.
- [x] No channel reaches resident or pane children. Repeated/concurrent later
  launches cannot reuse an expired offer after foreground completion.
- [ ] Cold/warm capability-query measurements classify every transient process
  and retain the zero persistent-forwarder requirement after attachment.
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
- [ ] Native write activity preserves quiet and absolute limits, strict
  settlement and truthful publication/health outcomes for opaque acquisition.

## Journal

See the [append-only execution journal](picker-launch-handoff-journal.md) for
review, source, release and qualification outcomes.
