---
name: cleaning-worktrees
description: >
  Plans and executes an explicitly scoped cleanup pass through the owning
  repository's supported lifecycle. Use for clean stale worktrees, reclaim disk
  space, inspect finalized-retained checkouts, clean claimed child worktrees,
  or investigate ignored files blocking removal. Preserves live owners,
  unlanded work and retained history.
---

# Cleaning worktrees

Use the selected installation's exact command from the session catalog.
Read the [worktree guide](../worktree/SKILL.md), Cleanup Procedure, and the
[cleanup guidebook](../worktree/references/cleanup-details.md) before effects.
The [closeout distinction](../worktree/references/closeout-and-reclamation.md)
owns retained resources and cross-repository cleanup reporting.

Select and preview the scope; reconcile source, live use, artifact/history
disposition and actual repository ownership. Include known finalized-retained
children rather than equating an empty active-obligation view with an empty
disk footprint. Apply only supported, authorized cleanup and verify each outcome.
Never force-release a claim, infer disposability from `.gitignore`, or delete
a managed checkout outside its owning lifecycle.
