# Dormant charter correction

Back to [Monitor agent fleet](../SKILL.md).

An inactive workspace or session cannot be steered. Its next resumer, successor
or reviewer acts on what its **persisted charter** says. When that record now
points in the wrong direction, a message reaches nobody and silence lets the
wrong plan resume. This mode corrects those records, and only those records,
under a separate explicit grant. Without one, report each proposed correction
as a recommendation with **records changed: none**.

## Grant and targets

Record a dormant-correction scope in [the charter](../assets/CHARTER.md):
correctable record kinds, apply mode, observation period, whether finalized
workspaces are included, and excluded effects. A read-only or steering grant
does not authorize record edits; a correction grant does not authorize
messages.

Write each record only through its existing owner:

| Record | Owner path | Correction shape |
|--------|------------|------------------|
| Canonical effort plan | The effort repository's publication flow, from the monitor's own writable workspace | Append a dated course correction and update the next slice; never rewrite history or the original request |
| Workspace title, summary or follow-up | The workspace owner's status command | Replace an instruction-shaped, stale or misleading theme with the evidenced root |
| Adopted agenda cursor | The agenda owner's semantic update with an expected revision | Restore the root, saved return and next action; preserve history and continuations |
| Pending handoff baton | Only the handoff owner's verified record-only supersede for that target | A superseding corrected baton that carries the original request; never edit or consume the old one |

A baton is correctable only through a verified, non-activating, record-only
supersede that targets the dormant owner's handoff and makes every existing
locator resolve to the replacement. An ordinary handoff save is not that
operation: it is scoped to the caller's own workspace, and it can add claims,
abandon older pending handoffs or leave existing tokens resolving to the stale
baton. Without the verified operation, report the baton correction as advisory
with **records changed: none**. Where the grant allows, carry the correction in
the effort or workspace record that the resumer also reads.

Never write the target's checkout, branch, checkpoint or claims. Never
finalize, abandon, reset, resume, start or message the target, and never change
approvals, holds, review state or deployment. Mark obsolete work **recommend
retirement** for its owner's decision.

## Select candidates honestly

1. Freeze the period and roster through the workspace/session readers
   ([fabric readers](fabric-readers.md)). Activity metadata, titles and
   timestamps nominate candidates; they never establish a verdict.
2. Classify liveness as running, live-idle, held, terminal or unknown. Only
   terminal or confirmed inactive owners are candidates. A live-idle owner is a
   steering target, not a correction target. Unknown liveness blocks mutation
   for that target only; continue independent targets.
3. A finalized workspace is not a Done objective. When the grant includes
   finalized workspaces, include one whose canonical effort or pending baton is
   still open. Correct the effort or baton; never revive the workspace.
4. A workspace with no direct session is not ownerless. If a live root or
   coordinator owns it, it is that owner's subordinate: exclude it.
5. Separate one-turn probe, acknowledgement and connectivity sessions launched
   inside a real workspace. They do not define its root. A probe-derived title
   or head binding is a record finding; an operator-set marker stays.

## Reconstruct, then judge direction

Follow [history analysis](history-analysis.md) over each candidate's complete
chronology: original operator request, genuine scope changes, decisions,
latest substantive results and relevant predecessors. Compare the persisted
records with that reconstruction, not with the monitor's own preference.

A record is **wrong-direction** only when cited evidence shows its next action
would work against the root:

- It drops, narrows or contradicts the verbatim original request or a later
  operator decision (see *Handoff rewrites intent* in
  [misdirection patterns](misdirection-patterns.md)).
- It waits on a blocker that later recorded evidence already resolved.
- It resumes work whose custody the operator later transferred to another
  owner (compare *Superseded agent untold*).
- It schedules work that already landed, or a detour whose parent return was
  lost.
- It claims Done while a recorded acceptance criterion remains.
- Its title or summary is instruction-shaped or describes a different task.

These are **not** wrong-direction: an accurate open plan, a required external
wait, an accurate effort filed in the wrong folder, a cosmetic title, or an
older entry that a newer entry already superseded. Report filing and other
bookkeeping separately from direction corrections.

## Clear versus ambiguous

Apply a correction directly only when the grant's apply mode allows it and
every condition holds:

- one defensible correction, directly evidenced by the operator source and
  later recorded results;
- the root, original approvals, holds, limits and custody are preserved;
- the edit is additive or superseding, reversible and within the granted
  record kinds.

Everything else is ambiguous: competing roots, conflicting operator
instructions, an objective or scope change, a retirement decision, unknown
earlier effects or partial evidence. Batch ambiguous cases into one decision
packet with evidence, options, a recommended decision and its default. Do not
ask per target, and do not hold clear corrections behind the packet.

## Write safely

Immediately before each write:

1. Recheck liveness through the native owner. A newly active target is
   excluded and reported, never interrupted or messaged.
2. Re-read the record's current revision. If it changed since analysis,
   reassess instead of overwriting.
3. Persist the planned correction (target, record, before excerpt, evidence,
   new text and source) in the audit record.

Write through the owner, then read back through the same owner. Record the
before and after, owner revision or commit, publication state and observation
time. A write without readback is unknown, not done; reconcile it before any
retry. A correction published through a reviewed repository is not applied
until that repository's gate lands it.

## Reactivating an inactive head

Dormant correction itself never reactivates a target. Reactivation is a
separate effect: it needs a [repair](repair-escalation.md) grant whose rung
ceiling reaches R3 and whose recipients name the target, recorded as head
reactivation in [the charter](../assets/CHARTER.md). Without that grant,
report the inactive head and the recommended reactivation. Under relentless
[drive posture](watchdog-checks.md#drive-posture), an inactive head whose root
is still open is a reactivation candidate when granted, and a finding
otherwise.

1. **Resume the same session.** Use the workspace owner's supported resume or
   embody path for the recorded head session. Never start a new session,
   reseed it or replace the head. This is R3. A seeded successor in the same
   worktree is R4: it needs a separate active R4 grant class and follows the
   [recovery rules](repair-escalation.md#recovery-rules), never this
   procedure.
2. **Launch from an interactive logon on Windows.** Start the resume from the
   host's interactive logon session, for example by steering an interactive
   session already running on that host; that steer needs its own steering
   scope for that recipient. A launch from a non-interactive remote logon,
   such as an SSH session, can create the terminal multiplexer pane that then
   dies, because named kernel objects in the per-session `Local\` namespace
   and the launching logon's job scope do not outlive that logon.
3. **Verify the pane stays live.** Read the head back through the owner after
   at least one reobservation interval. A pane that appeared and exited is a
   failed resume, not a recovery; reconcile it before retrying.
4. **Relay the goal once.** After a verified resume, send one message that
   restates the recorded root goal and next action, only under a current
   steering scope that covers the resumed head and that relay purpose. A
   repair grant, or a steering scope for the interactive helper, does not
   cover it. Without such a scope, report the proposed relay without sending
   it. Do not repeat it.

## Coverage and report

Unreachable machines, missing readers, unread histories and activity outside
the period are coverage gaps. Never infer a correction from metadata to fill a
gap. Report per candidate: liveness evidence, root, verdict (none, corrected,
ambiguous, advisory, bookkeeping, excluded or gap), receipts and **messages
sent: none**. Record recurring causes as patterns, not per-target blame.
