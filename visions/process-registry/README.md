# Process Registry - Vision

- **Subject:** Attributing the agent fabric's currently running OS processes to
  their role, source, installation, session, and worktree through a stateful
  local register and an inspectable snapshot journal.
- **Scope:** leaf (cross-cutting operational capability)
- **Status:** Draft
- **Last revised:** 2026-10-07

## Purpose & Intent

An operator should be able to distinguish the fabric's Python interpreters,
shells, console hosts, services, and MCP shims from unrelated processes without
guessing from executable names. The system should answer what is registered,
what is confirmed running, what has exited, and what cannot be established.

Process Registry should be a stateful log, comparable to a resource-reservation
ledger or an on-demand session-log tool, not a formal telemetry pipeline.
Processes should cheaply leave identifying check-ins; a single optional local
broker should validate, reconcile, enrich, and serve that state. The system
designed to investigate stuck processes must never make useful work depend on
its own availability.

## Concepts & Components

- **One host-owned broker.** All participating plugins, installations,
  worktrees, and versions should share one broker role on the host, not start
  competing per-plugin or per-worktree daemons. Its designated operator owns
  installation, activation, updates, and access policy.
- **Lightweight file check-ins.** A producer or its existing invoker should
  publish small local registration records without launching a reporting
  process, waiting for an acknowledgement, or calling a telemetry sink.
- **Attributable process instances.** OS process identity should distinguish
  reused PIDs, wrappers, interpreters, and ownership lineage. Claimed metadata
  and corroborated OS facts should remain distinguishable.
- **Reconciled live state.** The broker should recognize new check-ins and
  periodically verify registered instances. File notifications are hints;
  recovery and reconciliation should not depend on receiving every event.
- **On-demand views and local history.** Operators should obtain a current
  state snapshot and consult a bounded folder journal containing a couple of
  snapshots each hour, without configuring a formal reporting backend.

## Features

### attributable-process-inventory

Report role, owning plugin/source and version, PID identity, owner session,
cwd, worktree, and relevant launcher/child relationships when known. Show
physical OS-process counts separately from logical worker or service counts.
Include managed script/native children, not just Python services.

### cooperative-registration

Existing invokers and participating programs should publish and withdraw their
own registrations. Missing exit announcements should be reconciled through OS
identity checks rather than leaving permanent false-running records.

### one-shared-local-handler

The designated operator should register one optional local handler. All
participating installations should use that handler without taking over its
management or creating another broker. No handler should mean no reporting,
not an installation or launch request.

### snapshot-journal-and-query

Answer current-state queries and retain bounded periodic snapshots for
on-demand historical reporting. Report observation freshness, coverage, and
inspection uncertainty with the result.

### economical-enrichment

Research process facts only as needed, cache stable facts, and spread
reconciliation across bounded work. A burst of process check-ins should not
create a burst of helpers, disk writes, or full-machine process scans.

## Behaviors

### never-wedge-the-producer

Reporting should not delay startup, useful work, or shutdown while waiting on
the broker or storage. Overload and unavailable reporting paths should degrade
coverage explicitly, not become a new dependency of the process being observed.

### no-reporting-subprocess

No subprocess should exist solely to announce or remove a registration.
Script check-ins should use safe in-process facilities or rely on the existing
invoker; unavailable safe facilities should be identified as a coverage gap.

### identity-safe-reconciliation

A stale file, delayed exit, missed notification, broker restart, or reused PID
should never remove or attribute a different live process. Permission failures
and unsupported inspection should produce uncertainty, not a dead-process claim.

### explicit-trust-and-coverage

Only the designated operator's security scope should participate. Sensitive
state should remain private. A file claim alone should not be represented as
authenticated provenance, and a registration should not authorize termination.

### independently-usable-adopters

Plugins should remain usable without Worktree Manager or this optional broker.
Existing process ownership, singleton, shutdown, and installation-isolation
contracts should remain authoritative; the registry should observe them, not
replace them.

## Non-Goals / Boundaries

- **No formal telemetry integration.** No sink registration, telemetry event
  dependency, remote exporter, resource-churn pipeline, or mandatory collector.
  [Process Telemetry](../process-telemetry/README.md) is a separate capability.
- **No automatic process remediation.** Registration and inspection grant no
  authority to kill, restart, or repair a process or installation.
- **No competing broker fleet.** An absent handler must not cause producers to
  bootstrap their own daemon or spawn a reporter.
- **No completeness inferred from silence.** Uninstrumented children, very
  short-lived processes, unavailable storage, and foreign PID namespaces must
  not be silently counted as covered.
- **No arbitrary cross-user monitoring.** Serving all OS accounts is outside
  this operator-scoped capability.

## See Also

- Governing context: [Agent Fabric](../agent-fabric/README.md).
- Related intent: [Installer](../installer/README.md),
  [installation isolation](../plugin-services/installation-cells/README.md),
  and [Process Telemetry](../process-telemetry/README.md).
- Design conventions: [process-slot ownership](../../docs/patterns/process-slot-ownership.md)
  and [service transport](../../docs/patterns/service-transport.md).
- Implementation campaign and proposed architecture:
  [Agent-Process Self-Report Registry](../../efforts/active/agent-process-self-report-registry/README.md).

## Provenance

- **2026-10-07:** Operator clarified one shared broker, file-based check-ins,
  no process spawned just to report, and operator-account security scope;
  registration and local snapshot history are distinct from formal telemetry.
