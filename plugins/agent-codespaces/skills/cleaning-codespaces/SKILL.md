---
name: cleaning-codespaces
description: >-
  Safely retire completed GitHub CodeSpaces with a PR, branch, dirty-work, and
  active-session report before confirmation; archive related effort state and
  reclaim only approved resources. Use when asked to "clean up codespaces",
  "retire a codespace", "delete an old codespace", or "prune codespaces".
---

# Cleaning CodeSpaces

This is a safety-orchestration wrapper. The `codespaces-lifecycle` skill owns
list/status, stop/finalize/delete, session recovery and logger storage, reclaim
markers, pruning, and failure semantics. The `borrowing-codespaces` skill owns
lease and release semantics. Use those skills rather than duplicating their
mechanics here.

Use the exact `argv` from the agent-codespaces session command catalog for
CodeSpace operations and the exact `argv` from the agent-containers catalog
when reconciling a paired container lease. Append the arguments shown below;
never substitute a same-named command found through `PATH`.

## 1. Identify candidates

Start from `<agent-codespaces catalog argv[0]> list --json`, lifecycle reclaim state, and any
resource explicitly named by the user. A completed label or a `recovered` /
`prunable` marker is a candidate hint, not permission to delete.

Associate each candidate with its repository, branch, pull request, effort slug,
and bridge session where available.

## 2. Build a provider-neutral safety report

For every candidate, report:

1. **Pull request:** query the repository's configured provider for the source
   branch. A merged or abandoned request is normally safe; an open or missing
   request requires caution.
2. **Branch:** verify commits are present on a durable remote and identify
   unpushed commits or unmerged branches.
3. **Dirty work:** inspect the actual work checkout when safe to do so. Dirty or
   unexported work blocks routine retirement.

   **`gh codespace list --json gitStatus` (or its `hasUncommittedChanges`/
   `hasUnpushedChanges`/`ref` fields from any raw `gh` call) reports git state
   for only the CodeSpace's own bound/creation repo -- never for a second repo
   checked out alongside it under `/workspaces/`.** A CodeSpace fleet that
   boots from a scaffold/devcontainer-definition repo (e.g. `*-codespaces`)
   and then clones the *actual* product repo into a sibling `/workspaces/<repo>`
   directory will report that field against the scaffold repo, which is
   usually untouched -- silently hiding real uncommitted or unpushed work in
   the product checkout (a false "safe"), or conversely flagging the scaffold
   repo's own incidental local state as dirty on a box whose real work is
   actually clean (a false "unsafe"). Observed directly: a raw `gh codespace
   list --json gitStatus` sweep of a scaffold-repo-launched fleet reported
   nearly every box dirty, yet most were clean once checked properly -- while
   at least one the raw field didn't flag as having unpushed work turned out
   to have a genuinely uncommitted file and an unpushed commit in its real
   sibling product-repo checkout. **Do not use that raw field, from
   any repo-scoped `gh` or `agent-codespaces list` call, to decide dirty vs.
   clean for a multi-repo workspace.** Use `<agent-codespaces catalog argv[0]>
   verify <name> --json` instead -- its cleanliness probe scans *every* git
   repo under `/workspaces/*` and aggregates `dirty`/`ahead`/
   `unpushed_branches` across all of them (see `cleanliness.py`'s
   `probe_cleanliness`/`probe_command`) -- or SSH in and check each
   `/workspaces/*/.git` checkout directly when a definitive per-repo diff is
   needed.
4. **Live session:** use bridge/lifecycle status. Never connect diagnostically
   in a way that can disrupt an active dispatch.
5. **Effort:** locate the matching effort in the **user's state repo**, not
   necessarily the launch or product repo, and report whether it is active or
   archived.
6. **Optional repository export hook:** if repository policy defines a
   permission or settings export hook, run it and report the result. This is an
   optional repo-specific step; do not invent a command or make it a portable
   prerequisite.

Classify each candidate as safe, caution, or blocked and explain every
non-safe classification.

## 3. Confirm

Present the complete report and ask for explicit confirmation of the exact
resources and disposition. Never delete a blocked candidate automatically.
If the user explicitly accepts a known risk, restate what may be lost before
continuing.

## 4. Retire and reconcile

Prefer lazy reclaim:

1. `<agent-codespaces catalog argv[0]> finalize <name>` to preserve and stop the resource.
2. Archive the associated effort in the user's state repo after its work and PR
   conditions are satisfied.
3. Mark it reclaimable:
   `<agent-codespaces catalog argv[0]> mark <name> prunable --reason "<verified reason>"`.
4. Preview or run `<agent-codespaces catalog argv[0]> prune` according to the user's request.

For confirmed eager deletion, use:

```bash
<agent-codespaces catalog argv[0]> finalize <name> --delete
```

Do not bypass a lifecycle refusal here. Diagnose through
`codespaces-lifecycle`; genuinely corrupted resources belong to
`recovering-codespaces`.

When the effort records `**Container:**`, also run
`<agent-containers catalog argv[0]> release <effort-slug>` and reconcile that binding through
`borrowing-containers`. CodeSpace lease release follows
`borrowing-codespaces` and the lifecycle command's documented behavior.

## Edge conditions

- No candidates: report that nothing qualifies; do not broaden the selection.
- Open PR or recoverable dirty work: preserve the resource and leave it
  unprunable.
- Missing provider metadata: report uncertainty and require confirmation.
- Already archived effort: do not archive twice; verify its resource references
  are settled.
