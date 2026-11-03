# Durable Worktree Data - Vision

- **Subject:** Discoverable worktree-scoped data and its lifetime beyond a checkout.
- **Scope:** leaf; child of agent-worktrees
- **Status:** Draft
- **Last revised:** 2026-11-02
- **Reality docs:** [agent-worktrees](../../../../plugins/agent-worktrees/README.md),
  [efforts](../../../../plugins/efforts/README.md) and
  [agent-logger](../../../../plugins/agent-logger/README.md)

## Purpose & Intent

Worktree-scoped agendas and accepted history should have a predictable durable
home that their owner, Manager, subsequent sessions, logging and archival can
all discover. Their lifetime should not depend on retaining a source checkout
or remembering an invocation-specific path.

Worktree identity anchors discovery and retention. Domain owners retain their
state, transaction and export semantics; logging preserves their evidence
without becoming another live writer or acquiring agency authority.

## Concepts & Components

- **Worktree data home** is a worktree-scoped namespace whose lifetime is
  independent of the checkout and current execution session.
- **Owner-published locator** resolves stable repository, worktree and domain
  identities to an admitted current location without a separate consumer map.
- **Domain-owned state** includes an agenda and its accepted history, separate
  from the bounded agency record and from session transcripts.
- **Consistent export** preserves the domain's complete declared data at an
  identified revision, not merely its current presentation or loaded pages.
- **Archive receipt** identifies exactly which exported evidence has actually
  reached its admitted durable destination.

## Features

### predictable-shared-discovery

Manager, continuing agents and archival consumers discover the same admitted
data through the existing worktree owner. Identity remains stable when a
checkout disappears, a session changes or a physical location moves. A stale
or unavailable locator is explicit, never permission to guess a directory.

### checkout-independent-retention

Removing a safely disposable checkout does not erase its agenda or accepted
history. Retained data remains discoverable without leaving a phantom active
worktree or preserving unfinished source responsibility artificially.

### domain-owned-lossless-export

Each domain supplies a consistent, complete export with attributable identity,
revision and integrity evidence. An optional archivist consumes that export
without writing canonical domain state or silently replacing it with a lossy
projection.

### worktree-scoped-archival

Worktree data is collected once at its own identity and revision, with references
from contributing sessions. Later history and logging can retrieve it after
checkout retirement without treating one arbitrarily selected session as its
owner.

## Behaviors

### one-allocation-authority

Managed data placement and discovery have one worktree-owner boundary.
Presentation and archive consumers do not establish parallel path registries,
allocate missing state during reads or infer authority from a copied locator.
Domain plugins remain usable independently where managed worktree integration
has not been adopted.

### relocation-preserves-identity-and-history

An admitted relocation preserves store identity and accepted history, switches
the authoritative location once and does not leave two live writers. Historical
and current locations remain distinguishable; unsupported or uncertain
restoration never fabricates continuity.

### checkout-retirement-is-not-data-reclamation

Checkout disposal, archive collection and persistent-data deletion are separate
decisions. An archive transfer result does not itself permit reclamation.
Deletion requires the applicable retention and authorization rules plus
verified evidence for the exact data revision being removed.

### preserve-legacy-data-before-disposal

Existing checkout-local data is preserved or explicitly rescued before its
containing checkout is removed. Missing, unreadable or ambiguous preservation
evidence cannot be treated as an empty agenda or successful archival.

### historical-evidence-is-not-live-authority

Archival and restoration preserve history without restoring credentials,
exclusive leases, resource custody or session-head ownership. Collection and
publication obey their existing privacy, repository and destination policies.

## Non-Goals / Boundaries

- Manager and agent-logger do not become canonical agenda writers.
- Rich domain data does not become an unbounded worktree agency record.
- A live database is not made safe for concurrent writers by copying or syncing it.
- A local transfer or destination copy is not proof of durable remote archival.
- This vision does not authorize migration, cleanup, collection, publication or
  rollout, nor replace effort, resource or source-control completion gates.
- This vision does not prescribe a physical root, directory layout, storage
  engine, wire schema, configuration key or archive provider.

## See Also

- Parent: [agent-worktrees](../README.md).
- Domain owner: [efforts](../../efforts/README.md).
- Archivist: [agent-logger](../../agent-logger/README.md).
- Consumer: [Mux Companion](../../../mux-companion/README.md).
