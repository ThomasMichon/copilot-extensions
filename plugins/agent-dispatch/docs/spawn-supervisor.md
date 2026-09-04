# agent-dispatch — Embody Spawn Supervisor (design)

Commands in this design that launch the persistent supervisor, scheduler, or
remote worker are explicit management boundaries and intentionally use the
global compatibility wrappers. Interactive task operations should instead use
the exact `argv[0]` from the agent-dispatch session command catalog.

Status: **in progress** — the spawn-reservation primitive, the supervisor loop
(spawn-at-most-once), the liveness-gated lease heartbeat, **confirmed-gone
auto-recovery** (local CLI worktree bodies, local headless bodies, and headless
fleet bodies), **nudge-before-recover**, push-accelerated fixed-interval
reconciliation, and **fleet dispatch (a health-gated remote embody pool, Model
C)** are built. Label-scoped
**disposable local-worker conclusion** is also built (the configuration field
retains its legacy `disposable_cli_labels` name): a registration may opt
selected CLI or local headless ACP worker classes into safe terminal session
conclusion and managed-worktree GC priming without deleting the worktree
directly. The
backlog-catch-up policy, authenticated container transport, per-host fleet
concurrency caps, and load-aware pool selection land in follow-up slices.
Public trackers: [#44](https://github.com/ThomasMichon/copilot-extensions/issues/44)
(supervisor) · [#49](https://github.com/ThomasMichon/copilot-extensions/issues/49)
(fleet dispatch).

This note is the design of record for turning a **queued task** into **exactly
one host-side embody autopilot session**, durably and idempotently. It realizes
the [agent-fabric](../../../visions/agent-fabric/README.md) vision's
**delegation layer** — specifically `delegate-and-hand-off` (work is delegated to
a spun-off agent with a shared record) and `recover-not-lose` (an interrupted
spawn is reconciled, never silently double-run or lost).

## The problem

`agent-dispatch create --spawn` (and any watcher that "sees a queued task and
runs `embody`") has a fatal gap for an **autonomous PR-authoring** trigger:

- The queue's claim/dedup is **transactional** (a single-writer SQLite
  `BEGIN IMMEDIATE`).
- The **embody spawn is a separate, non-transactional step** (`agent-worktrees
  embody` in a subprocess).

Between *observing* a spawn-eligible task and *actually spawning* it there is an
open window. A crash, a re-poll, or any later re-surface of the same queued task
in that window **double-spawns** (two autonomous sessions competing on one task,
opening rival PRs) or **loses** the spawn. Concretely, `create --spawn` on a
colliding `dedup_key` returned the existing task but still invoked spawn a second
time.

"Usually once" is unacceptable precisely because the side effect is autonomous.

## The primitive: an atomic spawn reservation

A **spawn reservation** is an atomic record, distinct from the execution claim,
that guarantees **exactly one embody spawn per (task, attempt)**. A task may
also carry an `exclusive_key`, which extends the same guarantee across task ids
that represent successive episodes for one logical resource.

- **Distinct from the claim.** The execution *claim* is taken later, by the
  embodied worker, under its own worktree identity (`claim`/`start`/`complete`).
  The *reservation* is taken first, by the **spawner** (a `create --spawn` CLI,
  or — later — the supervisor loop), **before** launching embody.
- **Keyed** `dispatch-task:<task_id>:<attempt>`.
- **Lifecycle:** `reserving → spawned → settled`, with `cold` for a suspended
  reusable headless body, `releasing` while a failed/yielded/terminal attempt
  concludes its exact created allocation, `failed` for a bounded retry that
  mints a fresh attempt, and `rearmed` when an operator atomically retires
  failed history after repairing the transport.

  | state       | meaning                                                        |
  |-------------|----------------------------------------------------------------|
  | `reserving` | this spawner owns the (task, attempt) spawn; embody not yet confirmed launched. A restart reconciles a pre-recorded worktree to `spawned`, `failed`, or held-unknown. |
  | `spawned`   | embody launched; the session/worktree handle is recorded.      |
  | `cold`      | the headless process is stopped while its resumable session, worktree, and task ownership remain bound. |
  | `releasing` | the body is gone and its reservation-created worktree is being conservatively concluded; replacement remains fenced while cleanup is pending. |
  | `settled`   | the reserved attempt reached a terminal outcome; no more spawning. Optional `conclusion_state` / structured `conclusion_detail` keep post-settlement disposable-worktree priming retryable and visible. |
  | `failed`    | spawn failed or was lost; a fresh attempt may now be reserved.  |
  | `rearmed`   | a failed attempt was retired by an audited operator rearm; preserved for history but excluded from the dead-letter count. |

- **Exactly-one invariant.** `reserve_spawn(task_id)` is a single
  `BEGIN IMMEDIATE` transaction: if any reservation for the task is **active**
  (`reserving`/`spawned`/`cold`/`releasing`), it returns
  `(existing, reserved=False)` — the caller must **not** spawn. For an
  `exclusive_key`, the active lookup and a partial unique index use that key
  across all task ids, so two episodes cannot reserve the same resource. A new
  exclusive reservation carries the most recent recorded worktree and bridge
  session as reuse candidates. Otherwise the task must still be `queued` and
  unowned; a different task state is refused. It then mints attempt
  `max(prior)+1` (or `1`), `reserving`, and returns `(new, reserved=True)`. A
  prior `failed`/`settled`/`rearmed` reservation therefore never blocks a
  legitimate retry, but no two callers ever spawn the same attempt or exclusive
  resource.

### Where it lives

The reservation table lives in the **coordinator's SQLite DB** — the coordinator
is already the queue's single writer, so reservations inherit the same
atomic-under-concurrency guarantee with no new locking. HTTP surface:

```
POST /spawn-reservations               {task_id, reserved_by} -> {reserved, reservation}
POST /spawn-reservations/{key}/worktree {worktree, ownership, creating_host, driver}
POST /spawn-reservations/{key}/spawned  {session_handle, worktree}
POST /spawn-reservations/{key}/release  {detail, disposition}
POST /spawn-reservations/{key}/fail     {detail}
POST /spawn-reservations/{key}/settle   {detail, conclusion_state?, conclusion_detail?}
POST /spawn-reservations/tasks/{task_id}/rearm {permitted, reason, min_failures}
GET  /spawn-reservations                ?task_id&state&limit
GET  /spawn-reservations/{key}
```

`DispatchClient` exposes each as a method. Events (`spawn.reserved`,
`spawn.spawned`, `spawn.failed`, `spawn.settled`, `spawn.rearmed`) are published
on the SSE bus.

### How `create --spawn` uses it (the bug fix)

`create --spawn` now **reserves before spawning**:

1. `reserve_spawn(task_id)`. If `reserved=False`, print a skip note and return
   (an active spawn already exists — the double-spawn is prevented).
2. Resolve a targeted/carried worktree or create one, then record that id plus
   `created|targeted|reused` ownership while the reservation is still
   `reserving`. A newly created worktree is stamped before launch with its
   task, reservation, attempt, driver, supervisor, and creating-machine
   provenance. It is deliberately top-level: the dispatch reservation owns its
   cleanup, so an interactive caller's worktree does not acquire a competing
   outbound-resource claim. A positively missing carried id may be replaced; an
   indeterminate lookup fails closed.
3. Run the spawn (`embody` or `bridge` backend) in that recorded checkout. The
   bridge backend first resumes a valid carried ACP session and creates a
   replacement only after the prior session is confirmed gone.
4. On success, `record_spawn(key, session_handle, worktree)`. A non-zero exit
   or missing mechanism moves a reservation-created allocation to `releasing`;
   `create --spawn` immediately attempts the same exact-ID managed teardown as
   the supervisor. A later run cannot retry until the ground layer safely
   removes the worktree or explicitly holds it for attention.

Fail-safe: if the reservation call itself errors, `create --spawn` **does not
spawn** (better to leave the task queued than risk a second autonomous worker).

## The supervisor loop (built)

The reservation primitive makes a safe host-side supervisor possible. The loop
(`supervisor.py`, CLI `agent-dispatch supervise`) is built around one hard
invariant:

> **A task is spawned only when a *fresh* spawn reservation is acquired for it.**

Because `reserve_spawn` returns `reserved=False` whenever an *active*
(`reserving`/`spawned`/`cold`/`releasing`) reservation already exists, a task that is already being
spawned — or is still held by a slow-but-alive embody — is skipped. **Elapsed
time is not treated as death**, so a slow-but-alive embody is never
double-spawned. Each cycle:

1. **reconcile** — settle reservations whose task reached a **terminal** state
   (`completed`/`abandoned`). An exclusive reservation is not released merely
   because its task became terminal: live/unknown bodies remain bound, and only
   a confirmed-gone body or an explicit end/conclusion permits release. A
   completed idle **local** headless session may remain carried for the next task
   episode; a fleet body is ended because no remote carry/resume path exists.
   A provenance-bearing reservation-created worktree moves to `releasing`
   before settlement and remains fenced while exact-ID managed teardown is
   transiently pending. Legacy CLI worktrees explicitly opted in with
   `--disposable-cli-label` retain their existing post-settlement conclusion
   path: the dispatcher durably checkpoints the ACP session identity and marks
   conclusion pending, ends the idle or stopped local body, then asks
   agent-worktrees to conclude and remove the exact recorded session/worktree.
   A running worker or operational failure remains pending for a later
   reconcile; a preservation decision is held visibly.
2. **recover interrupted reservations** — a `reserving` row with a pre-recorded
   worktree is promoted when that worker is confirmed live. A confirmed-gone
   reservation-created worktree enters `releasing`; targeted/reused legacy
   rows may fail directly. Unknown liveness remains reserved. An unbound
   reservation is never guessed away.
3. **fulfill yield/release requests** — yielding marks the active reservation
   for release but does not settle it transactionally. The supervisor ends a
   confirmed-live headless body (or observes it gone), concludes only a
   provenance-verified reservation-created worktree, and persists pending or
   held cleanup across restarts. Targeted, reused, foreign-host, origin-unknown,
   dirty, committed, live, and obligated worktrees are preserved.
4. **poll** — for each eligible queued task (in the lane, due, matching the
   optional **label opt-in**), up to `--max-concurrent` in-flight: `reserve_spawn`
   → if reserved, pre-create and record its worktree → spawn embody →
   `record_spawn` (or fenced release on error). A task that accumulates
   `--max-attempts` **failed**
   spawn attempts is treated as **spawn-dead-lettered** by the supervisor — held
   out of auto-retry, with its failed history queryable via `reservations list
   --state failed` for a human — so a persistently-unspawnable task can't drive a
   retry storm. This does **not** mutate the task's queue status to
   `dead_letter`; it is a reservation-history guard.

CLI:

```
agent-dispatch supervise [--repo R | --all-repos] [--label L ...] \
    [--max-concurrent N] [--max-attempts N] [--no-heartbeat] \
    [--no-reactive] [--reactive-interval S] \
    [--embody-backend headless|cli] [--cli-label L ...] \
    [--headless-label L ...] [--disposable-cli-label L ...] \
    [--headless-agent AGENT] [--interval S] [--once]
agent-dispatch reservations list [--task ID] [--state S]
agent-dispatch reservations fail|settle <key> [--detail ...]
agent-dispatch reservations rearm <task> --permit --reason "transport repaired" \
    [--min-failures 3]
```

By default each long-lived lane supervisor owns one authenticated local Agent
Bridge event client. It sends the complete set of active fleet reservation
host/session identities over one aggregate SSE connection; Agent Bridge then
multiplexes their logical subscriptions over its per-host persistent carriers.
Durable turn, session-state, and handoff boundaries coalesce into one ordinary
reconciliation wake. Cursors are acknowledged only after that pass completes.
Reservation settlement or rebinding replaces the aggregate subscription set.

Stream exit, heartbeat/progress failure, cursor invalidation, and local Bridge
cutover mark acceleration degraded and wake one immediate full pass. Reconnect
uses bounded backoff and rediscovers the active local Bridge endpoint. The
outage guard resets only after heartbeat or event progress, so an immediately
failing reconnect cannot turn backoff into short reconciliation polling. After
a cursor invalidation or replay gap, the completed full pass acknowledges the
identified authoritative head and continuity before reconnecting.
The ordinary `--interval` remains the sole correctness floor and continues unchanged
when Bridge or a remote carrier is missing or version-skewed. `--no-reactive`
disables push acceleration. `--reactive-interval` remains accepted only for
configuration compatibility and never enables polling.

The rearm is one coordinator write transaction. It succeeds only while the
task is still queued and unowned, no `reserving`/`spawned` reservation exists,
and at least three (or the explicitly higher threshold) failed attempts remain.
It changes those rows to `rearmed`, appends the operator reason to their audit
detail and the task event trail, then makes exactly one fresh reservation
attempt eligible. If a racing claim or reservation acquires the SQLite write lock
first, the rearm fails without mutation; if the rearm linearizes first, the
later claim or reservation observes the rearmed state normally.

Dead-letter visibility is set-oriented: a supervisor logs one bounded,
actionable summary when its blocked task set changes, rather than repeating one
warning per task on every cycle.

## Registered supervision (built) — register-and-return

The bare `supervise` above **is the foreground loop** — it holds the terminal
open. That is the transitional shape. The north star (see the vision Concept
*the supervisor*, Feature *registered-supervision*, Behavior
*supervise-registers-and-returns*) is that a caller **registers** a unit with the
host's **singleton** supervisor and gets back a durable **registration handle**,
and the call *returns*; exactly one supervisor process per machine-and-environment
runs every registration, each in its own subprocess.

The registration surface is built as the first increment of that architecture:

```
agent-dispatch supervise register [--kind KIND] [--id ID] [--spec JSON|@FILE] \
    [--machine M] [--env E] [--ensure] \
    # supervised-lane convenience flags (when --spec is omitted):
    [--repo R | --all-repos] [--label L ...] [--max-concurrent N] \
    [--max-attempts N] [--label-max-attempts LABEL=N ...] \
    [--headless-label L ...] [--disposable-cli-label L ...] \
    [--headless-agent AGENT] [--evaluator SPEC] \
    [--interval S]
agent-dispatch supervise status <id>
agent-dispatch supervise list [--kind KIND] [--machine M] [--env E] [--active]
agent-dispatch supervise remove <id>
agent-dispatch supervise serve [--machine M] [--env E] [--interval S] [--once]
agent-dispatch supervise daemon-status [--machine M] [--env E]
```

- **`register`** writes a durable **registration** row (kind ∈
  `supervised-lane | schedule | emitter | evaluator`, plus a `spec` — the config
  the unit's runtime consumes — scoped to a `machine`+`env`) and **emits the
  handle**; it does *not* start the loop. The id is the caller's `--id` or a value
  **derived deterministically** from `(kind, machine, env, spec)`, so
  re-registering the same unit **upserts** (idempotent by handle) rather than
  duplicating it, preserving `created_at` and the paused/active status.
  `--ensure` additionally makes sure the host's singleton daemon is running
  (starts it detached if not) so the just-registered unit is picked up.
- **`status <id>`** returns one registration; **`list`** enumerates them
  (filterable by kind / machine / env, `--active` to hide paused ones);
  **`remove <id>`** drops one.
- **`serve`** runs the **singleton supervisor daemon** in the foreground (see
  below); **`daemon-status`** reports whether a daemon holds this scope and the
  registrations it would run.
- Registrations live in the coordinator's single-writer SQLite (`registrations`
  table) beside tasks and schedules, reached over the same secured mesh.

### The singleton daemon (built) — one master, per-unit subprocesses

`supervise serve` is the **singleton supervisor daemon**: exactly **one master
process per machine-and-environment** reads the registration registry and runs
each **active** registration in its **own subprocess**, reconciling the running
set against the registry on every tick.

- **Single-instance.** At startup the daemon takes a **crash-safe OS lock** on a
  lock file keyed by the `supervisor:<machine>:<env>` scope. The kernel releases
  the lock automatically if the daemon dies, so a **restart reacquires cleanly**
  (no permanent lock), while a *live* second daemon for the same scope is refused
  and **stands down** (exit 3) rather than spawning a rival loop — the *one
  supervisor per machine-and-environment* guarantee. A double launch (e.g. two
  `--ensure` registers racing) is self-correcting: the loser stands down.
- **Reconcile each tick.** Start a newly-registered unit; **restart** one whose
  spec or attributed runtime revision changed; **wind down** one that was removed
  or paused (`terminate` its subprocess); **revive** one whose subprocess crashed,
  gated by a restart backoff and bounded by `max_restarts` (a crash-looping unit
  is left stopped and surfaced, never retried forever).
- **Migrate direct registrations into declarations without double-running.** When
  a repo declaration and a store-backed direct registration have the same
  effective `kind`+`spec`, the declaration is the one live child and the direct
  row stays dormant. Removing the declaration restores the direct row, making
  migration reversible. If the two entries share a logical id but their specs
  differ, both remain live and the daemon emits an explicit conflict diagnostic;
  it never silently chooses one of two distinct intents.
- **Isolation.** Each unit is its own child, so one busy or failing unit never
  blocks its siblings or the master.
- **Kinds.** Every registration kind is a runtime the daemon drives in its own
  subprocess: **supervised-lane** → the `agent-dispatch supervise` embody loop;
  **evaluator** → that loop with `--evaluator` (subsumes the foreground
  `supervise --evaluator` flag), the evaluator spec materialized to a file;
  **schedule** → the timer producer (`schedule serve`) over a one-entry spec (a
  *self-run emitter*, dedup-keyed `sched:<id>:<epoch>` by the producer);
  **emitter** → either a periodic command emitter (`emitter serve`) with a
  declared `command` argv, `interval_seconds`, and pin-not-failover job lease,
  or the reactive producer (`webhook`) over a config on the spec's `host`/`port`.
  Each registration's inline spec is
  **materialized** to a per-registration file under the run dir so its subprocess
  can read it. A kind the daemon can't build a command for is logged and skipped,
  never fatal.
- **Attributed plugin companions.** Active plugin discovery may contribute the
  non-direct `plugin-companion` kind. Its optional provider decides whether the
  unit is active and may add arguments/environment; provider uncertainty retains
  a previous answer only under the exact same declaration authority. The daemon
  validates plugin-root containment, strips inherited dispatch authority,
  launches through a pre-execution containment gate, and records PID plus process
  creation identity before plugin code runs. Windows uses a kill-on-close Job;
  POSIX uses a process group and can recover a matching receipt after supervisor
  restart. Confirmed unhealthy probes restart the unit; indeterminate probes do
  not.
- **Managed companion runtime declarations.** A plugin companion may carry a
  versioned `managed_runtime` data contract containing bounded logical runtime
  identities, plugin-relative Python project inputs, validation imports, and
  interpreter environment bindings. The contract is attributed and
  restart-significant, but this increment is non-operative: the daemon does not
  create a runtime, invoke a package manager, or alter launch behavior from this
  metadata.

> **Increment status.** The **registration store + verbs**, the **singleton
> daemon** (reconcile, per-unit subprocesses, crash-safe OS-lock single-instance,
> crash-revive, wind-down), the **four direct kinds** (supervised-lane, evaluator,
> schedule, emitter), and attributed-plugin-only **plugin companions** are built.
> The daemon runs each active unit as its own contained subprocess,
> subsuming the foreground `supervise --evaluator` flag into an evaluator
> registration. The bare `supervise` foreground loop remains available.

Registrar declarations can name any daemon kind with a generic `kind` + `spec`
shape. A periodic emitter declaration is therefore source-controlled beside its
producer and discovered like a pool:

```yaml
name: repository-sweep
kind: emitter
spec:
  id: repository-sweep
  command: [repository-sweep, tick]
  interval_seconds: 3600
  timeout_seconds: 900
filters:
  permit:
    machine: [host-a]
```

The machine filter selects which singleton may run the unit; the emitter's
`emitter:<id>` job lease is a second, cross-host single-producer guard.
`supervise daemon-status` inspects the discovered unit and `supervise override
disable|enable declared:<owner>:<name>` provides immediate pause/resume parity
without racing a declaration sync.

### Plugin-owned registrar drop-ins

Registrar discovery has three inputs:

- operator-managed pointers in `~/.agent-dispatch/registrar/pointers.json`; and
- declarations under a pointed repo's `.agent-dispatch/registrar/`; and
- attributed plugin candidates in `~/.agent-dispatch/registrar.d/*.json`.

The plugin contribution directory follows the agent-bridge `providers.d` and
agent-codespaces `config.d` conventions. A contributing plugin ships
declarations in its own footprint and its
`sessionStart` hook atomically writes an idempotent candidate manifest:

```json
{
  "schema_version": 1,
  "plugin": "example-producer@example-marketplace",
  "plugin_root": "/absolute/path/to/example-producer",
  "registrar": "references/agent-dispatch/registrar"
}
```

`registrar` is relative to `plugin_root`; path canonicalization must prove it
does not escape that root. The filename is only a stable collision-free key.
The manifest's canonical `name@marketplace` identity is the authorization key.

Unlike the operator-managed pointer registry, `registrar.d` is an **untrusted
candidate index**, not proof that a contribution should run. On every reconcile,
the singleton computes a machine-wide eligible plugin set:

1. truthy `enabledPlugins` in user-global Copilot settings
   (`~/.copilot/settings.json`, with the local override applied); plus
2. truthy `enabledPlugins` from every adopted project in
   `~/.agent-worktrees/projects.yaml`, joined to the current-platform checkout
   in `~/.agent-worktrees/repos.yaml` and read through the normal native-first
   repo-settings resolver.

One registered project enabling a plugin is sufficient; the contribution is
then available machine-wide and its declaration filters own repo/machine/env
scope. Missing agent-worktrees registries simply contribute no repo-enabled
plugins, leaving the user-global lane usable.

A candidate is active only when all of these hold:

- its exact `name@marketplace` source is in that effective enabled set;
- every registered-project checkout used as enablement evidence proves it is the
  registered repo: canonical Git top-level plus normalized remote identity match
  the joined `projects.yaml`/`repos.yaml` record;
- `plugin_root` is the **one unambiguous, currently resolvable** on-disk root for
  that source (an installed marketplace payload or the enabled repo's resolved
  local plugin), after canonicalization;
- the root's own plugin manifest names the requested plugin, and the marketplace
  resolution/installed layout proves the requested marketplace rather than only
  finding an arbitrary directory that happens to contain a plugin manifest;
- the relative registrar directory remains inside that root; and
- the manifest and declaration documents validate.

All enabling scopes for one canonical source must converge on the same real
plugin root. If two registered projects reuse a marketplace name for different
local plugin roots, or global and repo resolution disagree, that source is
**ambiguous and contributes nothing** until the conflict is removed. The
source-derived drop-in filename may be overwritten idempotently by several hooks,
but reconciliation never makes session timing or last-writer order choose the
active root.

Therefore a disabled plugin, an uninstalled payload, a deleted registered repo,
an existing path reused by the wrong repo, an obsolete or identity-mismatched
plugin root, an ambiguous source, a malformed manifest, or a path-escape attempt
contributes nothing even if its old `registrar.d` file remains. There is no TTL
and no uninstall cleanup requirement: the live eligibility/root check makes
stale entries inert.

Plugin candidates are isolated from trusted discovery. The existing fail-loud
duplicate-name rule remains within the trusted `pointers.json` tier. Trusted
declarations win over plugin candidates with the same profile name. If two
manifests claim the same plugin source, both manifests are quarantined until the
stale sibling is removed. If two distinct plugin sources claim one profile name,
that **name alone** is quarantined from both sources; their other non-conflicting
declarations still reconcile. A malformed or conflicting plugin candidate
therefore cannot abort aggregation, preserve a last-known desired set, or
prevent an unrelated authorization removal from winding a unit down.
Diagnostics report every skipped candidate/name.

The trusted `pointers.json` surface remains for explicit operator/service
adoption. Plugin hooks never edit that aggregate JSON file; each owns only its
drop-in, so independent plugin starts cannot race or erase one another.
Enablement and root changes are desired-set changes: live reconcile starts,
updates, or winds down the affected units without restarting the singleton.

The runtime keeps independent last-known sets for the two tiers. A transient or
invalid trusted `pointers.json` read retains only the prior trusted declarations;
plugin candidates continue to reconcile, so a confirmed plugin disablement or
deletion still winds down its units. Within `registrar.d`, an unreadable registry
retains the prior candidate set and an unreadable declaration document retains
only that document's prior value. Confirmed malformed, missing, disabled, or
identity-mismatched entries withdraw immediately and never activate from cached
payload presence.

Operational warnings are fingerprint-deduplicated and capped at ten details per
refresh, followed by a suppressed-count summary. Run:

```
agent-dispatch registrar doctor
agent-dispatch registrar doctor --json
```

Doctor is exhaustive and renders trusted `pointers.json` health separately from
untrusted `registrar.d` findings while consuming the same classifier as the
runtime. Cleanup is intentionally report-only: agent-dispatch does not own a
receipt ledger that could prove a candidate file is safe to delete.

### Operator overrides (built) — the kill-switch

The running set is the declared/registered set **reconciled with operator
overrides**, and an override **wins**. `supervise override` is a fast, local,
reversible **enable/disable** veto on one supervised unit (addressed by its
registration id — the same id `daemon-status` / `list` show):

```
agent-dispatch supervise override disable <id> [--reason "..."]
agent-dispatch supervise override enable  <id>
agent-dispatch supervise override list
```

- **Precedence.** The daemon subtracts overridden-off ids from its desired set
  **after** it merges the declared + store-backed sets, so an override outranks
  both a unit's declaration and the discovery layer. A disabled unit is **wound
  down** on the next reconcile (the stop-not-desired path) and **stays down** —
  a later repo re-sync that re-declares it does **not** quietly revive it.
- **Out of band + local.** The override lives in a machine-local JSON store
  (`~/.agent-dispatch/overrides.json`, honoring `AGENT_DISPATCH_OVERRIDES`), *not*
  a repo commit + sync cycle — so a misbehaving unit is stopped **right now**
  without editing, or racing a repo-sync against, its declaration.
- **Reversible + legible.** `enable` clears the override and the unit returns to
  whatever its declaration/registration says; `override list` and `daemon-status`
  surface the overridden-off set (and each unit's reason) beside what is declared.
- **Fail-safe.** A missing or unreadable override store means "no overrides in
  effect" — a bad read never winds a unit down. This is the emergency stop that
  *discover-and-live-reconcile* needs to be safe.

### Embody body: headless by default, CLI opt-out

The supervisor embodies each spawned task as a **headless agent-bridge ACP**
session **by default** (`--embody-backend headless`) — the right body for a
dispatched/supervised task, which is a self-contained, autonomous unit that needs
no human attach. Headless also sidesteps the **CLI-start-prompt** path entirely (a
seeded CLI/mux session can *race the input-prompt caret and never deliver its
seed*, deadlocking at 0%). `--headless-agent AGENT` names the agent-bridge agent
used (default `task-worker`).

> **Preflight (fail-loud, best-effort).** At loop startup a headless lane checks
> that its `--headless-agent` is actually registered with agent-bridge on the host
> where the body will spawn — the local registry, or each `--pool` host over SSH
> for a fleet lane — and prints a one-line **WARNING** for any host where the agent
> is provably absent (the classic trap being the bogus `task-worker` default naming
> an agent nobody registered, which otherwise fails as `'task-worker' is not a
> known agent name`, retries, and dead-letters *silently*). The check never blocks
> the lane and stays silent when the registry can't be read (bridge absent,
> unreachable, timeout) — it only warns on a confirmed miss. It is skipped for
> `--once` so hot one-shot/cron polls stay cheap.

When a task *does* want an attachable **mux-wrapped CLI autopilot**
(`agent-worktrees embody`) — standalone/durable work a human may take over — opt
out:

- `--embody-backend cli` makes the **whole lane** CLI-embodied; then
  `--headless-label LABEL` (repeatable) forces specific labels back to headless (a
  mixed lane).
- `--cli-label LABEL` (repeatable) forces specific labels to CLI while the lane
  stays headless-by-default (the common opt-out).

The routing is **per label within one supervisor**, so a single service can
embody self-contained sweeps headless (the default) while opting a "kick a
session" interactive label out to CLI. The headless body reuses the **same
autopilot seed** as the CLI backend (claim-under-identity, contract-net
evaluation, deferred completion), so a headless-embodied task is *driven*
identically; only its body differs. A headless body is not a worktree, so the
worktree-keyed lease heartbeat does not apply to it (bounded sweeps drive their
own lifecycle to completion); reconciliation still settles its reservation on the
task's terminal state. `--embody-backend`/`--cli-label`/`--headless-label` apply
to **local** (non-pool) spawn. In fleet (`--pool`) mode the body choice is
**fleet-wide** instead: fleet bodies are headless by default too (only
`--embody-backend cli` makes them CLI on the pool host; the legacy `--headless`
flag remains an explicit force), and the per-label flags are ignored.

The local supervisor and every worker use the ordinary `agent-dispatch` client
discovery path. The supervisor resolves a fresh client for every coordinator
operation, and a spawned worker's prompt carries only the task id plus a
**routing intent** -- discovery for the default local coordinator, or the
`--shared` moniker -- never a raw coordinator URL. A zero-downtime coordinator
cutover may change its dynamic loopback port while a long-running supervisor
still has an old keep-alive connection; neither that connection nor a worker
seed may pin newly-issued work to the retired address. A raw `--url` target
cannot be handed to a locally-spawned worker (it would bake a raw,
possibly-dynamic endpoint into the body); route by the default local
coordinator, `--shared`, or fleet mode (`--pool`/`--origin`, which routes by
machine alias).

### Disposable local-worker conclusion (built) — provenance-first priming

Every locally created dispatch worktree carries immutable task, reservation,
attempt, driver, supervisor, and creating-machine provenance. That proof
authorizes automatic conclusion for both CLI and local headless ACP bodies; no
label opt-in or branch-name inference is needed. Pre-existing targeted/reused
worktrees and legacy records without exact ownership are never treated as
dispatch-owned.

The older `--disposable-cli-label LABEL` path remains for explicitly declared
CLI worker classes whose worktrees predate allocation provenance. A registrar
may supply the equivalent `body.disposable_cli_labels`.

On terminal task settlement the supervisor uses only the spawn reservation's
recorded `session_handle` and `worktree`. It never infers an allocation by
branch name or age. When the initial session handle is the known
`wt-<worktree>` mux placeholder, the supervisor upgrades it from the task's
durably captured `owner_session_id` (or a matching live-session observation)
before settlement; an already-exact handle is never replaced by a successor.
For a provenance-bearing allocation, the reservation stays active in
`releasing` while cleanup is transiently pending, preventing a retry from
allocating another worktree. The supervisor invokes:

```bash
agent-worktrees conclude-disposable \
    --worktree <exact-id> \
    --session <exact-session-id> \
    --policy dispatch-attempt \
    --reservation <exact-reservation-key> \
    --owner agent-dispatch \
    --remove \
    --json
```

The legacy label-scoped CLI path instead invokes:

```bash
agent-worktrees conclude-disposable \
    --worktree <exact-id> \
    --session <exact-session-id> \
    --policy disposable-cli \
    --owner agent-dispatch \
    --remove \
    --json
```

The ground layer preserves any live mux/bound session, follow-up or resource
obligation, open pull request, branch drift, arbitrary dirty path, or local
commit. It also preserves a different or unresolved asserted lifecycle head
rather than making a clean checkout GC-eligible around a resumable session.
Dirty generated overlays are preserved under the same rule as other dirty work;
a clean branch with zero commits ahead of the configured upstream may remain
behind without being rewritten. A safe result concludes the exact recorded
session, marks the checkout as a managed final worker, and immediately asks the
managed sweep to remove only that exact id with zero idle grace. The sweep
remains the deletion authority:
it freshly re-checks liveness, lifecycle state, Git state, and record invariants
under the same lifecycle fence used by CLI embodiment. Final Git removal is
non-forced, runs after the short record recheck lock is released, and retains
the tracking record whenever Git refuses removal.

A safe preservation decision (`dirty-work`, `local-commits`,
`session-mismatch`, and related reasons) is a structured held outcome, not an
error. `live-session` / `live-mux` and operational failures remain durably pending and
retry with exponential backoff. On the first live-session result, the supervisor
sends one bounded conclusion prompt to the exact same local ACP session and
worktree; it never creates a replacement merely to clean the first. Each cycle processes a bounded batch; after
twelve failed conclusion attempts (a teardown window of roughly 40 minutes at
the bounded cadence) the reservation becomes visibly held instead of hot-looping
forever. A removed or held outcome is idempotent and does not run again.

### Lease heartbeat (built) — the live-worker safety net

Each cycle the supervisor also **holds the lease of every confirmed-alive
embodied worker** (`hold_live_leases`, gated on `--no-heartbeat`). For each
`spawned` reservation whose task is leased (`claimed`/`started`), it probes the
embody session's liveness (`tracking.resolve_live_session` → the agent-bridge
live-session registry, cross-machine over SSH for a remote owner) and, **only on
a confirmed-alive result**, sends a lease heartbeat on the task's behalf. This
keeps a live-but-quiet worker (one not emitting progress between phases) from
being misclassified as recoverable — closing the "don't trust the LLM to emit
progress to prove it is alive" gap.

The safety hinge: heartbeats fire **only** on a positive liveness result. A
`None` probe collapses *dead* and *bridge-unreachable* together, so it is treated
as neither alive (no heartbeat) nor proof-of-death (no recovery). A genuinely
dead worker is recovered only once a later probe returns a positive `gone`
verdict; a transient bridge miss can't mask a live worker, whose own activity
still updates its task.

### Recovery and nudging (built) — confirmed death only

The supervisor now has the positive-death signal the earlier design deferred to:
`recover_gone` probes each spawned body with a tri-state verdict and acts only on
`gone`.

- **local CLI/worktree body** — probe the owner worktree's captured
  `owner_session_id` through the agent-bridge live-session registry (local or over
  SSH for a remote owner). A different/absent session is `gone`.
- **local headless body** — parse the local `agent-bridge` session id from the
  reservation handle and probe that session on this host.
- **headless fleet body** — parse `fleet-body:<host>:<session-id>` and probe the
  pool host over SSH (`agent-bridge --json status <session-id>`).

For a confirmed-gone body, the supervisor yields the task on the dead owner's
behalf when it is still leased (preserving `goal` and `progress_log`), marks the
reservation failed, and the next cycle can reserve a fresh attempt. `live` bodies
are heartbeated; `unknown` bodies are left untouched. A confirmed-alive but quiet
worktree body can also be nudged once per stall window (`nudge_stalled`) instead
of being recovered. The remaining unsupported recovery path is **CLI/mux fleet**
auto-recovery: without a headless bridge session handle, a synthetic-owner fleet
body is still not auto-joined to the origin's live-session registry; use
headless fleet for recoverable remote sweeps.

## Transport for a containerized producer

A producer running in a **Docker container** (e.g. a scheduled sweep container)
reaches the host coordinator over `host.docker.internal` (with
`extra_hosts: host.docker.internal:host-gateway`). Two facts shape the safe bind:

- The coordinator defaults to **loopback on an OS-assigned port** (advertised by
  `~/.agent-dispatch/run/endpoint.json`; `AGENT_DISPATCH_PORT` pins a legacy
  fixed port when deliberately set), which a container **cannot** reach through
  the host's `127.0.0.1`.
- On Linux, each compose service gets its **own** bridge network with its own
  host-local gateway (all in `172.16/12`, none LAN-routed), so no *single*
  host-local IP is reachable from every container. The address reachable from all
  of them **and** the host is the wildcard `0.0.0.0` — which also exposes the LAN.

So the containerized-producer transport is: **bind `0.0.0.0`, require a bearer
token, and firewall the port off the LAN** (allow loopback + the Docker bridge
subnets `172.16.0.0/12`; drop the LAN interfaces). The token is enforced by the
**bind-safety guard** (`server.check_bind_safety`): binding a wildcard host
without `AGENT_DISPATCH_TOKEN` is refused outright, so the powerful task-control
API can never land on the network unauthenticated. (A *specific* host-local bind
— loopback, a Windows vEthernet(WSL) IP, or one shared Docker bridge gateway — is
a deliberate non-LAN choice and is not guarded; a future shared-network refinement
could bind one gateway and drop the firewall requirement.) The producer sends the
same token as a bearer credential; producer credentials should be **create-only**,
separate from runner credentials.

## Fleet dispatch: a health-gated remote embody pool (Model C)

The supervisor spawns embody on its **own** machine by default
(`make_embody_spawn`). **Fleet dispatch** lets one always-on supervisor instead
fan bodies out across a **pool of capable-but-not-always-on hosts** — the shape a
containerized, always-on producer needs when the real work should run on
workstations elsewhere in the mesh. It reuses the supervisor loop and the
reservation primitive unchanged; only the spawn target and a capacity gate are
new (`fleet.py`).

Three properties define it:

- **Origin-owned lease (Model C).** The spawn reservation and the task lease stay
  on the supervisor's (origin's) coordinator, so at-most-once is **fleet-wide**,
  not per-pool-host. Only the *body* runs remotely; it drives the origin task's
  lifecycle (`claim`/`start`/`progress`/`complete`) **back to the origin over the
  existing bidirectional SSH mesh** — `ssh <origin> agent-dispatch <verb> …`,
  under a supervisor-assigned **synthetic owner** (the body's own worktree can't
  identify it to the origin). This introduces **no new network bind** on the
  origin: its control API never leaves loopback. The body runs **detached** on the
  pool host, so an SSH blip after launch never kills a running job.
- **Liveness-gated selection.** A pool host is a candidate only when it is
  reachable over SSH **and** has `agent-worktrees` (a single cheap
  `command -v agent-worktrees` probe, cached briefly) — or, in **headless-fleet**
  mode, `agent-bridge` (the binary a headless body embodies through). The first
  live candidate by policy (config order; a task's `target_machine`, if in the
  pool, is tried first) is chosen.
- **Defer, don't fail, when the pool is asleep.** `FleetSpawner.can_spawn` is wired
  as the supervisor's **`capacity_gate`** — an optional pre-reservation check
  (default no-op → the local path is unchanged). When no host is live, the task is
  skipped for the cycle **without a reservation**, so an all-asleep pool never
  burns spawn attempts toward the dead-letter bound.

### Headless-fleet body (`--headless`) — the reliable remote embodiment

By default a fleet body is a **CLI/mux embody** on the pool host
(`agent-worktrees embody`). But a seeded CLI session can *race the input caret and
never deliver its startup seed* (the documented "Loading…" hang,
github/copilot-agent-runtime#13492) — so a kicked fleet body may never claim its
task, exactly the failure headless sweeps hit on anomalous-potato-wsl. `--headless`
(fleet-wide) instead embodies each fleet body as a **headless agent-bridge ACP
session** on the pool host — `ssh <host> agent-bridge create <agent> "<fleet
seed>" --no-wait` (`fleet.py` → `embody.spawn_fleet_headless_worker`) — spawning
the body in that host's own persistent agent-bridge daemon, which owns it
independently of the launching SSH invocation. It sidesteps the
CLI-start-prompt path entirely, so a bounded sweep embodies reliably on a remote
pool host with **no human attach**.

The seed is the **same** Model-C fleet seed as the CLI body
(`fleet_autopilot_worker_prompt` — drive the origin lease over `ssh <origin>`
under the synthetic owner), so a headless-fleet task claims + loops + progresses +
completes identically; only the *body* differs. A headless-fleet body is **not a
worktree**, so the *worktree-keyed* liveness probe doesn't apply — instead its
recovery handle is the **pool host's agent-bridge session id** (captured from
`create --no-wait --json` and stamped on the reservation as
`fleet-body:<host>:<session-id>`). The supervisor probes *that* over SSH
(`ssh <host> agent-bridge --json status <session-id>`, `embody.fleet_body_verdict`)
for the same tri-state verdict: a **confirmed-live** body is heartbeated
(`hold_live_leases`) so a quiet-but-alive sweep isn't wrongly recovered, and a
**confirmed-gone** body (its ACP session terminal or absent) has its reservation
released (`recover_gone`) so the next cycle re-embodies it — resuming from the
task's `progress_log`. As always, an `unknown` probe (ssh/bridge unreachable,
lagging reconcile) is never treated as death, so recovery cannot double-spawn a
live body. `--headless-agent AGENT` names the agent-bridge agent
(default `task-worker`).

For local headless bodies, a settled ACP turn is an implicit suspension
boundary. The supervisor publishes `IDLE`, suspends the task, and uses
`agent-bridge stop` so the Copilot process releases capacity while the ACP
session, task owner, worktree, and reservation remain durable. A later steer or
Resume sends the next turn to that same session id and returns the cold
reservation to `spawned`; it does not release the task for a new embodiment.
Confirmed session loss still follows the ordinary liveness recovery path.

CLI:

```
agent-dispatch supervise --pool host-a,host-b [--origin <alias>] \
    [--headless [--headless-agent AGENT]] [--label L …]
```

`--origin` is the supervisor machine's own SSH alias that bodies report back to
(defaults to the resolved local machine). Omit `--pool` for local spawn. Add
`--headless` for a headless agent-bridge ACP body on the pool host instead of a
CLI/mux one.

**Recovery-on-kill (headless fleet):** because the recovery handle is the pool
host's bridge session, a killed headless-fleet body is auto-recovered
(confirmed-gone → re-embody from progress), closing the fleet gap in the
liveness model. A **CLI/mux fleet body** still records a worktree handle but under
a synthetic owner it is not auto-joined to the *origin's* live-session registry,
so its auto-recovery remains deferred (the headless body is the recoverable fleet
path today).

**Deliberately deferred:** per-host concurrency caps (the global
`--max-concurrent` still applies fleet-wide); and load-aware pool selection beyond
config order.

## Running as a persistent service (the always-on last mile)

`agent-dispatch supervise` is only useful when something keeps it running: a
dispatched task queues forever until a supervise cycle observes it. On a
standalone deploy host the plugin installer therefore manages persistent
supervisors alongside the coordinator — systemd user units on Linux, Scheduled
Tasks on Windows — using the same `install|update|status|start|stop|uninstall`
verbs.

The legacy primary supervisor is unchanged: Linux installs
`agent-dispatch-supervisor.service`, Windows installs the Scheduled Task
`agent-dispatch-supervisor`, and both read `~/.agent-dispatch/supervisor.env`.
Existing hosts that use only the primary env keep the same behavior.

A host that needs more than one supervisor can add named profiles under
`~/.agent-dispatch/supervisors/<name>.env` (Windows:
`$HOME\.agent-dispatch\supervisors\<name>.env`). Profile names must contain only
letters, digits, `_`, or `-`. Each profile uses the same env-var schema as
`supervisor.env`; the installer maps it to its own supervisor:

- Linux: `agent-dispatch-supervisor-<name>.service`
- Windows: Scheduled Task `agent-dispatch-supervisor-<name>`

All supervisors share the generated launcher (`supervise-service.sh` on Linux,
`supervise-service.ps1` on Windows). The unit/task supplies the env file, so the
launcher stays generic and turns that env's label list into repeated `--label`
flags before running `supervise --all-repos`.

It is just the existing `Supervisor.serve` loop hosted persistently, so the
spawn-reservation guarantee (one embody per (task, attempt), across restarts and
multiple loops) is unchanged.

**Safety: every supervisor is label-gated independently.** `--all-repos` avoids
the lane-scoping gotcha where a short `--repo owner/name` form silently filters
*every* task out, which makes the **label opt-in the only thing between a
supervisor and embodying every queued task** (handoffs, interactive
worktree-pinned tasks, …). A primary or profile supervisor is enabled and
started only when its own env file sets at least one label; with no labels set
it is installed but left inert. The generated launcher also hard-refuses to run
label-less as a defense-in-depth guard.

```
AGENT_DISPATCH_SUPERVISE_LABELS=            # comma/space list; REQUIRED to enable
AGENT_DISPATCH_SUPERVISE_INTERVAL=30        # poll seconds
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1   # max-one-active by default
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3     # dead-letter after N failed spawns
AGENT_DISPATCH_SUPERVISE_LABEL_MAX_ATTEMPTS= # optional LABEL=N overrides
AGENT_DISPATCH_SUPERVISE_HEADLESS_LABELS=   # labels embodied headless-ACP (subset of LABELS)
AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT=    # agent-bridge agent for headless bodies (default task-worker)
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=        # advanced, e.g. --pool a,b --origin host [--headless]
```

Profile reconciliation is idempotent: `install`/`update` writes the primary
supervisor, writes every valid profile whose env file exists, and removes any
`agent-dispatch-supervisor-<name>` unit/task whose `supervisors/<name>.env` has
been deleted. Reconcile never touches the primary. `start`, `stop`, `status`,
and `uninstall` iterate the primary plus every present profile; status prints
whether each supervisor is active/enabled or inert because labels are absent. A
WSL guest or client-only host (`--no-service`) installs none and removes stale
supervisors; `--no-supervisor` opts a full host out of all supervisors while
leaving the coordinator installed.

On Windows, `install`/`update` stops the service-manager roots and inventories
the detached process tree before starting the current launchers. It retires
every prior `conhost`/PowerShell wrapper, supervisor master or direct loop,
registrar child (lane, evaluator, schedule, emitter), and descendant across all
installed runtime slots. The retirement runs once before the primary/profile
reconcile, so an old generation cannot keep producing after a version update and
starting one profile cannot tear down a sibling started moments earlier.

Example multi-label supervisor profile (`~/.agent-dispatch/supervisors/review.env`):

```
AGENT_DISPATCH_SUPERVISE_LABELS=code-review,nightly-scan
AGENT_DISPATCH_SUPERVISE_INTERVAL=30
AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=1
AGENT_DISPATCH_SUPERVISE_MAX_ATTEMPTS=3
AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT=review-worker
AGENT_DISPATCH_SUPERVISE_EXTRA_ARGS=--pool anomalous-potato-wsl --origin mantis-counter --headless
```

## Genericity

Nothing here is specific to any one producer. The reservation is keyed only by
task id + attempt; the supervisor is a generic delegation-layer capability. Its
first consumer is an external nightly-sweep producer, but no consumer-specific
identifier appears in this code.
