# Harness Guidance — Vision

- **Subject:** Ambient guidance across repositories, plugins, skills, and operator policy
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-08-20
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
available on demand.

### portable-operator-policy

An operator should be able to carry personal policy across target repositories
without copying it into each repository or erasing repository-owned overrides.

### attributable-context-budget

Always-on context should be measurable by source and category so repositories,
plugins, and operators can make deliberate tradeoffs within a shared budget.
Injected kernels should carry a stable plugin owner marker.

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

## Non-Goals / Boundaries

- This vision does not prescribe one configuration schema or hook script.
- It does not move repository-specific identity or invariants into plugins.
- It does not make skills an always-on policy channel.
- It does not require executing dynamic guidance producers to estimate their
  contribution.

## See Also

- Parent vision: none
- Child visions: none (leaf)
- Reality docs: `docs/patterns/context-injection.md`, `docs/harness-runbook.md`
