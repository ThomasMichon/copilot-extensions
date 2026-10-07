# Agent-Process Self-Report Registry

- **Slug:** `agent-process-self-report-registry`
- **Repo:** copilot-extensions
- **Branch(es):** independent per-slice worktrees
- **Created:** 2026-10-06
- **Status:** Draft
- **Umbrella issue:** #5559
- **Sub-issues:** #5557 (mux-daemon stale-retirement bug, found while auditing
  for this effort) · #5558 (agent-dispatch worker-pool version-skew bug, same)

## Guiding Intent

Make "what agent-spawned processes are running right now, what are they, and
are they healthy/current" a single query instead of manual ps-based
archaeology. Today every long-lived daemon/singleton (`agent-dispatch serve`,
`agent-bridge start`, `agent-mcp bridge`, `worktree-manager mux-daemon`, and
whatever else adopts the pattern going forward) manages its own siloed
on-disk state in its own plugin-specific directory, with no shared,
consistent self-report. A unified, lightweight registry — written by each
process at startup and cleaned up on normal exit — would let one command
answer "how many of each agent-* singleton do we have, who owns each one,
and is anything stale/orphaned/version-skewed" across the whole fleet, on
any machine.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Driving agent | Design the registry contract + `agent-procutil` hook, land it, migrate one pilot consumer | `copilot-extensions` worktree |
| `agent-procutil` (`plugins/agent-worktrees/libs/agent-procutil/`) | Owns the shared self-report hook every consumer calls | shared library, already a near-universal dependency |

## Context

**The literal trigger:** while investigating a user-reported "we're
accumulating python.exe/pwsh/conhost processes" concern on Lambda-Core
(2026-10-06), diagnosing required manually dumping every `python.exe`/
`pwsh.exe`/`conhost.exe` command line via `Get-CimInstance Win32_Process` and
pattern-matching plugin names out of install paths, by hand. That
investigation found two genuine, already-filed bugs it would otherwise have
taken far longer to notice:
- **#5557** — a mux-daemon instance from 3+ versions back (`worktree-manager`,
  port 58547, started 2026-10-05) was still alive and bound to its port, long
  after `mux-daemon-routing/active.json` had moved on to generation 15 with no
  reference to it at all. Both of the daemon's own designed retirement paths
  (the new daemon actively killing the old one; the old daemon self-polling
  `is_superseded()` and retiring itself) apparently failed for this instance.
  Manually confirmed it was genuinely orphaned (not the active daemon, not
  referenced in `active`/`previous`) and killed it.
- **#5558** — all 6 `agent_dispatch supervise` worker pools and all 6
  `agent_dispatch emitter serve` watchers were running `v0.7.11-dev1`, while
  the main `agent_dispatch serve` coordinator had already moved on to
  `v0.12.5-dev1` — a five-minor-version skew, invisible without manually
  cross-referencing each process's own venv path segment.

Existing *partial* infrastructure this effort builds on rather than
replaces:
- `agent-procutil` (`plugins/agent-worktrees/libs/agent-procutil/`) is already
  a near-universal cross-plugin dependency (`agent-index`, `agent-vault`,
  `agent-bridge`, `agent-containers`, `agent-worktrees` itself,
  `agent-codespaces`, `agent-ssh`, `agent-mcp`, `agent-logger`,
  `agent-machines`, `agent-dispatch` all depend on it per their own
  `pyproject.toml`) — the natural place to add a shared self-report hook once,
  rather than re-inventing it per plugin.
- `agent-single-instance-lease` and `agent-work-coalescing-singleton` are
  existing shared libraries several plugins already use for their own
  locking/singleton semantics — this effort's registry is a complementary
  *reporting* layer, not a replacement for either's *locking* semantics.
- `worktree-manager`'s own `mux-daemon-routing/{active,cutover,
  zdd-cutover}.lock` + `active.json` + `lifecycle.log` is a good worked
  example of a domain doing its own structured self-tracking well (generation
  numbers, a JSON active/previous record, an append-only log) — just scoped
  to its own daemon only, not shared or queryable alongside anything else.
- `agent-dispatch`'s own `~/.agent-dispatch/run/supervisor/
  supervisor-<host>-<name>/declared-*.json` files track per-supervisor/
  emitter state, again scoped to its own domain only.

## Request

Operator (verbatim, from the diagnosis conversation): "We really need a way
to have our processes self-report: role, [h]ost plugin, Pid, owner
sessionid, cwd, worktree, etc. I wonder if there is a way or place do track
this reliably and with consistency" — followed by "All three" when offered
the choice to (a) investigate/fix the stale processes found, (b) file both
findings as tracked issues, and (c) scope this registry idea as a real
effort (this document is (c); (a) and (b) are already done — see Context).

## Plan

### Phase 1 — Design the self-report contract
- [ ] Decide the record shape: role (daemon kind, e.g. `mux-daemon`,
      `supervise`, `serve`), owning plugin + version, pid + process start-time
      token (to avoid PID-reuse false positives, same discipline
      `worktree-manager`'s own `_terminate_mux_daemon_pid` already uses), owner
      session id (when known), cwd, worktree id (when known), started_at.
- [ ] Decide the storage location + format: a single shared directory (e.g.
      `~/.aperture-procutil/registry/<pid>.json` or similar — exact home
      TBD, must work cross-platform) with one small JSON file per live
      process, versus a single append/compact log. One-file-per-pid is
      simpler to keep live-accurate (write on start, delete on clean exit)
      and trivially survives a crash (a stale file just means "check if this
      pid is still alive" — same reconciliation `agent-procutil` likely
      already needs for other purposes).
- [ ] Decide failure semantics: what happens to a record when the process is
      killed uncleanly (no delete). A periodic reconciliation sweep
      (liveness-checked by pid + start-time token) is likely needed regardless
      of storage shape — a write-once, stale-entries-never-expire registry
      would just become a second thing to leak.

### Phase 2 — Implement the shared hook in `agent-procutil`
- [ ] Add a small, dependency-free self-report helper (register-on-start,
      unregister-on-clean-exit, best-effort — never block or fail the
      caller's own startup if the registry write fails).
- [ ] A query/list command (`agent-procutil ps` or similar) that reads the
      registry and renders it — the direct fix for "how many of each agent-*
      singleton do we have" without hand ps-archaeology.

### Phase 3 — Pilot migration
- [ ] Adopt the hook in one real long-lived daemon first (candidate:
      `worktree-manager`'s mux-daemon, since it already has the richest
      existing self-tracking to migrate from) to prove the contract before
      asking every other plugin to adopt it.
- [ ] Journal what the pilot surfaced (a design gap in the record shape is
      cheaper to find in one plugin than after a wide rollout).

### Phase 4 — Wider rollout _(agent-recommended; not yet operator-requested)_
- [ ] Once the pilot's shape is proven, adopt the hook in the other daemon
      consumers this investigation found (`agent-dispatch serve` +
      `supervise`/`emitter serve`, `agent-bridge start`, `agent-mcp bridge`)
      and any others identified along the way.

## Validation Plan

- [ ] Confirm the query command correctly reports every live daemon the pilot
      plugin spawns, with the fields the operator asked for (role, plugin,
      pid, owner session id, cwd, worktree), cross-checked against a manual
      `Get-CimInstance`/`ps` sweep like the one that found #5557/#5558.
- [ ] Confirm a killed-uncleanly process's stale record is detected and
      reconciled (not left as a permanent false "running" entry) within one
      reconciliation cycle.
- [ ] Confirm the hook never blocks or fails a consumer's own startup if the
      registry write itself fails (a self-report mechanism must never become
      a new reliability dependency for the thing it's reporting on).
- [ ] Re-run the same kind of fleet-wide process sweep that originally found
      #5557/#5558 and confirm the registry alone (no manual command-line
      archaeology) surfaces an equivalent orphan/skew finding, if one exists
      at the time.

## Proposal

_Pending — Phase 1 design decisions above need to land here once settled._

## Journal

### 2026-10-06 — Kickoff
- Effort created from a live diagnosis session: auditing a reported
  process-accumulation concern surfaced two genuine bugs (#5557, #5558,
  investigated and filed in the same session; #5557's one still-orphaned
  instance was also manually killed) and the operator's own request for a
  unified self-report mechanism, captured above verbatim. Plan is Phase
  1-3 as operator-requested scope (design + shared hook + one pilot
  migration); Phase 4 (wider rollout) is marked agent-recommended since the
  operator's own request didn't specify adopting it everywhere yet.
