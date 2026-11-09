# Agent Topology - Vision

- **Subject:** Portable role assignment and conversational fleet dispatch
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-11-09

## Purpose & Intent

An operator with a compatible harness and machine mesh can ask one agent to
organize other agents into a useful topology. The dispatcher is a conversational
front door for getting work done, not the worker for every request. It preserves
what it assigned, why, to whom, and what completion means across interrupted
conversations, changing sessions and temporarily unavailable machines.

The concept is portable. Shared tooling owns the reusable role and contract;
each adopter owns its private machine descriptions, desired composition,
assignments and authorization.

## Concepts & Components

- **Desired topology:** machine purposes and allowed assignments, role meanings,
  placement constraints, bounded capacity and the minimum useful role coverage.
- **Dispatcher:** an explicitly assigned agent that allocates, relays, observes
  and reconciles work within its charter.
- **Assignment map:** durable request and role associations with complete
  charters and the native worktree owners responsible for them.
- **Execution owners:** existing workspace, session, messaging, task and resource
  authorities; a topology composes them rather than acquiring their authority.
- **Overview:** a derived account of desired coverage, observed readiness,
  ownership, load, uncertainty and required decisions.

## Features

### conversational-delegation

The operator can request new work, adjust existing work or inquire about it at
the same front door. The dispatcher routes adjustments and inquiries to the
assigned owner's current head. Unrelated requests get separate temporary
workers instead of distracting a standing role or becoming the dispatcher's
own implementation task.

### durable-assignment-map

Every assignment retains the original request, complete charter, placement,
native ownership, permitted supervision and completion criteria. Its durable
identity survives dispatch interruptions and head succession. Intent is
recorded before effects, and unresolved effects remain recognizable on recovery.

### purposeful-placement

Machines describe their purpose and allowed work. Placement first satisfies
scope, capability, locality, authority and capacity constraints, then balances
eligible load. An unavailable preferred machine does not silently authorize
another destination or an expanded resource budget.

### minimum-role-coverage

Desired topology expresses minimum coverage in meaningful roles and scopes, not
just a desired process count. Current coverage depends on evidence that the
rightful owner is ready and has accepted the applicable charter. Partial,
historical, unreachable and unknown evidence remain distinguishable.

### bounded-supervision

The dispatcher keeps tabs on its assignments using existing observation and
communication mechanisms at an authorized cadence. It distinguishes admitted
delivery, consumption, acceptance and actual progress, preserves waits, and
escalates unresolved decisions without becoming an independent busy loop.

## Behaviors

### discovery-before-allocation

Reconciliation discovers existing work and eligible owners before creating
more. Adoption preserves native custody and needs the applicable agreement.
Names and process presence alone cannot prove role coverage or permit takeover.

### recover-without-duplication

Restart and concurrent requests preserve the original assignment and allocation
identity. Uncertain allocation or launch results require reconciliation through
their owner, not a fresh worker, host move or resend. Conflicting dispatcher
custody blocks effects until resolved.

### same-owner-continuity

Follow-ups preserve the assigned worktree across session succession. A stale
head is resolved through its native owner, not treated as an abandoned task.
Changes that exceed the original charter require the missing authorization.

### truthful-completion

The overview is rebuildable from desired intent and native evidence. It does
not become a competing lifecycle store. A worker is complete only when its
actual acceptance and obligations are resolved; an idle process, successful
send or fulfilled role minimum is not enough.

## Non-Goals / Boundaries

- No replacement task queue, claim system, scheduler, agent host or deployment
  controller.
- No authority inferred from a topology document, schema validation, membership,
  available capacity or a successful tool call.
- No automatic live activation merely because a plugin is enabled.
- No implicit movement, takeover or replacement of existing agents.
- No public inventory of an adopter's private machines or work.

## See Also

- [Agentic Factory](../README.md)
- [Repository topology](../repository-topology/README.md)
- [Fleet loadouts](../../../machine-fleet/loadouts/README.md)
- [Worktree agency](../../../plugins/agent-worktrees/README.md)
- [Bridge coordination](../../../plugins/agent-bridge/README.md)
- [Dispatch delegation](../../../plugins/agent-dispatch/README.md)
- [Role skills and charters](../../../../plugins/agent-roles/README.md)
