# Already-claimed pickup recovery

Used by the [context-handoff skill](../SKILL.md) when another session already
holds the requested baton. Preserve exclusive pickup; the refused receiver
does not acquire that objective.

## 1. Deduce the continuation session

State the reported claimant id. It is an evidence-backed starting point, not
necessarily the current head: the claimant may have handed off again.

When agent-worktrees is available, use its exact session command catalog argv
for `session-lineage --session-id <claimant>` and
`head-session --worktree <worktree-id> --json`. Follow the validated bound
relation's lineage to its terminal continuation session; compare it with the
worktree's recorded head and exact pending handoff token/candidate. Inspect
that session's `session-recovery` / `session-binding` evidence when needed to
establish its worktree and resumability.

A controller relation, title, newest timestamp, or unverified spawn candidate
is not proof of the rightful head. Missing, conflicting, or incomplete evidence
stays a visible blocker. With no optional worktree authority available, report
the claimant as an unverified continuation candidate, not a verified head.
Never invent a session or binding target.

## 2. Offer binding repair, not bug filing

Point at the deduced true head and its evidence, separately naming any stale
recorded head. If the head already matches, no repair is needed. Otherwise
offer to bind the verified continuation session as the new head, asking for
explicit operator consent before changing the binding. Never bind the refused
session instead.

Use the supported `bind-session --worktree-id <worktree-id>
--session-id <continuation-session> --handoff-token <exact-pending-token>` only
when the token and candidate identify that session and the operation permits
the transition. Run it in the continuation session's own context, or supply
only that session's verified hosting identity through the owner's supported
recovery surface. The refused session's ambient pane, PID, launch, and
assignment bindings must not be transferred to the continuation session.

Re-read `head-session` and require it to name the requested session:
`bound: true` alone does not prove promotion. If there is no safe supported
transition, report that limitation instead of forcing an override or editing
tracking files. Bug filing is not the default recovery offer.

## 3. Recommend the correct pickup

Recommend resuming the verified continuation session, then using
`/consume-handoff` there for its pending baton. A session is resumed, not
itself consumed. Do not retry the old claimed baton in the refused session,
spawn a replacement, or take over its objective. An interrupted delivery may
be retried by its original claimant; a completed delivery is not replayed.
If the verified head has a newer handoff, use that continuation, not the old
locator. Do not recommend consumption when no pending delivery remains.
