# Harness Guidance — Vision

- **Subject:** Ambient guidance across repositories, plugins, skills, and operator policy
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-09-04
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
- **Role/model routing policy** classifies delegated work by purpose, risk,
  execution surface, and demonstrated capability, then selects from an
  evidence-backed mapping of acceptable models rather than treating every
  worker as interchangeable.
- **Routing evidence** links model-purpose eligibility to reviewed outcomes,
  including retries, discarded work, coordinator repair, and downstream
  findings, so a cheap call is not mistaken for a cheap accepted result.
- **Operator policy** is personal ambient guidance that can follow an agent
  across target repositories. A plugin may explicitly delegate narrow
  configuration keys to a repository, but safety, publication, attribution,
  and sanitization policy remains with the operator/plugin owner.
- **Context accounting** makes each loaded guidance source visible as a
  contributor to a shared budget.
- **Grounding guides** hold detailed behavioral control and reference material
  that remains overarching but does not need to occupy every model request.
- **Aggregate document structure** gives independently owned contributions a
  coherent, navigable shape without transferring their authorship to the
  composition authority.

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

### resume-stable-context

Ambient guidance should remain available after restart, resume, compaction, or
other context reconstruction boundaries. When a start-time delivery channel is
not durably represented in reconstructed history, a bounded model-facing prompt
recovery channel should re-establish an exact, attributable context pointer
without duplicating the full aggregate on every turn.

### progressive-context-disclosure

Always-loaded guidance should contain only the critical policy, constraints,
orientation, and decision cues an agent needs before it can safely choose what
to inspect. Detailed overarching behavior and grounding material should remain
available through attributable on-demand references rather than being eagerly
loaded into every session.

### navigable-on-demand-grounding

Deferred guidance should remain easy for an agent to discover and apply. Each
reference should make its owner, subject, applicability, and expected use clear
enough that the agent reads the right guide when needed and does not explore
irrelevant material by default.

### coherent-attributable-assembly

When several plugins contribute ambient guidance, the assembled context should
form a deterministic, legible hierarchy of critical constraints, orientation,
capability grounding, and deferred references. Composition should preserve
source attribution and owner boundaries rather than rewriting independent
policy into an unattributed central voice.

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

### evidence-calibrated-model-routing

Harnesses should help a coordinator choose the least-expensive available model
that has demonstrated the capability, tools, context, and reliability required
for the delegated role. Model choice should follow task classification and the
direct-versus-delegate decision rather than becoming a reason to fragment
cohesive work.

### purpose-to-model-grounding

Current model eligibility should be supplied through attributable, configurable
grounding that distinguishes demonstrated choices from candidates, holds, and
known failures. The portable strategy should remain stable while repositories
and operators can update the models that satisfy it as providers, costs,
capabilities, and execution surfaces change.

### guarded-real-work-evidence

Model-routing evidence should come from bounded real work whose authority grows
through explicit stages: read-only investigation, reviewed documentation,
contained implementation, and ordinary publication gates. Trial workers should
be isolated from credentials and authority their assigned role does not need.

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

### ambient-delivery-fails-open

Ambient delivery plumbing should fail open and never block session startup when
a contributor is inapplicable, unavailable, or malformed. That delivery
failure must remain attributable and diagnosable rather than being mistaken for
successful policy application. Fail-closed behavior belongs at authorization,
trust, and model-eligibility boundaries, not in the mechanism that lets an
otherwise-usable session start.

### recovery-revalidates-authority

Context recovery should never replay a previously valid aggregate solely
because session-local state exists. It should revalidate repository trust,
scope, authority, and contributor identity at the current boundary, then either
recover the matching context or fail closed with bounded guidance.

### critical-before-comprehensive

Context authors should prefer a short, stable kernel that enables safe first
decisions over a comprehensive procedure dump. Deferral must never hide a rule
the agent needs in order to know that a guide exists or that an action is unsafe.

### references-carry-applicability

An on-demand reference should state when it matters, not merely where it lives.
The agent should be able to distinguish mandatory grounding for the current
task from optional background and unrelated capability documentation.

### composition-preserves-owner-boundaries

A composition authority may order, group, label, and budget contributed
material, but should not silently paraphrase, merge, or resolve disagreements
between independently owned policies. Conflicts remain attributable and
diagnosable.

### deferral-is-evidence-calibrated

Decisions about kernel size, reference form, emphasis, and hierarchy should be
validated against observed agent behavior. Context reduction is successful only
when first-turn correctness and task-appropriate grounding are retained without
causing routine unnecessary exploration.

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

### routing-policy-before-delegation

A Task-capable decision-maker should receive the compact model-routing policy
before its first delegation decision. A worker that cannot delegate should
receive its bounded assignment rather than paying the context cost of the
complete routing catalog.

### least-expensive-demonstrated-choice

Selection should prefer the least-expensive available choice that has
demonstrated the required task capability and execution-surface support. An
unavailable preferred model may fall through to another demonstrated choice;
an unproven candidate runs only as an explicit trial and never becomes a silent
default.

### accepted-outcome-economics

Model-routing decisions should be evaluated against accepted work products, not
isolated call prices. Retries, abandoned output, coordinator repair, review
findings, and attributable follow-up work remain visible so apparent savings
cannot be created by moving cost or failure outside the selected worker's call.

### independent-promotion

The agents and pairs being evaluated should not validate themselves. Promotion
into ordinary routing should derive from durable trial evidence and a separate
reviewed decision with explicit uncertainty and applicability boundaries.

### product-gates-outrank-routing

Routing optimization must not lower the correctness, safety, review, or
publication requirements of the work being routed. A cheaper pair that fails
the product's ordinary acceptance gate is not an optimization.

### configuration-is-inert-data

Repository, plugin, and operator configuration used to compose guidance or
resolve model eligibility should be parsed and validated as inert declarative
data. A harness must not source or execute configuration merely to discover
policy, availability, or routing choices.

## Non-Goals / Boundaries

- This vision does not prescribe one configuration schema or hook script.
- It does not maximize sub-agent count or require delegation for every lookup.
- It does not delegate final synthesis, goal ownership, or completion judgment.
- It does not prescribe one model, task API, agent runtime, or orchestration
  transport.
- It does not treat the cheapest available model as qualified merely because it
  can be launched.
- It does not let a model, coordinator, worker, or pair promote itself into the
  accepted routing policy.
- It does not replace product review, safety, deployment, or publication gates
  with a routing score.
- It does not authorize recursive self-delegation or overlapping edit ownership.
- It does not move repository-specific identity or invariants into plugins.
- It does not make skills an always-on policy channel.
- It does not require detailed overarching guidance to be recast as a skill
  merely because it is loaded on demand.
- It does not assume that a particular Markdown or path representation is
  reliably followed or ignored without behavioral evidence.
- It does not authorize a composition authority to rewrite plugin-owned policy
  into one synthesized voice.
- It does not require executing dynamic guidance producers to estimate their
  contribution.

## See Also

- Parent vision: none
- Child visions: none (leaf)
- Reality docs: `docs/patterns/context-injection.md`, `docs/harness-runbook.md`
