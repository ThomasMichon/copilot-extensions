# Pattern: graceful daemon cutover (update without killing in-flight work)

> **Serves the vision:** a runtime update is invisible to work in progress.
> **Embodied by:** `agent-bridge` (reference), `agent-index` (service),
> and — as this pattern rolls out — `agent-dispatch` and `agent-vault`.
> **Companion patterns:** [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md)
> (decoupling a warm daemon from the swappable runtime),
> [`service-lifecycle-supervision`](service-lifecycle-supervision.md),
> [`local-endpoint-discovery`](local-endpoint-discovery.md).
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
| `zdd.breadcrumb` | Recovers a **stranded survivor** left drained by an *aborted* cutover: `recover_stale_cutover` undrains it so it is not stuck closed to new work (agent-bridge `deploy --recover`). |

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

## Per-plugin adoption

| Plugin | Daemon(s) | State today | Safe cutover point | Work |
|--------|-----------|-------------|--------------------|------|
| **agent-bridge** | session-host (hosts a live Copilot CLI per session) | ✅ **Reference mechanism** — full `zdd` (active/passive, `drain`/`undrain`, breadcrumb recover). Cutover is already moving into the install path via the `-ZeroDowntime` switch; a `deploy` subcommand also exists | **turn boundary with no active background task** — drain refuses new turns, waits for the in-flight turn to finish | Make cutover **fully installer-driven**: the `update`/activation path invokes the `zdd` cutover **in-process** whenever it detects a live daemon, so no `-ZeroDowntime` opt-in and no `deploy` invocation is needed. Demote any `deploy` subcommand to an installer-internal seam (or retire it). Keep it the exemplar; extract shared shapes into `zdd` |
| **agent-index** | (a) service shell; (b) indexing worker subprocesses; (c) warm embedding **engine** daemon | ⚠️ **Partial** — the *service* has full `zdd` (routing/cutover/breadcrumb) and **worker re-adoption** (workers are detached, persist progress to `tasks.db`, and are re-adopted after a service restart); the **engine daemon is left untouched by a service update by design** | service: between task dispatches; worker: `run_reindex` checkpoints `path_index` per flush, `resume_since` skips stored files (the only non-resumable slice is the current unflushed batch) | Ensure the service cutover is **installer-driven** (activation performs it, not a `deploy` call). Give the **engine daemon** an explicit *outlive + reconnect* story (it is the [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) warm runtime): confirm it survives a service cutover and the new service reconnects to it; the installer version- + health-gates its own engine cutover |
| **agent-dispatch** | coordinator (`serve`, FastAPI); supervisor (`supervise` spawn loop); spawned embodied/headless workers; detached `run` waiters | ❌ **None** — `install.ps1 update` **kills** old `serve`+`supervise` then reinstalls; `running-version.json` is signal-only | coordinator: between task **claims** (drain = stop claiming, let a claimed-but-unstarted task settle to a resumable state in the queue DB); supervisor: between spawn reservations | **Adopt `zdd`** for the coordinator (vendor `libs/zdd`) and make **`install.ps1 update`/activation auto-cutover in-process** — replace the kill-old-`serve`/`supervise` step with: detect the live coordinator, cut over, retire. Expose `drain`/`undrain` only as **internal seams** the installer calls (over the FastAPI app), not an operator `deploy` verb. **Repossess** the supervisor + spawned workers: let them **outlive** the coordinator swap and re-adopt via the durable SQLite queue DB + `running-version.json` (they already run detached). Add `stamp`/`provision` (Thread A) at the same time |
| **agent-vault** | `agent_vault.service` (owns the in-memory unlocked KeePass master + credential cache; serves loopback/pipe/TCP) | ❌ **None** — update re-registers the task + starts; the unlocked master + cache **die on restart** | no in-flight secret request in flight (requests are short) | **Lightest tier** (connection-owner, dotfiles#1333): the "connection" is the *authenticated in-memory session*. Make **`install.ps1 update`/activation** adopt `zdd` routing (clients follow the new daemon) and perform the flip automatically, and define **reconnect** as re-establishing the auth state on the new daemon — either (a) hand off via the opt-in **encrypted persistent cache** (`credential-cache.enc` + wrapped key) so the new daemon warms without a re-prompt, or (b) accept a single re-unlock prompt on first post-cutover use. Drain = finish the in-flight request; never cut over mid-request |

### Classification note

agent-vault is **borderline Thread-A/Thread-B**: it is a connection-owner (holds
authenticated session state) but is otherwise a local service with durable
discovery and a restartable runtime layout. Treat its cutover as the *lightest*
tier — routing flip + reconnect-the-auth-state — not the full turn-boundary drain
agent-bridge needs.

## Invariants (binding)

1. **Cutover is installer-driven and automatic — there is NO externally-driven
   `deploy` command.** The installer's `update`/activation (and first-use
   `provision`) performs the cutover in-process whenever it detects a live
   daemon. No operator/agent runs a cutover verb, no opt-in switch is required;
   any `drain`/`undrain`/cutover entry points are installer-internal seams, not
   an operator CLI surface.
2. **No stop-then-start for a routine version bump.** The default is a cutover
   (new slot beside old → flip → drain → retire). Stop-and-swap is the fallback
   only when a cutover cannot run or fails.
3. **The old daemon is retired only after its drain reaches the safe cutover
   point** (or forced past a bounded timeout, logged). Define that point per
   daemon; never retire mid-non-resumable-work.
4. **Never strand clients.** After the commit point, if the old endpoint is
   unreachable, **commit-forward** to the healthy new one (never roll back into a
   dead daemon). An aborted pre-commit cutover **rolls back** (old stays live).
5. **A passive instance seizes no shared singleton** (relay port, pinned socket,
   single-instance lock) until promotion.
6. **`zdd` stays pure + consumer-agnostic** and byte-identical across consumers
   (synced from the repo-root `libs/zdd/`), exactly like `versioned_runtime.py`.
7. **The cutover is validated off the live box first.** Prefer an isolated-HOME /
   clean-room rehearsal of an installer `update` that exercises the cutover; the
   live-daemon activation is operator-gated (agent-bridge is the launcher the
   harness itself runs under).
   (agent-bridge is the launcher the harness itself runs under).

## Rollout sequencing

1. **agent-dispatch** — highest value, currently kill-and-restart. Vendor `zdd`,
   make `install.ps1 update`/activation **auto-cutover in-process** (internal
   `drain`/`undrain` seams over the FastAPI app — no operator `deploy` verb),
   repossess the supervisor/workers via the queue DB, and add Thread-A
   `stamp`/`provision`.
2. **agent-vault** — installer-driven routing flip + auth-state reconnect
   (persistent-cache hand-off or re-unlock). Add Thread-A `stamp`/`provision`.
3. **agent-index engine daemon** — the outlive + reconnect gap (installer-driven
   engine cutover / re-adoption guarantee).
4. **agent-bridge** — fold cutover fully into the installer (retire the
   `-ZeroDowntime` opt-in and demote/retire the `deploy` subcommand), and fold
   any newly-shared shapes back into `zdd` (keep it the single source).

Each lands so the installer's own `update`/activation path performs the cutover
automatically, is `check-install-contract`-clean, and — per the `install-contract`
Hard rule on service lifecycle — keeps start/stop user-mode and never gates
*starting* the daemon on an elevation-capable step.
