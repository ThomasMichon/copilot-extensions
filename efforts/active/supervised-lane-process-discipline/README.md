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

Surfaced live while diagnosing a stalled repro-queue on a downstream
harness's agent-dispatch fleet (same investigation that produced
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

### Phase 1 — trace the actual (re-)spawn entry point(s), and diagnose the crash cause
- [ ] Identify every code path that can cause a supervised-lane child to be
      (re-)spawned on this machine: a Copilot CLI sessionStart hook, the
      `agent-machines-self-update-sweep`/`-watchdog` scheduled tasks,
      `supervise --ensure`-style idempotent calls, or the daemon's own
      `reconcile_once`/`_launch_managed`. This is the real "how did
      duplicates get in" question — a lock fixes the symptom; this
      identifies whether multiple independent triggers are racing each
      other in the first place.
- [ ] Diagnose why an observed supervised-lane child actually exits/crashes
      in the first place (not only how a duplicate gets spawned) — the
      original Request explicitly asked this ("are the emitters failing?";
      confirmed during investigation that emitters were *not* the cause).
      Capture the real exit reason for at least one reproduced case before
      moving to Phase 4's logging design, so that design is grounded in an
      actual observed failure mode rather than a generic shape.

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
- [ ] **Two-layer termination safety required if termination is chosen**
      (per `docs/patterns/graceful-daemon-cutover.md` point 5 and
      `docs/patterns/process-slot-ownership.md`'s fail-safe-defaults
      rationale — a discovery-time command-line/lock match is a snapshot,
      not proof at the moment of action; the PID can exit and be reused by
      an unrelated process in between):
      1. **Identity-bound termination** — re-verify the PID's identity
         token immediately before signaling via `agent-zdd`'s own
         `zdd.diagnostics.process_start_time`/`terminate_pid_if_identity`
         (already an `agent-dispatch` dependency, per
         `plugins/agent-dispatch/pyproject.toml`); refuse on any mismatch
         or uncertainty rather than guessing.
      2. **Owner validation** — a separate, higher-level check that the
         candidate is genuinely the orphan being reconciled, not merely an
         unrelated live process that happens to match superficially, via
         `zdd.diagnostics.audit_daemon_health`/`apply_daemon_health`,
         scoped to managed-child ownership rather than daemon routing.
      Reuse `agent-zdd`'s shared primitives directly, not a private
      reimplementation — per `docs/patterns/graceful-daemon-cutover.md`
      point 5, `zdd`'s diagnostics are a proper installable shared library
      meant for cross-plugin reuse (unlike a plugin-private module such as
      `agent_worktrees.locks`/`agent_worktrees.procs`, which is not).
      Managed-child *owner validation* stays agent-dispatch-specific logic
      built on top of those primitives, not duplicated low-level
      identity-token handling. Fail-safe default throughout: an ambiguous
      case leaves the candidate alone (bounded cost: a lingering idle
      process) rather than terminating it (unbounded cost: killing a live,
      in-flight unrelated process) — but "leave it alone" must not mean
      "silently stuck forever" **when the candidate is still genuinely
      live and holding Phase 2's singleton lock** (the owner-validation
      refusal case specifically): that keeps the restarted daemon from
      ever adopting or replacing it, preserving the lane outage
      indefinitely with no signal anyone needs to look. Surface that case
      as an explicit operator-visible blocked/unhealthy state (e.g. in the
      same health-file mechanism Phase 4 adds) naming the PID and why
      reconciliation refused it, plus a defined later retry-or-manual-
      repair seam — without ever relaxing the refusal to terminate
      unsafely. The identity-mismatch refusal case is different and must
      **not** report this same blocked state unconditionally: the OS
      releases the original holder's lock the instant that process exits
      (`libs/single-instance-lease`'s exclusive, non-blocking,
      kernel-held lock — see its own module docstring), so a reused PID
      means the lease is already acquirable again; reconciliation should
      simply retry rather than report a persistent block. Make the
      surfaced state conditional on the lease still actually being
      contended at the time of refusal, not on which refusal case fired.

### Phase 4 — supervised-lane child logging/health file
- [ ] Give supervised-lane children the same `ok`/`returncode`/`error`/
      `duration_seconds`-shaped health file emitters already get (or an
      equivalent), so a stall or crash is diagnosable without catching a
      live PID by luck. Ground the shape in Phase 1's actual observed crash
      cause, not a generic guess.

## Validation Plan

- [ ] **Phase 1:** documented findings — (a) which trigger(s) actually cause
      duplication, (b) the actual diagnosed cause of at least one observed
      supervised-lane child exit/crash (confirming or ruling out emitter
      failure as the cause, per the original Request). This phase's
      deliverable is the trace+diagnosis itself, not a code change;
      subsequent phases' designs depend on both answers.
- [ ] **Phase 2:** automated test simulating two near-simultaneous launch
      attempts for the same managed-child id; exactly one acquires the
      lock, the other exits cleanly (or defers) rather than running
      duplicated.
- [ ] **Phase 3:** the restart/reconciliation test is unconditional —
      simulate a daemon restart with a still-alive orphaned child from a
      previous generation; confirm the new daemon detects and reconciles
      it rather than launching a duplicate alongside it. **If termination
      is the chosen reconciliation path**, add dedicated, direct safety
      tests for that path specifically — not merely an end-to-end
      rehearsal (per `graceful-daemon-cutover.md` point 5): (a) a positive
      case — a genuine stale orphan is correctly identified and
      terminated; (b) an identity-mismatch refusal case — the discovered
      PID has since exited and been reused by an unrelated process;
      confirm termination is refused, not attempted; (c) an
      owner-validation refusal case — the identity token still matches but
      owner validation independently fails; confirm termination is refused
      here too, since identity matching and owner validation are separate
      required guards in the plan. (d) a blocked-state visibility case —
      for the owner-validation refusal (c) specifically, confirm the still
      genuinely-live candidate's held Phase 2 lock is surfaced as an
      explicit operator-visible blocked/unhealthy state (naming the PID
      and refusal reason), not merely silently left alone; (e) a
      non-blocked-after-PID-reuse case — for the identity-mismatch refusal
      (b), confirm the daemon does **not** report that same blocked state,
      since the OS already released the original holder's lease when that
      process exited and the lease is acquirable again — the daemon should
      simply retry reconciliation, not surface a persistent block for a
      race that already resolved itself.
- [ ] **Phase 4:** automated test confirming a supervised-lane child's
      health file reflects a real crash (non-zero exit, error captured) the
      same way an emitter's already does — using the actual crash shape
      Phase 1 diagnosed, not a synthetic one.
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

### 2026-10-05 — Plan PR #5309 review round (COMMENTED, 1 High + 1 Medium)
- **High:** Phase 3's orphan termination had no safety against a classic
  PID-reuse race (the discovered PID exits and an unrelated process reuses
  it before termination actually runs). Read the cited existing patterns
  (`docs/patterns/graceful-daemon-cutover.md` point 5,
  `docs/patterns/process-slot-ownership.md`'s fail-safe-defaults rationale)
  and found the shared `agent-zdd` library already solves this with a
  two-layer discipline (identity-token re-verification immediately before
  signaling, plus a separate owner-validation check) — `agent-dispatch`
  already depends on `agent-zdd`
  (`plugins/agent-dispatch/pyproject.toml`), so the Plan now specifies
  reusing `zdd.diagnostics`'s primitives directly rather than a private
  reimplementation, keeping only managed-child owner validation
  dispatch-specific, plus the dedicated positive/refusal safety tests the
  pattern doc requires (not just an end-to-end rehearsal).
- Medium: the original Request explicitly asked to diagnose the crash-loop
  cause ("are the emitters failing?" — confirmed during investigation they
  were not), but the Plan only committed to tracing *spawn* triggers, never
  committed to actually diagnosing *why* a child exits. Added that as an
  explicit Phase 1 validation obligation, and made Phase 4's logging design
  depend on Phase 1's real diagnosed cause rather than a generic shape.

### 2026-10-09 — Plan PR #5309 review round 2 (1 High + 2 Low)
- **High:** confirmed `agent-dispatch` already declares `agent-zdd` as a
  real dependency (`plugins/agent-dispatch/pyproject.toml`), and
  `graceful-daemon-cutover.md` point 5 requires reusing its
  `zdd.diagnostics` identity-bound termination and owner-validation
  primitives directly — the Plan's "not cross-plugin importable, needs its
  own analogous pair" claim was factually wrong (that describes a
  plugin-private module, not a proper installable shared library like
  `zdd`). Corrected Phase 3 and the prior journal entry to require direct
  reuse, keeping only managed-child owner validation dispatch-specific.
- **Low:** corrected the Validation Plan so the restart/reconciliation test
  stays unconditional regardless of whether termination is the chosen
  reconciliation path, with termination-specific tests conditional on that
  choice; added the owner-validation-fails-despite-matching-identity
  refusal case alongside the existing identity-mismatch refusal case.

### 2026-10-10 — Plan PR #5309 review round 3 (1 previously-missed Medium)
- **Medium:** the fail-safe "leave an ambiguous candidate alone" default
  had an unstated cost: it still holds Phase 2's singleton lock, so the
  restarted daemon can neither adopt nor replace it, silently preserving
  the lane outage with no signal to look. Added an explicit requirement
  that this state surface as an operator-visible blocked/unhealthy state
  (naming the PID and refusal reason, via the same Phase 4 health-file
  mechanism) plus a later retry/manual-repair seam, without relaxing the
  termination refusal itself. Added a matching Validation Plan case (d)
  requiring this visibility to be proven alongside whichever refusal case
  the daemon actually hits.
- The two remaining Low findings from round 1
  (discussion_r4182389155/r4182389102) were re-checked against the
  current text and both already match what they ask for verbatim (the
  restart test is unconditional with termination conditional, including
  the owner-validation refusal case; the zdd reuse instruction and journal
  correction are both in place) — their anchor lines no longer resolve in
  the current diff (GitHub reports `line: null`), consistent with a stale
  unresolved thread rather than a persisting content gap. Left as-is
  rather than guessing at further rewording with no new information.

### 2026-10-10 — Plan PR #5309 review round 4 (1 new Medium)
- **Medium:** round 3's blocked-state fix was unconditionally worded,
  which round 4 correctly flagged as misclassifying the identity-mismatch
  (PID-reuse) refusal case: `libs/single-instance-lease`'s lock is an
  OS-level, kernel-held, non-blocking lease released automatically the
  instant the original holder's process exits (see that library's own
  module docstring) — a reused PID therefore means the lease is already
  acquirable again, not still contended. Scoped the blocked/unhealthy
  surface to the owner-validation refusal case specifically (the one
  where the candidate is still genuinely live), and added an explicit
  Validation Plan case proving the identity-mismatch case does *not*
  report that same blocked state, since that race already resolved
  itself and the daemon should simply retry.

### 2026-10-10 — Plan PR #5309 review round 5 (1 previously-missed Low)
- **Low:** Context named a specific downstream repro-queue identifier
  (`file-picker-repro`) in this public effort — the same category of issue
  the sibling `worker-status-observability-hooks` effort already recorded
  fixing (generalize to "a downstream harness's ... repro-queue fleet",
  dropping the specific name while keeping the motivating context).
  Applied the same fix here: dropped the specific queue name, kept the
  "downstream harness's agent-dispatch fleet" context.
- The two remaining Low findings from round 1 were checked again; no new
  information since round 3's note, left as-is.

