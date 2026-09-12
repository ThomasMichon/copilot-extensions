# Process Telemetry — Vision

- **Subject:** How `agent-*` runtime plugins emit the signal of their own process
  footprint — spawns, lifetimes, and resource churn — so a control-plane repo can
  attribute fleet-wide CPU/GPU/RAM/disk/network activity back to the fabric that
  caused it
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-09-12
- **Reality docs:** *(emerging — no build yet)*; see `docs/patterns/runtime-agent-plugin.md`
  for the current runtime-plugin shape this instruments

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
optional, stdlib-friendly hook and — for free — become attributable: a consumer
downstream can answer "how many processes has `agent-worktrees` spawned in the
last hour, and how much did they churn?" without guessing from a bare process
list. Adoption is a plugin author's choice per launch site, never a mandatory
gate, and a plugin that never calls the hook is exactly as valid as one that
calls it everywhere — this vision states the *standard*, not a requirement that
every spawn use it.

## Concepts & Components

- **The emission hook.** A small, dependency-light library (an
  OpenTelemetry-compatible span/event API, or a stdlib-only fallback that speaks
  the same shape) a runtime plugin's own launch code calls around a spawn: open
  a span when the process starts, attach identifying attributes, close it (with
  whatever exit/resource figures were cheap to capture) when it exits or is
  reaped. The hook is a **library call**, not a wrapper process — it must not add
  a supervising process of its own per spawn.
- **Provenance attributes.** The load-bearing low-cardinality tags every emitted
  span carries: the emitting **plugin name**, the **command/verb** that
  triggered the spawn, a stable **source tag** (a short, stable identifier for
  *why* — e.g. "worktree-setup", "session-host", "mcp-server", "dispatch-poll" —
  not a free-text reason), and the **parent lineage** (the immediate
  agent-fabric actor responsible: a worktree id, a dispatch task id, a session
  id) when one exists. These are the fields a consumer groups and buckets by;
  the vision fixes their *presence and shape*, not their transport encoding.
- **Best-effort resource figures.** Where cheap to sample without adding
  overhead — CPU time consumed, peak/resident memory, wall-clock lifetime — the
  hook attaches them to the closing event. A figure that is expensive, racy, or
  platform-unavailable is simply omitted; the hook never blocks or fails a spawn
  to obtain a resource number.
- **The optional local exporter.** A pluggable, swappable sink the hook writes
  to — stdout/OTLP-shaped JSON lines by default so any collector can tail it, an
  OTLP exporter when a collector is configured, or a no-op when nothing is
  configured. The hook never assumes a particular collector, backend, or
  network endpoint exists; **emitting is always safe with nothing listening.**
- **Cross-plugin adoption surface.** Because the hook is a small shared library
  (living beside the fabric's other shared libs), any `agent-*` runtime plugin's
  launch site can adopt it with a few lines — the same shape the
  `docs/patterns/runtime-agent-plugin.md` pattern doc points new plugin authors
  at, alongside service supervision and endpoint discovery.
- **The consuming control-plane (out of scope, named for orientation).** A
  downstream repo's telemetry spine (or nothing) is what actually aggregates,
  buckets by source, and alerts on this signal. This vision's subject ends at
  emission; a control-plane's own vision — e.g. a private facility's process-
  provenance/churn-attribution capability — is what consumes it.

## Features

### opt-in process-provenance spans
A runtime plugin's launch code can wrap a spawn in one call that emits a
start/end span carrying plugin, command/verb, source tag, and parent lineage —
no per-plugin bespoke logging format.

### best-effort resource attachment
Cheap-to-sample resource figures (CPU time, peak memory, lifetime) ride along on
the closing event when available; their absence is never an error and never
blocks the spawn.

### swappable, safe-when-unconfigured export
The hook writes through a pluggable sink: local structured lines by default, an
OTLP exporter when configured, silence when nothing is configured — adopting the
hook never requires standing up a collector.

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
Emitting a span never adds meaningful latency, never fails or delays the spawn
it describes, and never introduces a supervising process. A telemetry failure
degrades to a dropped span, never a blocked launch.

### fail-open and silent by default
With no exporter configured, the hook is a cheap no-op (or a harmless local
log line) — a plugin can adopt it with zero downstream wiring and pay no
meaningful cost.

### tag with identity, not narration
Attributes are structured, low-cardinality, and machine-groupable (plugin,
verb, source tag, lineage id) — never a free-text description a consumer would
have to parse or guess a taxonomy from.

### attribute the whole lineage, not just the leaf
Where the fabric already knows a spawn's cause (a worktree, a dispatch task, a
session), that identity rides on the span so a consumer can trace a resource
spike back through the fabric's own actors, not just to a bare process name.

### one hook, every OS
The same emission call and attribute shape works whether the runtime plugin is
launching on Windows, WSL, or Linux — platform differences live in how the
resource figures are sampled, never in the emitted shape.

## Non-Goals / Boundaries

- **Not a system-wide process monitor.** This vision instruments processes the
  `agent-*` fabric itself spawns and supervises. Watching arbitrary
  non-fabric processes (a user's shell, an unrelated app) is a different
  capability, not this one's job.
- **Not an aggregation, storage, alerting, or dashboarding system.** Emission
  stops at the exporter boundary. Collecting, retaining, bucketing by source,
  or raising alerts on this signal belongs to whatever control-plane wires a
  collector up — this repo ships the producer, not the consumer.
- **Not a mandatory instrumentation gate.** Adopting the hook is a per-plugin,
  per-launch-site choice. A plugin (or a specific spawn) that never calls it is
  not in violation of anything; this vision states a standard for those who
  opt in, not a requirement that every process ever be wrapped.
- **Not a generic APM/tracing product.** This is narrowly about **process
  spawn provenance and resource churn**, not full distributed tracing of
  request flows, log aggregation, or a general observability platform.
- **Not spec-level here.** The exact library API, attribute key names, OTLP
  schema, and default exporter format live in the reality docs once built, not
  in this vision.

## See Also

- Parent vision: none (top-level cross-cutting capability)
- Child visions: none (leaf)
- Related: [`../agent-fabric`](../agent-fabric/README.md) (the coordination
  fabric whose processes this instruments), [`../plugin-services`](../plugin-services/README.md)
  (the shared plugin-service model this rides alongside — a sibling concern to
  service supervision and endpoint discovery)
- Reality docs: *(emerging)* — `docs/patterns/runtime-agent-plugin.md` (the
  runtime-plugin shape a launch site lives in)

---

## Provenance

- **2026-09-12** — Conceived as the generalized, portable half of a downstream
  control-plane's process-provenance/churn-attribution effort: that repo wants
  to attribute host CPU/GPU/RAM/disk/network activity to its own tooling
  (CLI shells, Copilot.exe, native binaries it builds on) bucketed by source,
  and asked that the `agent-*` plugins carry OpenTelemetry-style hooks so their
  own spawns are attributable rather than opaque. This vision states the
  generic half only: an opt-in, safe-by-default process-provenance emission
  hook any runtime plugin can adopt. The private/deployment-specific
  aggregation, digesting, and HAB/EAB-facing consumption stays in the
  downstream repo's own vision, which links back here.
