# Pattern: ephemeral-process-reaping

**Serves:** *Vision plugin-services* — resource hygiene underpins every
lifecycle tier; an unreaped background process is the same availability/
host-health failure whether or not it is a registered "service".
**Exemplars:** agent-worktrees (per-worktree `git fsmonitor--daemon`, the
`status-monitor`'s `pane_reaper.py` dead-mux-pane reconciliation, `gc.py`'s
orphan-directory sweep).

## Problem

A plugin frequently needs a helper process (or a per-worktree/per-task
resource) that must outlive its immediate invocation — specifically, it must
survive the parent shell/session ending, an SSH connection dropping briefly,
or environment variables that only exist for one invocation.
`DETACHED_PROCESS` / `start_new_session=True` / `git ... --detach` solve
exactly that survival problem. But survival and reaping are two different
obligations, and detaching only ever solves the first. Once the reason the
process exists goes away — the worktree it served, the session that owned
it, the task it was running — nothing sweeps it up unless the plugin builds
that in on purpose. This is not a rare edge case: it is the **default**
outcome, because a detached process is, by construction, unreachable through
the parent's own exit/cleanup path that would otherwise have caught it.

This recurs because each layer looks like it solves it and doesn't:

- **A graceful-shutdown hook** (`sessionEnd`, `atexit`, a signal handler)
  looks like a sufficient reap trigger. It is not: it never runs on an abrupt
  kill (Windows tearing down a terminal window and its whole process tree at
  once, `Stop-Process`, a crash, an OOM kill) — exactly the situations
  detaching was meant to survive in the first place.
- **A preservation-oriented lifecycle command** (`finalize`/`cleanup`, or
  anything whose job is "mark this unit of work concluded while keeping its
  state inspectable" — see
  [service-lifecycle-supervision](service-lifecycle-supervision.md) and
  [drop-in-registry-hygiene](drop-in-registry-hygiene.md)) is the wrong place
  to *also* own process teardown. Conflating "concluded" with "kill its
  background helper" either blocks legitimate preservation or silently drops
  the teardown obligation whenever preservation wins.
- **It is invisible day-to-day.** Each single leaked process is small (a few
  MB, near-zero CPU), so nothing alarms until dozens-to-hundreds accumulate
  across a fleet of worktrees/sessions built up over weeks — at which point
  it presents as generic "why is my machine hot / why are there so many
  processes", far from the code that caused it.

Recent incidents in this repo: agent-worktrees' per-worktree `git
fsmonitor--daemon` leaked across every worktree that ever ran a git command,
un-reaped by `finalize` (#2265) — and still un-reaped after that sessionEnd
fix whenever the session died abruptly instead of exiting cleanly
(#2269/#2270). The same shape motivated `gc.py`'s orphan-directory sweep
(#66, #828, #1027) and `pane_reaper.py`'s dead-mux-pane reconciliation.

## Standard approach

**Every detached process needs an independent reaper, not a cooperative
one.** Design the reap trigger around the real signal that the process's
reason for existing has ended — not around a notification that may or may
not arrive:

1. **Identify the owning unit and its real liveness signal(s).** A
   worktree's liveness is its mux session membership *and* whether any of
   its registered Copilot sessions still holds a live PID lock
   (`sessions.worktree_has_live_session`) — not "did a hook fire". A
   registered service's liveness is its own lease/lock file. Pick the
   signal(s) that stay true or false correctly regardless of *how* the
   owning unit died.
2. **Poll, don't only hook.** Keep a hook-based reap as the fast path for the
   clean-exit case (cheap, worth having) but pair it with a periodic,
   liveness-based sweep that requires no cooperation from the dying process
   at all. This repo already runs exactly such a resident sweep — the
   `status-monitor`'s bounded per-record reconciler
   (`session_catalog.ResidentSessionReconciler`) — extend it rather than
   inventing a second poller.
3. **Corroborate before acting.** A single stale/racy signal (an aged mux
   snapshot, a lock file not yet cleaned up) is not enough to reap on its
   own. Require an *authoritative, fresh* observation of the primary signal
   (a live→dark **transition**, not merely "currently dark") and cross-check
   a second, independent signal before tearing anything down — see
   `ResidentSessionReconciler._index_record`'s mux-transition + PID-check
   pairing.
4. **Make the reap itself idempotent and silent.** Stopping an
   already-stopped process, or reaping a resource whose owning directory is
   already gone, must be a normal, silent no-op — never an error the sweep
   has to special-case (`tracking.stop_fsmonitor_daemon`: missing git,
   missing daemon, and a since-removed worktree directory are all fine
   outcomes).
5. **Never fold this into a preservation-oriented lifecycle command.**
   `finalize` (or any "mark concluded, keep inspectable" command) must not
   also own killing a companion process. Give the reap its own trigger (hook
   + poll), decoupled from whether the unit is ever finalized at all.

## Rationale

Detaching a process solves exactly one problem — surviving the parent's
exit — and, by construction, removes the parent's own ability to clean it up
afterward. Treating reaping as "someone else's problem" (a hook, a later
`finalize`) is how it silently becomes nobody's problem. A liveness-signal
poller costs nothing when nothing has died, and is the only mechanism that
reliably fires on every death path — clean exit, abrupt kill, and crash
alike.

## See Also

- [service-lifecycle-supervision](service-lifecycle-supervision.md) —
  registered, supervised daemons; this pattern's polling reaper is the
  complement for ephemeral, per-unit-of-work helper processes a plugin spawns
  outside that supervision model.
- [windows-background-process-launch](windows-background-process-launch.md)
  — how to detach/launch invisibly in the first place; this pattern picks up
  immediately after, on the teardown side.
- [drop-in-registry-hygiene](drop-in-registry-hygiene.md) — the same
  "surface it, don't silently drop it, but don't let cleanup masquerade as
  correctness" discipline, applied to `*.d` registry entries instead of
  processes.
- agent-worktrees: `session_catalog.py` (`ResidentSessionReconciler`),
  `tracking.stop_fsmonitor_daemon`, `pane_reaper.py`, `gc.py`.
