# Large-Corpus Session Sync

- **Slug:** `large-corpus-session-sync`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed increments against Dev
- **Created:** 2026-10-24
- **Status:** Active
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

- [x] Land this proposal through normal automated review before implementation.
- [x] Attribute the real timeout to bounded source phases using content-free
  timings/counts and the actual scheduled-task result. _(agent-recommended)_
- [x] Inventory the relevant reader, ledger, change-tracker, filesystem-transfer,
  task-lifetime and cancellation contracts; identify exact compatibility and
  publication consumers before changing them. _(agent-recommended)_

### Phase 2 - Make accounting converge over large histories

- [x] Implement bounded incremental or resumable accounting collection so total
  historical size alone does not permanently reject an otherwise valid session.
  Choose the smallest source-grounded design after Phase 1. _(agent-recommended)_
- [x] Preserve source identity and revision checks, append/rewrite detection,
  per-record/line safety, exact multiplicity and idempotence. Partial tails,
  malformed usage, conflicting source records and genuine over-budget individual
  records remain visible; a cursor is never permission to skip unverified bytes.
- [x] Keep complete, deferred and failed coverage distinguishable. Only actually
  preserved accounting may permit cold compaction; interruption cannot discard
  the last good ledger, publish fabricated completeness or lose pending progress.
- [x] Avoid re-reading unchanged historical prefixes on every periodic pass.
  Keep cached state rebuildable and bounded, and keep conversation content out
  of accounting records. _(agent-recommended)_
- [ ] Resolve the remaining multi-session SQLite wall-budget failures through
  bounded scope reduction, preserving the five-second and 20,000-row reader
  limits and explicit failures when an individual scope still cannot fit.

### Phase 3 - Bound and resume the real scheduled operation

- [x] Fix the phase responsible for the actual timeout, preserving the existing
  single-writer lock, repository scope and target publication contract.
- [x] Make finite progress and continuation durable when all work cannot finish
  in one task lifetime. Do not substitute a silently enlarged deadline, another
  scheduler, unowned process or disabled accounting for a working operation.
- [x] Preserve windowlessness, real exit outcomes, diagnostic persistence and
  safe process-tree cleanup on completion, timeout and cancellation.
- [ ] Land source changes through approved PRs, observe a containing release and
  use the authorized updater; preserve explicit failure if deployment is blocked.

### Phase 4 - Prove the installed outcome

- [x] Verify complete accounting for large finite histories, append/catch-up,
  unchanged repeats and interrupted continuation without double counting.
- [ ] Run the actual installed scheduled consumer over the real corpus through
  two observed cycles, with explicit accounting/transfer outcomes, no unwanted
  console/foreground transitions and no leaked descendants.
- [ ] Resolve or transfer every remaining Plan/Validation item to a named tracked
  objective, record the outcome, archive this effort and finalize owned worktrees.
  A successful source PR alone is not completion.

## Validation Plan

The following verification design is agent-recommended:

- [x] Regression first: synthetic histories above the existing total-byte/event
  threshold reproduce the permanent failure before the source correction.
- [x] Bounded-work tests measure consumed bytes/records and progress per pass;
  process a large source with a deliberately small budget through convergence.
- [x] Compare one-shot reference evidence with resumed evidence, including exact
  multiplicity, append, truncation/replacement, same-size rewrite, partial tails,
  invalid JSON, oversized individual records, missing sources and corrupt cursors.
- [x] Verify last-good state and compaction refusal on incomplete/failed evidence;
  preserve repository filtering and every affected target's public contract.
- [x] Exercise real subprocess/filesystem consumers, timeout/cancellation and
  restart recovery without widening existing safety budgets.
- [x] Run focused plugin tests, touched Python lint and install-contract checks.
- [x] Exercise fresh-state native fixtures where cursor/installer behavior changes;
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
- Plan #8740 merged as `725712d4e1dd776caef19cf349321f613b2afc1d`
  after current-head approval and green required CI. Phase 1 now owns measured
  consumer attribution; no implementation or deployment success is inferred.

### 2026-10-24 - Measured filesystem traversal

- Two content-free live stack samples place the surviving timed-out consumer in
  `FilesystemTarget.push` / `_iter_regular_source_files`, called from a full
  reconciliation batch. Its iterator enters the entire source tree before the
  file-level publication predicate discards unrelated files. Each batch repeats
  that traversal, including non-session trees and unselected sessions.
- The scheduled root had exited while its shell/runtime descendants retained
  the sync lock. A later scheduled trigger returned zero only because it skipped
  that lock; it is not a successful second-cycle validation.
- Added directory pruning without changing file-level inclusion, index,
  provenance, lock deferral or rescue replacement contracts. Six local/OneDrive
  controls fail against the prior walk and pass after pruning. A read-only
  real-corpus 100-session traversal visits 4,453 files in 3.674 seconds.
- Added flushed phase timings so installed diagnostics can distinguish metadata,
  accounting, transfer, compaction and retention; a timing completion marker does
  not claim successful coverage or publication.
- Focused transfer/protection/rescue tests pass (364, two platform skips).
  Validation exposed an interpreter-dependent memory-fixture allocation:
  Python 3.14's default read buffer is 128 KiB rather than 8 KiB. The fixture now
  pins its buffer to 8 KiB while retaining its original measured threshold.
- This increment does not resolve large-transcript accounting, bounded run
  continuation or descendant cleanup. Those gates remain open.
- #8752 merged as `dc033b6ab3b0a8a92139acaa1b73f2564f08d633`
  after current-head approval and green CI. Review extended pruning to the
  protected publication loop as well; all/essential/redacted local and OneDrive
  scope controls now pass (376 focused tests, two platform skips). Release and
  installed task qualification are still pending.

### 2026-10-25 - Bounded transcript accounting

- Continuing Phase 2 in managed implementation workspace `spry-gauge`; this
  README remains the canonical campaign record.
- A real scheduled-accounting regression above the old 64 MiB source limit
  reproduces permanent failure before the new collector. Bounded checkpoints
  now converge without widening that per-pass byte or event budget.
- Checkpoints contain only validated scalar observations and source revision,
  offset and event counts. Unchanged completed sources need no transcript reads.
  Any changed generation, including append, restarts validation at byte zero:
  sampling a boundary is not proof that an earlier prefix was not rewritten.
- Deferred work retains the last good ledger and blocks compaction without
  inventing complete coverage. Invalid/corrupt source evidence remains failed;
  corrupt disposable progress is visibly rebuilt from the source.
- Initial accounting/reader/feature tests pass (220), including deliberately
  small budgets, multiplicity, rewrite/replacement/truncation, partial tails,
  failed checkpoint replacement, concurrent mutation and unchanged reuse.
  Further event-threshold and subprocess restart validation precede publication.
- Expanded accounting/reader/feature/SQLite validation passes (286 tests, one
  platform skip), including a new-process continuation and both original total
  byte/event thresholds. Individual limits and one-shot reader APIs are unchanged.
- A fresh, private disposable cache over ten stable real histories of
  119,568,078 through 278,316,336 bytes converges in two through five passes,
  with a measured maximum of 67,108,864 source bytes per pass. All unchanged
  repeats read zero transcript bytes. The complete probe takes 38.268 seconds,
  retains no conversation content and does not mutate the source or its ledger.
- An earlier probe correctly rejected a history that changed during capture.
  The stable-history proof excludes that moving source rather than treating
  rejected evidence as successful coverage.
- Review identified saturated native-replay merge work and starvation behind
  repeatedly failing/moving early sessions. Regression-first controls reproduce
  both. Shared keyed observation aggregation and a durable, source-scoped rotating
  collection position resolve them while retaining the existing merge semantics.
- The final focused accounting packet passes 291 tests with one platform skip.
  A production-size synthetic checkpoint with 20,000 observations and 200,000
  native replays performs 240,000 normalizations in 13.784 seconds, rather than
  re-merging the entire checkpoint per event.
- #8767 merged as `c81d898f266825afbc87dc926e8f8b324a6579ab`
  after current-head approval and green required CI. An unrelated retained
  Bridge-closure CI blocker was coordinated with its existing owner, not bypassed.
  Release, runtime selection and actual scheduled qualification remain open.

### 2026-10-25 - Scheduled descendant ownership

- Continuing the existing Phase 3 in `shiny-pedal`. The GUI root now reuses the
  installed runtime's canonical process primitive: suspended child launch into a
  required kill-on-close Job with no descendant breakaway.
- Cancellation, normal completion and a 25-minute launcher guard all settle the
  owned child tree. The existing Scheduler limit remains 30 minutes; timeout is
  exit 124 with retained diagnostics, never successful completion.
- Fresh generated-launcher native tests pass for cancellation and timeout,
  including shell plus two Python generations. Existing two-cycle window/
  foreground and exit-code controls pass for both PowerShell generations.
  The 12 native tests pass; eleven POSIX-only cases skip on this host.
- The installer declaration generator must now refresh the changed diagnostic
  surface. The broader installer/transfer packet remains in progress; publication
  waits for its actual result.
- The generated installer diagnostic declaration is refreshed; install-contract
  checks pass (13), as does touched Python lint. The isolated native
  installer/transfer packet passes 213 tests with fourteen platform skips in
  613.20 seconds, including cold installation, signed interpreter probing,
  cancellation, timeout and interrupted full reconciliation. A completed first
  batch retains its signatures without a premature full-sync marker; retry copies
  only remaining files. Native timeout/cancellation tests reap both descendant
  generations, and both PowerShell generations preserve exit codes and focus.
- Fresh-state Windows fixtures exercise the relevant installed-launcher contract.
  No separate remote Windows venue is available for this host-specific task;
  actual released local scheduled-task qualification remains mandatory.
- Rebased only the two unmerged commits above the already-merged traversal
  increment. The only remaining conflict was the generated installer entrypoint
  hash; regeneration preserves upstream's new shared installer dependencies.
  The combined fresh-install/native/transfer packet passes 215 tests with
  fourteen platform skips in 744.97 seconds; install contracts and generator
  consistency also pass.
- #8808 review found that the GUI root pinned process support to its original
  runtime slot, which later provisioning could collect without refreshing the
  Python launcher. Four native upgrade controls reproduce exit 1 after removing
  that slot. The root now uses the installed canonical runtime resolver on each
  invocation before loading selected support; it spawns no resolver subprocess
  and duplicates no selector rules. All twelve native controls pass, including
  both shell generations, slot collection between cycles and tree cleanup;
  eleven POSIX cases skip on Windows. Install contracts and touched lint pass.
- #8808 merged as `1587a895b96e7192c489b8b12073cfb68011ecde`
  after current-head approval, green required CI and resolution of its upgrade
  finding. Source changes are complete. The remaining Phase 3 gate is a containing
  release and successful authorized unified rollout; Phase 4 still requires two
  actual scheduled-consumer observations before effort completion.
- Containing release observed on Main `10f7206cb2cf166c91c30b1f0c3621df050419d6`
  as agent-logger `0.3.85-dev1`. Pinned release bytes contain the final live
  resolver, required no-breakaway Job, timeout exit 124 and bounded resumable
  accounting. Started the previously authorized normal unified updater;
  neither update startup nor release publication is installed-consumer success.
- The normal unified rollout ended with engine-repair exit 124 after 573.0
  seconds. The Manager cutover succeeded, and
  read-only doctor reports one healthy engine generation with no cutover in
  progress, but the overall update failed before proving the plugin rollout.
  No guard or process was force-cleared. Started one ordinary idempotent retry
  under existing rollout authorization; per-plugin recovery remains unapproved.
- The ordinary retry installs and verifies Logger `0.3.85-dev1`, including
  byte-for-byte accounting/transfer source and the exact generated GUI launcher
  against the pinned containing release. Its overall result is still failed
  because Bridge's separate cutover timed out; no shared service was manually
  restarted or altered to clear that result.
- The first final released scheduled cycle observes no new console windows or
  foreground transitions and no recurrence of the original directory error.
  Transfer finishes in 96.366 seconds, but two multi-session SQLite reads exceed
  the existing five-second budget, so exit 1 is correctly retained. Missing
  historical transcripts and bounded deferred reads remain explicit.
- Read-only native profiling finds an indexed 159 MB store: the largest
  individual session has 8,023 rows and reads in 0.300 seconds. A heavy
  multi-session scope reaches the unchanged row limit in about 0.6 seconds.
  The remaining timeout branch currently lacks the row-budget branch's bounded
  partitioning. Extend that same scope-reduction contract, not the deadline,
  and retain failed single-session reads rather than classifying them complete.
- The regression first reproduces that unhandled expired-scope failure. Typed
  wall-budget exhaustion now partitions outside the failed reader's exception
  lifetime, just like row exhaustion; SQLite VM cancellation is distinguished
  only when this reader's own budget callback caused it.
- The combined accounting/transfer packet passes 473 tests with three platform
  skips. Controls cover exact multiplicity after partitioning, both limits,
  failed-buffer release, last-good retention, one failing leaf without retry,
  the 63-read worst case for a 32-session batch, and no partitioning of unrelated
  unsafe-source errors. Touched lint and thirteen install contracts pass.
  Publication, release and actual scheduled requalification remain pending.
