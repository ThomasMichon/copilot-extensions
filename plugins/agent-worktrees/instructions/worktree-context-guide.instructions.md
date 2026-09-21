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

## Never clone another repo as a child directory of this checkout

Needing another repo's own files (its instructions, source, or docs) is a
cross-repo need, not a reason to `git clone`/copy it into a subdirectory of
the current checkout (e.g. `external/<repo>/`, `vendor/<repo>/`,
`third_party/<repo>/`). A child clone drifts silently (no update path), is
never tracked by this tool's worktree/finalize lifecycle, shows the current
checkout as permanently dirty (an untracked directory git will never
recognize as intentional), and duplicates a repo this tool almost certainly
already has a registered, resolvable worktree home for.

Instead, resolve the other repo the same way as any related work:

- `agent-worktrees repos list --json` / `agent-worktrees repos find <name>` --
  discover whether it is already registered and where its checkout lives.
- `agent-worktrees related resolve <name>` -- the delegation-aware answer for
  *how* to work on it from here (direct worktree vs. an agent-guarded repo).
- `agent-worktrees -p <name> create --json` -- open your own worktree on that
  repo when you need to read or edit its real, live tree; read its
  instructions/docs directly from that worktree, never from a copy embedded
  in this one.

If a repo genuinely is not registered anywhere, that is a topology gap to
fix in the registry (or ask the operator), not a reason to reach for `git
clone` into a subdirectory here.
