# Worktree — Finalize's Outbound Resource Obligations

Full itemized procedure for resolving each obligation kind that blocks
`finalize`'s obligation gate (`AGENT_WORKTREES_OBLIGATION_GATE=block` by
default). The entrypoint's rule stands: resolve, never bypass, never
force-release a claim.

- **A cross-repo worktree you created** -- finalize *it* first; its finalize
  flips this worktree's claim to `at-rest` automatically (no manual step).
- **A borrowed CodeSpace/container** -- merge or move its work off-box, then
  disconnect (the disconnect hook stamps it `at-rest` **and mirrors that onto the
  shared lease**, so the settle is visible cross-machine), or run
  `<agent-codespaces catalog argv[0]> finalize <name>`.
- **A bridge session** -- drive its worktree to final.
- **A crashed/gone holder that never settled** --
  `<agent-worktrees catalog argv[0]> claims sweep`
  (dry-run) then `--apply` explicitly reclaims provably-gone-and-safe
  obligations. Finalize never auto-reclaims creator ownership. A stale *codespace*
  obligation -- including one owned on a different machine -- is reclaimed by
  reading the disposition mirror off the shared lease, so a clean disconnect
  anywhere unblocks it.
- **Genuinely cannot close the children yourself** -- ownership still stays with
  the creating agent. Do **not** choose a handoff unilaterally: ask the operator.
  Only after the operator explicitly names another recipient/flow may you run
  `<agent-worktrees catalog argv[0]> finalize --abandon --handoff-to <recipient-or-flow>`.
  `--abandon` without `--handoff-to` is refused. The command re-homes the
  obligations to a durable orphanage with that recipient recorded; it never
  drops them. **Creating-agent cleanup is the default; affirmative handoff is the
  only exception.** The creating agent remains responsible until the named flow
  accepts the transfer. Immediately:
  1. save the finalizing worktree id printed in the orphan entries;
  2. run `<agent-worktrees catalog argv[0]> claims cleanup <source-worktree-id>` as a dry-run;
  3. investigate each selected resource (child git/PR state, CodeSpace work,
     active sessions), then finalize/settle it through its owning lifecycle;
  4. use `<agent-worktrees catalog argv[0]> claims cleanup <source-worktree-id> --apply` only for
     the selected resources you intend to reclaim, and rerun the selective
     dry-run until it reports no matches.

  Never run unfiltered `claims cleanup --apply` merely to clear your blocker:
  with no selector it acts on the **entire orphanage**, including unrelated
  agents' resources. Do not report the parent fully closed while its selected
  orphan entries remain; if the named recipient cannot accept them, return to
  the operator rather than inventing a different flow.

Inspect the ledger any time with
`<agent-worktrees catalog argv[0]> claims show`. Creator
ownership is invariant: `AGENT_WORKTREES_OBLIGATION_GATE=warn|off` does not
permit releasing unsettled resources without the affirmative handoff above.

> **Investigating someone else's already-open PR or worktree, not your own
> obligations?** The `owner_ref` recorded above is walkable in reverse: given
> a worktree id (in any coordinated project), `claims <id> --json` reports
> the worktree that created it, and `claimant-liveness <owner_ref> --json`
> reports whether that creator is still live. See the **`tracing-claimant-graphs`**
> skill for the full walk (including chaining multiple hops back to a root).

## Resources you create out-of-band aren't auto-journaled -- claim them by hand

Auto-journaling only covers resources created through the blessed paths: a
worktree via `<agent-worktrees catalog argv[0]> create`/bridge dispatch, and a
CodeSpace via `<agent-codespaces catalog argv[0]> ssh`. Anything you bring into being **another way** is invisible
to the finalize gate unless you journal it yourself — so `finalize` would let this
worktree vanish while that work is still open. Journal it as a claim on **this**
worktree, and settle it when it's done:

```
# You opened a cross-repo / ADO PR out-of-band (e.g. an example-web PR created with
# the AZ CLI / ADO REST / gh, NOT the payload-local create-pr operation):
aw='<agent-worktrees catalog argv[0]>'
"$aw" claims add pr <pr-url-or-id> --owner-ref "$("$aw" get owner-ref)"
# ...later, when that PR merges or closes:
"$aw" claims settle <pr-url-or-id>     # or: claims release <pr-url-or-id> --remove
```

The gate is **kind-agnostic** — a `pr` (or `codespace`/`container`/`workdir`)
claim blocks finalize exactly like a worktree claim, so this keeps you honest
about unfinished cross-repo work. But the reclaim **sweep spares `pr`-kind
claims** (it can't prove an arbitrary PR safe), so a `pr` claim is **manual to
settle** — there is no auto-reclaim. Kinds: `worktree|codespace|container|ssh|
workdir|pr`.
