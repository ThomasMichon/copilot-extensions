# Inception request

Parent: [Machine Health Heartbeats](README.md).

This captures the operator's public-safe wording. A private configuration
repository name in the later scope answer is replaced by a bracketed generic
description; the surrounding intent and activation restriction are preserved.

## Standing heartbeat intent

> We should come up with a scheme by which each machine emits periodic health checks into a designated slot on OneDrive. Keep 7-days worth of history, once an hour (for 24x7 total update slots). We need to know health of SSH, Gateway visibility, auth status, active agent count, etc. Lack of an update indicates an outage, liekly due to a machine restarting or going offline. Then we need to report any diagosed errors within those hourly blocks. OneDrive can be used as a high-latency multicast broadcaster so all machines know the status of all others, even if SSH or the Gateway are down. This is a good addendum to the Telemetry and Logging visions in Copilot Extensions. Since we already rely on OneDrive as the default Vault carrier, we can also treat it as a Health Heartbeat carrier, and a user-side Preferences carrier.

## Effort and implementation request

> Great. Help come up with an effort with a plan, architecture and design, get it reviewed, and then start implementing. We can do some rounds on filling out the information based on various outages we've seen.
> One thing also worth doing is activating the "local telemetry sink" for all machines, to collect the OpenTelemetry events on each machine to a local file; this watchdog can collect and summarize that data, to produce "charts" of performance and reliability from the collected telemetry events.
> I wonder what component should take responsibility for making this. In theory worktree-manager, agent-machines, or agent-worktrees could do it, unless we already made some telemetry/watchdog service as part of the copilot-extensions suite. For every service we add, we add another point of failure that requires update and itself can go out, so we need to be careful.

## Later scope answer

> We'll make it be part of our default ([adopter-owned configuration]) config, and actively roll it out to all visible Windows machines initially

The selected activation answer was:

> configuration-only

Its presented meaning was:

> Prepare configuration only; ask before activating any running service

## Interpretation, not additional authority

The operator subsequently endorsed the owner and two-way data flow:

> Yeah this makes sense for agent-machines to propagate core machine config preference via OneDrive and then aggregate and report telemetry and health data back.

The plan preserves the hourly/seven-day requirement, out-of-band carrier,
diagnosed-error context, local events and performance/reliability charts.
It treats missing updates as a suspected outage with possible sync/publisher
failure, not proof of restart. The exact existing JSONL versus SDK/OTLP
capability remains explicit rather than weakening the interoperability request.

The later answer narrows initial adopter effects to versioned defaults and
configuration-only rollout to selected visible Windows machines. It does not
authorize service activation, cloud selection, Linux rollout or credential
acquisition. Agent Machines ownership and the dependency-ordered first source
slice are agent recommendations submitted for review. The later endorsement
selects Agent Machines for supported preference propagation and health/report
aggregation without removing the configuration-only activation restriction.

## Preferred harness-to-knowledge mapping

The operator then selected a carrier application-data home and all four
preference categories. The exact private account/folder is omitted here.
The selection form explicitly authorized no OneDrive files or service activation.

The subsequent requirement is preserved verbatim:

> I definitely want to store the preferred mapping of each harness repo to knowledge repo. Note that this can be overridden per-machine, but the defaults go on OneDrive

This requires a stored default relationship for each harness identity, with
machine-specific overrides. It does not authorize automatically replacing a
binding, registering/cloning a repository, selecting credentials or activating
the carrier. The [Phase 4 amendment](preferences.md) separates the requested
behavior from the proposed schema and implementation sequence.

## Installable-service contribution follow-up

On 2026-11-09 the operator resumed this effort:

> Oh perfect. This means you can get back on-track with that effort. We'll ned a by-convention way that installable services can surface their hourly data payloads and telemetry, to be properly aggregated into the reports.

This adds a reusable service contribution/discovery requirement to the
continuing health/report flow. It does not remove the earlier configuration-only
activation boundary. The [service contribution amendment](service-contributions.md)
separates the required outcome from agent-recommended protocol and sequencing.
