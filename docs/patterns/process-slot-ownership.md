# Pattern: process-slot-ownership

**Serves:** *Vision agent-fabric* §Behaviors/**reclaim-idle-process** and
§Behaviors/**claimed-resource-not-reclaimed** — a background process is
memory-safe only when it is both single-occupancy (a role/slot never gains a
second live holder) and owner-tethered (its lifetime ends only once the
thing that justified it is *confirmed* gone, never while a live claimant
remains).
**Exemplars:** `libs/single-instance-lease` (vendored into agent-bridge,
agent-dispatch, agent-mcp, agent-vault, agent-worktrees) — `SingleInstance`
(the lease), `is_superseded` + `pid_alive` (the fail-safe supersession
decision), `reconcile_set_reap` (the reap-set backstop); the `libs/zdd`
generation self-retire loop and abandoned-passive reap
(`zdd.breadcrumb.reap_abandoned_passive`) vendored into the cutover-capable
plugins — agent-bridge, agent-dispatch, agent-index, agent-mcp, agent-vault
(agent-worktrees vendors `single-instance-lease` but not `zdd`; its
single-owner-slot exemplar is the token election below); agent-worktrees'
`status-updater` `@aw_updater` token election (the original proof of both
pillars together).

## Problem

Detaching a process (`DETACHED_PROCESS` / `setsid` / a job object) is
sometimes the *correct* choice — it stops a transient parent's exit/SIGHUP
from taking down a daemon that must outlive it, and it stops a child from
pinning a directory or venv lock the parent needs to release. But detaching
severs the one lifecycle signal the child had for free: its parent. Two
independent failure modes follow from that single severance, and a fix for
one does not fix the other:

1. **Nothing tells the child its reason for existing is gone.** A launcher
   shell survives the mux pane that spawned it; a passive daemon that a
   zero-downtime cutover promoted survives the cutover orchestrator that was
   supposed to retire its predecessor if that orchestrator crashed mid-flight.
   The process is alive, correctly detached, and permanently orphaned.
2. **Nothing stops a second spawn of the same role.** A status query, a
   crawl, or a daemon start that races a live equivalent has no way to notice
   it and defers to it — so callers pile up duplicate holders of the same
   slot instead of reusing or awaiting the one already running.

Both failure modes produce the same symptom fleet-wide: idle detached
sessions and stray daemons accumulating for hours to days, discovered only
as generic "why is my machine hot" / "why are there so many processes"
reports — far from the spawn site that caused them. The observed incident
that motivated consolidating this as one discipline: on a Windows+WSL dev
box, detached Copilot sessions piled up for 11–34h with no live mux (failure
mode 1), while agent-bridge separately re-spawned stuck `agent-worktrees`
status queries that never checked for an in-flight holder (failure mode 2).
Each was patched reactively, per component, before either was named as an
instance of the same underlying discipline.

## Standard approach

Treat every spawned process the way a memory-safe language treats an
allocation: **owned, tethered, and single-occupant per slot.** Two coupled
pillars, applied together — one alone leaves the other failure mode intact:

1. **Owner-liveness tether (lifetime, downward).** A detached process must
   track the liveness of its *logical owner* — not assume a parent-death
   signal it deliberately gave up — and drain and exit on its own once that
   owner is *confirmed* gone. "Confirmed" is load-bearing: an ambiguous read
   (can't tell, or momentarily dark) must never be treated as gone.
   - **Generation supersession** (`single_instance_lease.supersession
     .is_superseded`): a demoted daemon self-retires only when the routing
     table's `active` entry is a *different* pid, at a *strictly higher*
     generation, that is *actually listening* — every ambiguous case (no
     table, unparsable entry, our own pid still active, a not-higher
     generation, a successor that isn't yet accepting connections) returns
     `False` (stay alive). This is the daemon retiring *itself* from the
     inside.
   - **Abandoned-passive reap** (`zdd.breadcrumb.reap_abandoned_passive`):
     the complementary *outside* backstop for the case self-retire cannot
     cover — a passive daemon whose promoting cutover died before ever
     flipping the routing table, so the passive never observes its own pid
     as `active` and its self-retire gate never arms. A durable breadcrumb
     recorded at spawn time (before the health gate, flip, or drain begin)
     lets a later sweep reap it once aged past a grace window, without
     disturbing a genuinely in-flight cutover.
   - **Reconcile-set reap** (`single_instance_lease.reaper
     .reconcile_set_reap`): after a successful cutover (and on start), the
     newly-promoted daemon retires every one of the service's *own*,
     positively-identified pids that is neither the routing table's `active`
     nor itself — the general backstop across repeated cutovers and plain
     restarts, not just the one predecessor a single cutover names.
2. **Single-owner slot + debounce (spawn discipline, upward).** Before
   spawning, a spawner checks whether a live holder already occupies the
   role/task-keyed slot; if so, it reuses, awaits, or debounces that holder
   instead of piling on a duplicate. Only a *confirmed-dead* holder is
   reclaimed — never a merely-slow one.
   - **OS-level exclusive lease** (`single_instance_lease.lease
     .SingleInstance`): an advisory OS byte-range lock (`flock`/`msvcrt
     .locking`), held for the process's life, keyed on a lock file (plus an
     optional port so an active/passive pair can coexist during a cutover by
     binding different ports). This is **liveness-reconciled by
     construction** — the kernel releases the lock the instant its holder
     dies by any path (graceful exit, crash, kill, power loss) — so there is
     no stale-lock state to detect or reclaim, unlike a PID-file heuristic.
   - **Token election** (agent-worktrees' `@aw_updater`): for slots that
     aren't "one process, one lock file" but "one *logical role* among many
     candidate processes" (e.g. which of several sessions serves a shared
     mux pane's status), an atomic token write elects exactly one occupant;
     every other candidate observes it already taken and defers.

## Rationale

Owner-tether and single-owner-slot solve different halves of the same
problem and must both be present, deliberately, anywhere a process is
detached. Fixing only the tether still lets duplicate spawns of the same
role pile up (each individually tethered, still redundant and pinning
resources concurrently); fixing only the slot still lets an orphaned single
occupant run forever once its cutover or owner dies mid-flight. Extracting
both as one shared library (rather than reimplementing the primitives per
plugin) matters concretely: a real correctness bug was found and fixed
exactly once, centrally — `single_instance_lease.supersession.pid_alive`'s
Windows branch had treated *any* `OpenProcess` failure as "dead", including
access-denied (which actually proves the pid exists), silently violating its
own documented fail-open contract — and the fix propagated to every
consumer instead of needing rediscovery in each one independently.

Fail-safe defaults are the load-bearing property throughout: every ambiguous
observation resolves toward *not* acting (stay alive, don't reap, don't
supersede) rather than toward the destructive branch, because the cost of a
missed reap (a lingering idle process) is bounded and cheap, while the cost
of a wrong reap (killing a live, in-flight owner) is not.

## See Also

- [ephemeral-process-reaping](ephemeral-process-reaping.md) — the
  independent-reaper discipline for detached *helper* processes generally;
  this pattern specializes it for the **role/slot** case, where a second
  concurrent holder of the same role is itself the failure to prevent, not
  just an unreaped stray.
- [graceful-daemon-cutover](graceful-daemon-cutover.md) — the zero-downtime
  active/passive cutover (`zdd` routing-table flip + drain at a safe point)
  that this pattern's generation-supersession and abandoned-passive reap are
  the self-defense layer for; that doc owns the cutover's happy path, this
  one owns what happens when it doesn't complete cleanly.
- [work-coalescing-singleton](work-coalescing-singleton.md) — the sibling
  discipline for *cheap, idempotent, shareable work* (coalesce many callers
  onto one warm worker); this pattern is about *roles that must have at most
  one live occupant*, a related but distinct property from work coalescing.
