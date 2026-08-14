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

Cut over **without a stop** by standing the new version up *beside* the old one
and moving work to it at a safe point:

```
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
`zdd` library already implements — do **not** reinvent it per plugin.

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

### Wiring it to the runtime layout

- The versioned-runtime reconciler chooses cutover for a **routine version-bump
  redeploy**: update the venv in place (no stop) and hand off via the plugin's
  `deploy` verb (the agent-bridge `-ZeroDowntime` install switch), falling back
  to stop-and-swap only if the cutover cannot run or fails.
- Binstubs, the scheduled-task launcher, and the deploy manifest are pinned at
  the concrete slot python and **rewritten on every cutover**.
- `running-version.json` (dotfiles#533) is the **reconcile signal** (current
  version/pid/start-time), *not* itself a handoff mechanism — the handoff is the
  routing flip.

## Per-plugin adoption

| Plugin | Daemon(s) | State today | Safe cutover point | Work |
|--------|-----------|-------------|--------------------|------|
| **agent-bridge** | session-host (hosts a live Copilot CLI per session) | ✅ **Reference** — full `zdd`: `deploy` (active/passive), `drain`/`undrain`, breadcrumb recover, `-ZeroDowntime` install switch | **turn boundary with no active background task** — drain refuses new turns, waits for the in-flight turn to finish | none (keep as the exemplar; extract shared shapes into `zdd` where useful) |
| **agent-index** | (a) service shell; (b) indexing worker subprocesses; (c) warm embedding **engine** daemon | ⚠️ **Partial** — the *service* has full `zdd` (routing/cutover/breadcrumb) and **worker re-adoption** (workers are detached, persist progress to `tasks.db`, and are re-adopted after a service restart); the **engine daemon is left untouched by a service update by design** | service: between task dispatches; worker: `run_reindex` checkpoints `path_index` per flush, `resume_since` skips stored files (the only non-resumable slice is the current unflushed batch) | Give the **engine daemon** an explicit *outlive + reconnect* story (it is the [`durable-vs-versioned-runtime`](durable-vs-versioned-runtime.md) warm runtime): confirm it survives a service cutover and the new service reconnects to it; version + health-gate its own separate `engine-update` cutover |
| **agent-dispatch** | coordinator (`serve`, FastAPI); supervisor (`supervise` spawn loop); spawned embodied/headless workers; detached `run` waiters | ❌ **None** — `install.ps1 update` **kills** old `serve`+`supervise` then reinstalls; `running-version.json` is signal-only | coordinator: between task **claims** (drain = stop claiming, let a claimed-but-unstarted task settle to a resumable state in the queue DB); supervisor: between spawn reservations | **Adopt `zdd`** for the coordinator (vendor `libs/zdd`, add a `deploy` verb + `drain`/`undrain` over the FastAPI app). **Repossess** the supervisor + spawned workers: let them **outlive** the coordinator swap and re-adopt via the durable SQLite queue DB + `running-version.json` (they already run detached), rather than being killed. Add `stamp`/`provision` (Thread A) at the same time |
| **agent-vault** | `agent_vault.service` (owns the in-memory unlocked KeePass master + credential cache; serves loopback/pipe/TCP) | ❌ **None** — update re-registers the task + starts; the unlocked master + cache **die on restart** | no in-flight secret request in flight (requests are short) | **Lightest tier** (connection-owner, dotfiles#1333): the "connection" is the *authenticated in-memory session*. Adopt `zdd` routing so clients follow the new daemon, and define **reconnect** as re-establishing the auth state on the new daemon — either (a) hand off via the opt-in **encrypted persistent cache** (`credential-cache.enc` + wrapped key) so the new daemon warms without a re-prompt, or (b) accept a single re-unlock prompt on first post-cutover use. Drain = finish the in-flight request; never cut over mid-request |

### Classification note

agent-vault is **borderline Thread-A/Thread-B**: it is a connection-owner (holds
authenticated session state) but is otherwise a local service with durable
discovery and a restartable runtime layout. Treat its cutover as the *lightest*
tier — routing flip + reconnect-the-auth-state — not the full turn-boundary drain
agent-bridge needs.

## Invariants (binding)

1. **No stop-then-start for a routine version bump.** The default is a cutover
   (new slot beside old → flip → drain → retire). Stop-and-swap is the fallback
   only when a cutover cannot run or fails.
2. **The old daemon is retired only after its drain reaches the safe cutover
   point** (or `--force` past a bounded timeout, logged). Define that point per
   daemon; never retire mid-non-resumable-work.
3. **Never strand clients.** After the commit point, if the old endpoint is
   unreachable, **commit-forward** to the healthy new one (never roll back into a
   dead daemon). An aborted pre-commit cutover **rolls back** (old stays live).
4. **A passive instance seizes no shared singleton** (relay port, pinned socket,
   single-instance lock) until promotion.
5. **`zdd` stays pure + consumer-agnostic** and byte-identical across consumers
   (synced from the repo-root `libs/zdd/`), exactly like `versioned_runtime.py`.
6. **The cutover is validated off the live box first.** Prefer an isolated-HOME /
   clean-room rehearsal of `deploy`; the live-daemon deploy is operator-gated
   (agent-bridge is the launcher the harness itself runs under).

## Rollout sequencing

1. **agent-dispatch** — highest value, currently kill-and-restart. Vendor `zdd`,
   add `deploy` + `drain`/`undrain` to the coordinator, repossess the
   supervisor/workers via the queue DB, and add Thread-A `stamp`/`provision`.
2. **agent-vault** — routing flip + auth-state reconnect (persistent-cache hand-off
   or re-unlock). Add Thread-A `stamp`/`provision`.
3. **agent-index engine daemon** — the outlive + reconnect gap (its own
   `engine-update` cutover / re-adoption guarantee).
4. Fold any newly-shared shapes back into `zdd` (keep it the single source).

Each lands behind the existing `-ZeroDowntime`/reconcile switch, is
`check-install-contract`-clean, and — per the `install-contract` Hard rule on
service lifecycle — keeps start/stop user-mode and never gates *starting* the
daemon on an elevation-capable step.
