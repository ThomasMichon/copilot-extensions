"""Cleanup / GC CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path

from . import finalize as fin, git_ops, output, prune, reap_cli, sessions, tracking
from . import claimant as claimant_mod
from . import config as cfg


def _core():
    from . import __main__ as core
    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _age_str(*args, **kwargs): return _core()._age_str(*args, **kwargs)
def _apply_tracking_override(*args, **kwargs): return _core()._apply_tracking_override(*args, **kwargs)
def _build_active_paths(*args, **kwargs): return _core()._build_active_paths(*args, **kwargs)
def _hosted_session_blocks_cleanup(*args, **kwargs): return _core()._hosted_session_blocks_cleanup(*args, **kwargs)
def _local_claimant_alive(*args, **kwargs): return _core()._local_claimant_alive(*args, **kwargs)
def _make_pr_lookup(*args, **kwargs): return _core()._make_pr_lookup(*args, **kwargs)
def _reap_worktree(*args, **kwargs): return _core()._reap_worktree(*args, **kwargs)
def reap_one(*args, **kwargs): return _core().reap_one(*args, **kwargs)
def reap_orphan_launcher_shells(*args, **kwargs): return reap_cli.reap_orphan_launcher_shells(*args, **kwargs)
def sweep_managed_worktrees(*args, **kwargs): return reap_cli.sweep_managed_worktrees(*args, **kwargs)
def sweep_finished_session_worktrees(*args, **kwargs): return reap_cli.sweep_finished_session_worktrees(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("cleanup", help="List and clean orphaned worktrees")
    p.add_argument("--clean", action="store_true")
    p.add_argument("--worktree-id", default=None, help="Clean a single worktree by ID (non-interactive, re-checks prune-safety; pair with --json for the picker's per-item progress)")
    p.add_argument("--force", action="store_true", help="With --worktree-id: reap even if prune-safety would skip it (still refuses an active session)")
    p.add_argument("--json", action="store_true", help="With --worktree-id: emit a single JSON result object")
    p.add_argument("--include-unused", action="store_true", help="Also clean truly-empty worktrees (no commits, zero conversation turns)")
    p.add_argument("--include-conversations", action="store_true", help="Also clean conversation-only worktrees (no commits but the session held turns); implies --include-unused")
    p.add_argument("--reconcile-prs", action="store_true", help="Refresh tracked PR state from the provider before deciding (heals stale 'open' PRs merged externally); requires network + provider credentials")
    p.add_argument("--max-age-days", type=int, default=7)
    p = sub.add_parser("gc", help="Garbage-collect worktrees: tracked reap (cleanup verdict) + on-disk orphan-directory sweep + git worktree prune")
    p.add_argument("--dry-run", action="store_true", help="Report what would be removed without removing anything")
    p.add_argument("--json", action="store_true", help="Emit the orphan-sweep report as JSON (the tracked reap runs in text mode)")
    p.add_argument("--orphans-only", action="store_true", help="Only sweep orphan directories; skip the tracked reap and the managed (system/bridge) sweep")
    p.add_argument("--no-managed", action="store_true", help="Skip the managed (system/bridge) leak sweep")
    p.add_argument("--no-reap-shells", action="store_true", help="Skip the orphaned launcher-shell reap (pwsh/python scaffolding stranded by a force-closed terminal)")
    p.add_argument("--reap-shells-grace-hours", type=float, default=None, help="Idle window before an orphaned launcher shell is eligible (default 1h); a fresh one is always spared")
    p.add_argument("--managed-grace-hours", type=float, default=None, help="Idle window before a dead managed worktree is reaped (default 1h); a still-fresh one is always spared")
    p.add_argument("--include-unused", action="store_true", help="Also reap truly-empty tracked worktrees (no commits, zero conversation turns)")
    p.add_argument("--include-conversations", action="store_true", help="Also reap conversation-only worktrees (no commits but the session held turns); implies --include-unused")
    p.add_argument("--reconcile-prs", action="store_true", help="Refresh tracked PR state from the provider before deciding (heals stale 'open' PRs merged externally)")
    p.add_argument("--max-age-days", type=int, default=7)
    p.add_argument(
        "--lease-gc", action="store_true",
        help="Also squash released lease refs (refs/agent-worktrees/leases/v1) past "
             "--lease-retention-days to a minimal 2-commit history; opt-in (network + "
             "force-push to the lease store)")
    p.add_argument(
        "--lease-retention-days", type=int, default=30,
        help="Age (since release) past which a released lease ref is eligible for "
             "--lease-gc (default 30)")
    p.add_argument(
        "--lease-kind", default=None,
        help="With --lease-gc: restrict to one lease kind (codespace/container/task/...); "
             "default every kind")
    p.add_argument(
        "--mirror-claim-history", action="store_true",
        help="Also push any locally-recorded, not-yet-mirrored claim_history "
             "events (refs/agent-worktrees/claim-history/v1) to the shared "
             "store -- opt-in (network + force-push), resumable, idempotent")
    p = sub.add_parser("sweep-managed", help="Machine-readable managed-worktree leak sweep for external control planes")
    p.add_argument("--dry-run", action="store_true", help="Report what would be removed without removing anything")
    p.add_argument("--json", action="store_true", help="Emit the managed sweep result as JSON")
    p = sub.add_parser("sweep-finished-sessions", help="Machine-readable finished-session auto-clean sweep for external control planes")
    p.add_argument("--dry-run", action="store_true", help="Report what would be removed without removing anything")
    p.add_argument("--json", action="store_true", help="Emit the finished-session sweep result as JSON")

def _cleanup_one(args: argparse.Namespace) -> int:
    """``cleanup --worktree-id <id>`` -- thin CLI wrapper over :func:`reap_one`."""
    payload = _core_helper("reap_one", reap_one)(
        args.worktree_id,
        force=getattr(args, "force", False),
        include_unused=getattr(args, "include_unused", False),
        include_conversations=getattr(args, "include_conversations", False),
        reconcile_prs=getattr(args, "reconcile_prs", False),
    )
    if getattr(args, "json", False):
        output._json_output(payload)
    else:
        tag = (
            "removed"
            if payload.get("removed")
            else ("skipped" if payload.get("skipped") else "error")
        )
        line = f"{payload['worktree_id']}: {tag}"
        if payload.get("reason"):
            line += f" -- {payload['reason']}"
        print(line)
    return 0 if payload.get("ok") else 1


#: `cleanup_disposition` buckets that get their OWN per-item skip line (a
#: specific, actionable reason worth calling out individually) rather than
#: folding into the aggregate unused/conversation/dirty/wip summary counters.
#: worktree-finality-and-obligations Phase 5: `held-claims`/`follow-up` were
#: previously missing here, so a worktree blocked by either silently vanished
#: from `cleanup`'s report entirely -- neither listed as skipped nor counted.
_CLEANUP_PER_ITEM_BUCKETS = frozenset({
    "claimed", "open-pr", "closed-unmerged", "paired-pending",
    "held-claims", "held-claims-cross-machine", "follow-up",
})


def _cleanup_per_item_skip_reason(disp: prune.CleanupDisposition) -> str:
    """The exact per-worktree skip line for `cleanup`'s report, or "" when the
    bucket instead folds into an aggregate summary counter."""
    if disp.bucket == "active":
        return "active Copilot session in use"
    if disp.bucket in _CLEANUP_PER_ITEM_BUCKETS:
        return disp.reason
    return ""


def _closure_blockers(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    disp: prune.CleanupDisposition,
    *,
    turn_count: int = 0,
) -> list[dict]:
    """The closure descriptor's full ``blockers`` list for ``rec``/``info``/
    ``disp`` (worktree-finality-and-obligations Phase 4) -- REPORTING only,
    never a decision input; cleanup/GC's actual cleanable/not gate stays
    exactly ``disp.cleanable`` (see the Plan bullet's own note on why a full
    switch to the descriptor's ``action_disposition`` was deferred: it
    degrades "safe" to "blocked" whenever evidence isn't a fresh fetch,
    which would newly block cleanups this revalidation path -- no-fetch by
    design -- has always allowed).

    Unlike ``disp.reason`` (only the single FIRST blocking condition
    ``cleanup_disposition`` short-circuited on), ``blockers`` independently
    names every concurrently-true one -- e.g. a worktree that is both
    held-claims AND has open follow-ups reports both, not just whichever
    check ran first. ``evidence_mode``/``repo_fetch_fresh`` are fixed,
    conservative placeholders: this helper only ever reads the returned
    ``blockers`` field, which does not depend on either.
    """
    held_claims = sum(1 for c in rec.resources if c.is_live)
    open_follow_ups = tracking.effective_open_follow_up_count(rec)
    descriptor = prune.assemble_closure_descriptor(
        rec, info, disp,
        held_claims=held_claims, open_follow_ups=open_follow_ups,
        evidence_mode="cached", turn_count=turn_count,
        cross_machine_claims=prune.cross_machine_claim_count(rec),
    )
    return descriptor.blockers


def _enrich_reason_with_blockers(reason: str, blockers: list[dict]) -> str:
    """Append every blocker code+count beyond the single one already named in
    ``reason``, when :func:`_closure_blockers` found more than one
    concurrently-true blocker -- a no-op (returns ``reason`` unchanged) when
    there are 0 or 1, since the existing text already says everything."""
    if len(blockers) <= 1:
        return reason
    parts = [f"{b['code']}\u00d7{b['count']}" for b in blockers]
    return f"{reason} \u00b7 blockers: {', '.join(parts)}"


@dataclasses.dataclass
class RevalidationResult:
    """The canonical outcome of :func:`_revalidate_cleanup_safety`."""

    cleanable: bool
    reason: str
    bucket: str
    #: Populated only when ``cleanable`` -- the fresh record/classification the
    #: reap must act on, never the caller's pre-lock objects.
    record: tracking.WorktreeRecord | None = None
    info: git_ops.WorktreeStateInfo | None = None
    #: Populated only when a ``reap`` callback was supplied and actually ran.
    failures: int = 0
    warnings: list[str] = dataclasses.field(default_factory=list)
    reaped: bool = False


def _revalidate_cleanup_safety(
    wt_id: str,
    *,
    repo,
    tracking_path: Path,
    force: bool = False,
    include_unused: bool = False,
    include_conversations: bool = False,
    reap: Callable[
        [tracking.WorktreeRecord, git_ops.WorktreeStateInfo], tuple[int, list[str]]
    ]
    | None = None,
) -> RevalidationResult:
    """The one canonical, under-``FinalizeLock`` safety recheck every reaper
    shares (cleanup-toctou-revalidation effort, replacing the narrower
    ``_revalidate_before_reap``).

    Reloads the tracking record fresh, rebuilds a single-worktree
    ``active_paths``, re-classifies git state fresh (no network fetch),
    re-derives ``turn_count``, and runs the complete (ordering-fixed)
    :func:`prune.cleanup_disposition` -- never a narrowed dirty/active-only
    check. Returns a :class:`RevalidationResult` carrying the fresh
    ``record``/``info`` the caller must reap, not the caller's stale pre-lock
    objects.

    **Two named contracts** (Phase 2):

    - **Non-forced** (default): re-checks the complete disposition --
      dirty/WIP/conversation/held-claims/follow-up/paired-pending/branch-merge
      (for a missing directory) -- via the *ordering-fixed*
      ``cleanup_disposition``, in addition to liveness. When ``reap`` is
      given and the fresh decision is cleanable, this acquires
      ``tracking._RecordLock(yaml_path, require_sidecar=True)`` and invokes
      ``reap`` **while continuing to hold it**, so the fresh read and the
      delete happen as one uninterrupted, lock-held sequence (Option 1(b)):
      a racing write from this codebase's OTHER record mutators (resource
      claims, follow-ups, session registration -- all of which already take
      the same sidecar lock for their own RMW) is fenced out for the whole
      window, not just the read. ``require_sidecar=True`` means lock
      contention **fails closed** (raises ``TimeoutError``, reported as the
      ``"locked"`` bucket) rather than degrading to the in-process-only
      lock. Callers must already hold ``FinalizeLock`` before calling this
      (lock order: ``FinalizeLock`` then ``_RecordLock`` -- never reversed).
    - **Forced** (`force=True`): mirrors ``reap_one --force``'s documented,
      narrower escape hatch -- skips ``cleanup_disposition`` entirely (dirty/
      WIP/claims/follow-ups/branch-merge are NOT re-checked), but still
      refreshes liveness fresh, under the lock, via the *same* liveness-read
      code path as the non-forced contract (closing the gap where ``--force``
      used to trust a stale pre-lock ``active_paths`` snapshot). The one
      check ``--force`` never bypasses is an active/hosted session. Forced
      mode does not take ``_RecordLock`` -- its narrower contract isn't
      gated on the fields other writers mutate.

    A worktree with no on-disk path (``GONE``) re-proves the branch-merged
    gate itself (``cleanup_disposition`` deliberately excludes ``GONE`` --
    that proof is caller-owned), so a branch that became unmerged after an
    earlier, caller-side pre-lock proof is still caught here.

    This function never performs a network PR reconciliation -- ``rec.prs``
    is read as already reconciled at scan time (or not reconciled at all, in
    which case a stale ``open`` fails safe toward "don't reap").
    """
    yaml_path = tracking_path / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return RevalidationResult(False, f"worktree not found: {wt_id}", "gone")

    def _fresh_liveness(latest: tracking.WorktreeRecord) -> tuple[
        git_ops.WorktreeStateInfo, set[str], sessions.SessionContext
    ]:
        from .execution_spaces import require_cleanup_identity
        require_cleanup_identity(latest, repo.anchor)
        session_ctx = sessions.scan_sessions_fast([latest])
        active_paths = _build_active_paths([latest], session_ctx)
        if latest.worktree_path and Path(latest.worktree_path).exists():
            fresh_info = git_ops.classify_worktree(
                latest.worktree_path,
                latest.branch,
                fetch=False,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            fresh_info = _apply_tracking_override(latest, fresh_info)
        elif latest.status == "finalized":
            fresh_info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        else:
            fresh_info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
        return fresh_info, active_paths, session_ctx

    def _do_reap(
        latest: tracking.WorktreeRecord, fresh_info: git_ops.WorktreeStateInfo
    ) -> tuple[int, list[str]]:
        if reap is not None:
            return reap(latest, fresh_info)
        return 0, []

    if force:
        latest = tracking.load_record(yaml_path)
        from .execution_spaces import ExecutionSpaceError, require_cleanup_identity
        try:
            require_cleanup_identity(latest, repo.anchor)
        except ExecutionSpaceError as exc:
            return RevalidationResult(False, str(exc), "execution-space")
        if _hosted_session_blocks_cleanup(latest):
            return RevalidationResult(
                False, "active hosted Copilot session in use", "active")
        fresh_info, _active_paths, _ctx = _fresh_liveness(latest)
        if fresh_info.state == git_ops.WorktreeState.ACTIVE:
            return RevalidationResult(
                False, "worktree became active since the initial scan", "active")
        failures, warnings = _do_reap(latest, fresh_info)
        return RevalidationResult(
            True, "forced", "forced", latest, fresh_info,
            failures=failures, warnings=warnings, reaped=reap is not None,
        )

    try:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            latest = tracking.load_record(yaml_path)
            from .execution_spaces import ExecutionSpaceError, require_cleanup_identity
            try:
                require_cleanup_identity(latest, repo.anchor)
            except ExecutionSpaceError as exc:
                return RevalidationResult(False, str(exc), "execution-space")
            if _hosted_session_blocks_cleanup(latest):
                return RevalidationResult(
                    False, "active hosted Copilot session in use", "active")
            fresh_info, active_paths, session_ctx = _fresh_liveness(latest)
            if fresh_info.state == git_ops.WorktreeState.ACTIVE:
                return RevalidationResult(
                    False, "worktree became active since the initial scan",
                    "active")

            if fresh_info.state == git_ops.WorktreeState.GONE:
                upstream = f"{repo.remote}/{repo.default_branch}"
                if latest.branch and not git_ops.is_branch_merged(
                    latest.branch, upstream, cwd=repo.anchor,
                ):
                    return RevalidationResult(
                        False,
                        "branch has unmerged commits (worktree dir missing)",
                        "unmerged")
                failures, warnings = _do_reap(latest, fresh_info)
                return RevalidationResult(
                    True, "gone; branch merged", "clean", latest, fresh_info,
                    failures=failures, warnings=warnings, reaped=reap is not None,
                )

            norm = sessions._normalize_path(latest.worktree_path) if latest.worktree_path else ""
            turns = session_ctx.turn_count.get(norm, 0)
            disp = prune.cleanup_disposition(
                latest,
                fresh_info,
                turn_count=turns,
                include_unused=include_unused,
                include_conversations=include_conversations,
                claimant_alive=claimant_mod.resolve_claimant_alive,
                paired_sibling_final=prune.default_paired_sibling_final,
            )
            if not disp.cleanable:
                blockers = _closure_blockers(latest, fresh_info, disp, turn_count=turns)
                reason = _enrich_reason_with_blockers(disp.reason, blockers)
                return RevalidationResult(False, reason, disp.bucket)
            failures, warnings = _do_reap(latest, fresh_info)
            return RevalidationResult(
                True, disp.reason, disp.bucket, latest, fresh_info,
                failures=failures, warnings=warnings, reaped=reap is not None,
            )
    except TimeoutError:
        return RevalidationResult(
            False, "revalidation lock contended", "locked")


def cmd_cleanup(args: argparse.Namespace) -> int:
    if getattr(args, "worktree_id", None):
        return _core_helper("_cleanup_one", _cleanup_one)(args)

    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()

    records = tracking.list_records(tracking_path)
    if not records:
        print("No tracked sessions.")
        return 0

    # System worktrees are daemon-owned (never auto-removed here). Archived
    # records have nothing left to reap -- exclude both from cleanup.
    # A dispatch-created worktree that hasn't yet been concluded (its `kind`
    # is still an ordinary one, not yet flipped to a MANAGED_KINDS value by
    # `terminal_conclusion.conclude_disposable_worktree`) is excluded too:
    # only that explicit conclusion step -- driven by the owning
    # agent-dispatch task reaching a terminal state -- may hand it to the
    # managed sweep. Without this, a dispatch-created worktree sitting idle
    # between retry attempts (clean, no commits yet, no active session --
    # indistinguishable from an ordinary "unused" worktree) is a routine
    # auto-clean candidate for THIS sweep despite a still-pending task
    # depending on it (observed live: a dispatch task's spawn kept retrying a
    # worktree that had been swept out from under it between retry attempts).
    records = [r for r in records if r.kind not in tracking.MANAGED_KINDS
               and r.status != "archived"
               and r.dispatch_attempt is None]
    if not records:
        print("No tracked sessions.")
        return 0

    to_clean: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    skipped: list[tuple[tracking.WorktreeRecord, str]] = []
    unused_count = 0
    conversation_count = 0
    dirty_count = 0
    wip_count = 0

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Sessions")
    print()
    print(f"{'Worktree ID':<50} {'State':<12} {'Age':<12} Path")
    print(f"{'─' * 48:<50} {'─' * 10:<12} {'─' * 10:<12} {'─' * 30}")

    # Fetch once for accurate classification (skip gracefully if there is no
    # remote -- a local-only repo must not crash cleanup).
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"

    # Scan for live Copilot sessions and mux sessions
    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    # Optional: heal stale tracked PR state from the provider (network) so a
    # PR merged externally (local record still "open") is recognized as landed.
    pr_lookup = _make_pr_lookup(config) if getattr(args, "reconcile_prs", False) else None

    for rec in records:
        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path,
                rec.branch,
                fetch=False,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(rec, info)
            state_str = info.state.value
        elif rec.status == "finalized":
            state_str = "completed"
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
            state_str = "gone"

        age = _age_str(rec.started_at)
        path_display = rec.worktree_path if Path(rec.worktree_path).exists() else "(gone)"

        # Compute prune-safety verdict (combines git state, PR records, and
        # session activity) -- drives the cleanup decision and enriches display.
        norm = sessions._normalize_path(rec.worktree_path)
        turns = session_ctx.turn_count.get(norm, 0)

        # Heal stale PR state from the provider before assessing (opt-in).
        if pr_lookup is not None and rec.prs:
            # Best-effort reconcile write (#4547): reconcile unlocked (provider
            # I/O), then re-apply the deltas onto a fresh snapshot under a
            # best-effort lock so a concurrent foreground verb is never
            # clobbered; skip on contention (self-heals next pass).
            prune.reconcile_and_persist_best_effort(rec, pr_lookup)

        verdict = prune.assess(rec, info, turn_count=turns, claimant_alive=_local_claimant_alive)

        # Annotate state with dirty indicator / turn count when relevant
        if info.dirty > 0 and info.state != git_ops.WorktreeState.DIRTY:
            state_display = f"{state_str} ({info.dirty}△)"
        elif verdict.category == "conversation-only":
            state_display = f"{state_str} ({turns}💬)"
        else:
            state_display = state_str
        print(f"{rec.worktree_id:<50} {state_display:<12} {age:<12} {path_display}")

        # Determine if cleanable
        cleanable = False
        skip_reason = ""
        include_conversations = getattr(args, "include_conversations", False)

        if info.state == git_ops.WorktreeState.GONE:
            # Directory missing -- verify branch content is on master first.
            if rec.branch and not git_ops.is_branch_merged(
                rec.branch,
                upstream,
                cwd=repo.anchor,
            ):
                skip_reason = "branch has unmerged commits (worktree dir missing)"
            else:
                cleanable = True
        else:
            disp = prune.cleanup_disposition(
                rec,
                info,
                turn_count=turns,
                include_unused=args.include_unused,
                include_conversations=include_conversations,
                claimant_alive=claimant_mod.resolve_claimant_alive,
                paired_sibling_final=prune.default_paired_sibling_final,
            )
            cleanable = disp.cleanable
            skip_reason = _cleanup_per_item_skip_reason(disp)
            if skip_reason:
                # worktree-finality-and-obligations Phase 4: enrich with any
                # OTHER concurrently-true blocker cleanup_disposition's own
                # short-circuit would otherwise hide (reporting only -- see
                # _closure_blockers' docstring; cleanable/not stays disp.cleanable).
                blockers = _closure_blockers(rec, info, disp, turn_count=turns)
                skip_reason = _enrich_reason_with_blockers(skip_reason, blockers)
            if not skip_reason and disp.bucket == "unused" and not cleanable:
                unused_count += 1
            elif not skip_reason and disp.bucket == "conversation" and not cleanable:
                conversation_count += 1
            elif not skip_reason and disp.bucket == "dirty":
                dirty_count += 1
            elif not skip_reason and disp.bucket == "wip":
                wip_count += 1

        if cleanable:
            to_clean.append((rec, info))
        elif skip_reason:
            skipped.append((rec, skip_reason))

    print()

    if skipped:
        for rec, reason in skipped:
            output.warn(f"Skipping {rec.worktree_id}: {reason}")
        print()

    if (
        not to_clean
        and unused_count == 0
        and conversation_count == 0
        and dirty_count == 0
        and wip_count == 0
        and not skipped
    ):
        print("Nothing to clean.")
        return 0

    if to_clean:
        print(f"{len(to_clean)} session(s) eligible for cleanup.")

    if not args.include_unused and unused_count > 0:
        print(
            f"{unused_count} unused worktree(s) preserved -- no commits, "
            "no uncommitted changes (pass --include-unused to also clean)."
        )

    if not getattr(args, "include_conversations", False) and conversation_count > 0:
        print(
            f"{conversation_count} conversation-only worktree(s) preserved -- "
            "no commits, but the session held conversation turns (pass "
            "--include-conversations to also clean)."
        )

    if dirty_count > 0 or wip_count > 0:
        parts = []
        if dirty_count:
            parts.append(f"{dirty_count} with uncommitted changes")
        if wip_count:
            parts.append(f"{wip_count} with unmerged commits")
        output.warn(f"{' and '.join(parts)} -- not eligible for cleanup.")

    if not args.clean or not to_clean:
        if to_clean:
            print("Run with --clean to remove them.")
        return 0

    # Acquire finalization lock to prevent races with post-exit finalization
    lock_path = Path(repo.worktree_root) / ".finalize.lock"
    lock = fin.FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock -- another finalization in progress?")
        return 1

    failures = 0
    cleaned_count = 0
    try:
        for rec, info in to_clean:

            def _reap_cb(latest_rec, latest_info):
                return _reap_worktree(latest_rec, latest_info, repo, tracking_path)

            result = _core_helper("_revalidate_cleanup_safety", _revalidate_cleanup_safety)(
                rec.worktree_id,
                repo=repo,
                tracking_path=tracking_path,
                include_unused=args.include_unused,
                include_conversations=include_conversations,
                reap=_reap_cb,
            )
            if not result.cleanable:
                output.warn(f"Skipping {rec.worktree_id}: {result.reason}")
                continue
            print(f"Cleaning {rec.worktree_id} ({result.info.state.value})...")
            for w in result.warnings:
                output.warn(w)
            failures += result.failures
            cleaned_count += 1

        # Prune stale worktree entries
        git_ops.prune_worktrees(cwd=repo.anchor)
    finally:
        lock.release()

    print()
    if failures:
        output.warn(f"Cleaned {cleaned_count} session(s) with {failures} warning(s).")
    else:
        output.ok(f"Cleaned {cleaned_count} session(s).")
    return 0


def _print_gc_orphans(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the orphan-directory sweep."""
    removed = report.get("removed", [])
    skipped = report.get("skipped", [])
    print()
    print("🧹 Orphan directories -- on disk, not a git worktree, not tracked")
    if not report.get("scanned"):
        print("  none found.")
        return
    verb = "would remove" if dry_run else "removed"
    for item in removed:
        print(f"  ✓ {verb}: {item['path']}  ({item['reason']})")
    for item in skipped:
        output.warn(f"  skipped: {item['path']}  ({item['reason']})")
    print()
    if dry_run:
        print(f"{len(removed)} orphan dir(s) would be removed, {len(skipped)} skipped.")
    else:
        output.ok(f"Removed {len(removed)} orphan dir(s); {len(skipped)} skipped.")


def _print_gc_managed(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the managed (system/bridge) worktree sweep."""
    removed = report.get("removed", [])
    skipped = report.get("skipped", [])
    print()
    print("🧹 Managed worktrees -- leaked system/bridge (dead, final/unused)")
    if not removed and not skipped:
        print("  none tracked.")
        return
    verb = "would remove" if dry_run else "removed"
    for item in removed:
        print(f"  ✓ {verb}: {item['id']}  ({item['reason']})")
    for item in skipped:
        print(f"  · kept: {item['id']}  ({item['reason']})")
    print()
    if dry_run:
        print(f"{len(removed)} managed worktree(s) would be reaped, {len(skipped)} kept.")
    else:
        output.ok(f"Reaped {len(removed)} managed worktree(s); {len(skipped)} kept.")


def _print_gc_shells(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the orphaned launcher-shell reap."""
    reaped = report.get("reaped", [])
    candidates = report.get("candidates", [])
    print()
    print("🧹 Launcher shells -- orphaned pwsh/python scaffolding (stranded)")
    if not report.get("available"):
        print("  (skipped or process enumeration unavailable.)")
        return
    if not reaped:
        print("  none found.")
        return
    verb = "would reap" if dry_run else "reaped"
    for c in candidates:
        print(f"  ✓ {verb}: pid {c['pid']}  {c['cmdline'][:80]}")
    for e in report.get("errors", []):
        output.warn(f"  ! pid {e['pid']}: {e['reason']}")
    print()
    if dry_run:
        print(f"{len(reaped)} orphaned launcher shell(s) would be reaped.")
    else:
        output.ok(f"Reaped {len(reaped)} orphaned launcher shell(s).")


def cmd_gc(args: argparse.Namespace) -> int:
    """Garbage-collect this project's worktrees on this machine.

    One command, three sweeps, then a prune:

      1. **Claim-history mirror sync** (opt-in, ``--mirror-claim-history``)
         -- pushes every locally-recorded, not-yet-mirrored
         :mod:`claim_history` event (``refs/agent-worktrees/claim-history/v1``,
         same shared store as the lease refs) to its own append-only remote
         ref, so PR ownership history survives this machine's own cleanup
         instead of evaporating with it (worktree-claims-transitive-finalization
         Phase 3b). Deliberately runs FIRST, before any reap/sweep step
         below can delete the tracking records a legacy (unstamped) event's
         eligibility falls back to. Stateless, idempotent, and a silent
         no-op when no store is configured.
      2. **Tracked reap** -- the same prune-safety verdict as ``cleanup``
         (finalized/merged/clean; ``--include-unused`` / ``--include-conversations``
         widen it). Never touches a dirty / ahead / follow-up / active / system
         worktree. ``--dry-run`` lists without removing.
      3. **Managed (system/bridge) sweep** -- reaps *leaked* daemon-owned
         worktrees that routine cleanup skips: only the provably dead ones
         (FINAL or UNUSED, no active mux/session/attach, no follow-up, idle past
         the grace window). Skip with ``--no-managed`` (#1069).
      4. **Orphan-directory sweep** -- removes *effectively-empty* on-disk
         directories under the worktree roots that are neither a registered git
         worktree nor a tracking record (leftovers from interrupted/forced
         removals), with a locked-directory retry/skip; a leftover holding real
         files is reported, never auto-deleted.
      5. **Orphaned launcher-shell reap** -- terminates pwsh/python
         ``-m agent_worktrees`` scaffolding stranded by a force-closed terminal
         (parent exited, nothing live under it, idle past the grace window).
         Service-safe (positive launcher-signature only). Skip with
         ``--no-reap-shells`` (copilot-extensions #102).
      6. ``git worktree prune`` to drop stale registrations.
      7. **Lease-ref squash** (opt-in, ``--lease-gc``) -- collapses each
         RELEASED cross-machine lease ref (``refs/agent-worktrees/leases/v1``)
         past ``--lease-retention-days`` (default 30) to a minimal 2-commit
         history, so its per-acquire/renew/release chain stops growing
         forever. Never deleted, never touches a live/stale ``leased`` ref.
         Network + force-push to the lease store; skipped entirely unless
         ``--lease-gc`` is passed, and a silent no-op (not an error) when no
         lease store is configured for this project.

    Idempotent: a second run right after the first finds nothing to do.

    ``--json`` reports the managed + orphan + shell + lease + claim-history
    mirror sweeps (machine-readable); the tracked reap runs in text mode.
    """
    from . import gc as gc_mod

    config = cfg.load_config()
    repo = config.default_repo
    records = tracking.list_records(cfg.tracking_dir())
    dry = getattr(args, "dry_run", False)
    json_mode = getattr(args, "json", False)
    orphans_only = getattr(args, "orphans_only", False)
    do_managed = not getattr(args, "no_managed", False) and not orphans_only
    do_shells = not getattr(args, "no_reap_shells", False) and not orphans_only

    # 1. Claim-history mirror sync (opt-in, network + force-push to the
    #    shared store -- worktree-claims-transitive-finalization Phase
    #    3b's remote-mirroring item). Deliberately FIRST, before any
    #    reap/sweep step below: a legacy (unstamped) event's eligibility
    #    falls back to the CURRENTLY-LIVE tracking records
    #    (_current_project_worktree_ids), and the tracked reap / managed
    #    sweep steps that follow can delete those same records (e.g.
    #    reaping the second half of a paired worktree, or a managed-leak
    #    sweep) -- syncing after that would silently and permanently
    #    exclude their events from every future sweep too.
    history_mirror = (
        _run_claim_history_mirror(args)
        if getattr(args, "mirror_claim_history", False)
        else None
    )

    # 2. Tracked reap -- reuse the cleanup verdict machinery (one fetch, full
    #    safety). Skipped in --json mode (its output is text) and --orphans-only.
    if not json_mode and not orphans_only:
        cmd_cleanup(
            argparse.Namespace(
                clean=not dry,
                worktree_id=None,
                force=False,
                json=False,
                include_unused=getattr(args, "include_unused", False),
                include_conversations=getattr(args, "include_conversations", False),
                reconcile_prs=getattr(args, "reconcile_prs", False),
                max_age_days=getattr(args, "max_age_days", 7),
            )
        )

    # 3. Managed (system/bridge) leak sweep -- the daemon-owned kinds cleanup
    #    skips. Only provably-dead ones are reaped (#1069).
    grace_hours = getattr(args, "managed_grace_hours", None)
    managed_kwargs = {"dry_run": dry}
    if grace_hours is not None:
        managed_kwargs["min_idle_secs"] = float(grace_hours) * 3600
    managed = (
        _core_helper("sweep_managed_worktrees", sweep_managed_worktrees)(**managed_kwargs) if do_managed else {"removed": [], "skipped": []}
    )

    # 4. Orphan-directory sweep (the GC-specific capability).
    orphans = gc_mod.sweep_orphans(repo, records, dry_run=dry)

    # 5. Orphaned launcher-shell reap (machine-wide; #102). Service-safe and
    #    idle-gated -- only pwsh/python launcher scaffolding stranded by a
    #    force-closed terminal is reaped.
    shells_grace = getattr(args, "reap_shells_grace_hours", None)
    shell_kwargs = {"dry_run": dry}
    if shells_grace is not None:
        shell_kwargs["idle_grace_secs"] = float(shells_grace) * 3600
    shells = (
        _core_helper("reap_orphan_launcher_shells", reap_orphan_launcher_shells)(**shell_kwargs)
        if do_shells
        else {"available": False, "reaped": [], "candidates": [], "skipped": [], "errors": []}
    )

    # 6. Prune stale worktree registrations.
    if not dry:
        git_ops.prune_worktrees(cwd=repo.anchor)

    # 7. Lease-ref squash (opt-in, network + force-push to the lease store).
    lease_gc = _run_lease_gc(args) if getattr(args, "lease_gc", False) else None

    if json_mode:
        payload = {
            "dry_run": dry,
            "repo": config.repo_name,
            "managed": managed,
            "orphans": orphans,
            "shells": shells,
        }
        if lease_gc is not None:
            payload["lease_gc"] = lease_gc
        if history_mirror is not None:
            payload["claim_history_mirror"] = history_mirror
        print(json.dumps(payload, indent=2))
        return 0
    if do_managed:
        _print_gc_managed(managed, dry)
    _print_gc_orphans(orphans, dry)
    if do_shells:
        _print_gc_shells(shells, dry)
    if lease_gc is not None:
        _print_gc_lease(lease_gc, dry)
    if history_mirror is not None:
        _print_gc_claim_history_mirror(history_mirror, dry)
    return 0


def _run_claim_history_mirror(args: argparse.Namespace) -> dict[str, object]:
    """Push pending claim_history events (step 7 of ``gc``), degrading to a
    reported no-op when no shared store is configured for this project --
    this sweep is opt-in and must never fail an otherwise-successful ``gc``.
    """
    from . import claim_history_mirror

    try:
        return claim_history_mirror.sync_pending(dry_run=getattr(args, "dry_run", False))
    except Exception as exc:
        # The "never fail gc" contract above must hold even for a failure
        # sync_pending() itself didn't anticipate (an import-time error, a
        # non-ConfigError failure inside settings resolution, ...) -- not
        # just the ones it already catches internally.
        return {
            "available": True, "pushed": 0, "refs": [],
            "failed": [{"ref": None, "kind": None, "error": str(exc)}],
        }


def _print_gc_claim_history_mirror(result: dict[str, object], dry: bool) -> None:
    if not result.get("available"):
        return
    refs = result.get("refs") or []
    failed = result.get("failed") or []
    if dry:
        pending = sum(int(r.get("pending", 0)) for r in refs)
        confirmed = len(refs)
        print(
            f"Would mirror {pending} pending claim-history event(s) across "
            f"{confirmed} ref(s) confirmed by a remote read."
        )
        if failed:
            print(
                f"  {len(failed)} ref(s) could not be inspected -- their true pending "
                "count is UNKNOWN, not zero:"
            )
            for entry in failed:
                print(f"  · {entry['ref']}: {entry['error']}")
        return
    pushed = result.get("pushed", 0)
    print(f"Mirrored {pushed} claim-history event(s) across {len(refs)} ref(s).")
    if failed:
        print(f"  {len(failed)} ref(s) left with events still pending (will retry next sweep):")
        for entry in failed:
            print(f"  · {entry['ref']}: {entry['error']}")


def _run_lease_gc(args: argparse.Namespace) -> dict[str, object]:
    """Squash stale released lease refs (step 6 of ``gc``), degrading to a
    reported no-op when no lease store is configured for this project --
    this sweep is opt-in and must never fail an otherwise-successful ``gc``.
    """
    from . import lease_config
    from .lease_store import GitLeaseStore

    try:
        settings = lease_config.load_lease_settings()
    except lease_config.ConfigError as exc:
        return {"available": False, "squashed": [], "error": str(exc)}
    store = GitLeaseStore(settings)
    squashed = store.squash_stale(
        retention_days=getattr(args, "lease_retention_days", 30),
        kind=getattr(args, "lease_kind", None),
        dry_run=getattr(args, "dry_run", False),
    )
    return {"available": True, "squashed": squashed}


def _print_gc_lease(lease_gc: dict[str, object], dry: bool) -> None:
    if not lease_gc.get("available"):
        return
    squashed = lease_gc.get("squashed") or []
    verb = "Would squash" if dry else "Squashed"
    if not squashed:
        print(f"{verb} 0 stale lease ref(s).")
        return
    print(f"{verb} {len(squashed)} stale lease ref(s):")
    for entry in squashed:
        print(f"  · {entry['ref']} ({entry['old_oid'][:10]})")


def cmd_sweep_managed(args: argparse.Namespace) -> int:
    payload = sweep_managed_worktrees(dry_run=getattr(args, "dry_run", False))
    if getattr(args, "json", False):
        output._json_output(payload)
        return 0
    removed = payload.get("removed") or []
    print(f"Removed {len(removed)} managed worktree(s).")
    return 0


def cmd_sweep_finished_sessions(args: argparse.Namespace) -> int:
    payload = _core_helper(
        "sweep_finished_session_worktrees",
        sweep_finished_session_worktrees,
    )(dry_run=getattr(args, "dry_run", False))
    if getattr(args, "json", False):
        output._json_output(payload)
        return 0
    removed = payload.get("removed") or []
    print(f"Removed {len(removed)} finished session worktree(s).")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# sync (fast-forward worktrees to the default branch)
# ═══════════════════════════════════════════════════════════════════════════
