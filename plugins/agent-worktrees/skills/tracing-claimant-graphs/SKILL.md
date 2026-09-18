---
name: tracing-claimant-graphs
description: >
  Walk the cross-repo claimant graph backwards from a worktree, or forwards
  from a PR, to find the root owner that spawned it and whether that owner is
  still alive. `claims <id> --json` -> `owner_ref` names the worktree that
  created a given worktree; `claimant-liveness <owner_ref> --json` reports if
  that owner is still live, so you can chain hops to a root or a dead end.
  Use when asked to:
  - 'which worktree opened this PR'
  - 'who owns PR #<n>' / 'find the root owner of this PR'
  - 'trace this PR/worktree back to its root'
  - 'walk the claimant graph'
  - 'is this worktree's owner still alive' / 'is this PR orphaned'
  - 'find the worktree behind this PR'
  - triaging open upstream/dependency-repo PRs to decide if they're still
    actively driven before commenting, reviewing, or pinging
  The reverse/discovery direction of the `worktree` skill's own outbound-claim
  accounting -- read that skill if you're creating the resource, not tracing one.
---

# Tracing claimant graphs

Every worktree created through the blessed paths (`<agent-worktrees catalog
argv[0]> create`, or bridge dispatch) journals an **outbound claim on its
creator** at birth: the created worktree's own claim record carries an
`owner_ref` of the form `machine/project/worktree_id[#session]` naming the
worktree (in *any* coordinated project, not just the same repo) that spawned
it. This is exactly how a PR that surfaces in an upstream/dependency repo
traces back to the local session that opened it -- even when that PR was
opened from a **different repo's** worktree than the one you're standing in.

Use this whenever the question is "who is actually behind this", not "let me
create/finalize my own claim" (that's the `worktree` skill).

## The two hops

1. **Worktree -> owner.** From the worktree that did the work (in its own
   project, not necessarily this one):
   ```
   <agent-worktrees catalog argv[0]> claims <worktree-id> --json
   ```
   Read `.owner_ref` from the result. An empty/absent `owner_ref` means this
   worktree has no recorded creator -- it's either a root (started directly by
   a human/script) or was created out-of-band without journaling (see the
   `worktree` skill's *resources you create out-of-band* section).

2. **Owner -> liveness.** Take that `owner_ref` and check whether the session
   that created it is still around:
   ```
   <agent-worktrees catalog argv[0]> claimant-liveness "<owner_ref>" --json
   ```
   `{"alive": true}` means the root session is still live -- the PR is still
   being actively driven; the right move is usually to let it be, or engage
   the owning agent/operator directly rather than opening a competing PR (see
   the harness's own cross-repo PR-ownership policy). `{"alive": false}` means
   the owning worktree/session is gone; treat the PR as orphaned/stale from
   this machine's point of view -- confirm via the PR's own state on GitHub
   before assuming abandonment, since a dead local claimant does not by
   itself mean the PR was abandoned by its author elsewhere.

## Chain multiple hops

`owner_ref` can itself point at a worktree that has its own `owner_ref` (e.g.
worktree C in repo X was created from worktree B in repo Y, which was created
from worktree A in repo Z). Repeat step 1 against the owner project's own
worktree list until you reach a worktree with no further `owner_ref` -- that's
the root. Resolve each project's local path first with `<agent-worktrees
catalog argv[0]> repos find <project>` (never hardcode it); step 1's `claims`
command must be run against (or naming a worktree id inside) that project.

## Quick recipe

```
aw='<agent-worktrees catalog argv[0]>'
wt_id='<worktree-id-that-opened-the-pr>'
proj='<its-project-name>'

owner=$("$aw" claims "$wt_id" --json | jq -r '.owner_ref')
if [ -z "$owner" ] || [ "$owner" = "null" ]; then
  echo "no recorded owner -- $wt_id is a root (or was created out-of-band)"
else
  "$aw" claimant-liveness "$owner" --json
fi
```

## What this does NOT tell you

- **Liveness is a local claim-graph fact, not upstream PR state.** Always
  cross-check the PR's actual GitHub/ADO status (open/closed/merged, last
  activity) -- a dead local claimant plus a genuinely active PR (someone else
  picked it up, or the author is working from another machine untracked
  here) is possible.
- **A root worktree already `finalized` locally is not the same as "dead".**
  `finalized` means the worktree's content landed and its checkout may be
  pruned; check `<agent-worktrees catalog argv[0]> list --json` for that
  worktree's `status` in addition to `claimant-liveness`, since a finalized
  root that already merged its own PR is a normal, healthy outcome -- not an
  orphaned obligation.
- This graph only covers worktrees created through the tracked paths above;
  a PR opened fully out-of-band (raw `gh`/API, no `create-pr` and no claim
  journaled by hand) has no claimant to trace at all.

## See also

- **`worktree` skill** -- the *creating* side of this same graph: journaling
  outbound claims, the finalize obligation gate, and settling a claim when
  its resource closes.
- **`working-cross-repo` skill** -- opening and journaling a cross-repo PR in
  the first place (`claims add pr <url> --owner-ref ...`).
