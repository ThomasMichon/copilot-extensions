---
name: troubleshooting-agent-dispatch
description: Diagnose a stalled, stuck, or silently-starved agent-dispatch task queue -- queued tasks that never claim/spawn, a dead-lettered task, a self-exclusion that permanently strands a task, a supervisor that reports healthy but does nothing, or "no logs anywhere to diagnose with". Use when asked to "troubleshoot agent-dispatch", "agent-dispatch isn't spawning anything", "task stuck queued", "supervisor stalled", "dead-lettered task", "rearm spawn", "carried session confirmed gone", "clear a task exclude", "agent-dispatch has no logs", or "diagnose a stuck dispatch lane".
---

# Troubleshooting agent-dispatch

Diagnose from the outside in: **daemon liveness -> reservation history -> the
task's own fields -> the log files**. Never guess from `list`/`show`/`doctor`
alone -- each of those omits exactly the state this skill exists to surface.

## Quick checks

> **Before you start — use the payload-local session commands.**
> The agent-dispatch, agent-bridge, and agent-mcp session command catalogs
> each supply an exact `argv[0]` owned by that plugin's own payload. Replace
> `<agent-dispatch catalog argv[0]>` / `<agent-bridge catalog argv[0]>` /
> `<agent-mcp catalog argv[0]>` below with those paths; never search `PATH` or
> substitute a same-named command from another payload. In PowerShell, invoke
> a catalog path as `& "<agent-dispatch catalog argv[0]>" <args>`.

```bash
<agent-dispatch catalog argv[0]> doctor --check-live-sessions   # ground truth ONLY for started/claimed tasks
<agent-dispatch catalog argv[0]> supervise --repo <lane> --label <L> --max-concurrent N \
  --max-attempts N --headless-agent <A> --no-reactive --once   # the single most
  # useful diagnostic: prints dead-letter summaries doctor/list never surface
<agent-dispatch catalog argv[0]> reservations list --task <id>              # full reservation history
<agent-dispatch catalog argv[0]> reservations list --state failed            # every dead-lettered attempt
<agent-dispatch catalog argv[0]> show <task_id>                               # excludes/hold_reason/spawn_reservation
```

`<agent-dispatch catalog argv[0]> doctor --check-live-sessions` only checks
worktree existence for tasks already `started`/`claimed` -- it says nothing
about a `queued` task that never gets picked up, and it can report "healthy"
while the supervised lane itself has stalled. Use
`<agent-bridge catalog argv[0]> status <session>` /
`<agent-bridge catalog argv[0]> live-sessions` for ground truth on a
suspected-dead body instead of trusting a task's recorded status alone.

---

## Symptom -> cause -> action

| Symptom | Likely cause | Action |
|---|---|---|
| Queued tasks never get claimed/started; `daemon-status`/`supervise daemon-status` reports the lane `"running": true` the whole time | **Stalled supervised-lane subprocess.** The outer daemon only checks that each declared unit's *process* is alive, not that its own internal claim loop is still ticking -- a wedged inner loop looks identical to a healthy one from the outside. | Restart via the sanctioned path: `install.ps1 -Action update` (graceful cutover, retires every prior supervisor generation, no in-flight work killed). **Never kill a supervisor process by hand** -- `supervisor_health.py`'s own stall detector says exactly this. |
| `<agent-dispatch catalog argv[0]> supervise ... --once` prints `N spawn-dead-lettered task(s)`, but `list`/`show`/`doctor` show nothing wrong | A task hit `--max-attempts` failed spawn reservations and is held out of auto-retry -- a **reservation-history** guard, not a task-status change (the task stays `queued`, never becomes `dead_letter`). Nothing else surfaces this; you must run `supervise --once` (or read the supervisor log, see below) to see it at all. | `<agent-dispatch catalog argv[0]> reservations list --task <id> --state failed` to see why each attempt failed, then `<agent-dispatch catalog argv[0]> reservations rearm <id> --permit --reason "<why, citing the fixed root cause>"` (requires >= `min_failures`, default 3; the task must be queued and unowned with no active reservation). |
| A rearmed task **immediately** dead-letters again, every time against the exact same `local-body:<id>` / `fleet-body:host:id` session handle in its failure `detail` | **Carried-session bug** (copilot-extensions#4978, fixed in #4990 -- confirm your `<agent-dispatch catalog argv[0]> status` version includes it). `reserve_spawn` carries the most recent reservation's `session_handle` forward (across *every* task sharing the same `exclusive_key`) as a resume candidate, and only drops it once that reservation is marked `release_requested=1` in a terminal state. Pre-fix, `fail_spawn`/`settle_spawn`/`rearm_spawn` never set that flag, so a confirmed-gone body's handle got carried and re-resumed forever. | Update agent-dispatch (`install.ps1 -Action update`) to a build with #4990. If still on an older build and you need to unstick a task *now*: confirm via `<agent-dispatch catalog argv[0]> reservations list --task <id>` that recent `detail`s repeat the same session handle, then as a last resort directly mark that reservation's row `release_requested=1` in `tasks.db` (back up the DB first; this is a stopgap, not a substitute for updating). |
| A task's `excludes` permanently contains `machine:<the-lane's-only-permitted-machine>` (or any token that excludes its only viable target) and it never gets claimed again | **Self-exclusion with no undo.** `yield --exclude`/`--exclude-self` appends a durable token with no symmetric way to remove it before copilot-extensions#4966. | `<agent-dispatch catalog argv[0]> unexclude <task_id> [--exclude <token>]` (omit `--exclude` to clear every exclusion). Needs a build including #4966; confirm `unexclude` appears in `<agent-dispatch catalog argv[0]> --help`. |
| A task sits `queued` with a non-null `hold_reason`/`hold_actor` long after whatever blocked it was actually fixed | **Stale operator hold** (Phase 1's "Pause" primitive) outlived its root cause -- holds are never auto-cleared. | `<agent-dispatch catalog argv[0]> show <task_id>` to read the hold reason, confirm the cause is actually fixed, then `<agent-dispatch catalog argv[0]> unpause <task_id> --actor <you>`. |
| Nothing shows up in any log file at all, even for an obviously-misbehaving supervisor | Pre-copilot-extensions#4990, **no logging handler was ever configured anywhere in the package** -- every `log.info`/`log.warning`/`log.exception` call was silently dropped, and both daemons run headless (`pythonw.exe`, no console) with nowhere for the logging module's stderr fallback to go. | Update to a build with #4990, then read `<install_dir>/logs/coordinator.log` and `<install_dir>/logs/supervisor.log` (`<install_dir>` defaults to `~/.agent-dispatch` -- `install_paths.install_dir()` resolves the override-aware path). Still nothing? Confirm the daemon actually restarted after the update (`<agent-dispatch catalog argv[0]> status`) -- a stale pre-fix process keeps running with no handler until it's cycled. |
| A headless worker reports something like `Unsupported native sessions host effect custom_agent_prompt` and self-releases or yields | A **host-level limitation in that specific embodiment**: some headless ACP sessions cannot invoke a custom sub-agent (`@name`) at all. This is not an agent-dispatch bug to fix here -- it's a capability gap the *worker identity* driving the task must route around. | The task's own worker-identity/prompt should fall back to a non-sub-agent path for that capability (e.g. `<agent-mcp catalog argv[0]> materialize <bridge> --no-serve` projects an MCP tool catalog into standalone CLI stubs invocable directly, bypassing the blocked delegation). If the identity has no such fallback documented, that's a gap in the *consuming repo's* worker-identity doc, not in agent-dispatch. |
| A task is `queued`, `exclusive_key` is set, nothing is held/excluded/dead-lettered, and a `reservations list --state reserving,spawned,releasing` shows a genuinely fresh `reserving` row for a *different* task on the same `exclusive_key` | Working as designed: `max_concurrent` (often 1 for a "one headed browser at a time" lane) means only one task on that `exclusive_key` spawns at a time. | Not a bug -- wait for the in-flight attempt to resolve (succeed, fail, or dead-letter) before expecting the next task to start. |

---

## Reading `reservations list` output

Every row matters more than it first looks:

- `state` -- see the state-machine table in `docs/spawn-supervisor.md`
  (`reserving -> spawned -> settled`, with `cold`/`releasing`/`failed`/
  `rearmed` as the recovery/retry states). Only `release_requested=1` **and**
  a terminal state (`settled`/`failed`/`rearmed`) retires a carried session.
- `detail` -- the human-readable reason; repeating identical text/session
  handles across consecutive `attempt`s is the carried-session tell.
- `session_handle` -- `local-body:<id>` (this host's agent-bridge session),
  `fleet-body:<host>:<id>` (a remote pool host), or `script-<id>` (a plain
  subprocess worker). Cross-check liveness directly:
  `<agent-bridge catalog argv[0]> status <id>` (local) rather than trusting
  the reservation's own last-known state.
- `worktree_ownership` -- `created` (this reservation owns worktree
  teardown), `reused`/`targeted` (carried from elsewhere; never delete it
  based on this reservation alone).

## See also

- [../../docs/spawn-supervisor.md](../../docs/spawn-supervisor.md) -- the full
  reservation state machine, the supervisor loop's per-cycle steps, and the
  `release_requested`/logging notes this skill's table summarizes.
- [../../docs/entity-relationship-model.md](../../docs/entity-relationship-model.md)
  -- the suite-wide diagnostic playbook: given a task id, which command
  resolves its worktree/session/bridge state.
- The main `agent-dispatch` skill's "Gotchas" section for everyday (non-stuck)
  usage pitfalls -- this skill is specifically for *stuck/silent* failures.
