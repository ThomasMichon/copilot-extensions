# Original issue 2467 — description and discussion

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)

<!-- ADMITTED_PACKET_BYTES_BEGIN -->
Source: https://github.com/ThomasMichon/copilot-extensions/issues/2467

Historical record copy of the admitted description and complete discussion. Qualification metadata is omitted; source control bytes other than tabs/newlines are displayed as literal \xNN escapes, not repaired commands. Historical OPEN, intake and receipt wording does not countermand a later explicit adoption or supply new authority. Original issue content and source/resource custody are unchanged.

## Original description

## Problem

A worktree that is still actively resumed by a live session (`resume_count`:
6, `session_count`: 2, the current session still working in it) had
`status: "finalized"` in its tracking record. This was apparently set by a
stale or premature completion signal, not any deliberate operator
`finalize`/`remove-system` action — the session never intended to retire the
worktree and continued working in it normally afterward without issue.

The finalized state was otherwise transparent: reads, writes, and continued
resumption inside the worktree all worked fine. But it silently blocked one
specific capability with no supported recovery: creating a *new child*
worktree with this one as the resolved owner failed with

```
owner-claim journaling failed (non-fatal): owner worktree <id> is finalized; creator ownership is frozen
{"version": 1, "error": "owner <machine>/<repo>/<worktree-id> cannot accept a new worktree obligation"}
```

`--owner <id>` (documented as a `--system`-only flag) did not help either;
the only workaround found was `create --no-owner`, which creates a
deliberately top-level, unowned worktree instead of a normal child of the
active one — losing the intended parent/child relationship.

## Why this is a bug

A worktree that is finalized-but-still-actively-resumed is an inconsistent
middle state: nothing is protected by refusing a *new* obligation from it while
the very same live session can keep acting on it in every other way. Either:

- the worktree is genuinely done, in which case it should also stop being
  resumable (or the tooling should refuse/warn on resume rather than silently
  succeeding), or
- the worktree is still live (as evidenced by continued resume), in which case
  its finalized flag is wrong and the owning session should have a supported
  way to reactivate it — lifting the frozen-ownership restriction without
  losing its existing durable record, lineage, claims, or history.

Right now there is no supported "unfinalize"/reactivate path for the second
case; the only escape is `--no-owner`, which works around the symptom rather
than fixing the state.

## Ask

1. Investigate how a worktree with a live, still-resuming session can end up
   `finalized` in the first place (stale signal? race between a background
   reaper and an active session? multi-session/resume-count interaction?).
2. Add an explicit reactivate/"unfinalize" path for a worktree whose owning
   session is still live, restoring its capacity to originate new claims,
   obligations, and child worktrees.
3. Alternatively/additionally, make continued resume of a truly-finalized
   worktree fail loudly (or require an explicit override) instead of silently
   succeeding while child-worktree creation silently fails — the current
   split behavior is the confusing part.

## Vision

Tracked by a new vision behavior,
[`finalization-is-reversible-under-live-resume`](https://github.com/ThomasMichon/copilot-extensions/blob/main/visions/plugins/agent-worktrees/README.md#finalization-is-reversible-under-live-resume),
added in the same change that files this issue.

## Reconciled acceptance from historical trackers

*The following contribution was assisted using Copilot.*

### Same rightful owner plus missing paired-sibling readiness from #2634

Preserve the full existing #2467 recovery/target-authority contract and comments. This is additional acceptance, not a claim that #2606/#2545 completed broader live-owner recovery or permission to reclaim another holder's resources.

- [ ] Distinguish current rightful-owner continuation from merely observing a live process/CWD, a later timestamp, stale hook replay or a responsive old session. Require the existing source/target acknowledgement and project/session identity; refuse foreign, competing, ambiguous or stale authority without arbitrary expiry/reassignment.
- [ ] Preserve the already-delivered conditional new-obligation reopen transaction, durable record/history/lineage and reciprocal child claims. Restore legitimate cross-repo create/re-pair capacity without --no-owner, an untracked clone or automatic re-acquisition of previously released resources.
- [ ] Resolve a paired sibling's structural relationship separately from its usable checkout readiness. A retained pair record may remain historically paired, but a missing directory must be explicitly unavailable/gone rather than silently advertised as ready through a recorded finalized path.
- [ ] Distinguish positively missing from unreadable, unavailable or cross-machine-unknown sibling evidence. Unknown is a hold; it neither authorizes pairing replacement nor proves that a holder has relinquished custody.
- [ ] Preserve qualified machine/repository/worktree identity, matching pair ID, complementary roles and reciprocal references while reporting degraded readiness. Do not erase a legitimate relationship, invent a new pair or bypass terminal-managed/finalizing/obligation guards solely to hide a stale path.
- [ ] Qualify the reported retained-finalized harness with a missing knowledge checkout, ordinary new-child creation, competing claim, wrong project/source, stale activation replay and partial pair metadata. Keep native/release/installed proof distinct; no live recovery or cleanup is authorized by this transfer.


Existing implementation owners, claims, holds and activation gates remain unchanged. This is tracker consolidation, not a completion or reassignment of the transferred work. Coordination: #7556.

<!-- backlog-reconciliation:7556:transfer:2634:to:2467 -->

*The following contribution was assisted using Copilot.*

## Cohort intake: rightful continuation versus inherited-CWD activation

I retain the entire current #2467 original, every #2634 row and complete
discussion constraints, and add #4095's complete original below. These are
complementary sides of terminal-worktree authority, not permission to reopen
a checkout or take another owner's resources.

### Plan

- [ ] Keep #2467's explicit rightful-source continuation/reactivation OR
  loud genuinely-retired resume refusal, exact source/target acknowledgement,
  qualified project/session identity, durable history/lineage and legitimate
  new-child/obligation capacity without --no-owner. Preserve the supported
  deliberate resume path and refusal of stale/foreign/competing authority.
- [ ] Preserve BOTH #4095 outcomes: inherited detached/background CWD must
  not implicitly reactivate or attribute liveness to a finalized checkout,
  and the actual background-agent spawn/workspace owner must provide the
  original repository-root CWD fallback. Resolve any owner-accepted alternate
  disposition explicitly rather than quietly substituting neutral daemon
  CWD, an attribution filter, or a terminal-writer guard for that second clause.
- [ ] Credit positive detached-marker filtering, terminal-managed activation
  refusal, #5445's named daemon CWD repair and #2606's increased-obligation
  reopen transaction as distinct partials. Missing/unreadable markers, CWD,
  a live PID, timestamps, stale hooks or ambiguous ancestry are not positive
  authority to reopen, kill, delete, re-pair or release claims.
- [ ] Preserve the unconfirmed wrong-parent-finalize hypothesis as an
  investigation, not an established cause. Keep all #2634 missing-sibling
  versus unreadable/remote-unknown readiness and reciprocal pair/claim
  identity requirements. The archived Done Finality effort is not reopened.

### Validation and ownership

- [ ] Cover known detached and unclassified background provenance, marker
  read failures, finalized inherited CWD, actual repo-root fallback and
  intentional authorized continuation, with current activation attribution
  and real spawn/workspace behavior distinguished.
- [ ] Preserve the original live-owner/new-child, wrong-project/source,
  competing claim, stale replay and missing/unknown pair controls. Retain
  source/unit/native/release/installed evidence at its own stated tier;
  no new universal platform or contributor-signoff condition is imposed.
- [ ] Read back this complete unchecked intake in OPEN #2467 before
  retiring #4095 as subsumed tracking. Existing rightful-source, pair,
  spawn/workspace, implementation and resource owners, assignments, claims
  and holds remain unchanged; released resources are not reacquired.

This is tracking only. No finalization, reactivation, background spawn,
process/CWD mutation, native probe, claim operation, cleanup or rollout runs.


### Preserved original scope from #4095

Detached background agent sessions inherit the parent process CWD. When the parent CWD is inside a finalized worktree, the detached session treats it as active, re-activating it unintentionally.

**Expected:** Finalized worktrees should be locked against re-activation. Processes inheriting a finalized worktree CWD should fall back to the repo root.\x0d


## Complete discussion

### Comment 5645845730

Corroborating report from another operator: hit the identical symptom today. A harness worktree with a live, actively-resumed session had status: "finalized" in its tracking record — not the result of any deliberate \x0cinalize/\x0demove-system call. Root cause suspected: a delegated background sub-agent, tasked with implementing a fix in its own freshly-created child worktree, appears to have instead run ^Ggent-worktrees finalize against the parent/orchestrator's shared cwd (rather than its own child worktree path), retiring the wrong worktree.

Confirmed the same split behavior described above: normal reads/writes/git ops in the worktree kept working fine, but create --json (both -p <same-project> and for an unrelated second project) failed with the identical owner-claim journaling failed ... owner worktree <id> is finalized; creator ownership is frozen / cannot accept a new worktree obligation error. create --no-owner was the workaround used to proceed.

+1 on both asks: (1) guard against a child-scoped \x0cinalize call accidentally targeting the parent/cwd worktree instead of the worktree it was actually invoked in, and (2) a supported reactivate/"unfinalize" path for a still-live session.

### Comment 6314477359

*The following contribution was assisted using Copilot.*

Retain the finalized/live-resume recovery owner. Current source contains conditional claim/follow-up reopening and terminal-managed activation guards, but those are not proof of the complete source-authorized live-owner recovery and wrong-parent-finalization case described here. Preserve a supported explicit continuation/reactivation decision by the rightful source owner, or an explicit refusal before genuinely retired work is resumed; liveness, a new timestamp, a stale hook replay or an unrelated restart acknowledgement must not silently authorize reopening. Preserve durable record, lineage, history and legitimate claims, while not automatically stealing back released resources. Investigate the original status transition and the reported child-scoped finalize/parent-CWD hypothesis as evidence, not an established root cause. Restore valid child/obligation capacity without --no-owner as a semantic workaround, and retain exact target/source acknowledgement, project authority and race/foreign/stale-source refusal. No finalization, reactivation or claim was attempted.

Evidence references:
- https://github.com/ThomasMichon/copilot-extensions/issues/2467 - original recovery/inconsistent-resume requirements and corroborating wrong-parent hypothesis, not a completed implementation.
- https://github.com/ThomasMichon/copilot-extensions/issues/2467#issuecomment-5645845730 - corroboration explicitly distinguishes suspected delegated finalize against parent cwd from confirmed split behavior.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/visions/plugins/agent-worktrees/README.md#L785-L798 - standing rightful-live-owner reversible-finalization intent.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/tracking_session_registration_write.py#L147-L164 - terminal managed records refuse new activation; not an explicit source-owner unfinalize transaction.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/tracking_claims.py#L476-L510 - conditional finalized-owner reopening exists within resource-claim handling, not proof of every live-resume/target-authority case.
- https://github.com/ThomasMichon/copilot-extensions/blob/9d612c2d05f1401dfd94e9eb40165bdb8d80e9f4/plugins/agent-worktrees/src/agent_worktrees/tracking_disposition_write.py#L77-L94 - terminal/effort guards and conditional follow-up reactivation are partial existing mechanisms.
- No exact completed source-owner acknowledgement plus original child-creation recovery acceptance was established; no new command shape or native proof is asserted.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/0b30012da7c80d102384a50fdd4941bb8577b47c). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-16:issue-2467 -->

### Comment 6356665399

*The following contribution was assisted using Copilot.*

Retain this existing rightful-live-owner recovery canonical, including every inherited2634 row. Actual2606/current claims code delivers conditional reopen on genuinely increased live obligations and history preservation; managed terminal activation still refuses. That finite partial does not prove the original inconsistent resume/finalize transition, suspected child-finalize/parent-CWD error, explicit rightful-source recovery OR loud retired-resume refusal, or missing paired-sibling readiness.

Preserve exact source/target acknowledgement and qualified project/session authority where already accepted, without turning it into a new contributor signoff gate. Liveness, timestamps, stale hooks and remote uncertainty cannot reopen or reassign another owner's resources. Distinguish pair relationship from usable checkout, positively missing from unreadable/remote-unknown, and retain lineage/history/claims without silently reacquiring released resources. The historical Finality effort is archived Done, not an eligible Active receiver or proof of these later gaps; narrower closed2545 is not a live token. Keep this stronger current canonical rather than transfer it to a UI or diagnosis-only umbrella.

Evidence references:
- Fresh complete paginated collaborator-roster verification admitted the original author before body access. Eligible comments were filtered before rendering; metadata and membership are rechecked before publication. Nonmember-origin issues remain untouched and count in the whole-queue census.
- https://github.com/ThomasMichon/copilot-extensions/issues/2467 : complete original, both eligible original versions, both comments and all timeline events; includes the full direct2634 six-row checklist. Final OPEN/unlocked/unassigned.
- https://github.com/ThomasMichon/copilot-extensions/issues/2467#issuecomment-5645845730 : wrong-parent finalize is a corroborated hypothesis, not an established root cause.
- https://github.com/ThomasMichon/copilot-extensions/pull/2606 : FULL actual mergedfd292aa60d50a476c9f3e1df9baf8e70377fc9c9; centralized increased-obligation reopen/history/freeze-rollback partial, with explicitly remaining triggers/recovery at that time.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/plugins/agent-worktrees/src/agent_worktrees/tracking_claims.py#L482-L550 : actual reopen transaction, new/reactivated-live versus no-op claim distinction and reserved/frozen guards.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/plugins/agent-worktrees/src/agent_worktrees/tracking_session_registration_write.py#L147-L168 : current managed terminal registration refusal, not blanket rightful-source recovery.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/visions/plugins/agent-worktrees/README.md#L785-L798 : rightful live-owner reversible finality with preserved durable state OR genuinely retired active resumption.
- https://github.com/ThomasMichon/copilot-extensions/blob/69bdc07d996d2720eaf89333cde1d97e014cd139/efforts/2026/08/28%20worktree-finality-and-obligations/README.md : historical Done effort, not a current Active receiver. Its completed broad contract is not proof of later2634/2467 authority/readiness cases.
- Closed2545's full current member disposition credits only the narrow new-obligation journaling repair and explicitly preserves2467's broader recovery/target-authority scope.

[Inspected released source](https://github.com/ThomasMichon/copilot-extensions/tree/9489a0fe8e4284538c3e00d5aaad2ac0cf474f75). Reconciliation: #7556.

<!-- backlog-reconciliation:7556:batch-73:issue-2467 -->

<!-- ADMITTED_PACKET_BYTES_END -->

[Back to cohort overview](knowledge-pair-receiving-cohort.md) · [Campaign README](README.md)
