# agent-dispatch repository-issue-loops — Vision

- **Subject:** The durable, cooperative repository-backlog loop built on
  agent-dispatch recipes and worktree identity: a declarative engine that
  triages and drives a bounded batch of a backlog's items to durable
  resolution with one worker per occurrence.
- **Scope:** leaf (a child of the
  [agent-dispatch](../README.md) plugin vision, sibling to the
  [reviewer](../reviewer/README.md) child vision)
- **Status:** Draft
- **Last revised:** 2026-09-05
- **Reality docs:** `plugins/agent-dispatch/src/agent_dispatch/repository_issue_loops.py` ·
  `plugins/agent-dispatch/src/agent_dispatch/supervisor.py`

## Purpose & Intent

A repository or product backlog is a durable stream of independent work items
that no single human can triage and drive continuously. A repository-issue-loop
turns that stream into a declarative, self-service engine: an adopter states
*what* backlog to work (its source, eligibility, cadence, and acting policy),
and the shared runtime supplies *how* — discovery, batching, dependency-aware
eligibility, reservation, embodiment, and durable outcome recording — without
the adopter writing or operating any orchestration code themselves.

Adoption should be as easy for a colleague on an unfamiliar team as it is for
its original author: declaring a new loop over a new backlog is authoring one
small declaration, not learning or forking the engine. The same declarative
engine should work equally over any backlog whose items can be listed,
reserved, and settled through a provider adapter — GitHub issues today, an
Azure DevOps (or other work-tracking) backlog tomorrow — without a
backlog-specific rewrite of the engine itself.

## Concepts & Components

### The declaration

A repository supplies one small, validated declaration: its backlog source
and provider, eligibility (labels/fields, quiet period, batch size,
priority), cadence, and acting identity. The declaration is the entire
adoption surface; it carries no orchestration logic.

### The backlog provider

Backlog capabilities are exposed through one coherent, provider-neutral
surface: list open items, reserve one, claim it under a task, and release it.
Loop policy composes this surface instead of embedding provider-specific
listing, reservation-marker, and mutation code per backlog kind. GitHub issues
and an Azure DevOps backlog are both, to the loop, just a `backlog provider`.

### The worker identity

Each embodied worker acts under one declared identity: a named, reusable
sub-agent definition that carries the acting theme, focus, and rules
structurally (instructions, tool boundaries, and behavior), the same way a
delegated review or execution sub-agent does elsewhere in this suite. A
declaration selects a worker identity; it does not restate that identity's
policy as inline prose duplicated across every adopting repository.

### The bounded batch

Each occurrence claims a small, quiet, eligible batch of items under one
dispatch task, drives it to durable resolution (closed, merged, explicitly
deferred, or durably blocked), and settles before the next occurrence begins.

## Features

### declarative-turnkey-adoption

Adopting a new backlog is authoring one declaration and pointing it at a
worker identity — no engine code, no bespoke scripts, no repository-specific
orchestration. A colleague unfamiliar with the runtime's internals can stand
up a new loop from the declaration schema and existing worker identities
alone.

### provider-neutral-backlog-capability

The reusable backlog-provider surface covers the common list/reserve/claim/
release operations across supported backlog kinds. Provider differences stay
behind adapters (GitHub issues, Azure DevOps work items, and others as
adopted); loop policy, eligibility, and batching never reimplement
provider-specific listing or mutation.

### declarative-worker-identity

A declaration names the sub-agent identity that embodies its workers, rather
than inlining that identity's theme, focus, and rules as ad hoc prompt prose.
The named identity is itself reusable and independently revisable; sharpening
its rules improves every declaration that selects it, and its structural
boundaries (instructions, permitted tools and mutations) hold even where prose
alone would not. The identity carries the shared "how to behave as a dispatch
worker" supplement by reference (parent vision
§Features/*preloaded-dispatch-supplement*), so only its domain-specific
policy is authored per identity; each embodiment then pulls its event-specific
charter on demand (parent vision §Features/*concise-event-then-charter-pull*)
rather than re-paying for the whole supplement inline every time.

### dependency-aware-eligibility

An item already durably blocked — by an upstream prerequisite, another
author's in-flight contribution, or an explicit maintainer hold — is never
silently re-selected into a future occurrence. The loop records that
dependency durably (through the backlog provider's own tagging/relationship
primitives) and re-admits the item automatically once the dependency clears,
without an operator having to remember or manually re-triage it.

## Behaviors

### batch-then-settle

Each occurrence reserves its batch, drives every item to a durable outcome or
an explicit, recorded reason it could not, and settles cleanly — leaving no
partial claim, no dangling reservation, and a clean, reusable workspace behind
it.

### never-supersede-a-contribution

When an eligible item's fix already exists in another author's in-flight
contribution, the worker renders constructive feedback and durably declares
the dependency; it never closes, supersedes, or replaces that contribution
with a competing one under its own identity.

### quiet-before-claim

An item that changed too recently is left for a later occurrence rather than
claimed mid-edit, so the loop never competes with active human or automation
attention on the same item.

### render-or-report

Each occurrence either resolves its claimed items or records why not — a
missing durable outcome is an explicit, visible reason (deferred, blocked,
steering needed), never silent churn or a forgotten claim.

## Non-Goals / Boundaries

- The generic engine does not embed one repository's contributor policy,
  acceptance rubric, or organizational identity — that belongs to the
  declaration and the worker identity it selects, not the runtime.
- A declaration does not reimplement discovery, reservation, batching, or
  settlement; it configures the shared engine.
- The engine does not guess a backlog-specific outcome when policy, scope, or
  ownership is materially ambiguous; it surfaces that ambiguity rather than
  guessing to keep the loop moving.
- Provider adapters do not leak backlog-kind-specific concepts (issue numbers,
  work-item IDs, area paths) into the provider-neutral eligibility and
  batching policy above them.

## See Also

- [agent-dispatch vision](../README.md)
- [reviewer child vision](../reviewer/README.md) — the sibling declarative
  engine for pull-request processing; shares the provider-neutral-capability
  and declarative-adoption intent over a different subject (a change under
  review, not a backlog item).
- Realization effort:
  [`efforts/active/review-automation-reliability/`](../../../../efforts/active/review-automation-reliability/)
  (lifecycle reliability) and
  [`efforts/active/declarative-dispatch-engine-generalization/`](../../../../efforts/active/declarative-dispatch-engine-generalization/)
  (turnkey adoption, provider-neutral capability, worker identity)

## Provenance

- **2026-09-05** — Extracted as a sibling to the reviewer child vision after
  live use of the `odsp-web-harness-backlog` declaration surfaced three
  forward intents worth stating explicitly: turnkey adoption for a colleague
  unfamiliar with the engine, provider-neutral capability toward Azure DevOps
  backlogs (the `ForgeProvider` seam already exists in
  `repository_issue_loops.py`; only a GitHub adapter is wired in today), and
  moving worker policy from inlined declaration prose to a declared, reusable
  sub-agent identity — the same live use that surfaced the
  never-supersede-a-contribution behavior below.
