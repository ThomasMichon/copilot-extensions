# Repository Topology - Vision

- **Subject:** Independently composable harness, knowledge, and product repositories
- **Scope:** leaf
- **Status:** Active
- **Last revised:** 2026-10-10

## Purpose & Intent

Agents should work across repositories without confusing where reusable agency
comes from, where intent belongs, or who may edit and publish a product.
Repository relationships express capabilities and ownership, not assumptions
based on directory names or one prescribed arrangement.

## Concepts & Components

**Harness repositories** carry reusable agent policy and tools. **Knowledge
repositories** carry operator or domain intent and history. **Product
repositories** carry code and assets. Multiple repositories in each role compose
independently; a converged repository may serve several roles.

One harness and knowledge pair serving many products is a useful arrangement,
not a mandate. Role is distinct from Git editing class, ownership, and hosting
provider. Qualified identities make same-named repositories and principals
unambiguous across independently administered sources.

## Features

### capability-based-relationships

Agents discover relationships through declared capabilities and attributable
identity. Placement of intent, execution, and artifacts follows the subject's
owner and available capabilities, allowing multiple harnesses, knowledge sources,
and products without treating a pairing as universal authority.

### principal-and-data-scope

Per-principal access distinguishes discoverable metadata, readable source, and
semantic evidence. Each scope is explicit and enforced through retrieval and
execution, including derived indexes, summaries, and cross-repository references.
Discovery is not write privilege.

### canonical-placement

Every objective, source, and record has one canonical owner. Relationships and
references let agents compose that ownership without copying authoritative intent
or creating rival task and history stores. A location change preserves identity,
responsibility, and traceability.

## Behaviors

### privacy-preserving-composition

Private knowledge does not automatically become public product documentation,
issues, commits, reports, or retrieval output. Publication is a separate,
authorized boundary; generalized public artifacts stand on their own without
private identifiers or rationale.

### honest-authority-and-availability

An agent distinguishes role, editing model, ownership, provider, and its own
effective access before acting. Missing or inaccessible relationships remain
explicitly unavailable, not an invitation to create duplicate repositories,
invent owners, or broaden credentials.

## Non-Goals / Boundaries

No mandatory repository split or convergence, no one global harness or knowledge
root, no ownership inferred from paths, and no access granted merely by semantic
discovery or a relationship.

## See Also

- Parent: [agentic-factory](../README.md)
- Repository identity and relationships: [agent-worktrees](../../../plugins/agent-worktrees/README.md)
- Task ownership: [agent-dispatch](../../../plugins/agent-dispatch/README.md)
- Effort placement: [efforts](../../../plugins/efforts/README.md)
- Scoped retrieval: [agent-index](../../../plugins/agent-index/README.md)
- Evidence ownership: [agent-logger](../../../plugins/agent-logger/README.md)
