---
name: resolving-state-home
description: |-
  Resolve the user's writable state/knowledge repo from a stateless harness and
  enforce the paired-worktree boundary. Use for personal state, rootless writes,
  knowledge-repo setup/repair, or questions about where state belongs.
  Trigger phrases include:
  - "resolve the knowledge repo"
  - "where does this live"
  - "write to the state repo"
  - "knowledge worktree"
---

# Resolving the state home

Use the exact `argv prefix` from the agent-worktrees session command catalog for all
commands below.

`agent-worktrees` owns the state split natively. A stateless harness contains
shared capability; its bound knowledge repo contains personal state. Never
hardcode either checkout and never fall back to writing state in the harness.

## Resolve binding and writable workspace

1. Run `state-root --json`.
   - `bound: false` with an empty `repo` means no knowledge repo is configured.
     Stop stateful work and run the `binding-knowledge` setup skill.
   - `bound: false` with a non-empty `repo` means the binding is configured but
     unresolved. Repair/register that repo as class `worktree`; do not write.
   - `bound: true` identifies the bound repo and its registered anchor. The
     anchor is identity/read-only, not the task workspace.
2. Run `get worktree-dir`. An empty result means the current project checkout is
   an anchor/bare checkout; do not edit it.
3. Run `state-root --pair --json`.
   - When `paired: true`, use the `sibling.path` whose role is `knowledge`.
   - Otherwise create a dedicated knowledge worktree with
     `-p <knowledge-name> create --json` and use only the returned path.

## Route writes

| Change | Destination |
|--------|-------------|
| Shared harness instructions, topology, skills, agents, plugins, docs | harness worktree |
| Efforts, logs, notes, weekly updates, personal visions, artifacts, preferences, personal plugins, ambiguous/rootless writes | knowledge worktree |
| Product code/configuration | resolved product repo |

For another repo, run `related resolve <name>` and honor its management class,
locus, and delegate. A target may be local, on another machine, in a CodeSpace,
or in a container; do not clone or reach across machines to bypass its declared
route.

## Worktree lifetime

One coherent task owns one worktree. Commit and land through that repo's
configured direct/PR flow. Keep blocked or unfinished worktrees with accurate
status and follow-up state. Run `finalize` only after the work is upstream and no
live session owns the worktree. Never delete a managed worktree by hand.
