---
applyTo: "**"
---

# Agent Worktrees session context guide

At the start of a session, `agent-worktrees` writes a bounded, computed
snapshot of the current checkout and cross-repo topology to your
session-state folder (see the paired `session-guidance.instructions.md`
pointer for how to find and read it). That file carries only the live,
computed values -- this guide explains what they mean and where to get more.

## Reading the `Checkout:` / `State:` / `Related:` facts

- `Checkout: repo=...; id=...; role=...; kind=...; status=...; writable=...;
  locus=...; delegate=...; path=...` describes the current worktree itself.
- `State: source=...; repo=...; status=...; path=...` describes the resolved
  state root (where personal/knowledge state for this project lives), with an
  optional trailing `Pair:`/`KnowledgePair:` clause describing a paired
  sibling worktree (e.g. a bound knowledge repo) when one exists.
- `Related: primary=...; important=...` is a **bounded, curated subset** of
  the full cross-repo topology -- not the complete list. It surfaces only the
  primary project and repos with a non-local locus or an active delegate, to
  stay within a small per-session byte budget.

## Getting the full picture

The session-state facts above are intentionally partial. For the complete,
authoritative topology, worktree binding, or related-repo detail, run these
live rather than relying on the snapshot:

- `agent-worktrees state-root --pair --json`
- `agent-worktrees related list`
- `agent-worktrees related resolve <name>`
