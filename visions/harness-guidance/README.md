# Harness Guidance — Vision

- **Subject:** Ambient guidance across repositories, plugins, skills, and operator policy
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-08-28
- **Reality docs:** `docs/patterns/context-injection.md`, `docs/harness-runbook.md`

## Purpose & Intent

Harness guidance should reach an agent from the authority that owns it, at the
scope where it applies, without turning every repository instruction file into a
copy of every enabled plugin's policy. The resulting context should be concise,
portable, attributable, and intentionally budgeted.

## Concepts & Components

- **Repository guidance** defines the repository's identity, configuration, and
  irreducible local invariants and fail-safes.
- **Plugin guidance** defines generic ambient policy owned by a reusable
  capability and delivers a concise context kernel wherever that capability is
  enabled.
- **Skills** provide detailed procedures at task time rather than occupying
  always-on context.
- **Coordinators and delegates** divide work by context cost and separability:
  the coordinating agent owns decomposition, integration, synthesis, and
  completion, while bounded delegates isolate independent evidence gathering,
  domain-tool interaction, explicitly disjoint implementation, and independent
  review roles.
- **Operator policy** is personal ambient guidance that can follow an agent
  across target repositories. A plugin may explicitly delegate narrow
  configuration keys to a repository, but safety, publication, attribution,
  and sanitization policy remains with the operator/plugin owner.
- **Context accounting** makes each loaded guidance source visible as a
  contributor to a shared budget.

## Features

### authoritative-ownership

Every piece of guidance should have one authoritative owner. Repository-owned
identity, configuration, and invariants should remain with the repository;
generic plugin policy should evolve with and be delivered by its plugin; and
detailed procedures should live in skills.

### concise-context-kernel

Plugin-owned ambient policy should reach every applicable session as a concise
kernel containing only what must remain active, with detailed mechanics
available on demand. When the host cannot compose independent hook outputs,
one attributable authority should compute the kernel and every proven producer
should return the same bytes, so host result selection cannot discard guidance.
When repeating the complete aggregate through every hook would exceed a host
budget, those identical bytes should instead be a compact critical kernel plus
an exact session-scoped pointer to the full attributable context.

### portable-operator-policy

An operator should be able to carry personal policy across target repositories
without copying it into each repository or erasing repository-owned overrides.

### attributable-context-budget

Always-on context should be measurable by source and category so repositories,
plugins, and operators can make deliberate tradeoffs within a shared budget.
Injected kernels should carry a stable plugin owner marker.

### coordinator-first-task-routing

Harnesses should provide model-neutral guidance that helps a coordinating agent
route work by expected context consumption and separability. Broad independent
research, comparisons, evaluations, bulk analysis, and disjoint bulk edits
should move into bounded delegate contexts before their source material floods
the coordinator, while small lookups, genuinely continuous traces, and cohesive
implementation remain direct when splitting them would cost more than it saves.

### bounded-delegate-contracts

A delegated scope should have explicit ownership, bounded inputs and outputs,
non-overlapping responsibility, an integration plan if it edits files, and a
result shape suitable for integration.
Domain-specific service catalogs and verbose tool payloads should remain with
the delegate that owns that domain; compact shared research and orchestration
signals may remain with the coordinator.

## Behaviors

### lean-repository-waypoint

A root `AGENTS.md` should remain a lean orientation map plus genuinely
repository-owned invariants and minimal fail-safes. It should not become a
materialized copy of generic policy from enabled plugins.

### guidance-follows-ownership

Guidance should evolve and ship with its owner. Updating a plugin's ambient
policy should not require synchronized edits across every adopting repository.

### task-detail-on-demand

Detailed procedures should enter context when their task requires them and
should remain discoverable from the concise ambient kernel.

### resilient-safety-boundary

Critical safety and publication constraints should retain a minimal static
fallback when a launch path cannot load the richer plugin-owned guidance.
Plugin setup should own any compatibility/fallback prose through a stable,
idempotently reconciled marker or dedicated rule file.

### transparent-cost

Context reporting should distinguish known static and metadata costs from
dynamic contributions whose emitted size cannot be known without execution.

### delegate-before-broad-ingestion

When work contains separable evidence tracks whose direct ingestion would
materially consume the coordinator's context, delegation should happen before
the coordinator opens the broad source bodies. A coordinator should not repeat
a delegated investigation without a concrete verification reason.

### coordinator-retains-the-goal

Delegation should not turn the coordinating agent into a passive dispatcher.
The coordinator remains responsible for the prompt's goal, chooses and adjusts
the decomposition, integrates evidence, directly drives cohesive implementation
by default, and produces the final synthesis and completion judgment.

### proportional-independent-review

Independent review should preserve distinct required roles without becoming an
unbounded loop. An unchanged artifact should not receive repeated same-role
review unless a concrete defect or materially changed evidence justifies it.

## Non-Goals / Boundaries

- This vision does not prescribe one configuration schema or hook script.
- It does not maximize sub-agent count or require delegation for every lookup.
- It does not delegate final synthesis, goal ownership, or completion judgment.
- It does not prescribe one model, task API, agent runtime, or orchestration
  transport.
- It does not authorize recursive self-delegation or overlapping edit ownership.
- It does not move repository-specific identity or invariants into plugins.
- It does not make skills an always-on policy channel.
- It does not require executing dynamic guidance producers to estimate their
  contribution.

## See Also

- Parent vision: none
- Child visions: none (leaf)
- Reality docs: `docs/patterns/context-injection.md`, `docs/harness-runbook.md`
