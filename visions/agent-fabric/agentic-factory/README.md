# Agentic Factory - Vision

- **Subject:** Operator-steerable composition of agents, durable work, and planning
- **Scope:** branch (links focused capability children)
- **Status:** Active
- **Last revised:** 2026-10-18

## Purpose & Intent

The factory turns accepted intent into verified outcomes across a fleet without
requiring people to babysit every execution leg. It supports proactive and
reactive work while keeping authorization, responsibility, evidence, and human
control legible. Independent capabilities compose; adopting the factory does not
require one central stack.

## Concepts & Components

These four layers describe responsibilities, not new stores or deployment units.

| Layer | Responsibility |
|-------|----------------|
| Base | Portable agents use tools and resources through isolated identity, claims, authorization, bounded capacity, and reliable venue launch and resume. |
| Task | Durable goals represent issues, efforts, maintenance, incidents, and reviews, with priorities, dependencies, restrictions, acceptance, and one rightful execution owner. |
| Orchestrator | Specialized agents hold standing portfolio charters, synthesize cross-contribution intent and guidance, derive reviewed work, prioritize within delegated authority, and watch and recover the fleet. |
| Planner | Quick ephemeral operator conversations develop visions, metrics, docs, decisions, and reports; accepted durable intent graduates into the appropriate owning construct. |

The children refine independently meaningful subjects:

- [Worktree stewardship](worktree-stewardship/README.md): consent-based continuity,
  incremental execution, human takeover, and safe completion.
- [Repository topology](repository-topology/README.md): independent composition
  of harness, knowledge, and product roles with scoped ownership.
- [Operational feedback](operational-feedback/README.md): evidence-grounded
  judgments and optional fleet services that propose and route work.

## Features

### portable-execution-and-collaboration

Agents can launch and resume reliably across worktrees, repositories, machines,
CodeSpaces, and containers within advertised capabilities. They can publish and
review pull requests, converse with people and peers, and request steering
without borrowing authority merely from reachability.

### durable-incremental-goals

Tasks preserve objective, acceptance, restrictions, ownership, and progress across
execution legs. Agents work incrementally, yield fairly, and hibernate while
waiting for people, other agents, reviews, dependencies, or resources. Fleet
visibility and control expose running, pending, held, and waiting responsibility,
not just live processes.

### reviewed-intent-derivation

Orchestrators reconcile visions and operator intent against evidence, deduplicate
proposals, and derive reviewable work through the existing effort, issue, and
task owners. They schedule only within delegated priorities and capacity, and
recover or fence failed execution without inventing a parallel lifecycle.

### standing-portfolio-oversight

A portfolio Orchestrator oversees the full contribution corpus without replacing
dedicated per-change reviewers. It synthesizes themes and goals across
contributions, publishes shared guidance, identifies dependencies and conflicting
scope, and prioritizes within its delegated charter without asking the operator
about every routine decision. It proposes and drives vision revisions through
their existing reviewed publication and owner-authorization paths; neither
oversight nor a vision proposal expands its own authority.

The [reviewer consultation contract](../../plugins/agent-dispatch/reviewer/README.md#portfolio-consultation)
connects independent reviews to this shared judgment without making the
Orchestrator an omniscient super-worker or granting it each reviewer's execution
authority. Dispatch carries durable charter relationships and scoped steering;
bridge hosts and messages agents, while worktrees retains durable agency.

### lightweight-planning

Planning conversations can remain ephemeral. Not every question, exploration,
report, or draft becomes a task. Accepted standing intent belongs in visions or
decisions; accepted execution belongs in durable goals with explicit acceptance.

## Behaviors

### reconcile-the-corpus

The Orchestrator coalesces consultations, prioritizes actual blockers and shared
themes, and avoids repeated unchanged assessments or replies. It periodically
reconciles the full corpus: summaries and event delivery are useful evidence,
not the sole truth. Missing or stale evidence remains explicit rather than
becoming an invented complete picture.

### inspect-steer-and-return

Operators can inspect any layer, steer, reprioritize, or swizzle tasks among
eligible agents and venues. They can take over and directly drive the same agent,
then explicitly return its agreed charter or withdraw it. Control ownership and
its revision fence autonomous wakes and reassignment: no silent autonomous
resumption while the operator owns control.

### canonical-authority-through-partitions

Worktree identity and claims, task ownership, service lifecycle, and evidence
retain their respective canonical owners. Central visibility augments local
continuity backup under partitions; backups preserve recoverable work without
competing claims or stale write authority. Reconnection reconciles through the
owner rather than choosing whichever copy is newest.

### outcomes-not-execution-counts

Completion follows the founding objective's acceptance and any canonical effort
gates. A finished session, merged slice, or silent process does not complete the
larger objective. Recovery and progress remain visible and accountable.

## Non-Goals / Boundaries

No independent factory task ledger, lifecycle store, universal controller, or
mandatory deployment topology. Planning and observation confer no execution or
destructive permission; automation stays scoped and withdrawable.

## See Also

- Parent: [agent-fabric](../README.md)
- Agency and claims: [agent-worktrees](../../plugins/agent-worktrees/README.md)
- Tasks and supervision: [agent-dispatch](../../plugins/agent-dispatch/README.md)
- Acceptance and continuity: [efforts](../../plugins/efforts/README.md)
- Conversation and venue coordination: [agent-bridge](../../plugins/agent-bridge/README.md)
- Fleet reach: [machine-fleet](../../machine-fleet/README.md)
- Service ownership: [plugin-services](../../plugin-services/README.md)
