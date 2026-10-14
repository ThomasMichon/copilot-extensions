# Cache-backed rendering and explicit refresh latency

A continuation of [Process Slot Ownership](README.md), applying the operator's
render/live latency distinction to the Agent Bridge reader of Worktree Manager.
It closes `visions/plugin-services/README.md`'s `work-coalescing-singleton`
intent using the existing [coalescing pattern](../../../docs/patterns/work-coalescing-singleton.md)
and #1919's catalog contract, not another catalog or store of record.

## Requested contract

- Enumeration for rendering returns in **under 2 seconds wall time**, reading
  a DB/in-memory cache backed by Worktree Manager's existing state.
- An explicit live on-demand refresh has a **10-second wall-clock goal**.
  This is not a CPU-seconds threshold or permission to replace native process
  execution deadlines. Numeric CPU admission limits remain deferred.
- Preserve owner-tethered single occupancy: caller timeout or cancellation is
  not actual refresh-holder completion and cannot admit overlapping work.

## Bounded implementation plan _(agent-recommended)_

1. Separate render reads from live refresh at the existing worktree route/cache
   seam. Ordinary rendering reads an already-published snapshot; no foreground
   fleet crawl, native/SSH reader, session-history scan, Git classification or
   mux recomputation. Use the existing Bridge memory projection and Worktree
   Manager DB/cache owner; add only the bounded source integration needed for
   this reader. Do not implement a rival DB, registry, observer or resident
   helper, or take over #1919's full paging/incremental-reconciliation scope.
2. Publish freshness, completeness and refresh progress alongside the
   compatible `groups` response. A cold cache can return promptly while one
   owned warm-up runs, but must say warming/unknown rather than authoritative
   empty. Stale last-known rows remain visibly stale. Confirmed complete empty
   results are distinct from failed, malformed, unsupported or pending reads.
   Preserve each source's last good rows when a refresh cannot establish a
   replacement; a failed source must not erase healthy peers.
3. Expose explicit refresh through the same cache owner. Join a retained,
   coalesced task with a caller wall wait of at most 10 seconds. Return complete,
   partial, timed-out or still-pending evidence honestly; expiration of that
   wait leaves the underlying owned task shielded and tracked. Completion
   records whether the goal was met, not invented success when work is pending.
   Repeated callers cannot start overlapping equivalent refreshes.
4. Keep refresh publication atomic and generation/provider fenced. Source
   replacement or shutdown must not allow old results to overwrite new state;
   retain superseded holders until actual completion. Shutdown observation
   remains bounded, but wait expiry leaves ownership tracked until completion.
   Apply existing native execution and cleanup policies,
   without new kill/restart authority or new across-provider fan-out.
5. Preserve exact-ID detail, resume, lineage and action lookup behavior and
   their existing budgets. Rendering's subprocess-free degraded path is the
   named coalescing-pattern exception, not removal of ordinary CLI/action
   fallback. An unavailable Worktree Manager must degrade the projection
   explicitly without making Agent Bridge require a sibling service.
6. Version the detectable HTTP refresh/metadata behavior through the existing
   Agent Bridge protocol contract. New clients use `daemon_supports()` before
   sending refresh controls or relying on the metadata. Legacy daemons retain
   their supported request shape; missing metadata means freshness/completeness
   unknown, never proof of current complete evidence. Do not silently claim the
   new render latency contract for a legacy daemon. Gate prompt cold-cache
   rendering behind a versioned request opt-in and migrate rendering readers
   to it. Unversioned requests retain their existing blocking cold-cache
   behavior, so an old client cannot mistake empty warming `groups` for a
   complete inventory merely because it ignores additive metadata.
7. Add focused route/cache tests and source-owner regressions where integration
   changes them. Update existing reader/API reality docs and the required
   changefile for each changed plugin. Land the plan first, then serial
   implementation and canonical closure deltas.

## Validation

- [ ] Actual render-route response and serialization finish in under 2 seconds
      with both a warm snapshot and cold cache, including a blocked slow
      source. Assert no foreground crawl/native/SSH/session-history/Git/mux
      computation; a hidden await or recomputation is not a cached read.
- [ ] Rendering uses the existing Worktree Manager-backed projection, not
      fabricated rows. A bounded integration fixture distinguishes published
      source generations and preserves compatible group/row semantics.
- [ ] Cold/warming, stale, incomplete, unavailable, incompatible and confirmed
      complete-empty states remain distinguishable. Malformed/failed refreshes
      preserve prior good rows and healthy peers, with explicit diagnostics.
- [ ] Concurrent cold requests and explicit refresh callers perform one
      underlying read per equivalent key. Cancellation and the 10-second
      caller wall wait do not cancel/release the holder; a later caller joins
      it and eventual completion publishes at most once.
- [ ] Measure real explicit-refresh wall behavior for fast success, partial
      source failure and a blocked holder. Verify the 10-second wait boundary
      and reported pending/timed-out state independently of CPU cost and
      unchanged native execution timeout.
- [ ] Provider/generation replacement and bounded shutdown fence old
      publication while retaining actual holder accounting.
- [ ] Protocol/capability gating handles old-client/new-daemon and
      new-client/old-daemon combinations. Unsupported refresh controls are not
      sent; metadata-free legacy responses remain explicitly unknown without
      breaking the prior supported request/response shape. An old client's
      unversioned cold request retains blocking semantics; only an opted-in
      rendering reader receives prompt warming/unknown results.
- [ ] Exact-action detail/resume/lineage, optional-sibling fallback, ownership,
      namespace circuit and privacy regressions remain green on supported
      platforms; existing test containment and portfolio limits are preserved.

The measured goal is response latency, not subprocess timeout configuration
or a mocked duration field. Real-source adoption remains the parent effort's
authorized release/running-state gate; merging this slice cannot close it.
