# Large-Corpus Session Sync

- **Slug:** `large-corpus-session-sync`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed increments against Dev
- **Created:** 2026-10-24
- **Status:** Draft
- **Vision:** [Session intelligence](../../../visions/plugins/agent-logger/session-intelligence/README.md): preserve-accounting-evidence, reproducible-rebuildable-products, bounded-safe-collection
- **Umbrella issue:** #8735
- **Sub-issues:** #8455, #8682

## Guiding Intent

Keep usage accounting enabled while making large histories and scheduled sync
work reliably. Bounded processing must converge without silently omitting usage,
calling partial evidence complete, or disabling safe publication and compaction.
The installed scheduled consumer, not only source tests, is the completion gate.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Sync repair owner | Plan, implementation, review, release observation and installed validation | Managed source worktree `shiny-pedal` |

## Coordination

- **Topology:** independent per-slice PRs, one owner for coupled source edits.
- **Host (owns PRs):** Sync repair owner.
- **Delegates:** none initially; bounded disjoint validation may be assigned.
- **Handoff:** retain the canonical README, open gates and exact next action;
  source publication, release and installed proof remain separate milestones.
- Existing release and suite-update owners retain their work. This effort does
  not supersede their PRs, change their authority or absorb unrelated defects.

## Context

#8460 delivered a GUI-root scheduled launcher with native child-window suppression,
real exit-code propagation and diagnostics, plus internal metadata exclusion.
#8688 delivered bounded SQLite batches without increasing the reader row cap,
including discarded-buffer release before retries. Both are merged and released.

An actual native scheduled run observed no newly visible console windows or
foreground transitions. It nevertheless reported accounting failures for histories
exceeding the transcript reader's 64 MiB/line/event bounds and was terminated at the
task's existing thirty-minute limit. That does not prove transfer itself is slow:
separately attribute accounting, signing/diff, transfer, retention and shutdown
before choosing the next source change. Missing transcript evidence remains
explicitly missing rather than becoming zero measured usage.

## Request

The original operator request:

> Investigate why I see a headed CMD window with this content:
> session-sync: accounting incomplete: session enumeration: invalid session directory identity
>
> Two issues:
> 1. I shouldn't see a headed window for this
> 2. It clearly crashed
> Help fix both problems

After source delivery and installed validation exposed the additional limits,
the operator selected:

> Continue engineering large-transcript accounting and long-running sync support

The operator confirmed the effort name `large-corpus-session-sync`.
The alternatives of disabling optional accounting or stopping at the original
fixes were not selected. Normal local rollout was previously authorized; this
does not authorize unrelated host changes, destructive cleanup or gate bypass.

## Plan

### Phase 1 - Establish the actual consumer budgets

- [ ] Land this proposal through normal automated review before implementation.
- [ ] Attribute the real timeout to bounded source phases using content-free
  timings/counts and the actual scheduled-task result. _(agent-recommended)_
- [ ] Inventory the relevant reader, ledger, change-tracker, filesystem-transfer,
  task-lifetime and cancellation contracts; identify exact compatibility and
  publication consumers before changing them. _(agent-recommended)_

### Phase 2 - Make accounting converge over large histories

- [ ] Implement bounded incremental or resumable accounting collection so total
  historical size alone does not permanently reject an otherwise valid session.
  Choose the smallest source-grounded design after Phase 1. _(agent-recommended)_
- [ ] Preserve source identity and revision checks, append/rewrite detection,
  per-record/line safety, exact multiplicity and idempotence. Partial tails,
  malformed usage, conflicting source records and genuine over-budget individual
  records remain visible; a cursor is never permission to skip unverified bytes.
- [ ] Keep complete, deferred and failed coverage distinguishable. Only actually
  preserved accounting may permit cold compaction; interruption cannot discard
  the last good ledger, publish fabricated completeness or lose pending progress.
- [ ] Avoid re-reading unchanged historical prefixes on every periodic pass.
  Keep cached state rebuildable and bounded, and keep conversation content out
  of accounting records. _(agent-recommended)_

### Phase 3 - Bound and resume the real scheduled operation

- [ ] Fix the phase responsible for the actual timeout, preserving the existing
  single-writer lock, repository scope and target publication contract.
- [ ] Make finite progress and continuation durable when all work cannot finish
  in one task lifetime. Do not substitute a silently enlarged deadline, another
  scheduler, unowned process or disabled accounting for a working operation.
- [ ] Preserve windowlessness, real exit outcomes, diagnostic persistence and
  safe process-tree cleanup on completion, timeout and cancellation.
- [ ] Land source changes through approved PRs, observe a containing release and
  use the authorized updater; preserve explicit failure if deployment is blocked.

### Phase 4 - Prove the installed outcome

- [ ] Verify complete accounting for large finite histories, append/catch-up,
  unchanged repeats and interrupted continuation without double counting.
- [ ] Run the actual installed scheduled consumer over the real corpus through
  two observed cycles, with explicit accounting/transfer outcomes, no unwanted
  console/foreground transitions and no leaked descendants.
- [ ] Resolve or transfer every remaining Plan/Validation item to a named tracked
  objective, record the outcome, archive this effort and finalize owned worktrees.
  A successful source PR alone is not completion.

## Validation Plan

The following verification design is agent-recommended:

- [ ] Regression first: synthetic histories above the existing total-byte/event
  threshold reproduce the permanent failure before the source correction.
- [ ] Bounded-work tests measure consumed bytes/records and progress per pass;
  process a large source with a deliberately small budget through convergence.
- [ ] Compare one-shot reference evidence with resumed evidence, including exact
  multiplicity, append, truncation/replacement, same-size rewrite, partial tails,
  invalid JSON, oversized individual records, missing sources and corrupt cursors.
- [ ] Verify last-good state and compaction refusal on incomplete/failed evidence;
  preserve repository filtering and every affected target's public contract.
- [ ] Exercise real subprocess/filesystem consumers, timeout/cancellation and
  restart recovery without widening existing safety budgets.
- [ ] Run focused plugin tests, touched Python lint and install-contract checks.
- [ ] Exercise fresh-state native fixtures where cursor/installer behavior changes;
  name unavailable external venues instead of claiming unobserved evidence.
- [ ] Observe the actual released/installed scheduled task and measured whole-run
  outcomes, including two cycles of native window/foreground monitoring.

## Proposal

Reviewed intent first. Implementation details depend on the measured phase and
source contract; an incremental reader is a proposed direction, not a claim of
existing behavior or permission to change evidence semantics.

## Journal

### 2026-10-24 - Expanded installed-consumer repair

- Original window/directory and aggregate-row fixes are delivered in #8460 and
  #8688. Native live validation confirmed window suppression but exposed
  additional transcript accounting bounds and task termination.
- The operator explicitly selected continued complete-accounting engineering,
  rather than disabling accounting, and confirmed this effort slug.
- Created this plan-only proposal. No new accounting, transfer or task-lifetime
  implementation has begun under it.
