# Working Cross-Repo — Venue & Claim Detail

Detail for two occasional scenarios in the `working-cross-repo` skill: dispatching
to a venue whose task depends on a doc in a different repo, and tracking a PR
you open (or find already open) in another repo.

## Mind cross-repo plan/effort state on a venue

When you delegate to a CodeSpace/container agent but the task tracks against a
**plan, effort, or spec doc that lives in a *different* repo** than the one on
the venue, the on-venue agent **cannot see it** unless that repo is *also*
materialized there (`/workspaces/<repo>` by convention). Don't point the agent
at a path that isn't present: either ensure the doc's repo is on the venue and
name its `/workspaces/<repo>` path, or **relay the needed context inline in the
dispatch prompt and have the agent report results back** for you (the host) to
record. Your control-plane's own dispatch skill owns the concrete host↔venue
interop.

## A cross-repo PR you open is an obligation on your worktree — journal it

When you open a PR in *another* repo via `<agent-worktrees catalog argv[0]>
create-pr --repo <foreign> --from-branch <branch>` (a branch already pushed
there by some other process) or `agent-pull-requests create --repo <foreign>
--head <branch>`, it is auto-journaled onto the CALLING worktree's own
ledger — no manual step needed; `finalize` already knows that cross-repo
work is still open.

Only a PR opened some OTHER way (the AZ CLI / ADO REST / a bare `gh`, on a
CodeSpace or locally, bypassing both tools above) is **not** auto-journaled.
Record it as a claim yourself so the gate keeps you accountable, then settle
it when the PR merges:

```
aw='<agent-worktrees catalog argv[0]>'
"$aw" claims add pr <pr-url> --owner-ref "$("$aw" get owner-ref)"
# when it merges/closes:
"$aw" claims settle <pr-url>     # (sweep spares pr-kind — manual)
```

See the `worktree` skill's finalize-gate section for the full model.

**Investigating a PR someone else already opened in the target repo** (not
journaling your own)? Walk the claim in the other direction instead: see the
**`tracing-claimant-graphs`** skill to resolve the PR's originating worktree
back to its root owner and check whether that owner is still live before
commenting, reviewing, or opening a competing PR.

## Checking or merging an EXISTING foreign PR needs no claim -- just a claimant CWD

The claim-journaling above is for a PR *you open* in another repo (a new
obligation this worktree now owns). Merely **checking status on, or merging,
an already-open** PR in a foreign repo is lighter weight: `pr-watch`/
`pr-merge <owner/name> <pr>` read/act on a PR that already exists -- no new
claim to journal. What they do still require is a **claimant**: run them from
your own worktree (never an untracked directory or the target's own
checkout), so there is always a traceable owner behind the operation. See
`pr-workflow.md`'s *Addressing a foreign repo* section for the full contract
and its refusal behavior when CWD doesn't qualify.
