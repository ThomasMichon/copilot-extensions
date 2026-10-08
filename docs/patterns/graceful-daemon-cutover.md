# Pattern: graceful daemon cutover (update without killing in-flight work)

> **Serves the vision:** a runtime update is invisible to work in progress.
> **Embodied by:** `agent-bridge`, `agent-dispatch`, `agent-index`,
> `agent-mcp`, `agent-worktrees`, and `worktree-manager`; `agent-vault`
> remains the main planned adopter still to land.
> **Companion patterns:** [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)
> (decoupling a warm daemon from the swappable runtime),
> [`service-lifecycle-supervision`](service-lifecycle-supervision.md),
> [`local-endpoint-discovery`](local-endpoint-discovery.md),
> [`process-slot-ownership`](process-slot-ownership.md) (the generation
> self-retire and abandoned-passive reap that defend this cutover when the
> orchestrator itself dies mid-flight),
> [`versioned-singleton-manager`](versioned-singleton-manager.md) (keeps the
> host's own service manager coherent across this protocol's two-overlapping-
> processes shape — a service-manager-side concern this pattern itself does
> not address).
> **Origin:** the `correct-install-flows` effort, Thread B (dotfiles#1393),
> building on the self-updating-runtime-integrity effort (dotfiles#533).

## The problem

A **Runtime service** plugin (see the plugin-shapes table) runs a long-lived
local daemon that holds **in-flight, non-resumable work**: agent-bridge hosts a
live Copilot CLI process mid-turn; agent-dispatch's coordinator holds claimed
tasks and spawn reservations; agent-index's worker is mid-index-batch;
agent-vault holds an unlocked master secret in memory. The immutable-versioned
runtime layout ([`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md),
dotfiles#581) makes a version bump *build* a new slot safely — but **activating**
it still needs the daemon to start running the new code, and the naive way to do
that (stop the old process, start the new one) **destroys whatever the old
process was doing**. On Windows that is worse: a stopped daemon that held an open
handle blocks the swap, and a mid-turn kill loses a non-resumable Copilot turn.

The rule (from the effort charge):

> An update must never kill in-flight, non-resumable work owned by a service.

## The protocol (the shape)

The installer triggers this **automatically** during activation (see *Wiring* below) —
cut over **without a stop** by standing the new version up *beside* the old one
and moving work to it at a safe point:

```
installer `update`/activation detects a live daemon   ← automatic trigger (no `deploy` verb)
      │
      ▼
persist connection/queue state durably
      │
      ▼
spawn the NEW version (installed slot) on a fresh port, PASSIVE
      │
      ▼
health-gate the new daemon                     ── fail ⇒ roll back (old stays live)
      │
      ▼
FLIP the routing table  (active → new, previous → old)   ← the commit point
      │
      ▼
DRAIN the old daemon at a SAFE CUTOVER POINT   (stop taking new work; let
      │                                          in-flight work reach a boundary)
      ▼
RECONNECT / load-shed survivors to the new daemon
      │
      ▼
RETIRE the old daemon      ── on abort after commit ⇒ commit-forward, never strand
```

This is exactly the **active/passive, routing-table (no-proxy)** model the shared
`zdd` library already implements — do **not** reinvent it per plugin, and do
**not** surface it as an operator-run command.

## The shared primitive: `zdd` (`libs/zdd/`)

`zdd` (package `agent-zdd`, import `zdd`) is a **canonical, vendored** library —
like `versioned_runtime.py`, it is copied byte-identically into each consumer's
`libs/zdd/` and kept in sync from `libs/zdd/` at the repo root (never a shared
runtime import — plugins are pulled independently from the marketplace). It is
pure stdlib and imports nothing from any consuming plugin; **keep it that way.**

| Module | Role |
|--------|------|
| `zdd.routing` | The file-based routing table `active.json` (no front proxy). The daemon `publish_active`s its endpoint on startup and flips `active`/`previous` atomically on cutover; short-lived clients re-read it every invocation and self-heal (dead `active` → `previous` → the caller's static config). API: `Endpoint`, `read_active_endpoint`, `publish_active`, `clear_if_owner`, `routing_table_path`. |
| `zdd.cutover` | `CutoverOrchestrator` drives one cutover: spawn passive → health-gate → flip → drain → retire, reversible up to an explicit commit point, with **rollback** (pre-commit failure) and **commit-forward** (retire the old even if unreachable, rather than strand clients). |
| `zdd.breadcrumb` | Recovers a **stranded survivor** left drained by an *aborted* cutover: `recover_stale_cutover` undrains it so it is not stuck closed to new work (agent-bridge `deploy --recover`). `reap_abandoned_passive` is the matching backstop for the *new* side: a `spawn_passive` daemon whose cutover's orchestrator died before promoting it never becomes anyone's "old" (the downstream tracker) -- see § Never-Promoted Abandoned Passive below. |
| `zdd.diagnostics` | Shared daemon-health audit + repair: report-only and apply modes that surface the four field-proven abnormality classes uniformly (duplicate resident daemon, stranded survivor, never-promoted abandoned passive, stale superseded generation) and gate destructive repair on a validated live owner plus identity-bound termination. |

**Why a routing *table*, not a front proxy:** a proxy on a stable port is itself
a long-lived process you must update — re-introducing the downtime, and demanding
socket hand-off between proxy generations (hardest on Windows). A file has no
process to update.

## The consumer contract (what each daemon implements)

`zdd` injects every side-effecting collaborator, so a consuming service keeps
control of its own process/health/**drain** semantics:

```python
from zdd.cutover import CutoverOrchestrator

orch = CutoverOrchestrator(
    config_dir, bind="127.0.0.1", version="1.2.3",
    spawn_passive=lambda port: ...,      # start the installed slot PASSIVE, detached -> handle(.pid/.terminate/.poll)
    health_check=lambda host, port: ..., # probe the new slot's /health -> bool
    make_client=lambda base_url: ...,    # -> client exposing drain/undrain/shutdown[/adopt_relay]
    pick_free_port=lambda: ...,          # -> int
)
result = orch.run(health_timeout=60, drain_timeout=300, force=False)
```

A consumer owns three things `zdd` cannot know:

1. **The drain endpoint + its SAFE CUTOVER POINT** — the crux. "Drain" means
   *stop accepting new work and wait for in-flight work to reach a boundary
   where retiring the old daemon loses nothing.* Each daemon defines that
   boundary (see per-plugin below). Expose `drain`/`undrain`; return busy vs.
   drained so the installer/ExecStop can gate on it.
2. **The edge adapter** — how *this* service's clients follow the flip.
   Short-lived clients re-read `active.json` directly. A service reached through
   a **fixed external port** (a reverse tunnel, a shared relay) instead has a hop
   watch the table and re-point at the live port.
3. **Passive-instance etiquette** — a passive daemon must NOT seize singleton
   resources the active one still owns (a shared credential-relay port, a pinned
   socket, a named single-instance lock) until *after* the flip. Bind a fresh
   port; acquire shared singletons only on promotion.
4. **Single-active reconciliation** — `RETIRE` shuts down only the *one*
   predecessor a cutover replaces (`old_client.shutdown()`). That is not enough
   to guarantee **one daemon per host** across *repeated* cutovers and plain
   restarts: a passive promoted by cutover *N* that is later displaced by a
   process which is not its direct successor (e.g. a plain `serve` restart that
   re-published the routing table over the top) is never anyone's "old" and
   leaks — a live process holding a port + RSS, indefinitely. After a successful
   cutover, **reconcile the full set**: enumerate the daemon's own processes and
   retire every one that is not the routing table's `active` (anchored on the
   port this cutover just promoted, so the freshly-promoted daemon is never
   reaped) and not self. Keep it best-effort and fail-soft — a stray that cannot
   be reaped must never fail the cutover. (agent-dispatch: `agent_dispatch.reap`,
   invoked from `_cmd_cutover` after commit.)

### Wiring it to the runtime layout — the installer drives cutover, automatically

**Cutover is installer-driven and automatic. There is no externally-driven
`deploy` command an operator or agent invokes.** The moment that activates a new
version *is* the moment of cutover: when the installer's **`update`** (and the
versioned-runtime **activation** step it calls, and — on a stamped box — the
first-use **`provision`**) is about to make a new slot live and **detects a live
daemon**, it performs the `zdd` cutover **in-process** (calling the library from
the freshly-installed venv python) as an intrinsic part of activation. No
separate step, no operator action, nothing to remember.

- **Trigger:** the same automatic paths that already run the installer — the
  Picker/operator `update` flow and the versioned-runtime reconciler on a routine
  version bump. They update the venv in place (no stop) and **cut over as part of
  activation**, falling back to stop-and-swap only if the cutover cannot run or
  fails. A human never runs a cutover verb.
- **Mechanism, not interface:** the drain/flip/retire logic lives in `zdd` (a
  library) and is invoked by the installer directly. Any `drain`/`undrain`/
  cutover entry points a daemon exposes are **internal seams the installer
  calls** (and self-recovery uses), not an operator-facing CLI surface. If a
  plugin still ships a `deploy`-style subcommand, it is an implementation detail
  the installer drives — never the thing that *has to* be run to get a cutover.
- Binstubs, the scheduled-task launcher, and the deploy manifest are pinned at
  the concrete slot python and **rewritten by the installer on every cutover**.
- `running-version.json` (dotfiles#533) is the **reconcile signal** (current
  version/pid/start-time) the installer reads to decide *whether a live daemon
  needs a cutover*, *not* itself a handoff mechanism — the handoff is the routing
  flip the installer performs.
- **Self-healing is automatic too:** a stranded survivor from an aborted cutover
  (breadcrumb) is undrained by the installer on its next run, not by a manual
  recover command.

## Generation self-retire (the demoted daemon cleans up after itself)

Invariant-4 reconciliation above is **successor-driven**: after a *successful*
cutover the new active daemon enumerates and retires every sibling that is not
the routing table's `active`. That closes the leak whenever a cutover runs to
completion — but it has a blind spot. If the **orchestrator itself dies** between
the flip and the retire (a crashed `update`, an abandoned cutover), *no successor
ever runs the reconcile*, and the demoted generation lingers indefinitely as a
stranded `serve --passive` process holding a port + RSS.

**Generation self-retire** is the complementary, **daemon-side** backstop: instead
of relying on a survivor to reap it, a demoted daemon notices *on its own* that it
has been superseded and exits. The two mechanisms cover each other — the
successor-reap handles a live orchestrator's strays promptly; the self-retire
handles the orphaned-by-a-dead-orchestrator case that the reap never reaches.

### The fail-safe predicate

Each daemon reads the routing table and asks a single question with a deliberately
narrow "yes":

> Is the `active` entry a **different pid**, at a **strictly higher generation**,
> that is **actually listening**?

Only then is it a *confirmed live successor* that has superseded us. Every
ambiguous state — no table, an absent/unparseable `active`, our **own** pid still
active, a not-higher generation, or a successor that is not (yet) accepting
connections — returns **False (stay alive)**. The consequence that makes this safe
to run inside a live daemon: the genuinely-active daemon always reads *its own* pid
as `active`, so it can **never** self-retire; only a demoted generation with a
confirmed live successor ever can. (Implemented as a pure, injectable
`self_retire.is_superseded(config_dir, my_pid, my_generation)` in each consumer.)

### Two more gates before it actually exits

The predicate decides *superseded?*; two additional gates decide *safe to exit
now?*:

1. **Safe cutover point (never drop in-flight work).** Exit is gated on the same
   drain boundary the cutover uses (§ consumer contract, point 1): agent-bridge
   waits for no active session; the agent-dispatch coordinator waits for the
   `DrainGate` to report no in-flight `/claim` (a claimed task is already durable
   in the queue DB). A busy demoted daemon keeps serving and only exits once it
   has drained to that boundary — clients have already followed the flipped route,
   so it trends to idle on its own.
2. **K-confirmation (honor "confirm the precondition, don't act on one transient
   miss").** The (superseded ∧ safe) condition must hold for *K consecutive*
   polls before the daemon requests its own clean shutdown (`uvicorn should_exit`,
   which runs the normal drain + `clear_if_owner`-is-a-no-op-since-we're-not-active
   path). Any miss resets the counter.

### A bounded ceiling on the K-confirmation gate — never an unbounded wait

Gate 2 above has a real failure mode a transient-miss debounce alone does not
cover: on a host with frequent, *unrelated* concurrent traffic (another channel
the same busy-predicate checks — a hook/classify/tracking-write call, a sibling
request), some channel can be non-idle often enough that "every channel quiet"
never lands on two consecutive polls **in a row**, even though the daemon
correctly observes *superseded* on every single poll. Confirmed in production:
a demoted `agent-worktrees` status-monitor lingered for **multiple days** this
way (copilot-extensions#5326 follow-up, landed in #5359) — busy_reasons() kept
resetting the confirm counter to zero indefinitely.

The fix generalizes to every consumer of this gate: once `superseded` is first
observed, start a clock (not a counter). Reaching a bounded ceiling
(`_SELF_RETIRE_MAX_GRACE_S`-shaped — on the order of 1-2 minutes, well short of
this doc's own "never linger more than ~10 minutes" architecture ceiling) forces
the exit **regardless of busy_reasons()** — the daemon finishes whatever single
bounded unit of work is already in flight, writes its state, and exits. K
consecutive clean confirms is the *fast, graceful* path; the deadline is the
**upper bound that must never be skippable**. A demoted daemon that depends
solely on K-confirmation, with no deadline fallback, does not satisfy this
pattern's invariants.

### Admission discipline once superseded — stop growing scope, finish what's already yours

The moment a daemon observes `superseded` (same predicate as above), it must
immediately close **admission** — it may keep *serving* and *finishing* what it
already has, but must never let its own bounded scope grow further:

- **Never admit a new tracked source.** Whatever "source" means for this
  daemon (a new worktree/project folder to sweep, a new owned task-daemon, a
  new long-lived mapping to track) must stop being discoverable/addable the
  instant admission closes — not merely "stop starting new sweep cycles while
  an old one still holds a stale source list," but literally refuse to grow
  the set from here on. An update to something *already* tracked is not new
  scope and may still be accepted/served.
- **Single-shot (request/response) callers get an explicit, actionable
  rejection — never a bare connection-refused.** A caller that opens a
  connection, asks once, and expects one reply must receive a structured
  "I'm going away, here's why, re-resolve discovery and call the replacement"
  response (the existing `{"fallback": true, ...}` wire shape this suite's
  `work-coalescing-singleton` servers already use for a deadline-exceeded
  case is the right shape to extend with a `reason` — not a hard-closed
  listening socket that degrades to an ambiguous OS-level connection-refused
  the caller has to guess at).
- **Persistent subscribers get a notice, then an explicit close — never a
  silent drop.** A caller that holds an open streaming channel (SSE,
  WebSocket) — the expected shape for **daemon-to-daemon** communication
  between two long-running resident services in this suite, as opposed to an
  ordinary CLI-to-daemon single-shot call — must be sent an explicit
  going-away notice over that same channel (reason: superseded/updating)
  before the daemon closes it. A subscriber that only ever sees its socket
  die with no message has no way to distinguish "the daemon updated" from "the
  daemon crashed."
- **Immediately drop, never queue, anything that would still need to be
  started.** If a request arrives after admission has closed, it is rejected
  per the two bullets above — it must never be accepted into an internal
  backlog to run "once there's room." There is no "once there's room" once
  superseded.
- **Only already-admitted, already-in-flight transactional work for which this
  daemon is the sole custody bearer may still be allowed to finish.** That is
  the *only* class of work a superseded daemon still owes completion — not new
  work, not queued-but-unstarted work, just the bounded unit(s) genuinely
  already running that no successor could resume in its place.

### Opt-in until validated on a real cutover

### Default-on, with an opt-out

The loop is **default-on (opt-out)**: it is armed unless the env guard
(`AGENT_BRIDGE_SELF_RETIRE` / `AGENT_DISPATCH_SELF_RETIRE`) is explicitly falsy
(`0`/`false`/`no`/`off`; cadence and K are env-tunable via the `_POLL_S` /
`_CONFIRMATIONS` suffixes). The guard is evaluated *before any task is created*,
so disabling it means **no self-retire code runs at all**. It became the default
after real-cutover validation (§ Invariant 7): it arms for cutover-promoted
daemons and self-retires a demoted generation once idle, without disturbing
in-flight work or the healthy successor.

Crucially, the loop does **not** gate on "did *this* process self-publish"
(`publish_on_ready` / `not passive`). A zero-downtime cutover spawns the new
daemon `--passive` and the *orchestrator* promotes it by flipping the routing
table — so a promoted daemon's internal "I published" flag stays False even
though it is now the active generation. Gating on that flag would leave
self-retire **inert for exactly the `serve --passive` daemons it targets**.
Instead the loop **self-gates on active-ness**: its startup phase waits until the
routing table's `active` entry is *its own pid* before it captures its generation
and begins watching. A normal boot daemon satisfies that as soon as it publishes;
a cutover-promoted daemon satisfies it the moment the orchestrator flips to it; a
passive daemon that is never promoted never satisfies it and arms nothing.

### Never-Promoted Abandoned Passive (the downstream tracker)

Self-retire's active-ness gate is exactly the reason it **cannot** rescue a
`spawn_passive` daemon whose cutover never reached the flip: if the
orchestrator process itself dies (crash, `kill -9`, an operator Ctrl-C)
before flipping the routing table -- or after the flip but before retiring the
old daemon, in a way `recover_stale_cutover` cannot reconcile because the old
survivor is also gone -- the freshly spawned passive never observes its own
pid as `active`. It lingers indefinitely holding a port/pid; two independent
field sightings (2026-08-19, 2026-08-28) found a `serve --passive`
`agent-dispatch` coordinator stranded for hours after an aborted cutover.

The fix mirrors the existing old-survivor recovery, but for the *new* side:

- `zdd.cutover.CutoverOrchestrator` records the passive's own pid (`new_pid`)
  in the breadcrumb the moment `spawn_passive` returns -- before the health
  gate, flip, or drain even begin -- so a crash anywhere past that point
  leaves a durable, attributable record naming it.
- `zdd.breadcrumb.reap_abandoned_passive` is the matching pure decision: it
  terminates `new_pid` only when the breadcrumb is non-terminal
  (`is_stale`), aged past a grace window (so a cutover still genuinely in
  flight -- especially mid-drain -- is never disturbed), and `new_pid` does
  **not** match the caller's confirmed-live routing-table `active` pid (i.e.
  it was truly never promoted). Every ambiguous case is a no-op.
- Each consumer composes it with its own identity-verified liveness check and
  termination: agent-dispatch's `reap.reap_abandoned_passive_backstop` reuses
  its coordinator-process enumeration (`is_live_coordinator_pid`); agent-bridge's
  `_reap_abandoned_passive` reuses its own `_pid_is_agent_bridge` +
  `_ensure_retired_daemon_exited`. Both run it once at the top of the next
  `deploy`/cutover attempt (alongside `recover_stale_cutover`, using the
  breadcrumb read *before* that call rewrites it to a terminal state).
  agent-dispatch additionally arms a **periodic** sweep in the coordinator
  itself (`AGENT_DISPATCH_ABANDONED_PASSIVE_REAP`, default-ON, gated the same
  way as self-retire -- only the confirmed-active coordinator runs it) so a
  stranded passive is reaped even if no operator ever runs another deploy.

### Slot-ownership observability: `/health["slot"]` (process-slot-ownership Phase 5)

The coordinator's `GET /health` (and therefore `agent-dispatch health`, which
renders that response verbatim) exposes a `"slot"` descriptor answering the
`process -> slot -> owner -> alive?` question the `process-slot-ownership`
effort's Phase 5 named: the routing table's `active`/`previous` entries are
the *slot*, this process's own pid compared against `active.pid` is the
*owner* question (`role`: `"active"` / `"passive"` / `"unknown"`), and the
self-retire and abandoned-passive-reap loops' own live status (`enabled`,
`armed`, and their current supersession/reap state) is the *alive?*
liveness-monitoring answer -- all without grepping logs. `_slot_descriptor()`
in `coordinator.py` is read-only and best-effort: a routing-table read
failure degrades to `active`/`previous: null` rather than failing the whole
`/health` response. agent-bridge parity (the same descriptor shape on its own
`/health`) is the natural next slice; not yet done.

## Per-plugin adoption

| Plugin | Daemon(s) | State today | Safe cutover point | Work |
|--------|-----------|-------------|--------------------|------|
| **agent-bridge** | session-host (hosts a live Copilot CLI per session) | ✅ **Cutover already the default** — full `zdd` (active/passive, `drain`/`undrain`, breadcrumb recover), and `install.ps1 update`/POSIX `update` already run the cutover unconditionally whenever a live daemon is running (Thread B, invariant #1) -- no flag, no opt-in. `-ZeroDowntime` is accepted only as a deprecated no-op for back-compat callers. A public `deploy` verb also exists as an operator escape hatch / self-triggered-update spawn target | **turn boundary with no active background task** — drain refuses new turns, waits for the in-flight turn to finish | Keep it the exemplar; extract shared shapes into `zdd`. Per invariant #1's target state, eventually demote the `deploy` verb to an installer-internal seam once nothing external needs to trigger a cutover by hand |
| **agent-index** | (a) service shell; (b) indexing worker subprocesses; (c) warm embedding **engine** daemon | ✅ **Service cutover already installer-driven** — `install.ps1 update`'s `Invoke-ServiceCutover` already runs `agent_index deploy` (the zdd active/passive flip) unconditionally whenever a live, healthy service is running -- no flag, no opt-in; `plugin.json` declares `"zeroDowntimeUpdate": true` and `install.ps1` accepts (and ignores) a back-compat `-ZeroDowntime` switch. **Worker re-adoption** already works too (workers are detached, persist progress to `tasks.db`, and are re-adopted after a service restart). The **engine daemon is left untouched by a service update by design** | service: between task dispatches; worker: `run_reindex` checkpoints `path_index` per flush, `resume_since` skips stored files (the only non-resumable slice is the current unflushed batch) | Give the **engine daemon** an explicit *outlive + reconnect* story (it is the [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) warm runtime): confirm it survives a service cutover and the new service reconnects to it; the installer version- + health-gates its own engine cutover |
| **agent-dispatch** | coordinator (`serve`, FastAPI); supervisor (`supervise` spawn loop); spawned embodied/headless workers; detached `run` waiters | ✅ **Cutover already the default** — `install.ps1 update`/`install.sh update` already run `Invoke-CoordinatorCutover`/`_coordinator_cutover` unconditionally whenever a live, routed coordinator is running (Thread B; parity with agent-bridge's Invariant #1, no flag involved on either platform), falling back to stop-and-swap only for a pre-Thread-B coordinator or a failed cutover. `plugin.json` now also declares `"zeroDowntimeUpdate": true` and `install.ps1` accepts (and ignores) a back-compat `-ZeroDowntime` switch, so the reconcile-at-launch path's redundant flag-pass no longer needs a special case. The coordinator also has a live self-update loop, **default-ON / opt-out** (`AGENT_DISPATCH_SELF_UPDATE=0` to disable) that notices a newer `current-version` slot between launches and self-triggers the same cutover; a public `deploy` verb exists (parity with agent-bridge's) for the same cutover run by hand. The **`supervise serve` singleton daemon** has its own analogous but distinct live self-update loop, **default-ON / opt-out** (`AGENT_DISPATCH_SUPERVISOR_SELF_UPDATE=0` to disable, #2259 -- there is no launch-path protocol in this harness to flip an opt-in flag before a daemon's first boot, so this one mirrors self-retire's default-on shape): since a reconcile tick always runs to completion before the next one starts, every tick boundary is already a safe cutover point (no K-confirmation needed, unlike the coordinator's routing-table race), so a stale check simply winds down every managed unit, releases the single-instance lease, spawns a successor daemon on the newer interpreter with its own unmodified `sys.argv`, and exits — closing the gap where a scheduled-task-launched daemon with no periodic trigger otherwise never cycles onto a newly-published version between logons | coordinator: between task **claims** (drain = stop claiming, let a claimed-but-unstarted task settle to a resumable state in the queue DB); supervisor: between spawn reservations | **Repossess** the supervisor + spawned workers: let them **outlive** the coordinator swap and re-adopt via the durable SQLite queue DB + `running-version.json` (they already run detached). Add `stamp`/`provision` (Thread A) at the same time |
| **agent-worktrees** | resident `status-monitor` (sweep loop + hook/classify/tracking-write control plane) | ✅ **Cutover already the default** — `zdd` is vendored under `plugins/agent-worktrees/libs/zdd/`; `plugin.json` declares `"zeroDowntimeUpdate": true`; and both `install.ps1 update` and `install.sh update` drive the `status_monitor_cutover` seam automatically whenever a live, routed monitor is running, while still accepting the historical `-ZeroDowntime` / `--zero-downtime` switches only for reconcile/back-compat callers. The routed loopback control plane is guarded by an owner-scoped bearer token loaded from `status-monitor-control.token`, so the installer/cutover client can drain, promote, and retire generations without exposing an unauthenticated local surface. `agent-worktrees doctor` now also reports the shared `zdd.diagnostics` audit and can opt in to the identity-bound repair pass with `--apply-daemon-health`. | **close admission first, keep the sweep marked active through reconciliation/pane-reap mutations, then wait for every already-accepted hook/classify/tracking-write handler plus in-flight write compute to drain**; generation self-retire re-checks routed supersession at the top of every sweep iteration and exits only at that same safe point | No plugin-specific adoption gap remains; future diagnostic/self-repair improvements should stay shared in `zdd` rather than reintroducing monitor-specific cutover logic |
| **worktree-manager** | resident `mux-daemon` (`mux-status-v1` status-apply + live-republish service) | ✅ **Cutover already the default** — `zdd` is vendored under `worktree-manager/libs/zdd/`, and `worktree-manager update`'s `self_install.self_update` runs `activate_after_update`, which drives a `CutoverOrchestrator`, whenever a live, **routed** `mux-daemon` is present on either platform. Because Worktree Manager is not a marketplace plugin, there is no `plugin.json` / `zeroDowntimeUpdate` manifest bit here; the activation seam is the self-update path itself. The one-time first upgrade from a pre-adoption **lock-only** daemon uses the legacy identity-bound replacement path instead of an orchestrated drain because there is no routed control plane to talk to yet. Once a routed generation exists, `activate_after_update` **no-ops with no orchestrator constructed at all** when that generation already reports the target version and answers healthy (`self_update` reconciles on every session launch, far more often than the payload actually changes) -- unless a durable breadcrumb marks a prior cutover aborted mid-flight, in which case that lock-free shortcut is skipped and recovery/reaping always run first, under the lock, before any no-op decision; the full spawn/flip/drain/retire cutover contract then runs only if the post-recovery route still needs it (a genuinely different version). The routed loopback control plane uses the same owner-scoped token shape, and passive generations self-promote + self-retire around the `mux-status-v1` active table. `worktree-manager doctor` now includes the shared `zdd.diagnostics` audit and can opt in to the repair pass with `--apply-daemon-health`. | **close `mux-status-v1` admissions, stop periodic live-mapping republishes, then wait for every already-accepted status-apply handler and the current republish cycle to drain**; disk-backed registry register/remove writes stay durable outside the resident drain gate | No new hand-off manifest is needed beyond `mux_mapping_registry` plus the shared `zdd` breadcrumb; future shared diagnostics belong in `zdd`, not a manager-only fork |
| **agent-vault** | `agent_vault.service` (owns the in-memory unlocked KeePass master + credential cache; serves loopback/pipe/TCP) | ❌ **None** — update re-registers the task + starts; the unlocked master + cache **die on restart** | no in-flight secret request in flight (requests are short) | **Lightest tier** (connection-owner, dotfiles#1333): the "connection" is the *authenticated in-memory session*. Make **`install.ps1 update`/activation** adopt `zdd` routing (clients follow the new daemon) and perform the flip automatically, and define **reconnect** as re-establishing the auth state on the new daemon — either (a) hand off via the opt-in **encrypted persistent cache** (`credential-cache.enc` + wrapped key) so the new daemon warms without a re-prompt, or (b) accept a single re-unlock prompt on first post-cutover use. Drain = finish the in-flight request; never cut over mid-request |
| **agent-mcp** | `serve` (resident warmth daemon: one-shot `call`/`materialize`/`list`, plus long-lived multiplexed `bridge`/`forward` attach sessions) | ✅ **Cutover already the default on activation** — `zdd` vendored + adopted; `init.ps1`/`init.sh` (agent-mcp's installer, named `init` rather than `install`) now invoke `agent-mcp cutover --require-live --force --json` unconditionally on every activation, right before the existing stale-slot process reap. `--require-live` makes this safe with no opt-in: it never starts a resident daemon where none was running (`serve` stays optional, on-demand warmth) and no-ops when the live daemon is already on the target version; only a genuinely live, differently-versioned daemon actually cuts over. A public `cutover` verb also remains for a manual/operator-triggered run | one-shot ops: unaffected by drain, finish normally; attach sessions: drain refuses *new* attaches (the client's existing "live host refuses attach → direct in-process bridge" fallback already covers this) and lets already-attached sessions ride out to natural close before the old generation retires | Add the **generation self-retire** backstop (deferred in v1, matching the reference implementation's own optional status for it). If agent-mcp's installer is ever renamed `install.ps1`/`install.sh` to converge with the other plugins' naming convention, also set `"zeroDowntimeUpdate": true` (inert today since the launch-time reconciler only wires that flag onto a script named `install.ps1`). See effort `agent-mcp-graceful-cutover` |

### Classification note

agent-vault is **borderline Thread-A/Thread-B**: it is a connection-owner (holds
authenticated session state) but is otherwise a local service with durable
discovery and a restartable runtime layout. Treat its cutover as the *lightest*
tier — routing flip + reconnect-the-auth-state — not the full turn-boundary drain
agent-bridge needs.

## Invariants (binding)

0. **Any long-lived resident daemon in the suite that serves callers through a
   discoverable local endpoint, routed control plane, or other non-resumable
   in-flight work MUST adopt this pattern.** The common case is an `agent-*`
   plugin in the [`Runtime service`](README.md#plugin-shapes) shape, but the
   obligation is behavioral, not nominal: if any suite component (including a
   sibling runtime such as `worktree-manager`) adds that class of resident
   daemon under some other label, it still owes graceful cutover semantics.
   "It isn't classified as a runtime service" is not an exemption. The only
   valid alternatives are to show that the process is **not** that class of
   daemon at all and belongs under a different lifecycle pattern instead (for
   example [`ephemeral-process-reaping`](ephemeral-process-reaping.md) for a
   detached helper), or that it explicitly fits the lighter
   [`service-lifecycle-supervision`](service-lifecycle-supervision.md)
   wind-down-and-successor path for a singleton daemon with no shared endpoint
   and no in-flight request to drain.
1. **Cutover is installer-driven and automatic — there is NO externally-driven
   `deploy` command.** The installer's `update`/activation (and first-use
   `provision`) performs the cutover in-process whenever it detects a live
   daemon. No operator/agent runs a cutover verb, no opt-in switch is required;
   any `drain`/`undrain`/cutover entry points are installer-internal seams, not
   an operator CLI surface.
   > **Interim reality (not yet converged):** agent-bridge, agent-dispatch,
   > and agent-index all now run their install-path cutover fully
   > installer-driven / unconditionally (no flag, no opt-in -- see the
   > per-plugin table); none of their `install.ps1`/`install.sh update` paths
   > have a gap left to close. Yet all three (agent-index included) still
   > ship a public `deploy` verb. It is not filling an install-path gap for
   > any of them -- it persists as (a) a manual operator escape hatch and,
   > for agent-dispatch specifically, (b) the spawn target for its opt-in
   > live self-update loop, which reacts to drift *between* installer runs, a
   > case the installer-driven path does not cover by itself. Once a manual
   > `deploy` is judged unnecessary for a given plugin (e.g. once
   > agent-dispatch's self-update loop, or an equivalent between-launch
   > trigger, has soaked enough to be trusted end to end), demote that
   > plugin's verb back to an installer-internal seam per this invariant --
   > don't treat its present existence as the target state.
2. **No stop-then-start for a routine version bump.** The default is a cutover
   (new slot beside old → flip → drain → retire). Stop-and-swap is the fallback
   only when a cutover cannot run or fails.
3. **The old daemon is retired only after its drain reaches the safe cutover
   point** (or forced past a bounded timeout, logged). Define that point per
   daemon; never retire mid-non-resumable-work.
4. **Never strand clients.** After the commit point, if the old endpoint is
   unreachable, **commit-forward** to the healthy new one (never roll back into a
   dead daemon). An aborted pre-commit cutover **rolls back** (old stays live).
   A demoted daemon's own admission-closing and caller-notification obligations
   are the daemon-side half of this same invariant — see
   [§ Admission discipline once superseded](#admission-discipline-once-superseded--stop-growing-scope-finish-whats-already-yours)
   and [§ A bounded ceiling on the K-confirmation gate](#a-bounded-ceiling-on-the-k-confirmation-gate--never-an-unbounded-wait).
5. **A passive instance seizes no shared singleton** (relay port, pinned socket,
   single-instance lock) until promotion.
6. **`zdd` stays pure + consumer-agnostic** and byte-identical across consumers
   (synced from the repo-root `libs/zdd/`), exactly like `versioned_runtime.py`.
7. **The cutover is validated off the live box first.** Prefer an isolated-HOME /
   clean-room rehearsal of an installer `update` that exercises the cutover; the
   live-daemon activation is operator-gated (agent-bridge is the launcher the
   harness itself runs under).
   (agent-bridge is the launcher the harness itself runs under).
8. **Cross-version process isolation: no reach-in, discovery + IPC only, and
   old versions self-retire.** A process belonging to version `N` must never
   (a) spawn a child with its `cwd`, working directory, or any other
   filesystem presence pinned inside a *different* version's slot — not an
   older version's, and not a newer one mid-install — or (b) terminate
   another version's resident daemon by a bare PID lookup. The only sanctioned
   way to interact with another version's daemon is to **discover** its
   reserved endpoint via the routing table (`zdd.routing`) and **talk to it
   over the existing control-plane IPC** (health/drain/shutdown). The daemon
   being cut away from is responsible for **retiring itself** on receiving
   that request (or, for a pre-protocol legacy resident, on losing the
   routing table's "active" claim) — the orchestrating process only ever
   asks, it never reaches in and kills. A kill-by-PID fallback is permitted
   **only** for a legacy resident that predates this protocol entirely (no
   routing-table entry, hence no IPC endpoint to ask), lives in one central,
   shared primitive (`zdd.diagnostics.terminate_pid_if_identity` —
   identity-bound via a captured process-start-time token, immediately before
   signaling, never a bare `os.kill`/`taskkill` by PID alone), and is only
   ever invocable against a version strictly older than the caller's own.
   Violating (a) is what let a spawned/stranded passive's open `cwd` handle
   block every later `shutil.rmtree(slot)` self-install attempt on Windows
   (copilot-extensions#4999); violating (b) with a bare PID kill reopens the
   TOCTOU race where the OS reuses that PID for an unrelated process between
   the identity check and the signal (copilot-extensions#5006).

## Cross-platform parity (Windows `install.ps1` ↔ POSIX `install.sh`)

The cutover **mechanism** is the OS-agnostic `zdd` library + the daemon's own
Python seam (`_cutover` / `deploy`), so the *hard part is shared*. What
legitimately differs is the **service-manager integration**, and the two
platforms reach the **same behavioral guarantee** (an update never hard-kills
in-flight, non-resumable work) by different, OS-appropriate routes:

For non-marketplace peers such as **worktree-manager**, read the
`install.ps1`/`install.sh` shorthand here as "the equivalent update activation
seam" — in its case `self_install.self_update`, which now drives the same `zdd`
cutover behavior on both platforms even though it has no plugin manifest.

| | Windows (`install.ps1`) | POSIX (`install.sh`) |
|---|---|---|
| Service manager | Scheduled Task, but `conhost --headless` **detaches** the daemon — the task never tracks the live process | systemd `--user` unit **tracks** the daemon (its `ExecStart` PID) |
| A plain "stop" is… | a **hard kill** (no clean SIGTERM-drain path) — so the zdd cutover is **required** to avoid dropping in-flight work | a **SIGTERM**: `Type=simple` + `Restart=on-failure`, and the daemons drain on SIGTERM (uvicorn for the coordinator/index service; a signal handler for agent-vault). So `systemctl restart` is already **graceful** (drains in-flight work, exits 0 → no resurrect), just with a brief API-unavailable blip |
| Default update path | in-process zdd cutover (no opt-in) | **zdd cutover when a live routed daemon is serving** (agent-bridge, agent-dispatch, agent-index), **falling back** to the SIGTERM-graceful `systemctl restart` when a cutover can't run or fails |
| Post-cutover reconcile | idempotently ensure the existing boot-task definition **without starting** it (`-NoStart`): no-op when unchanged, update in place for an intentional definition migration, and register only when absent; the detached daemon serves and the next boot resolves the new slot through the stable launcher | refresh the unit **without restarting** (`_install_service --no-restart`); the old (unit-tracked) daemon exits cleanly so `Restart=on-failure` never resurrects it; the detached survivor serves; next boot starts the new slot |

Consequences of this equivalence:

- **The binding invariant holds on both platforms.** POSIX meets it *natively*
  via SIGTERM-graceful drain even without the zdd cutover; the cutover on POSIX
  is the **zero-downtime enhancement** (removes the brief blip), gated on a live
  *routed* (Thread-B) daemon with the `systemctl restart` as the always-safe
  fallback.
- **Detached daemons still participate, but via activation-seam parity rather
  than a service-manager fallback.** `agent-worktrees` reaches the same
  cross-platform guarantee through `install.ps1`/`install.sh` plus the routed
  `status-monitor` control plane, and `worktree-manager` does so through its
  `self_install.self_update` seam even though it is not a marketplace plugin
  and therefore has no `zeroDowntimeUpdate` manifest bit. Neither daemon is a
  systemd-managed service, so their POSIX lane is the direct `zdd` cutover
  path, not a `systemctl restart` fallback.
- **The `AGENT_BRIDGE_ZERO_DOWNTIME` / `-ZeroDowntime` opt-in is retired on both
  lanes** — the cutover is the default whenever a live daemon is running.
- **agent-vault needs no `.sh` cutover:** its fixed-endpoint service drains on
  SIGTERM (`systemctl restart`) and reconnects via the OS-agnostic persistent
  encrypted cache — the POSIX twin of the `.ps1` cooperative `--stop` drain +
  reconnect.
- **Validation:** the OS-agnostic Python cutover is exercised by the clean-room
  `agent-dispatch-cutover` scenario (Linux); the systemd `--user` integration of
  the `.sh` path is operator-gated (like the live-daemon activation), and the
  `.sh` change always **falls back** to the already-proven SIGTERM-graceful
  `systemctl restart`, so the worst case is today's behavior.

## Common review findings — a pre-flight self-check

The `graceful-cutover-worktrees-and-ssh` effort landed six phases across eight
PRs (#4447, #4483, #4497, #4518, #4550, #4554, #4563, #4583); the four PRs that
actually implemented cutover/repair logic (#4447, #4497, #4518, #4563) needed
6-14 automated review rounds each before merging, almost entirely on a small,
*recurring* set of bug classes rather than novel ones each round. Read this
section — and actually check your own diff against it — **before** opening a
PR that touches cutover, drain, promotion, or process-repair logic; it is
cheaper than a review round.

1. **Serialize cutover attempts under one lease.** Two overlapping cutover
   invocations (a rapid-fire re-release, or a manual repair racing an
   in-progress installer-driven cutover) must never both proceed. Acquire a
   single exclusive lease/guard for the whole cutover *and* for any repair
   path that also touches promotion/retirement state — an audit/`doctor`
   snapshot may read without the lease, but anything that could mutate
   generation state must hold it.
2. **Promote before you retire — never the reverse.** The old generation may
   only retire once the successor's promotion is *confirmed*, not merely
   attempted. A failure between "route flipped" and "successor confirmed
   live" must roll back or retry, never leave both generations gone.
3. **Drain boundary = admission closed AND every already-admitted unit of
   work finished** — not "between sweep ticks." If the daemon serves any
   concurrent request surface (a hook/classify/control-plane endpoint)
   *separate* from its periodic sweep, both must be drained; a sweep-only
   drain boundary misses in-flight requests.
4. **A repair/self-heal path must re-validate its target immediately before
   acting, not just at snapshot time.** Between an audit snapshot and a
   repair action, the routed active daemon can change, a passive can be
   promoted, or a candidate can exit and its PID be reused. Re-check the
   target's identity (owner/lock/routing agreement) at the point of action,
   not only when the finding was first produced.
5. **Any process termination by PID needs a direct, dedicated safety test —
   an end-to-end rehearsal is not sufficient evidence.** Two separate safety
   layers are involved, and a reaper needs both: (a) **identity-bound
   termination** — `zdd.diagnostics.process_start_time` obtains a PID's
   identity token and `zdd.diagnostics.terminate_pid_if_identity` verifies
   only that the token still matches before signaling, refusing on any
   mismatch (stale PID, reused PID); and (b) **owner validation** — a
   separate check that the candidate is actually the legitimate target, not
   an unrelated live process, performed by the higher-level
   `zdd.diagnostics.audit_daemon_health`/`apply_daemon_health` path, not by
   the low-level pair alone. Reuse these shared primitives — a plugin-private
   module like `agent_worktrees.locks`/`agent_worktrees.procs` is an example
   of the same discipline, not something another plugin can import. Ship a
   unit test proving both layers, not just the identity-token match. This
   applies to every destructive repair/reap path, not only the first one you
   write.
6. **Loopback control-plane surfaces need owner-scoped auth,** not just
   "loopback-only." Any local process can otherwise reach the endpoint. Use a
   per-owner bearer token file (best-effort restrictive permissions), and
   keep ambient `PYTHONPATH`/environment from leaking into a
   subprocess-launched activation helper — clear or sanitize it explicitly at
   the launch seam.
7. **State your cross-platform coverage explicitly, and don't conflate
   "POSIX" with "Linux."** A process-census/liveness primitive written
   against `/proc` or `pidfd` is Linux-specific, not POSIX-general — macOS is
   POSIX but has neither. If your change claims general cross-platform
   support, name Windows, Linux, **and** macOS explicitly and say what each
   one does (implemented, or an explicit, justified exemption) rather than
   letting "POSIX" silently mean "Linux, untested elsewhere."
8. **Bookkeeping findings are the cheapest to prevent and the most common to
   ship anyway** — before opening the PR, re-check: when a change touches a
   vendored/shared library, the changefile names every **consuming plugin**
   whose payload actually changed as a result (never the shared library
   itself — a library like `zdd`/`ssh-manager` isn't independently released;
   see docs/pipelines.md's changefile requirement); the PR's Documentation
   impact statement matches the final diff, not an earlier draft; no
   unrelated generated/vendored directory rode along in the diff (`git
   status`/`git diff --stat` against your intended file list before
   pushing); and if the
   PR touches an effort README, its Journal's completion claims are checked
   against that same phase's own Validation Plan items, not asserted
   independently.

## Rollout sequencing

1. **agent-dispatch** — ~~highest value, currently kill-and-restart~~ **done**:
   vendored `zdd`, `install.ps1 update`/`install.sh update` auto-cutover
   in-process unconditionally (no operator `deploy` verb required, though one
   exists as an escape hatch), supervisor/workers already outlive the
   coordinator swap via the queue DB. Thread-A `stamp`/`provision` remains
   open.
2. **agent-worktrees** — **done**: vendored `zdd`, set
   `"zeroDowntimeUpdate": true`, and wired `install.ps1 update`/`install.sh
   update` to auto-cut over the routed `status-monitor` whenever a live daemon
   is present. The control plane is loopback-only behind an owner-scoped token,
   and demoted generations self-retire only after admissions close, the current
   sweep/reconciliation work finishes, and already-admitted
   hook/classify/tracking-write activity drains.
3. **worktree-manager** — **done**: vendored `zdd` and wired
   `self_install.self_update` to auto-cut over the resident `mux-daemon` on
   both platforms. Its routed `mux-status-v1` control plane uses the same
   owner-scoped loopback token shape, and demoted generations wait for admitted
   status-apply work plus the current republish cycle to drain before exit.
4. **agent-vault** — installer-driven routing flip + auth-state reconnect
   (persistent-cache hand-off or re-unlock). Add Thread-A `stamp`/`provision`.
5. **agent-index engine daemon** — the outlive + reconnect gap (installer-driven
   engine cutover / re-adoption guarantee). The *service* cutover itself is
   already installer-driven and unconditional (see the per-plugin table).
6. **agent-bridge** — cutover is already unconditional in the installer
   (invariant #1's automatic-activation half is done); only the `deploy`
   subcommand's eventual demotion/retirement (the invariant's stricter
   "no operator verb at all" half) and folding any newly-shared shapes back
   into `zdd` remain.
7. **agent-mcp** — **done**: core mechanism (`agent-mcp cutover`) plus
   installer-driven activation (`init.ps1`/`init.sh` calling
   `cutover --require-live` unconditionally, effort
   `agent-mcp-graceful-cutover`); the generation self-retire backstop remains
   open (Phase 3 of that effort).

Each lands so the installer's own `update`/activation path performs the cutover
automatically, is `check-install-contract`-clean, and — per the `install-contract`
Hard rule on service lifecycle — keeps start/stop user-mode and never gates
*starting* the daemon on an elevation-capable step.
