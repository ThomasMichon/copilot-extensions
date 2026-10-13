# Status-monitor singleton correctness — Vision

- **Subject:** agent-worktrees' resident `status-monitor` process — the
  guarantee that at most one actively-serving instance exists per host
  across every lifecycle transition, with bounded zero-coverage windows,
  and exactly one serving instance at healthy steady state while service is demanded.
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-12
- **Reality docs:** `plugins/agent-worktrees/docs/architecture.md`,
  `plugins/agent-worktrees/src/agent_worktrees/status_monitor_cli.py`,
  `plugins/agent-worktrees/src/agent_worktrees/status_monitor_runtime.py`

## Purpose & Intent

The resident `status-monitor` is the host-wide accelerator the parent
[agent-worktrees vision](../README.md#derived-status)
asserts must be "exactly **one** per host regardless of how many
worktrees, sessions, or CLI invocations reach it." That sentence states the
steady-state guarantee while resident service is demanded. Intentional idle
shutdown when no demand remains is healthy and may leave zero residents;
this vision specializes the guarantee across the
process's **transitions**, not just while it sits idle serving readers.
A resident process is never static — it is challenged by a cold start
racing an existing incumbent, superseded by a newer runtime after a
mux/muxless change, restarted in place by an auto-update cutover, or
promoted/demoted as the worktree topology it serves changes. Every one of
those transitions is a moment where two processes can briefly believe they
are each the one true resident, or where the one true resident can be torn
down before its replacement is actually ready to serve.

The should-be is simple to state and hard to realize: **at every instant,
including mid-transition, at most one `status-monitor` process is actively
serving for a given host, and a transition never leaves a window where
zero processes are serving for longer than a strictly bounded, intentional
duration while service is demanded.** Exactly one serving resident is the
healthy demanded steady state; zero residents is healthy after demand ends.
This is not a promise that startup or handoff can never pass through zero.
Exclusivity is not a cold-start-only property bolted onto an otherwise
best-effort lifecycle — it is a property the *entire* lifecycle upholds,
with cold start, replacement, restart, and promotion/cutover all held to the
same standard.

## Concepts & Components

### The hard exclusivity backstop

A single, OS-mediated mutual-exclusion primitive (a named lock/mutex an
operating system itself arbitrates, not a file whose presence is merely
checked-then-trusted) is the one mechanism every process attempting to
become *the* resident must win before it does anything else — including
before it reads or writes the softer metadata/lock-file bookkeeping below.
Losing this race is not an error condition to recover from; it is the
losing process's signal to exit immediately and cleanly, on the
expectation that the process which won is already serving or about to.
A legitimate replacement coordinates ownership transfer with the
incumbent; losing the cold-start lease alone must not abandon a required
replacement indefinitely.

### The soft metadata and supersession layer

Beneath the hard backstop sits the metadata-file bookkeeping
(lock files, published addresses, liveness markers) that readers actually
consult to find and trust the current resident, and that a resident uses
to recognize when it has been legitimately superseded (a newer runtime
epoch, a mux/muxless change) and should retire gracefully rather than
fight for exclusivity it no longer should hold. This layer answers "who is
the current resident and is it trustworthy," while the hard backstop above
answers only "is more than one process trying to hold that role right
now." Neither layer substitutes for the other.

### Lifecycle transitions as first-class subjects

Four transitions are the concrete subjects this vision holds to one
standard, not four separate problems with their own bar:

- **Cold start** — a process starts with no resident believed to exist yet.
- **Replacement** — a newer-runtime process supersedes an older one serving
  a now-stale muxless/runtime epoch.
- **Restart** — an in-place auto-update cutover retires the running resident
  and spawns its replacement from the newly installed slot.
- **Promotion/cutover** — the set of worktrees or the topology a resident
  serves changes, requiring a handoff of responsibility rather than a
  simple start or stop.

## Features

### atomic-exclusivity-at-every-transition
Every one of the four transitions above acquires the same hard exclusivity
backstop before taking any action that assumes sole ownership, not only
the cold-start path.

### bounded-zero-coverage-window
A transition may have a brief window where no process is yet serving (the
old one has retired, the new one has not yet published), but that window
is explicit, intentional, and bounded — never an open-ended wait on a
non-exit-aware liveness check, and never silently indefinite.

### no-orphaned-metadata-across-a-transition
A process never removes or overwrites another, still-legitimately-live
process's published metadata as a side effect of its own transition —
metadata cleanup is scoped to the exact identity a process itself
previously published, never "whatever happens to be in the well-known
location right now."

### graceful-loss-is-silent-and-prompt
A contender that never acquired ownership loses the exclusivity race and
exits promptly without retrying or treating ordinary contention as an error.
An incumbent relinquishes ownership only through the coordinated safe drain
boundary: admitted work drains within a bounded deadline before retirement,
without overlapping the successor's active service. Drain failure is reported
and handled explicitly rather than discarding in-flight work. Each process
cleans only metadata proven to belong to its own identity.

## Behaviors

### never-two-live-residents
At no instant do two processes both believe themselves to be, and both
act as, the serving resident for the same host.

### never-an-unbounded-coverage-gap
A transition's zero-resident window is bounded by a known, short deadline;
exceeding it is a detectable, reported condition (deferred/retry), never a
silent, indefinite stall that readers mistake for a healthy steady state.

### exit-awareness-not-existence-checking
Any logic that waits for a predecessor process to be gone before acting
confirms that the identified predecessor has terminated and can no longer
execute or serve, not merely whether its PID answers an existence probe.
A terminated zombie has exited and is non-serving even while its process
record remains unreaped; reaping that record is resource cleanup, not a
prerequisite for replacement admission. An unreaped record is not proof of
active service, and a recycled PID must not be mistaken for the predecessor
or used to infer its continued liveness. Identity-bound exit evidence and
the exclusivity backstop govern admission; uncertainty is bounded and
reported, never resolved by permitting two active servers or waiting
indefinitely for a terminated process record to disappear.

### identity-scoped-cleanup
Any cleanup of shared metadata is conditioned on the specific identity
(PID, lease token, or equivalent) the cleaning process itself owns or
definitively confirmed to be stale — never applied unconditionally to
whatever occupies a well-known path, which could belong to a new,
legitimate occupant that published during the transition.

### a-reader-never-observes-a-false-resident
A reader consulting the metadata/supersession layer is always able to
tell the difference between "no resident currently holds exclusivity" and
"a resident holds exclusivity but hasn't yet published fresh metadata" —
it never treats stale or orphaned metadata as proof a resident is live.

## Non-Goals / Boundaries

- **Not a cross-host authority.** This vision inherits the parent vision's
  "exactly one per host" scope exactly; it does not introduce or imply any
  coordination between hosts.
- **Not a replacement for the metadata/supersession layer.** The hard
  exclusivity backstop is additive — it closes races the softer layer
  cannot close alone, but the softer layer's job (identifying and trusting
  the current resident, recognizing legitimate supersession) remains its
  own.
- **Not a statement about what triggers a transition.** Why a replacement,
  restart, or promotion happens (auto-update policy, mux/muxless detection,
  topology changes) is out of scope here; this vision is only about what
  must hold true *while* a transition of any origin is in flight.
- **Not a performance or latency target.** A bounded coverage window is a
  correctness property (it must not be unbounded), not a tuned SLA.

## See Also

- Parent vision: [plugins/agent-worktrees](../README.md), specifically
  *The resident daemon as the authoritative live-state database* and
  *Derived status*'s "exactly one per host" guarantee this vision specializes.
- Child visions: none (leaf).
- Reality docs: `plugins/agent-worktrees/src/agent_worktrees/status_monitor_cli.py`,
  `plugins/agent-worktrees/src/agent_worktrees/status_monitor_runtime.py`,
  `plugins/agent-worktrees/docs/architecture.md`.
- Implementation campaign:
  [Status-monitor singleton correctness](../../../../efforts/active/status-monitor-singleton-correctness/README.md).

## Provenance

- **2026-10-06 — Inception.** PR #5412 and issues #5453 / #5512 ground
  the lifecycle distinction: ordinary-cold-start serialization does not
  establish safe replacement, restart, or promotion/cutover. One coherent
  standard covers all four transitions rather than independent point fixes.
  The [effort Journal](../../../../efforts/active/status-monitor-singleton-correctness/README.md#journal)
  preserves the creation and planning history.
