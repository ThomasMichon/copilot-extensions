# Checkout Hydration - Vision

- **Subject:** reversible checkout storage for durable worktree agency.
- **Scope:** leaf; child of agent-worktrees.
- **Status:** Active
- **Last revised:** 2026-11-20
- **Reality docs:** agent-worktrees lifecycle, entry and roster documentation.

## Purpose & Intent

A worktree's responsibility should not require a permanently materialized
checkout. Stopped agents should release reproducible disk content automatically
and resume through ordinary entry without losing their branch, conversation,
identity or unfinished work.

Storage lifetime is independent of source-control completion and agency
lifetime. A clean worktree with local, unpushed commits is as capable of
reversible storage release as a clean conversation-only or merged worktree.

## Concepts & Components

### Durable agency, optional checkout

Hydration is a storage sub-state, not a lifecycle conclusion. The retained local
branch preserves committed source, while sessions, relationships, claims,
objectives and domain-owned data retain their existing authorities.

### Nearby recovery catalog

Before removing a checkout, its owner durably records the identity, branch,
commit and relationship state needed for restoration and tracking recovery
outside the disposable directory. This recovery material remains near the
repository's worktree storage and discoverable without a surviving checkout.

### One storage and entry authority

Worktrees owns reversible storage transitions and coordinates them with its
normal lifecycle and launch admission. Execution providers, Bridge and Manager
consume that authority rather than inventing separate deletion or recovery rules.

## Features

### reversible-checkout-storage

Eligible checkouts can be removed and recreated at their established location.
The branch remains intact and its committed content remains reachable.
Unexpected branch changes are visible conflicts, not silently adopted or reset.

### automatic-retirement-and-entry

Actual agent-process retirement triggers eligible storage release. Ordinary
managed entry restores the required checkout before resume, regardless of mux
or Bridge presentation. Passive inspection never materializes checkout storage.

### catalog-aware-reconstruction

Roster reconstruction accounts for deliberately absent checkouts and their
nearby recovery material. Lost operational tracking can be reconstructed without
losing relationship evidence or falsely concluding responsibility. Conflicting
external authority remains explicitly unresolved.

### visible-storage-controls

Operators see dehydration as a suffix to the existing state, with visible
transition and refusal reasons. Manual actions, automatic/manual policy and
per-worktree holds share the same owner-mediated checks.

## Behaviors

### fresh-content-and-retirement-checks

Removal requires live Git evidence of no staged, modified or untracked content
and confirmed absence of checkout users or competing entry. Ignored files are
disposable; reproducible artifacts and projections return through their normal
owners. Unknown evidence preserves the checkout. Existing checkout-local durable
history retains the [worktree-data preservation boundary](../worktree-data/README.md#preserve-legacy-data-before-disposal):
its owner preserves the history and explicitly retires the local source before
disposal. Ignored status, a locator or a copy alone never discharges that hold.

### responsibility-survives-dehydration

Storage removal never finalizes work, releases claims, closes publication,
concludes conversations or severs paired-worktree identity. Unfinished work
remains discoverable and accountable while its checkout is absent.

### recoverable-exclusive-transitions

Removal and entry cannot act concurrently on the same checkout. Interrupted
transitions retain enough durable state for safe reconciliation. Recovery
material is retired only after verified restoration, and stale snapshots never
overwrite newer agency state or authorize replay of external effects.

## Non-Goals / Boundaries

- No branch deletion, source backup guarantee or mandatory branch-name namespace.
- No preservation of ignored build output or temporary files in the checkout.
- No deletion based solely on a display label, callback, age or cached clean state.
- No independent cleanup daemon, competing roster or hydration during passive reads.
- No implicit update, rebase, deployment or responsibility transfer on resume.
- No bypass of terminal collection's separate completion and ownership rules.

## See Also

- Parent: [agent-worktrees](../README.md).
- [Continuous reclamation](../continuous-reclamation/README.md) - terminal storage collection.
- [Worktree data](../worktree-data/README.md) - checkout-independent domain state.
- [Session hosting](../../../session-hosting/README.md) - execution-provider boundary.
- [Architecture patterns](../../../../docs/patterns/README.md).
- Reality: [worktree lifecycle](../../../../plugins/agent-worktrees/docs/worktree-lifecycle.md).
