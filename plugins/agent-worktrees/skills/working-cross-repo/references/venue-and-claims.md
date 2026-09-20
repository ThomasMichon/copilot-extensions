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

When you open a PR in *another* repo (e.g. an **example-web ADO PR** created
with the AZ CLI / ADO REST / `gh`, on a CodeSpace or locally) it is **not**
auto-journaled — only `<agent-worktrees catalog argv[0]> create-pr` in *this*
repo is. So your worktree's `finalize` won't know that cross-repo work is
still open. Record it as a claim so the gate keeps you accountable, then settle
it when the PR merges:

```
aw='<agent-worktrees catalog argv[0]>'
"$aw" claims add pr <pr-url> --owner-ref "$("$aw" get owner-ref)"
# when it merges/closes:
"$aw" claims settle <pr-url>     # (sweep spares pr-kind — manual)
```

See the `worktree` skill's finalize-gate section for the full model
(example-operator/dotfiles#1351 tracks auto-journaling these).

**Investigating a PR someone else already opened in the target repo** (not
journaling your own)? Walk the claim in the other direction instead: see the
**`tracing-claimant-graphs`** skill to resolve the PR's originating worktree
back to its root owner and check whether that owner is still live before
commenting, reviewing, or opening a competing PR.
