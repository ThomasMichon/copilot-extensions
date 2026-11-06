# Architecture and ownership

Parent: [Machine Health Heartbeats](README.md).

## Existing capabilities

| Capability | Existing owner and contract | Reuse |
|---|---|---|
| Local event capture | Bridge/Dispatch `telemetry.json` and `retained_spool` | Explicit opt-in; existing bounded writer; no collector service |
| Retention and append | `libs/process-telemetry`, `configured_spool`, `retained_spool` | Owner-provisioned exclusive namespace; preserve timestamps; 30-day local retention |
| Measurement semantics | `process_telemetry.measurement` and `report.summarize` | Known stages/outcomes, real durations, homogeneous percentiles, explicit unknowns |
| OpenTelemetry | Existing optional SDK/protobuf adapters under #7935 | Reuse actual signal adapters; never call legacy JSONL OTLP |
| Application diagnostics | Existing service-owned logging and `logs` inventories | Distinct from event capture; no arbitrary log parsing or secret export |
| Machine setup/scheduling | Agent Machines resources and native user jobs | Configure and later run a bounded one-shot job |
| Session/process state | Existing Worktrees/Bridge/Process Registry owners | Read admitted snapshots; no second authoritative agent inventory |
| Charts/presentation | Manager or an offline report consumer | Display retained observations; no UI/process dependency for collection |

Authoritative source contracts:
[telemetry library](../../../libs/process-telemetry/README.md),
[Bridge diagnostics](../../../plugins/agent-bridge/docs/diagnostic-logs.md),
[Agent Machines](../../../plugins/agent-machines/docs/architecture.md).
The active [performance campaign](../performance-telemetry/README.md) retains
its native producer, structured-signal and rollout acceptance.

## Owner decision

The operator endorsed Agent Machines as coordinator. This detailed
**agent-recommended** division remains subject to plan review.

| Candidate | Appropriate responsibility | Why not own everything |
|---|---|---|
| Agent Machines | Core preference propagation, configuration, bounded health/report CLI and explicit native periodic registration | Does not become a credential, SSH, session or telemetry-schema authority |
| Worktree Manager | Configuration/presentation over cached health and chart data | Optional and not continuously running on every supported machine |
| Agent Worktrees | Worktree/agent observations through its existing owner | Not a prerequisite for standalone machine telemetry or an excuse to keep an idle resident alive |
| Existing telemetry library | Typed records, bounded local sinks and statistical primitives | A library, not a service, fleet controller or scheduler |
| New watchdog service | None in this design | Another update, singleton, cutover and recovery obligation without a demonstrated need |

One user-scoped native periodic job is the target, with no added resident
collector. Do not extend or re-enable the old dtssh-specific watchdog, attach
health to an unrelated software-update cycle, or depend on unmerged Manager
automatic maintenance (#8932). Reuse qualified native scheduling primitives,
but keep observation independent of update/restore execution and opt-ins.

## Data flow

1. Existing instrumented owners enqueue content-minimized events through their
   existing writer into their own local retained files.
2. The bounded report command reads explicit local inputs, validates known
   schemas and time units, then produces hourly observation summaries and a
   deterministic self-contained chart. Raw inputs stay local.
3. A later one-shot health run composes separately timestamped domain-owned
   observations with these summaries, writes its local hourly record, and
   attempts publication into only its selected carrier namespace.
4. Each peer reads complete, age-qualified replicas and presents its own
   last-known view. No central collector or live SSH path is required.

The first implementation proves steps 1-2 end to end. Steps 3-4 are separate
reviewed implementation and activation checkpoints, not implied by that proof.

In the inbound direction, Agent Machines exchanges supported nonsecret core
preferences through the same selected carrier but a separate typed domain.
Its existing configuration engine validates, scopes and previews adoption,
preserving local overrides and mandatory confirmation. Health data never
becomes configuration input, and receiving preferences is not permission to
execute a command, install software, change a binding or activate a service.

## Ownership and failure domains

Raw telemetry keeps the producer's existing 30-day retention; shared health has
168 UTC-hour slots and seven-day validity. Neither consumer prunes the other's
storage or treats a diagnostic file as an authority ledger. Summary output
does not copy trace/session/account identities, paths, prompts or raw errors.

A failed report, inaccessible disk, unavailable provider or blocked sync leaves
ordinary work and independent local diagnostics usable. The health job does
not repair any of them. A missing heartbeat can mean machine, publisher,
authentication, network or carrier failure; it is not proof of a restart.

## Rollout

Adopter configuration is versioned outside this public source. The initial
cohort is selected Windows machines, configuration-only. Provision only the
admitted new local namespaces and missing supported configuration; refuse
unknown/custom sink replacement. Observe current configuration separately
from current writer health.

Do not install an SDK, reload/restart a service, enable periodic execution,
select a cloud location or widen the cohort under this configuration grant.
Source release/update, running capture, scheduled publication and remote receipt
each require their own owner evidence and applicable authority.

## Deferred design boundaries

Vault artifacts retain their existing custody owner; preference distribution
uses the Agent Machines configuration owner, not the health-record handler.
Genuine OTLP reception is not
provided by a JSONL reader. Any later SDK file exporter or receiver integration
must preserve #7935's signal and persistence contracts without a second writer
or a mandatory collector.
