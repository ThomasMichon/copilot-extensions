# Machine Health Heartbeats - Vision

- **Subject:** Independent, bounded machine-health observations distributed
  through an explicitly selected user-owned synchronization carrier
- **Scope:** leaf (child of portable telemetry)
- **Status:** Draft
- **Last revised:** 2026-11-05
- **Reality docs:** [Suite architecture](../../../docs/architecture.md),
  [agent-ssh](../../../plugins/agent-ssh/README.md)

## Purpose & Intent

Every participating machine should leave a small, periodic account of its own
health that its peers can read even when SSH, Gateway access or a central
collector is unavailable. An optional synchronized-folder carrier, including
OneDrive, should provide high-latency multicast: one machine publishes its
observations and all admitted readers can eventually receive them.

The useful outcome is a seven-day view of what each machine actually observed,
which errors it diagnosed, and where evidence stopped arriving. This is
out-of-band situational awareness, not a second operational authority or a
promise of immediate failure detection.

## Concepts & Components

- **Machine-owned publisher.** One configured publisher per machine uses that
  machine's existing lifecycle owner. It remains useful without an interactive
  agent, an elected chronicler, SSH or Gateway access.
- **Domain-owned observations.** SSH, transport, credential and session owners
  supply bounded observations within their existing access scopes. The
  publisher composes them rather than rebuilding their inventories or controls.
- **Hourly evidence block.** A compact account of measurements, observation
  coverage and diagnosed errors for one absolute UTC hour.
- **Independent carrier.** The selected
  [user-state carrier](../../user-state-carriers/README.md) replicates records
  without making peer SSH or Gateway connectivity a prerequisite.
- **Peer view.** Each reader assembles its own age-qualified view from locally
  available replicas; there is no globally synchronous fleet snapshot.

## Features

### hourly-seven-day-history

Each machine publishes once per hour into its own designated history, retaining
**168 logical hourly slots: 24 hours times seven days**. UTC hours avoid
daylight-saving gaps and duplicate local-hour names. Slots identify the actual
absolute hour they describe; a weekly slot position alone is never freshness
evidence. The active history remains bounded and evidence older than seven
days is expired even when its writer is offline.

Diagnosed events may revise their existing hourly block without allocating
additional history slots or creating a high-frequency telemetry stream.
Underlying provider version history and recycle-bin retention remain separate,
explicit storage-policy concerns, not a claim of physical deletion everywhere.

### ssh-gateway-authentication-and-agent-health

Reports distinguish the local SSH listener and supervisor from fresh,
authenticated route evidence, including self-route or admitted peer probes.
A live process, open socket or SSH banner is not end-to-end authenticated
reachability. Probe direction, observation time, coverage and unavailable
checks remain understandable.

Gateway visibility reports transport reachability, service responsiveness and
authorized consumer access separately. Authentication status covers only
configured providers and resources: usable, expired, interaction-required,
denied and unobserved evidence must not collapse into one green indicator.
Readiness inspection never starts login, refreshes access or exports tokens.

Agent activity reports the observed logical active-agent count and relevant
running, idle or unavailable coverage from the existing session owner. OS
process counts are a different measurement. An unavailable owner does not
become zero agents, and missing measurements are not healthy defaults.
Available machine restart or uptime evidence and publisher/carrier status help
explain reporting gaps without inferring their cause from silence.

### diagnosed-errors-in-their-hour

Hourly blocks include bounded, structured diagnostic summaries: affected
component, first and last observation, repeated occurrence counts, established
failure, and any already-authorized recovery outcome. Observed symptoms,
confirmed causes and unverified hypotheses remain distinct. A later diagnosis
can add attributable context without rewriting when the original evidence was
collected or pretending recovery occurred earlier.

Summaries compose existing local diagnostics and logging rather than copying
raw logs, stack traces, conversations, credentials or arbitrary exception text.
Detailed evidence retains its own owner, access rules and retention.

### multicast-peer-awareness

All admitted machines can read each participant's available history through
the selected carrier. Last-known observations remain inspectable during network
partition, Gateway outage or peer absence. A reader shows sample age and
expected update coverage rather than presenting a replicated record as a live
probe or requiring a central index to discover every update.

## Behaviors

### absence-is-an-outage-signal-not-a-cause

A missed expected update, after the selected cadence and delivery allowance,
is a visible suspected outage or unavailable-reporting signal. Restart, sleep,
network loss, publisher failure, expired carrier authentication and delayed
replication are possible explanations, not conclusions drawn from absence.
Reader-side carrier trouble must also remain visible; silence from all peers
does not prove a simultaneous fleet outage.

### sample-age-survives-delivery

Observation time, publisher continuity and the reader's receipt time remain
distinguishable. Delayed replication, clock skew, future-dated samples, weekly
slot reuse and conflicting copies cannot refresh old evidence or replace a
newer accepted observation silently. Unknown or incompatible records remain
unavailable evidence, not successful updates. Readers can disagree temporarily
without treating their partial views as consensus.

### honest-offline-catch-up

A returning publisher reports current health promptly and can deliver retained
historical observations with their original times. It never fills missed hours
with invented healthy samples, relabels a current check as past coverage, or
turns a backlog upload into proof of continuous uptime.

### bounded-independent-publication

Collection, diagnostics and local staging are bounded and noninteractive.
Records are published as complete observations; interrupted writes and
conflicted replicas cannot masquerade as valid health. Repeated failures are
summarized without unbounded growth or retry storms. Carrier failure leaves
independent local diagnostics usable and does not block the observed work.
Publication retains the parent telemetry vision's admitted audience, privacy
and attribution rules; possession of a synchronized folder does not authorize
raw identity or content export.

The machine's existing lifecycle authority owns scheduling and single-writer
publication. No per-agent AI polling loop, reporting subprocess per event,
peer takeover of another machine's slots or new fleet leader is required.

### observation-never-grants-remediation

Shared health may explain or suggest investigation. It does not enroll peers,
transfer work or claims, change credentials or preferences, restart services,
or authorize cleanup. A cached diagnosis or missing heartbeat cannot replace
the action owner's current authority and required live checks.

## Non-Goals / Boundaries

- No real-time transport, distributed failure detector, consensus, election,
  guaranteed delivery or exactly-once observation claim.
- No dependency on SSH, Gateway, Vault unlock, an interactive Copilot session
  or a central telemetry backend to produce a local health record.
- No implicit carrier setup, login, storage selection, export or rollout.
- No replacement for domain health/status owners, operational ledgers, full
  diagnostic logs or long-lived session evidence.
- No fixed file layout, schema, API, scheduler or implementation recipe.

## See Also

- Parent: [portable telemetry](../README.md).
- Carrier: [user-state carriers](../../user-state-carriers/README.md).
- Related: [agent logger](../../plugins/agent-logger/README.md),
  [Process Registry](../../process-registry/README.md),
  [Vault](../../vault/README.md).
- Lifecycle: [plugin services](../../plugin-services/README.md).
