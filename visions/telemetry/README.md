# Portable Telemetry — Vision

- **Subject:** Common observability hooks and structured logging across Copilot
  Extensions tools and services, with sinks owned by the adopting harness
- **Scope:** leaf (cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-10-22
- **Reality docs:** [Suite architecture](../../docs/architecture.md) ·
  [Dispatch telemetry seam](../../plugins/agent-dispatch/src/agent_dispatch/telemetry.py) ·
  [Bridge telemetry seam](../../plugins/agent-bridge/src/agent_bridge/telemetry.py)

## Purpose & Intent

Copilot Extensions tools and services should expose common,
**OpenTelemetry-compatible instrumentation hooks and coherent structured
logging** without choosing an observability backend for their adopters.
Lifecycle, operation, failure and performance signals should be understandable
together rather than as unrelated plugin-specific streams.

**Implementing harnesses bring their own sink.** A harness selects its consumer
through per-harness configuration or a user-local override, in the same adoption
shape as configurable logger and index capabilities. The generic suite supplies
portable producer seams, not an organization-specific sink, destination or
retention policy. Adoption is optional: an unconfigured installation exports
nothing and remains fully useful.

## Concepts & Components

- **Producer hooks.** Tools and services describe their own activity through a
  common event model and context. Structured logs, lifecycle events and real
  traces remain distinguishable signals that can be correlated, not competing
  accounts of an operation.
- **Harness binding.** The initiating harness owns the selected sink and allowed
  signal policy for an operation. Execution location is not configuration
  authority: changing directory, invoking a child or reaching a shared service
  does not select a different harness implicitly.
- **Isolated sink runtime.** The consumer runs outside the producer's runtime
  and dependency environment. A producer need not import an adopter's packages
  or bootstrap its observability stack to do useful work.
- **Transport-neutral delivery.** A continuously fed executable and a discovered
  local HTTP daemon are alternative ways to consume the same event model and
  context. The transport does not change attribution, privacy or trace meaning.
- **Independent local diagnostics.** Owner-local logs, status and diagnostic
  tools remain usable without export and without access to a configured sink.

## Features

### common-hooks-and-structured-logging

Tools and services offer consistent instrumentation seams and structured
diagnostic records, with stable source and operation attribution. Consumers
can relate activity across plugins without parsing free-form messages or
requiring every producer to depend on an observability SDK.

### harness-owned-sink-selection

Per-harness configuration selects the sink; a user-local override can specialize
the selection within the adopter's policy. The effective choice is explainable
without revealing credentials or sensitive configuration. The suite imposes no
particular organization's selection or override policy.

### continuous-executable-sink

A harness can select an executable launched for a particular producer or source
that accepts a **continuous stream of structured JSON events through standard
input**. That consumer has an explicit owner and lifetime; it is not a new
process for each event, and short-lived callbacks or hooks do not bootstrap a
reporter. Consumer failure or restart remains separate from producer success.

### discovered-local-http-sink

A harness can instead select a local HTTP daemon reached through the owning
service's endpoint or port-discovery mechanism. Clients discover the current
owned endpoint; they neither assume a fixed port nor scan for a listener.
Selecting this transport does not implicitly install or start a daemon.

### interoperable-signal-meaning

Both transports preserve the same event model, source context and correlation
semantics. OpenTelemetry compatibility means signals can be faithfully mapped
to its concepts; **a JSON stream or an HTTP request is not automatically OTLP**.
A consumer claiming OTLP support must actually satisfy that protocol rather
than relabel an arbitrary envelope.

## Behaviors

### initiating-harness-follows-the-operation

Binding follows the initiating harness across working-directory changes,
process boundaries, requests and remote admission. Receivers preserve the
admitted operation's binding within their own policy and trust boundaries;
remote execution does not grant permission to install a sink or redirect
exports. Missing or inadmissible context disables optional export rather than
guessing another harness's destination.

### shared-daemons-route-per-operation

A shared daemon routes each operation's signals using that operation's admitted
binding. Concurrent callers from different harnesses remain separate. The most
recent caller or configuration write never sets a process-global sink for
unrelated operations. Daemon-owned background work uses its own explicit
binding, not one borrowed from the last request.

### bounded-fail-open-emission

Disabled emission is a cheap no-op. Enabled emission is bounded and
nonblocking on the producer's critical path: slow consumers, broken pipes,
unreachable endpoints and full buffers cause bounded loss, not blocked work,
unbounded retries, growing queues or per-event child processes. Export work
stays off latency-sensitive callbacks. Bounded local diagnostics make drops and
configuration failures discoverable without turning export into a prerequisite.

### content-free-by-default

Signals use explicit attribute allowlists and bounded cardinality. Defaults
carry operational structure, not prompts, conversation or tool payloads, file
contents, credentials, private paths or arbitrary exception text. Opaque
identifiers are not presumed harmless; attribution is minimized and scoped.
Any richer collection requires deliberate adopter policy and consent, never
an automatic widening of a producer's fields.

### honest-trace-context

Trace and span context describes actual instrumented execution and causal
relationships, including across admitted remote boundaries. Task, worktree,
session and process identifiers remain correlation attributes, not fabricated
trace or span identifiers. An uninstrumented hop is unknown, not a made-up span;
missing measurements and dropped events are not zero cost or successful work.

### preserve-existing-integrations

Common hooks extend or compose the existing fail-open telemetry seams and
legacy integrations. Configured consumers retain their meaning and behavior
through explicit compatibility paths; this vision does not order their removal
or silently reroute their data. Process-provenance signals remain the narrower
subject of [Process Telemetry](../process-telemetry/README.md).

## Non-Goals / Boundaries

- No bundled organization sink, implicit off-site destination, automatic export
  when unconfigured, or mandatory observability stack for generic adopters.
- No implicit sink, SDK, exporter or daemon installation or rollout.
- No telemetry-owned process registry, discovery authority, liveness ledger or
  control plane. Observations do not authorize execution, recovery or cleanup;
  authoritative operational state remains with its existing owner.
- No replacement for independent local diagnostics, session evidence storage,
  durable task records or recovery journals.
- No prescribed event schema, API, configuration filename, port, wire envelope,
  library extraction or implementation sequence. These belong below vision
  altitude, not in standing intent.

## See Also

- [Vision index](../README.md)
- [Agent fabric — externally observable](../agent-fabric/README.md#externally-observable)
- [Process telemetry](../process-telemetry/README.md) — narrower spawn provenance
  and resource attribution
- [Process registry](../process-registry/README.md) — independent operational state
- [Agent logger](../plugins/agent-logger/README.md) and
  [Agent index](../plugins/agent-index/README.md) — related configurable capabilities
- [Local endpoint discovery](../../docs/patterns/local-endpoint-discovery.md) and
  [Performance measurement](../../docs/patterns/performance-measurement.md) —
  realization patterns
- [Plugin services](../plugin-services/README.md) — independent service ownership
