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
every registered project, which ones are behind a **currently open** PR in
some other repo" -- has a dedicated command:

```
<agent-worktrees catalog argv[0]> claims find pr --repo <owner/repo> \
  [--state open|closed|merged|all] [--live] [--json]
```

- Scans **every project registered on this machine** (not just the current
  one) for worktrees whose PR history (`prs`) names `--repo`, filtered by
  the locally tracked `--state` (default `open`).
- **Without `--live`** this is fast but a *candidate* list only: a
  worktree's locally tracked PR state can read `"open"` long after the PR
  actually merged or closed elsewhere -- expect heavy staleness (a real
  fleet sweep once returned 221 "open" candidates for 1 genuinely open PR).
- **With `--live`** each candidate is cross-checked against the PR host's
  actual current state and non-matches are dropped -- at the cost of one
  network round-trip per candidate (can take several minutes when the
  local candidate set is large; there is no caching or batching yet). A
  candidate whose live check is unreachable/unconfigured/unnumbered is
  **kept as unverified** (`live_state: null` in JSON) rather than dropped
  -- a network hiccup must never hide a real claim -- so a `--live` result
  is not uniformly authoritative; check each match's `live_state` before
  treating it as confirmed.
- Emits `owner_ref` and `codename` per match, so a hit feeds straight into
  the two-hop recipe above without a second lookup.

**What it does NOT cover:** a genuinely separate cell (e.g. a dual-boot
machine's native-Windows install next to its WSL install) keeps its own,
entirely separate tracking store that this command cannot see across --
same limitation as `list --include-other-platforms` itself. Repeat the
command over SSH into each cell's own alias (see the `facility-ssh` /
equivalent machine-alias doc for this facility) and merge results yourself.

### Independently confirming a match

`claims find`'s output already carries `owner_ref` per match, but for a
match that matters enough to act on, confirm it two more ways before
treating it as settled:

1. **Trace the two-hop recipe above** (`claims <id>` -> `owner_ref` ->
   `claimant-liveness`) from the *worktree that opened the PR* (the match's
   `worktree_id`), not the PR's target repo -- confirms the claiming
   worktree's own root is still alive.
2. **Cross-check via the PR's own attribution marker**, when one is present
   (`pr.source_attribution` in `"codename"` mode -- see the `worktree`
   skill's `references/pr-attribution.md`): read the PR body's `<!--
   agent-worktrees:source codename=<name> -->` marker (it may also carry
   an optional `root=<name>` field -- another worktree's own codename,
   present when this one is itself claimed as an outbound resource by a
   different calling worktree) and run `resolve --codename <name>
   --dry-run`. A codename match that agrees with the
   match's own `codename` field is a **third, independent** confirmation
   (local claim record, live PR state, and the PR's own self-declared
   source all agreeing) -- valuable because it doesn't depend on the local
   tracking store staying intact; the marker survives even if a claim
   record were ever pruned or corrupted. A codename that fails to resolve
   anywhere reachable from this machine (not local, no cross-machine SSH
   match) means the PR was opened from a worktree this sweep cannot see at
   all -- a different, unreachable machine, or a since-fully-pruned
   worktree -- not a sweep bug.

Implemented for
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
- **A worktree can be fully pruned from the local claim store while its
  originating session's own state folder still exists.** Confirmed live:
  `claims <worktree-id>` can return `"error": "worktree not found"` for a
  worktree that opened a real, merged PR only ten days earlier -- pruning
  runs on its own schedule, independent of the PR's lifecycle. When that
  happens, `claims find pr` also won't surface the PR at all (there's no
  claim record left to scan), and there may be no codename marker in the
  PR body either (see the marker gap below) -- both of this skill's normal
  paths come up empty even though the PR is genuine and well-attributed.

### Fallback when the claim graph has nothing: raw session-state search

When `claims find pr` returns no match and the PR carries no codename
marker, the claiming session's own raw state may still be recoverable
directly, bypassing the claim-graph tracking store entirely:

1. Grep every local session's `events.jsonl` for a **repo-qualified**
   reference to the target PR (its full URL, or `<owner>/<repo>#<n>`) --
   never a bare `PR #<n>` / `PR <n> opened` pattern. A bare number is not
   enough to establish who *opened* the PR: it also matches a later session
   that merely reviewed or discussed the same PR, or an entirely different
   repository's PR that happens to share the number. Exclude your own
   current session directory first regardless -- a search run *from* a
   session that itself discusses the target PR will match its own later
   narration, not a genuine other-session hit. This can span **all**
   per-user session-state roots on the machine -- there can be more than
   one independently-rooted store (for example a native-Windows Copilot
   install and a WSL install each keep their own
   `~/.copilot/session-state/`, matching the "separate cell" caveat above).
2. For each matching session directory, read its `worktree-binding.json`
   directly -- it carries `worktreeId`, `worktreeDir`, and `machine` as
   plain structured fields, which is more reliable than trying to reverse
   the worktree codename out of the PR's branch name (**branch names don't
   reliably carry it** -- confirmed live: a PR opened the same week the
   by-default codename effort landed had neither a branch-name suffix nor
   a body marker).
3. Even a repo-qualified match is **unverified** until you read the
   matching event's own text and confirm it actually records *creating/
   opening* the PR (e.g. "Opened PR #n" / a `create-pr`-style tool result),
   not just a mention in passing. Treat any hit found this way as
   attribution evidence of last resort, not a replacement for the claim
   graph or the codename marker when either is available -- it has no
   liveness signal and no cross-check of its own.

## See also

- **`worktree` skill** -- the *creating* side of this same graph: journaling
  outbound claims, the finalize obligation gate, and settling a claim when
  its resource closes. Its `references/pr-attribution.md` covers the PR
  attribution marker/codename mechanism the fleet-sweep's step 5 relies on.
- **`working-cross-repo` skill** -- opening and journaling a cross-repo PR in
  the first place (`claims add pr <url> --owner-ref ...`).
