# Process Telemetry — Vision

- **Subject:** How `agent-*` runtime plugins emit the signal of their own process
  footprint — spawns, lifetimes, and resource churn — so a control-plane repo can
  attribute fleet-wide CPU/GPU/RAM/disk/network activity back to the fabric that
  caused it
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-09-12
- **Reality docs:** [`plugins/agent-dispatch/src/agent_dispatch/telemetry.py`](../../plugins/agent-dispatch/src/agent_dispatch/telemetry.py) ·
  [`plugins/agent-bridge/src/agent_bridge/telemetry.py`](../../plugins/agent-bridge/src/agent_bridge/telemetry.py)
  — the existing generic telemetry seam (fail-open sink registration, the
  built-in spool sink, env/config-file wiring) this vision generalizes and
  extends with a new event kind; see also `docs/patterns/runtime-agent-plugin.md`
  for the current runtime-plugin shape

## Purpose & Intent

The agent fabric ([`visions/agent-fabric`](../agent-fabric/README.md)) spawns a
lot of short-lived processes on a host — worktree setup scripts, shells, CLI
subprocess wrappers, session hosts, MCP servers, dispatch supervisors — often in
bursts, often detached, often gone before anyone looks. When a host runs hot or
churns memory, a control-plane repo watching that host has no reliable way to
say *which plugin, which command, which spawned lineage* was responsible. The
process table shows a `conhost` or a `pwsh` and nothing about which `agent-*`
capability launched it or why.

**Process Telemetry is the standing capability for a runtime plugin to say, of
its own accord, "this process is mine, it exists for this reason, and here is
what it cost."** It is deliberately **narrow and cheap**: an opt-in
instrumentation hook a runtime plugin's own launch paths call when they spawn or
supervise a process, emitting a structured, low-cardinality provenance record
(plugin, command/verb, a stable source tag, parent lineage) alongside whatever
resource-usage figures are cheap to sample at spawn/exit. It does **not** run a
system-wide process monitor itself, and it does **not** aggregate, store, or
alert on anything — those are a consuming control-plane's job (a home facility's
telemetry spine, an org's observability stack, or nothing at all if a repo
never wires it up).

Success is that any `agent-*` plugin that spawns processes can adopt one small,
optional hook — the **same fail-open seam pattern `agent-dispatch` and
`agent-bridge` already ship** (a registrable sink, a built-in dependency-free
spool sink, env-var/config-file wiring, generic dict-shaped events) — and, for
free, become attributable: a consumer downstream can answer "how many processes
has `agent-worktrees` spawned in the last hour, and how much did they churn?"
without guessing from a bare process list. Adoption is a plugin author's choice
per launch site, never a mandatory gate, and a plugin that never calls the hook
is exactly as valid as one that calls it everywhere — this vision states the
*standard*, not a requirement that every spawn use it.

## Concepts & Components

- **The generic telemetry seam (existing, generalized).** `agent-dispatch` and
  `agent-bridge` each already carry a small `telemetry.py`: a process-wide,
  fail-open sink registration (`set_telemetry_sink` / `emit`), a built-in
  dependency-free **spool sink** (append-only JSON-Lines, drained out of process
  on a consumer's own schedule), and two wiring paths (an env var naming a sink
  factory, or a convention-located `telemetry.json` naming one) — no plugin ever
  imports a consumer's package. This vision's mechanism **is that seam**,
  generalized so any `agent-*` runtime plugin can carry its own copy (or a
  shared vendored one) rather than reinventing emission per plugin.
- **The process-spawn event (new event kind).** The existing seam already
  defines generic event *kinds* — `state_transition` (a task/session lifecycle
  change), `producer_fence` (dispatch admission) — each a plain dict shaped by a
  small helper function. This vision adds a **`process_spawn`** kind: a
  start/end pair (or a single closing record once the process has exited)
  carrying the emitting **plugin name**, the **command/verb** that triggered the
  spawn, a stable **source tag** (a short, low-cardinality identifier for *why*
  — e.g. `"worktree-setup"`, `"session-host"`, `"mcp-server"`, `"dispatch-poll"`
  — not a free-text reason), the **parent lineage** (the fabric actor
  responsible: a worktree id, a dispatch task id, a session id) when one exists,
  and whatever resource figures were cheap to sample. Shaped by a helper
  (`process_spawn_event(...)`) the same way `task_lifecycle_event` and
  `producer_fence_event` already are.
- **Best-effort resource figures.** Where cheap to sample without adding
  overhead — CPU time consumed, peak/resident memory, wall-clock lifetime — they
  ride on the closing record. A figure that is expensive, racy, or
  platform-unavailable is simply omitted; capturing one never blocks or fails a
  spawn.
- **Swappable, safe-when-unconfigured export (already proven).** The seam's
  existing behavior already satisfies this: a sink is a no-op until a consumer
  registers one, the built-in spool sink needs no consumer package on the
  daemon's own interpreter, and a downstream repo (an OTel collector, a plain
  log tail, or nothing) decides how the drained spool is interpreted. This
  vision does not add a new transport; it reuses the proven one.
- **Cross-plugin adoption surface.** Because the seam is already a small,
  self-contained per-plugin module, any `agent-*` runtime plugin's launch site
  (`agent-worktrees` process spawns, `agent-containers` provisioning,
  `agent-codespaces` SSH/dispatch, session-hosting launches) can adopt the same
  shape with a few lines — a candidate for extraction into a shared lib once a
  second and third plugin adopt it, per `docs/patterns/runtime-agent-plugin.md`.
- **The consuming control-plane (out of scope, named for orientation).** A
  downstream repo's telemetry spine (or nothing) is what actually drains the
  spool, aggregates, buckets by source, and alerts on this signal. This
  vision's subject ends at emission; a control-plane's own vision — e.g. a
  private facility's process-provenance/churn-attribution capability — is what
  consumes it.

## Features

### opt-in process-provenance events
A runtime plugin's launch code can wrap a spawn in one call to the existing
seam that emits a `process_spawn` record carrying plugin, command/verb, source
tag, and parent lineage — through the same `emit()` / sink pattern already
proven by `state_transition` and `producer_fence`, no new per-plugin bespoke
logging format.

### best-effort resource attachment
Cheap-to-sample resource figures (CPU time, peak memory, lifetime) ride along on
the closing record when available; their absence is never an error and never
blocks the spawn.

### reuse the proven, swappable, safe-when-unconfigured seam
Emission goes through the seam's existing sink registration: a no-op until a
consumer registers one, the built-in spool sink when a consumer just wants a
local file, or a richer sink (including one that re-exports as OpenTelemetry)
when a downstream repo wires one up — adopting the seam never requires standing
up a collector.

### low-cardinality, stable tagging
Source tags are a small, stable, documented vocabulary per plugin (not free
text), so a downstream consumer can group and bucket spawns reliably across
plugin versions.

### short-lived-spawn visibility
A process that starts and exits in well under a second — the exact shape that
is invisible to a periodic external process sampler — is still captured,
because the emitting code brackets the spawn itself rather than relying on a
poll catching it mid-life.

## Behaviors

### never perturb the spawn
Emitting a record never adds meaningful latency, never fails or delays the spawn
it describes, and never introduces a supervising process. A telemetry failure
degrades to a dropped record, never a blocked launch.

### fail-open and silent by default
With no sink registered, the seam is a cheap no-op — a plugin can adopt it with
zero downstream wiring and pay no meaningful cost. This is the existing seam's
proven behavior, not a new guarantee to build.

### tag with identity, not narration
Attributes are structured, low-cardinality, and machine-groupable (plugin,
verb, source tag, lineage id) — never a free-text description a consumer would
have to parse or guess a taxonomy from.

### attribute the whole lineage, not just the leaf
Where the fabric already knows a spawn's cause (a worktree, a dispatch task, a
session), that identity rides on the record so a consumer can trace a resource
spike back through the fabric's own actors, not just to a bare process name.

### one seam, every OS
The same emission call and record shape works whether the runtime plugin is
launching on Windows, WSL, or Linux — platform differences live in how the
resource figures are sampled, never in the emitted shape.

## Non-Goals / Boundaries

- **Not a system-wide process monitor.** This vision instruments processes the
  `agent-*` fabric itself spawns and supervises. Watching arbitrary
  non-fabric processes (a user's shell, an unrelated app) is a different
  capability, not this one's job.
- **Not an aggregation, storage, alerting, or dashboarding system.** Emission
  stops at the sink boundary. Draining a spool, retaining, bucketing by source,
  or raising alerts on this signal belongs to whatever control-plane registers
  a sink — this repo ships the producer, not the consumer.
- **Not a mandatory instrumentation gate.** Adopting the seam is a per-plugin,
  per-launch-site choice. A plugin (or a specific spawn) that never calls it is
  not in violation of anything; this vision states a standard for those who
  opt in, not a requirement that every process ever be wrapped.
- **Not a generic APM/tracing product.** This is narrowly about **process
  spawn provenance and resource churn**, not full distributed tracing of
  request flows, log aggregation, or a general observability platform.
- **Not a second telemetry mechanism.** This vision does not invent a parallel
  emission library alongside the existing `agent-dispatch`/`agent-bridge` seam —
  it generalizes that one seam (and adds one new event kind) so other
  `agent-*` plugins can adopt the identical shape.
- **Not spec-level here.** The exact shared-lib extraction point, the
  `process_spawn_event()` field names, and per-plugin adoption order live in
  the reality docs once built, not in this vision.

## See Also

- Parent vision: none (top-level cross-cutting capability)
- Child visions: none (leaf)
- Related: [`../agent-fabric`](../agent-fabric/README.md) (the coordination
  fabric whose processes this instruments), [`../plugin-services`](../plugin-services/README.md)
  (the shared plugin-service model this rides alongside — a sibling concern to
  service supervision and endpoint discovery)
- Reality docs: [`../../plugins/agent-dispatch/src/agent_dispatch/telemetry.py`](../../plugins/agent-dispatch/src/agent_dispatch/telemetry.py),
  [`../../plugins/agent-bridge/src/agent_bridge/telemetry.py`](../../plugins/agent-bridge/src/agent_bridge/telemetry.py),
  `docs/patterns/runtime-agent-plugin.md` (the runtime-plugin shape a launch
  site lives in)

---

## Provenance

- **2026-09-12** — Conceived as the generalized, portable half of a downstream
  control-plane's process-provenance/churn-attribution effort: that repo wants
  to attribute host CPU/GPU/RAM/disk/network activity to its own tooling (CLI
  shells, Copilot.exe, native binaries it builds on) bucketed by source, and
  asked that the `agent-*` plugins carry OpenTelemetry-style hooks so their own
  spawns are attributable rather than opaque. Surveying the existing repo found
  `agent-dispatch` and `agent-bridge` already ship exactly this shape of seam
  (fail-open sink, spool sink, env/config wiring) for task/session lifecycle
  events — so this vision **generalizes that proven seam** rather than
  proposing a parallel one: it adds a `process_spawn` event kind and states the
  intent that other spawning plugins adopt the same pattern. The
  private/deployment-specific aggregation, digesting, and HAB/EAB-facing
  consumption stays in the downstream repo's own vision, which links back here.
