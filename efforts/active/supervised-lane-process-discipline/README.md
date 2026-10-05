# Supervised-Lane Process Discipline (lock, orphan cleanup, logging)

- **Slug:** `supervised-lane-process-discipline`
- **Repo:** copilot-extensions (`plugins/agent-dispatch`)
- **Branch(es):** per-phase PRs off `dev`
- **Created:** 2026-10-05
- **Status:** Draft
- **Umbrella issue:** [#5301](https://github.com/ThomasMichon/copilot-extensions/issues/5301)
  (agent-dispatch: supervised-lane child processes duplicate across daemon
  restarts, with no lock, no logging, and no orphan cleanup)
- **Sub-issues:** _none yet_
- **Vision:** explicitly **not** a vision change — the one-process-per-
  managed-unit model this effort preserves is deliberate, documented intent
  in [`visions/plugins/agent-dispatch`](../../../visions/plugins/agent-dispatch/README.md)
  §Concepts/*The supervisor* ("each delegated to its own subprocess so a
  busy or failing unit never blocks its siblings or the master"), realizing
  the suite-wide *process-count-scales-with-services-not-sessions*
  guarantee for the daemon itself. The operator explicitly chose to scope
  this effort to fixing the duplication *within* that design rather than
  revising the vision toward a consolidated single-process model — see
  Journal for that decision.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Operator workstation | Sole implementer | a copilot-extensions worktree, per-phase PRs off `dev` |

## Coordination

Solo effort, single participant, no multi-agent branch topology.

## Guiding Intent

A managed supervised-lane child process (e.g. a `supervise --supervisor-id
<id>` instance) must be exactly one live process per id at all times, must
leave a diagnosable trace when it stops (crash or clean exit alike), and
must never be silently duplicated across a daemon restart. Today none of
that holds: duplication is possible, nothing protects against it, and
nothing logs it.

## Context

Surfaced live while diagnosing a stalled `file-picker-repro` queue on a
downstream harness's agent-dispatch fleet (same investigation that produced
`efforts/active/worker-status-observability-hooks/`, now merged). Direct
findings, confirmed by reading source and inspecting live process state —
not inferred:

- **Multiple processes with the identical `--supervisor-id <same-id>`** ran
  concurrently on the same machine: two alive since an earlier daemon
  generation, a third freshly spawned minutes later.
- **No lock file exists for any supervised-lane id** under the per-lane run
  directory (`~/.agent-dispatch/run/supervisor/.../*.lock*`) — confirmed
  empty. `single_instance.py`'s `SingleInstance` lock protects only the
  top-level `agent_dispatch serve` daemon (confirmed exactly one runs);
  nothing analogous exists for the managed children `supervisor_daemon.py`
  spawns.
- **No health/log file for a supervised-lane child** — unlike emitters,
  which get `<id>.emitter.health.json` (`ok`/`returncode`/`error`/
  `duration_seconds`). A dying supervised-lane child leaves zero trace; the
  only evidence this session had was catching a live PID and watching it
  disappear on a follow-up check seconds later.
- **The top-level daemon's own self-update handoff is careful and correct**
  (`supervisor_daemon.py`'s `_maybe_self_update`/`shutdown()`: drains
  in-flight work, releases its singleton lease, spawns a successor, only
  exits once that succeeds) — the gap is one layer down. Each managed
  child is tracked only in the parent daemon's **in-memory** `self._units`
  dict. A daemon restart (crash, a forced respawn via the
  `agent-machines-self-update-sweep`/`-watchdog` scheduled tasks, or a
  handoff that doesn't fully drain in time) loses that record entirely —
  the new instance reconciles desired state from the registrar and
  launches a fresh set of children with no check for still-alive orphans
  left behind by the dead parent.
- The observed stall **did** eventually clear on its own (one contender won
  out / orphans settled) — this is a transient, silent stall, not a
  permanent deadlock, but it recurs on every daemon restart and is
  undiagnosable without catching a live PID by luck.

Source pointers for implementation: `supervisor_daemon.py`
(`_maybe_self_update`, `shutdown`, `_launch_managed`, `reconcile_once`),
`single_instance.py` (`SingleInstance`, `lock_path_for`),
`supervise_cli.py`.

## Request

> Let's first focus on why we don't have visibility, and understanding the
> cause of the crash loop. Are the emitters failing?

> Yuck, we need to identify how these got in, and then durably fix this in
> the process-spawn locations, which are likely scheduled asks, auo-run
> binstub pointers, or the install/update flow, plus failure of old daemons
> from self-retiring. I expect there to be only *one* agent-dispatch
> daemon; I don't really want a process per loop, even. Emitters wind up in
> their own processes as needed, anyway. We should also get logging
> properly working.
(typo preserved verbatim: "auo-run" — read as "auto-run")

**Scope correction, operator-confirmed:** the "only one daemon, no process
per loop" part of the second quote conflicts with documented vision intent
(see Vision above) and was raised back to the operator rather than acted on
silently. The operator chose **(a)**: fix the duplication bug within the
current one-process-per-managed-unit design, not revise the vision toward
consolidation. This effort is scoped to (a); the rest of the Request above
(visibility, crash-loop cause, durable fix at the spawn locations, logging)
stands as originally asked.

## Plan

### Phase 1 — trace the actual (re-)spawn entry point(s)
- [ ] Identify every code path that can cause a supervised-lane child to be
      (re-)spawned on this machine: a Copilot CLI sessionStart hook, the
      `agent-machines-self-update-sweep`/`-watchdog` scheduled tasks,
      `supervise --ensure`-style idempotent calls, or the daemon's own
      `reconcile_once`/`_launch_managed`. This is the real "how did
      duplicates get in" question — a lock fixes the symptom; this
      identifies whether multiple independent triggers are racing each
      other in the first place.

### Phase 2 — per-managed-child single-instance lock
- [ ] Give each managed child (keyed by its registration/supervisor id) a
      `SingleInstance`-style lock analogous to the daemon's own, at a path
      that survives the *daemon's* restart (not merely in-memory
      `self._units` tracking).
- [ ] A restarting daemon must probe for a live holder before launching a
      fresh child for the same id, not assume a clean slate.

### Phase 3 — startup-time orphan detection and reconciliation
- [ ] On daemon start/restart, actively look for still-alive processes
      matching a previous generation's managed-child command line/lock and
      reconcile (adopt or terminate) them before reconciling desired state
      fresh — rather than relying solely on in-memory bookkeeping that
      resets on every restart.

### Phase 4 — supervised-lane child logging/health file
- [ ] Give supervised-lane children the same `ok`/`returncode`/`error`/
      `duration_seconds`-shaped health file emitters already get (or an
      equivalent), so a stall or crash is diagnosable without catching a
      live PID by luck.

## Validation Plan

- [ ] **Phase 1:** documented finding (which trigger(s) actually cause
      duplication) — this phase's deliverable is the trace itself, not a
      code change; subsequent phases' designs depend on its answer.
- [ ] **Phase 2:** automated test simulating two near-simultaneous launch
      attempts for the same managed-child id; exactly one acquires the
      lock, the other exits cleanly (or defers) rather than running
      duplicated.
- [ ] **Phase 3:** automated test simulating a daemon restart with a
      still-alive orphaned child from a previous generation; confirm the
      new daemon detects and reconciles it rather than launching a
      duplicate alongside it.
- [ ] **Phase 4:** automated test confirming a supervised-lane child's
      health file reflects a real crash (non-zero exit, error captured) the
      same way an emitter's already does.
- [ ] Dogfood against a live downstream repro-queue fleet if one is
      available during implementation, as supplemental evidence only.

## Proposal

_Pending — begin with Phase 1 (trace the actual spawn entry points)._

## Journal

### 2026-10-05 — Kickoff, scope decision
- Effort created from issue #5301, itself filed after live investigation
  during the same session that produced `worker-status-observability-hooks`
  (now merged).
- Initial issue draft asked for both the duplication fix *and* a
  consolidated single-daemon architecture ("no process per loop"). Checked
  the agent-dispatch vision before building an effort on that ask and found
  it conflicts with documented intent (*The supervisor*'s explicit
  one-subprocess-per-unit fault-isolation rationale). Raised this back to
  the operator rather than silently proceeding or silently dropping the
  architectural half of the ask.
- Operator chose scope **(a)**: fix the duplication within the current
  design. Issue #5301 rewritten accordingly before this effort was created,
  so effort and issue stay consistent from the start.
