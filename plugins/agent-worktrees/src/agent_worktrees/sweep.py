"""Never-wedge obligation-reclaim resolvers (resource-obligation-settlement Ph4).

The **gone** / **safe** verdicts the reclaim sweep injects into
:func:`tracking.sweep_abandoned_obligations`, extracted here so the manual
``agent-worktrees claims sweep`` verb **and** finalize's obligation gate (a
self-heal, dotfiles#1161) share **one** conservative implementation instead of
duplicating it across a module boundary (``finalize`` cannot import
``__main__``).

Everything is **same-machine only** and **conservative** -- every unknown is
spare, and the sweep only ever flips an ``active`` claim to ``abandoned`` on a
*definitive* gone-AND-safe verdict (:func:`obligations.should_abandon`). A
cross-machine child is deferred to the lease mirror (``None``). No verdict is
ever fabricated; nothing with real work is abandoned on a guess.

(Distinct from :mod:`agent_worktrees.reclaim`, which reaps stray *session
processes* -- this module reclaims *ledger obligations*.)
"""
from __future__ import annotations

import logging

from . import claimant as _claimant
from . import config as cfg
from . import git_ops, tracking

log = logging.getLogger(__name__)


def load_claim_child_record(
    ref: str, config: cfg.Config,
) -> tuple[tracking.WorktreeRecord | None, bool]:
    """Load the tracking record for a worktree-claim's child ref (same-machine).

    Returns ``(record_or_None, judgeable)``. ``judgeable`` is False for a
    cross-machine child (this machine cannot see it -- the sweep must defer to
    the lease mirror). A same-machine child that has no local record yields
    ``(None, True)`` -- judgeable, and gone.
    """
    parsed = tracking.parse_claim_ref(ref)
    if parsed is None:
        return (None, False)
    if parsed.machine and parsed.machine != config.machine:
        return (None, False)  # cross-machine -> not judgeable here
    if parsed.machine and parsed.project:
        path = (cfg.project_dir(parsed.project) / "worktrees"
                / f"{parsed.worktree_id}.yaml")
    else:  # bare/same-repo ref
        path = cfg.tracking_dir() / f"{parsed.worktree_id}.yaml"
    if not path.exists():
        return (None, True)
    try:
        return (tracking.load_record(path), True)
    except Exception:
        return (None, False)


def repo_for_project(
    project: str | None, config: cfg.Config,
) -> cfg.RepoConfig | None:
    """Resolve the :class:`RepoConfig` for a claim child's project (same-machine).

    Returns the repo from ``config.repos`` when the project is known there, or
    the active ``default_repo`` for a bare/same-project ref; ``None`` when the
    child's repo cannot be resolved from the current context (so the caller
    spares rather than guesses). Fully best-effort.
    """
    try:
        repos = getattr(config, "repos", None) or {}
        if project and project in repos:
            return repos[project]
        if not project or project == getattr(config, "repo_name", None):
            return config.default_repo
    except Exception:
        return None
    return None


def child_branch_merged(
    child: tracking.WorktreeRecord, ref: str, config: cfg.Config,
) -> bool | None:
    """Prove a gone, non-terminal child's work is SAFE via a branch-merged check.

    The crashed-holder case (dotfiles#1161): a child whose record is present but
    whose dir was removed while its status is still ``active``/``pushed`` -- its
    ``finalize`` never ran, so the ``finalized``-status shortcut in the sweep
    can't clear it, and it wedges the owner's ``block``-mode finalize forever.
    Prove it safe **only** when its feature branch's content already landed on
    its project upstream (a crash *after* the work merged), reusing finalize's
    squash-aware :func:`finalize._is_content_on_upstream`.

    Returns ``True`` on a positive merge proof, else ``None`` (spare -- never
    ``False``: a non-merged, branch-gone, or unresolvable child is left for a
    human, never abandoned on a guess). Same-machine + resolvable-repo only, and
    checked against the LOCAL ``origin/<default>`` (no fetch), so a stale anchor
    spares rather than misjudges.
    """
    parsed = tracking.parse_claim_ref(ref)
    if parsed is None:
        return None
    repo = repo_for_project(parsed.project, config)
    if repo is None:
        return None
    branch = None
    pr = getattr(child, "pr", None)
    if pr is not None and getattr(pr, "branch", None):
        branch = pr.branch
    if not branch:
        branch = f"worktree/{child.worktree_id}"
    upstream = f"{repo.remote}/{repo.default_branch}"
    try:
        verify = git_ops.git(
            "rev-parse", "--verify", "--quiet", branch,
            cwd=repo.anchor, check=False,
        )
        if verify.returncode != 0:
            return None  # branch ref gone -> can't prove -> spare
        from . import finalize as _finalize
        if _finalize._is_content_on_upstream(branch, upstream, cwd=repo.anchor):
            return True
    except Exception:
        return None
    return None


def gone_of(ref: str) -> bool | None:
    """Is the claim's holder **provably gone** on this machine? (tri-state).

    Reuses the same-machine claimant-liveness resolver: record absent / terminal
    status / worktree dir removed -> gone (``True``); still present -> ``False``;
    cross-machine or unresolvable -> ``None`` (spare).
    """
    alive = _claimant.local_claimant_alive(ref)
    return None if alive is None else (not alive)


def safe_of(claim: tracking.ResourceClaim, config: cfg.Config) -> bool | None:
    """Is the claim's resource **provably safe**? (tri-state, conservative).

    Only a ``worktree`` child is provable within agent-worktrees. A ``finalized``
    child is safe (its finalize verified content upstream); an ``orphaned`` child
    (push failed) is unsafe; a gone, non-terminal child is proven safe only when
    its branch content already landed upstream (:func:`child_branch_merged`).
    Everything else is ``None`` (spare).
    """
    if claim.kind != "worktree":
        return None
    child, judgeable = load_claim_child_record(claim.ref, config)
    if not judgeable or child is None:
        return None
    if child.status == "finalized":
        return True
    if child.status == "orphaned":
        return False
    return child_branch_merged(child, claim.ref, config)


def make_resolvers(config: cfg.Config):
    """Return the ``(gone_of, safe_of)`` pair for :func:`tracking.sweep_abandoned_obligations`.

    ``safe_of`` is bound to ``config`` (the sweep passes only the claim).
    """
    return gone_of, (lambda claim: safe_of(claim, config))


def self_heal(
    record: tracking.WorktreeRecord,
    config: cfg.Config,
    *,
    path=None,
    save: bool = True,
) -> list[tracking.ResourceClaim]:
    """Reclaim ``record``'s **own** blocking claims that are provably gone+safe.

    The never-wedge sweep applied to a single owner -- used by finalize's
    obligation gate to self-heal against a crashed/missed settlement before it
    would block. Returns the claims it flipped to ``abandoned`` (empty on a
    no-op). Conservative: only definitive gone-AND-safe claims flip; unknown is
    spare.
    """
    g, s = make_resolvers(config)
    return tracking.sweep_abandoned_obligations(
        record, gone_of=g, safe_of=s, save=save, path=path,
    )
