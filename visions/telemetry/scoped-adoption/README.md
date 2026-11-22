# Scoped Telemetry Adoption - Vision

- **Subject:** One composed sink policy and operation-origin boundary across participating applications, services and tools
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-11-21
- **Reality docs:** [Configuration](../../../docs/configuration.md) and [consumer telemetry command contract](../../../docs/patterns/consumer-telemetry-command.md)

## Purpose & Intent

An adopter configures telemetry once for its harness. Participating suite
applications, shared services and tools use that selection without separate
receiver declarations or application-specific preference models.

Collection belongs to the operation's admitted purpose. A harness-scoped sink
receives work driven by that harness and work verifiably delegated on its
behalf, not unrelated activity on the same machine. Installation and ordinary
application use remain useful independently of telemetry adoption.

## Concepts & Components

- **Sink policy.** The harness declares available consumers, meaningful signal
  capabilities and defaults without selecting an organization for generic users.
- **Usage preferences.** Bound knowledge and machine configuration specialize
  usages, logical-machine selections, scopes and exclusions through the existing
  configuration owners. Private deployment details remain outside shared policy.
- **Operation origin.** The driving repository and worktree or equivalent
  admitted context identify whose work is observed, separately from the
  executing application, target repository and collection process.
- **Producer adapter.** Each real instrumentation owner offers a reviewed
  projection through the common route while retaining its measurements,
  vocabulary, lifetime and independent local diagnostics.
- **Delivery disposition.** Supported, disabled, excluded, unavailable and
  unknown coverage remain distinguishable from receiver persistence and export.

## Features

### configure-once-across-applications

One effective harness policy applies across participating applications,
services, command entry points and session observers. App installation,
packaging or transport choice does not require the operator to repeat the sink
declaration. New instrumentation joins through the same adoption seam.

### composed-policy-with-native-ownership

Harness defaults, bound-knowledge preferences and machine-specific usage compose
through their established owners with explainable precedence and provenance.
No telemetry-specific knowledge binder or competing machine identity authority
is introduced. Optional knowledge integration does not make standalone use
depend on a private repository or another service.

### driving-origin-and-service-of-scope

An adopter can restrict a sink to eligible driving repositories and worktrees
and to work verifiably performed on their behalf. Legitimate child, related-repo
and remote work retains its admitted origin even when its execution directory
changes. An application's own location or a target label does not manufacture
that relationship.

### capability-aware-producer-enrollment

Each participating signal has an explicit supported route and truthful coverage
disposition. Lifecycle events, typed measurements and structured operational
signals retain their meaning through reviewed adapters. A configured sink or
library dependency alone is not evidence that a producer is enrolled.

### inspectable-effective-use

Operators can inspect which policy sources, origin, exclusions and producer
capabilities determine an operation's collection. The explanation is bounded
and does not expose credentials, raw private context or unrestricted records.

## Behaviors

### exclusions-before-shared-capture

Eligibility and effective exclusions apply before records enter storage or
delivery bound to that sink. Unknown or invalid origin does not fall back to
another harness or a machine-global shared destination. Applicable opt-outs
remain effective without disabling independent local diagnostics.

### immutable-origin-through-delegation

Accepted operation context survives child execution, admitted requests, queued
work and remote handoff. Concurrent origins do not overwrite each other's
selection. A receiving owner uses the admitted relationship rather than
reconstructing intent from mutable CWD or caller-supplied event attributes.

### explicit-service-owned-observations

Taskless health and background activity have separately declared purpose and
policy. A shared daemon does not borrow a previous caller's harness to export
machine-wide observations. A background sender may complete delivery of already
admitted records without enrolling unrelated activity.

### one-policy-across-intakes

Session listeners, registered flow reports, fallbacks, service adapters and
collection self-observations use the same eligibility semantics. Changing the
ingress path does not evade an exclusion or create implicit enrollment.

### bounded-independent-and-compatible

Selection and emission stay cheap and bounded. Unsupported policy or signal
capabilities are visible rather than silently weakened. Existing integrations
and product results remain independent of optional export; application
authentication and telemetry selection do not become prerequisites for each
other. Native admission and access controls retain their own authority.

## Non-Goals / Boundaries

- No automatic enrollment of every installed application or Copilot session
  into a harness's destination.
- No forwarding of arbitrary application logs, conversation content or unknown
  signal schemas merely because a sink exists.
- No process-global last-caller sink, target-directory inference or collection
  identity substituted for the driving operation's origin.
- No new configuration binder, observability SDK, event bus or mandatory
  collector daemon.
- No implicit installation, service reconfiguration, credentials or rollout
  authority from a policy file, telemetry record or reviewed plan.
- No protocol, schema, filename or implementation sequence prescribed by this
  vision; transport support is a capability to establish, not a relabeling.

## See Also

- Parent: [Portable telemetry](../README.md)
- [Index](../../plugins/agent-index/README.md) and
  [Logger](../../plugins/agent-logger/README.md) configuration ownership
- [Configuration](../../../docs/configuration.md)
- [Service contract evolution](../../../docs/patterns/service-contract-evolution.md)
- [Performance measurement](../../../docs/patterns/performance-measurement.md)
