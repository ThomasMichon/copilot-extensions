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
  - 'sweep the fleet for open PR claims'
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

## Fleet-wide sweep: which worktrees hold a claim on an open PR

The two-hop recipe above assumes you already know *which* worktree opened a
PR. The other direction -- "across every worktree this machine knows about, in
every registered project, on every platform, which ones are behind a
**currently open** PR in some other repo" -- has no single command yet (see
*A missing accelerator* below). Until one exists, this is the manual sweep:

1. **Enumerate every worktree in the PR's own project, on every platform this
   machine runs.** `list --json --include-other-platforms --tracking-status
   all --all` merges platforms that share one tracking store, but **a
   genuinely separate cell (e.g. a dual-boot machine's native-Windows install
   next to its WSL install) keeps its own, entirely separate tracking store**
   -- `--include-other-platforms` cannot see across that boundary. Repeat the
   `list` call over SSH into each cell's own alias (see the `facility-ssh` /
   equivalent machine-alias doc for this facility) with `--project <name>`,
   and merge the JSON yourself.
2. **Extract every locally-tracked "open" PR claim.** Each worktree record's
   `pr` (latest) and `prs` (full history) fields carry a `state` and a `url`;
   filter for `state == "open"`, and parse the PR number out of the `url`
   (some pre-existing records carry a stale/absent `number` even when parsing
   PR would be trivial by URL). Expect **heavy staleness**: a worktree's own
   `state` can still read `"open"` long after the PR actually merged or
   closed elsewhere -- this local field is a *candidate* list, never a
   verdict.
3. **Intersect against the live, authoritative state.** Pull the PR host's own
   current open list once (`gh pr list --repo <owner/repo> --state open
   --json number --limit 500` for GitHub) and intersect by PR number. Only
   numbers appearing in *both* sets are worth tracing further -- this
   typically collapses hundreds of stale local candidates down to a handful
   of real ones.
4. **Trace each surviving match with the two-hop recipe above** (`claims` ->
   `owner_ref` -> `claimant-liveness`), from the *worktree that opened the
   PR* (found in step 1/2), not the PR's target repo.
5. **Independently confirm via the PR's own attribution marker**, when one is
   present (`pr.source_attribution` in `"codename"` mode -- see the
   `worktree` skill's `references/pr-attribution.md`): read the PR body's
   `<!-- agent-worktrees:source codename=<name> -->` marker and run `resolve
   --codename <name> --dry-run`. A codename match that agrees with the
   claim-graph trace from step 4 is a **third, independent** confirmation
   (local claim record, live PR state, and the PR's own self-declared source
   all agreeing) -- valuable because it doesn't depend on the local tracking
   store staying intact; the marker survives even if a claim record were ever
   pruned or corrupted. A codename that fails to resolve anywhere reachable
   from this machine (not local, no cross-machine SSH match) means the PR
   was opened from a worktree this sweep cannot see at all -- a different,
   unreachable machine, or a since-fully-pruned worktree -- not a sweep bug.

### A missing accelerator

There is no `claims find` (or similar fleet-wide query) command today. Every
step above is a hand-rolled loop over `list --json` output piped through
`jq`/a script, repeated per platform/cell, plus a separate live-PR-state
fetch and manual intersection. This is real, repeated toil -- an
`agent-worktrees claims find --resource-kind pr --repo <owner/repo> --state
open [--include-other-platforms] [--all-cells]` (or equivalent) that did
steps 1-3 itself, across every registered project's tracking store, would
turn a several-tool-call investigation into one call. No such command
exists as of this writing; if you build one, update this section to point at
it instead of the manual recipe. Tracked as
[ThomasMichon/copilot-extensions#4086](https://github.com/ThomasMichon/copilot-extensions/issues/4086).

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
  its resource closes. Its `references/pr-attribution.md` covers the PR
  attribution marker/codename mechanism the fleet-sweep's step 5 relies on.
- **`working-cross-repo` skill** -- opening and journaling a cross-repo PR in
  the first place (`claims add pr <url> --owner-ref ...`).
