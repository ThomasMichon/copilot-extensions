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
from . import git_ops, obligations, tracking

log = logging.getLogger(__name__)

#: Resource kinds whose obligation disposition is mirrored onto a **cross-machine
#: lease** (the lease's diagnostic ``context`` under ``obligations.CONTEXT_KEY``),
#: rather than provable from a local worktree record. The sweep reads that
#: mirror generically -- it never learns any single resource plugin's internals,
#: only the shared obligation vocabulary + the lease store. agent-codespaces
#: populates it for ``codespace`` (at clean disconnect -> ``at-rest``, at release
#: -> ``released``); ``container`` is reserved for agent-containers.
_LEASEABLE_KINDS: frozenset[str] = frozenset({"codespace", "container"})


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


def lease_disposition_of(
    kind: str, ref: str, config: cfg.Config,
) -> obligations.Disposition | None:
    """Best-effort read of a leaseable resource's **mirrored disposition**.

    A leaseable resource (a CodeSpace, a container) carries its obligation
    disposition on its **cross-machine lease** -- the lease record's diagnostic
    ``context`` under :data:`obligations.CONTEXT_KEY`, populated by the owning
    plugin when it settles/releases (agent-codespaces at clean disconnect ->
    ``at-rest``, at release -> ``released``). The sweep reads it generically
    (``obligations.from_context``) so it can reclaim a stale obligation whose
    resource is provably settled -- both the **missed-settle** case (the local
    ledger never got flipped) and the **cross-machine** case (the box was
    settled from another machine; this machine's stale local claim reads the
    shared lease as the source of truth).

    Fully **degrade-safe**: an unconfigured store, an absent lease, a network
    failure, or any error -> ``None`` (spare; the sweep never abandons on an
    unreadable mirror). A present lease with no/``active`` disposition normalizes
    to ``active`` -> also spare.
    """
    try:
        from . import lease_config, lease_store
        settings = lease_config.load_lease_settings()
        snapshot = lease_store.GitLeaseStore(settings).inspect(kind, ref)
    except Exception as exc:  # unconfigured / network / protocol -> spare
        log.debug("lease disposition read for %s/%s degraded: %s", kind, ref, exc)
        return None
    if snapshot is None:
        return None  # absent lease -> unproven (spare); release tombstones it
    return obligations.from_context(snapshot.record.context)


def leaseable_settled(
    claim: tracking.ResourceClaim, config: cfg.Config,
) -> bool | None:
    """Is a leaseable claim's resource **provably settled** via its lease mirror?

    Returns ``True`` only when the mirrored disposition is a settled value
    (``at-rest`` / ``released`` / ``abandoned`` -- the work is off-box-safe and
    the obligation's liability is discharged); otherwise ``None`` (spare -- an
    ``active``, absent, or unreadable mirror is never reclaimed on a guess). This
    single settled/unsettled verdict drives **both** the gone and safe tri-states
    for a leaseable kind: a settled lease means the resource is safe AND the
    obligation is no longer a live liability.
    """
    disposition = lease_disposition_of(claim.kind, claim.ref, config)
    if disposition in (obligations.AT_REST, obligations.RELEASED,
                       obligations.ABANDONED):
        return True
    return None


def claim_gone(claim: tracking.ResourceClaim, config: cfg.Config) -> bool | None:
    """Tri-state **gone** verdict, dispatched by claim kind.

    A ``worktree`` claim reuses the same-machine claimant-liveness resolver
    (:func:`gone_of`). A leaseable kind (codespace/container) is *gone* when its
    lease mirror shows the obligation settled (:func:`leaseable_settled`) -- the
    resource is no longer a live liability. Every other kind is ``None`` (spare).
    """
    if claim.kind == "worktree":
        return gone_of(claim.ref)
    if claim.kind in _LEASEABLE_KINDS:
        return leaseable_settled(claim, config)
    return None


def claim_safe(claim: tracking.ResourceClaim, config: cfg.Config) -> bool | None:
    """Tri-state **safe** verdict, dispatched by claim kind.

    A ``worktree`` claim proves safety from the child's record/branch
    (:func:`safe_of`). A leaseable kind proves it from the lease's settled
    disposition mirror (:func:`leaseable_settled`). Every other kind is ``None``.
    """
    if claim.kind == "worktree":
        return safe_of(claim, config)
    if claim.kind in _LEASEABLE_KINDS:
        return leaseable_settled(claim, config)
    return None


def make_resolvers(config: cfg.Config):
    """Return the ``(gone_of, safe_of)`` pair for :func:`tracking.sweep_abandoned_obligations`.

    Both resolvers now take the **claim** (not just its ref) and are bound to
    ``config`` -- the gone verdict needs the claim's *kind* to route a leaseable
    resource to its lease disposition mirror rather than the worktree-liveness
    resolver.
    """
    return (lambda claim: claim_gone(claim, config),
            lambda claim: claim_safe(claim, config))


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
