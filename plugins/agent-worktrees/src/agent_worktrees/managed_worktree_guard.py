"""Removal-safety guard for managed (``system``/``bridge``) worktrees.

``remove-system`` used to unconditionally force-remove a managed worktree --
no check for uncommitted changes, unmerged/unpushed branch content, an open
PR, or a live outbound resource claim. This module is the guard that closes
that gap, split out from ``__main__.py`` to keep this repo's per-module
line-count cap honest (see ``tools/check-module-size.py``) rather than
growing an already very large file further.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import finalize as fin
from . import git_ops, tracking


def _resolve_repo(config, record):
    """Resolve a record's repository with the legacy/default fallback.

    A small local duplicate of ``__main__._repo_for_record`` -- kept here
    (rather than imported) to avoid a circular import back into
    ``__main__``, which imports this module.
    """
    repos = getattr(config, "repos", {})
    record_repo = getattr(record, "repo", "")
    repo = repos.get(record_repo) if hasattr(repos, "get") else None
    if repo is None and (not record_repo or record_repo == getattr(config, "repo_name", None)):
        try:
            repo = config.default_repo
        except (AttributeError, KeyError, ValueError):
            repo = None
    return repo or config.default_repo


def blockers_for(rec, repo) -> list[str]:
    """Reasons a managed (system/bridge) worktree is not safe to discard.

    Mirrors the checks ``cleanup`` already applies to ordinary worktrees --
    uncommitted changes and unpushed commits -- plus two checks
    ``cleanup``/``finalize`` apply that ``remove-system`` historically
    skipped for *managed* worktrees entirely: an open PR and a still-live
    outbound resource claim. A managed worktree is exactly as capable of
    holding real, never-landed content as an ordinary one -- ``kind`` alone
    is not evidence the worktree is disposable, and neither is a missing
    checkout directory: the branch ref (and any unpushed commits on it)
    lives in the shared repo regardless of whether the working directory
    still exists, so the merge check below runs unconditionally.
    """
    blockers: list[str] = []
    checkout_exists = bool(rec.worktree_path) and Path(rec.worktree_path).exists()
    if checkout_exists:
        info = git_ops.classify_worktree(
            rec.worktree_path,
            rec.branch,
            fetch=False,
            remote=repo.remote,
            default_branch=repo.default_branch,
        )
        if info.dirty:
            blockers.append(
                f"{info.dirty} uncommitted change(s) in the working tree"
            )
        elif info.state == git_ops.WorktreeState.UNKNOWN:
            # classify_worktree only returns UNKNOWN when its own git calls
            # timed out -- an honest "couldn't tell", not "clean". Treat it
            # as a blocker rather than letting a stalled probe fall through
            # to forced removal unclassified.
            blockers.append(
                "could not classify the working tree's git state (probe timed out)"
            )
        if info.branch_drift:
            # The checkout was switched to a different branch than the
            # tracked one (e.g. an unpushed feature branch). The merge check
            # below only looks at rec.branch -- if the drifted branch itself
            # carries unmerged commits, forced removal would silently
            # discard them. Refuse until the drift is reconciled.
            blockers.append(
                f"checkout is on branch {info.current_branch!r}, not the "
                f"tracked {rec.branch!r} -- reconcile the drift before removing"
            )
    if rec.branch:
        # Run against the anchor's shared repo, not the (possibly-missing)
        # checkout -- the branch ref and its commits outlive the working
        # directory, so a missing checkout must not silently skip this.
        # Squash-aware (tree/patch-id comparison, not commit-SHA identity):
        # a worktree branch intentionally keeps its pre-squash commits after
        # its content is squash-merged upstream, so a raw SHA-ancestry check
        # would refuse every already-landed worktree forever.
        upstream = f"{repo.remote}/{repo.default_branch}"
        if not git_ops.is_branch_merged(rec.branch, upstream, cwd=repo.anchor):
            where = "" if checkout_exists else " (checkout directory is missing)"
            blockers.append(
                f"{rec.branch} has content not merged into {upstream}{where}"
            )
    # A live PR is `has_live_pr()`'s notion of non-terminal -- open, creating,
    # AND the empty ("" -- not yet populated) state, matching the predicate
    # cleanup/gc already use. An unpopulated-but-real PR record is exactly as
    # much a recovery source as an open one; only skip terminal (merged/
    # closed) PRs.
    if rec.has_live_pr():
        live_prs = [p for p in rec.prs if p.state in tracking._PR_NON_TERMINAL]
        blockers.append(
            "in-progress or open PR(s): "
            + ", ".join(
                p.url or (f"#{p.number}" if p.number else "(unopened)")
                for p in live_prs
            )
        )
    live_claims = rec.live_resources
    if live_claims:
        blockers.append(
            "unsettled outbound resource claim(s): "
            + ", ".join(f"{c.kind}:{c.ref}" for c in live_claims if c.ref)
        )
    if rec.kind == "bridge" and blockers:
        blockers.append(
            "this is a bridge-owned worktree -- its owning agent-bridge session "
            "may have its own cleanup/teardown path (e.g. `agent-bridge stop`/"
            "`end`); check that before discarding the worktree out from under it"
        )
    return blockers


def refusal_message(
    wt_id: str, blockers: list[str], *, appeared_after_initial_check: bool = False
) -> str:
    """Format the refusal message ``remove-system`` reports for ``wt_id``."""
    joined = "; ".join(blockers)
    if appeared_after_initial_check:
        return (
            f"refusing to remove system worktree {wt_id}: {joined} "
            "(appeared after the initial check). Resolve this state first, "
            "or pass --force to discard it anyway."
        )
    return (
        f"refusing to remove system worktree {wt_id}: {joined}. Resolve this "
        "state first (commit+push+land the work, close/merge the PR, settle "
        "the claim), or pass --force to discard it anyway once you've "
        "confirmed that's correct."
    )


@dataclasses.dataclass
class GuardedRemoval:
    """Outcome of :func:`perform`."""

    ok: bool
    message: str = ""
    removed: bool = False
    warnings: list[str] = dataclasses.field(default_factory=list)
    rec: object = None


def perform(wt_id, yaml_path, config, *, force, remove_fn, tracking_path):
    """Run the full guarded remove-system flow.

    Checks blockers (unless ``force``), then serializes against other
    finalize/gc/remove-system callers with the same repo-wide lock the
    managed gc sweep uses, reloads the tracking record fresh, and rechecks
    blockers immediately before removing -- closing the gap between an
    initial check and the actual removal, where a claim/PR/dirty change
    could otherwise land unnoticed. ``remove_fn`` performs the actual git +
    tracking-record teardown (``__main__._remove_managed_worktree``,
    injected to avoid a circular import back into ``__main__``).
    """
    rec = tracking.load_record(yaml_path)
    repo = _resolve_repo(config, rec)
    if not force:
        blockers = blockers_for(rec, repo)
        if blockers:
            return GuardedRemoval(ok=False, message=refusal_message(wt_id, blockers))

    lifecycle_lock = fin.FinalizeLock(
        Path(repo.worktree_root) / ".finalize.lock", timeout=3, stale_after=3600,
    )
    try:
        lifecycle_lock.acquire()
    except TimeoutError:
        return GuardedRemoval(
            ok=False,
            message=f"could not acquire the repo lifecycle lock for {wt_id}; try again",
        )
    try:
        rec = tracking.load_record(yaml_path)
        repo = _resolve_repo(config, rec)
        if not force:
            blockers = blockers_for(rec, repo)
            if blockers:
                return GuardedRemoval(
                    ok=False,
                    message=refusal_message(
                        wt_id, blockers, appeared_after_initial_check=True
                    ),
                )
        removed, warnings = remove_fn(rec, repo, tracking_path, force=True)
    finally:
        lifecycle_lock.release()
    return GuardedRemoval(ok=True, removed=removed, warnings=warnings, rec=rec)
