# Operational Feedback - Vision

- **Subject:** Evidence-grounded fleet judgments and accountable feedback services
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-10

## Purpose & Intent

The fleet should learn whether work is useful, coherent, healthy, and faithful to
intent without confusing observation with permission. General incident, health,
coherence, reality, and efficiency judgments turn grounded evidence into
accountable proposals or justified declines, through normal tasks and reviews.

## Concepts & Components

Telemetry, usage, process and session evidence, and index connectors provide
attributable observations and retrieval. Optional, template-able fleet services
interpret that evidence under scoped operator enrollment. Providers are neutral:
no particular domain, device ecosystem, or smart-home service is required.

Controller, index, bus, queue, and evidence roles can compose as containerized
services through existing service and lifecycle owners. They remain separable;
their composition is not a mandatory central stack or a new ownership layer.

## Features

### grounded-judgment-and-routing

Judgments cite attributable evidence and distinguish uncertainty from a finding.
They can decline, declare no fault, deduplicate, propose, or route ordinary work
to its task, effort, and review owners. Evidence and retrieval aid judgment;
neither is authority to execute a remedy.

### withdrawable-automation

Automation has explicit scope, consent, capacity, and withdrawal. Detection does
not grant remediation permission. A proposed repair, restart, cleanup, or other
side effect follows the ordinary authorization and review gates rather than
inheriting permission from an alert.

### composable-durable-services

Persistent storage preserves accepted work and evidence independently of a
controller's process or container lifetime. Failover maintains one writer and
fences former owners. Credential and trust isolation keep evidence collection,
retrieval, coordination, and execution within their respective authority.
Retention, privacy, and state rescue remain explicit responsibilities of the
owning services, including before ephemeral venues disappear.

## Behaviors

### honest-evidence-coverage

Reports expose coverage, freshness, attribution, and unavailable inputs.
Unavailable usage, missing telemetry, and stale observations are not zero.
An index hit is a retrieval lead, not proof of live reality or accepted intent.
Contradictory evidence stays visible rather than being averaged into confidence.

### acceptance-not-activity

Measures reflect honest outcomes and acceptance, not raw sessions, tokens,
messages, or task counts. A false pass that depends on improvising around a
broken setup is not success. Independent outcome evidence challenges circular
metrics; generated work cannot count itself as proof that its generator is useful.

### bounded-feedback-not-runaway-work

Deduplication and scoped review prevent repeated findings, self-triggering work,
and runaway self-generation. No-fault and decline are legitimate outcomes.
Feedback conserves fleet capacity and exposes unresolved findings without
manufacturing activity to improve its own score.

### distinct-cost-and-capacity-measures

Public provider-credit entitlement and pacing remain distinct from operator
internal costs and physical resource capacity. Different currencies and limits
are not substituted for one another. Forecasts identify their account, evidence
freshness, and uncertainty; missing allowance or reset evidence cannot support a
confident pacing claim.

## Non-Goals / Boundaries

No automatic remediation from observation, no duplicate task or lifecycle
authority, no required provider or central stack, and no efficiency verdict based
solely on activity. Feedback does not authorize credential widening or publication
of private evidence.

## See Also

- Parent: [agentic-factory](../README.md)
- Tasks and supervised services: [agent-dispatch](../../../plugins/agent-dispatch/README.md)
- Service lifecycle: [plugin-services](../../../plugin-services/README.md)
- Fleet observation and reach: [machine-fleet](../../../machine-fleet/README.md)
- Retrieval: [agent-index](../../../plugins/agent-index/README.md)
- Preserved evidence: [agent-logger](../../../plugins/agent-logger/README.md)
- Session analysis: [session-intelligence](../../../plugins/agent-logger/session-intelligence/README.md)
- Process evidence: [process-telemetry](../../../process-telemetry/README.md)
