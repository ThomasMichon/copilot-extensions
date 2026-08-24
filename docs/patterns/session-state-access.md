# Pattern: session-state-access

**Serves:** *Vision picker* §Features/`programmatic-parity`,
§Behaviors/`live-not-snapshot` (the `--json` discovery substrate must stay cheap
enough to refresh live, at any history size).
**Exemplars:** agent-worktrees (session discovery for `list` and the Picker).

## Problem

agent-worktrees links each worktree to its Copilot session(s) so the Picker can
show titles, turn counts, activity, and a resume target. The session data lives
under the Copilot CLI's per-user state root (`~/.copilot/session-state/<id>/`),
**one subdirectory per session**. That directory is **append-only and unbounded**
— sessions accumulate for the life of the machine and are never pruned on the hot
path.

The naïve way to answer "which session is this worktree's latest?" is to **sweep**
that directory: iterate every subfolder and parse each `workspace.yaml`. A sweep
is **O(total sessions)** — and it parses YAML for *every session that ever ran*,
not just the ones that matter.

Two things turn that into a pathology:

- **The per-worktree multiplier.** `list` enriches **each** worktree. If the
  per-worktree enrichment sweeps, total cost becomes
  **O(worktrees × total sessions)** — the vicious multiplier. With dozens of
  worktrees and thousands of sessions, a single `list` burns seconds of pure-CPU
  YAML parsing and can hit its caller's timeout.
- **The periodic caller.** Any always-on consumer that re-invokes discovery on a
  schedule (a cross-machine discovery crawl, a cockpit refresh) pays that cost
  **repeatedly**, so a slow sweep doesn't just hurt once — it pins a core
  continuously and, under a command timeout, spawns a churn of killed-and-respawned
  workers.

The trap is subtle because the sweep usually hides as a **graceful fallback**:
"the registry has no entry for this worktree, so fall back to a full scan so the
row isn't blank." That fallback is exactly what makes an empty-registry worktree
detonate the multiplier on the hottest path.

## Standard approach

**The session registry is authoritative; sweeps are quarantined to backfill.**

1. **Resolve by exact session id, never by iteration.** The per-worktree registry
   (`WorktreeRecord.sessions`, populated by the `register-session` /
   `deregister-session` hooks as sessions come and go) lists the session ids that
   belong to a worktree. Every hot path — `list`, status, finalize, resume, title
   enrichment — random-accesses `~/.copilot/session-state/<exact-id>/` and reads
   only those directories. Cost is **O(this worktree's own sessions)**, with no
   directory iteration.

2. **An empty registry returns cheap, it does not sweep.** When a worktree has no
   registry entry (the hook never fired, or it predates the registry), the fast
   path returns *empty/None* — a blank-but-cheap row — rather than falling back to
   a full scan. A blank row is repaired out-of-band (next step), not by melting a
   core on the hot path.

3. **Repair is either explicit backfill or a bounded resident cursor.** The
   on-demand backfill (`backfill_sessions` / `backfill-sessions`, plus `doctor`)
   may walk the state root once in response to an explicit recovery request. A
   resident keeper may also hold one `scandir` iterator open and advance it by a
   fixed entry budget per sweep. It never restarts the walk per worktree or per
   read, and never parses more than its budget in one tick. The cursor populates
   missing registry entries over time; `list` and every other hot path remain
   exact-id-only.

4. **Enrich in a single pass.** When a scan *is* warranted (the sanctioned
   backfill, or the registry-driven fast scan), gather everything a caller needs
   in **one** traversal of the relevant directories — latest session id, summary,
   turn count, liveness — into a single context object. Never re-derive a
   per-worktree value (e.g. "latest session id") with a *second* pass while a
   first pass is already in flight.

5. **Use the C YAML loader for the one remaining scan.** The sanctioned backfill
   is the only O(sessions) path left; parse `workspace.yaml` with libyaml
   (`yaml.CSafeLoader` when available) so even the repair is fast.

## The invariant

> A **session-state directory sweep** — any iteration over the subfolders of the
> Copilot state root (`iterdir()`, `glob('*')`, `scandir`, `listdir`) — may occur
> **only** inside the explicit one-off backfill or the resident reconciler's
> fixed-budget, long-lived cursor.
>
> **Every other code path** resolves a session-state subfolder by **exact session
> id** (random access via the worktree session registry). No discovery,
> enrichment, status, finalize, or resume path may enumerate the state root to
> *find* sessions.
>
> A full immediate recovery sweep must be initiated explicitly. Normal
> operation may only make bounded incremental progress through the resident
> cursor; it cannot turn a single read or refresh into O(total sessions) work.

## Accepted tradeoff

A worktree whose `register-session` hook never fired has no registry entry, so it
renders **bare** (no title / turn count) until an on-demand `backfill-sessions`
(or `doctor`) repairs it. This cosmetic, self-healing gap is **deliberately
preferred** over a per-`list` O(total-sessions) sweep. Correctness of the
invariant beats momentary completeness of a row.

## Enforcement

- **Two choke points.** Session-state iteration lives only in
  `backfill_sessions` (explicit full recovery) and
  `ResidentSessionReconciler` (bounded resident cursor).
- **A regression guard.** A test asserts that no source outside the backfill path
  references `iterdir()` / `glob('*')` / `scandir` / `listdir` on the session-state
  root — so a future "helpful fallback" cannot silently reintroduce the sweep.
- **A review rule.** A change that adds a session-state sweep is only acceptable
  as part of `backfill`; anywhere else it fails this contract.

## Rationale

Discovery stays **O(worktrees)** instead of **O(worktrees × sessions)** by
construction — not by hoping the history stays small. The Picker's
`programmatic-parity` and `live-not-snapshot` promises depend on the `--json`
substrate being cheap enough to refresh on demand; an unbounded sweep on the hot
path breaks that promise the moment session history grows. Quarantining the one
unavoidable sweep to an explicit, rare, repair-only verb keeps the cost where it
belongs and makes "add a worktree" and "run for a year" both stay fast.

## Migration note

The legacy shape is the **empty-registry-falls-back-to-sweep** path (both the
per-worktree "latest session id" lookup and the batch session scan). Migrating
means: make the registry-first paths return cheap on a miss, redirect any
remaining sweep caller (e.g. finalize's title lookup) to an exact-id lookup, and
lean on `backfill-sessions` for repair. The storage of the registry itself
(per-worktree fragments today; a possible indexed store later) is **orthogonal** —
this invariant binds regardless of how the registry is stored: a store is never a
license to scan.

## See Also

- Intent: [`visions/picker/`](../../visions/picker/README.md) — the front door
  whose live, cheap `--json` substrate this pattern protects.
- Hub: [`docs/patterns/`](README.md) · Reality:
  [`../../plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)
  (session registry, discovery).
