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

import json
import logging
import re
import shutil
import subprocess
import sys

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

#: A full GitHub PR URL, e.g. ``https://github.com/owner/repo/pull/123``.
_GH_PR_URL = re.compile(
    r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)/?$", re.IGNORECASE)
#: The repo-qualified GitHub shorthand ``owner/repo#123`` (GitHub-only; ADO refs
#: are full URLs and never match this).
_GH_PR_SHORT = re.compile(r"^([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(\d+)$")

#: A classic ADO PR URL on the ``<org>.visualstudio.com`` host, e.g.
#: ``https://onedrive.visualstudio.com/ODSP-Web/_git/odsp-web/pullrequest/2285417``
#: (group 1 = ``<org>.visualstudio.com`` host, group 2 = PR id).
_ADO_PR_VSTS = re.compile(
    r"^https?://([A-Za-z0-9._-]+\.visualstudio\.com)/.+/pullrequest/(\d+)/?$",
    re.IGNORECASE)
#: The modern ADO PR URL on ``dev.azure.com/<org>``, e.g.
#: ``https://dev.azure.com/onedrive/ODSP-Web/_git/odsp-web/pullrequest/2285417``
#: (group 1 = ``<org>`` slug, group 2 = PR id).
_ADO_PR_DEVAZURE = re.compile(
    r"^https?://dev\.azure\.com/([A-Za-z0-9._-]+)/.+/pullrequest/(\d+)/?$",
    re.IGNORECASE)


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


def _github_pr_view_args(ref: str) -> list[str] | None:
    """Map a **GitHub** PR ref to ``gh pr view`` args, or ``None`` if not GitHub.

    Accepts a full URL (``https://github.com/owner/repo/pull/N`` -> ``[url]``,
    which ``gh`` resolves directly) or the repo-qualified shorthand
    ``owner/repo#N`` (-> ``[N, --repo, owner/repo]``). An **ADO** URL
    (``*.visualstudio.com`` / ``dev.azure.com``), a bare number, or any other
    shape yields ``None`` -- the sweep spares it (ADO is Phase 2; a bare number is
    ambiguous without a repo).
    """
    ref = (ref or "").strip()
    m = _GH_PR_URL.match(ref)
    if m:
        return [ref]
    m = _GH_PR_SHORT.match(ref)
    if m:
        return [m.group(2), "--repo", m.group(1)]
    return None


def _github_pr_merged(ref: str) -> bool | None:
    """Is a **GitHub** PR claim's PR provably **MERGED**? (tri-state).

    Shells ``gh pr view <ref> --json state`` and returns ``True`` only on a
    definitive ``MERGED`` state; an ``OPEN`` PR (still owed), a ``CLOSED`` but
    unmerged PR (abandoned work -- never silently reclaimed), a non-GitHub ref, a
    missing ``gh``, an auth/visibility failure (e.g. the ambient account can't
    see the repo), or any error -> ``None`` (spare). Never raises.
    """
    args = _github_pr_view_args(ref)
    if args is None:
        return None
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        proc = subprocess.run(
            [gh, "pr", "view", *args, "--json", "state"],
            capture_output=True, text=True, timeout=30,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32"
                           else 0),
        )
    except Exception as exc:
        log.debug("gh pr view for %s degraded: %s", ref, exc)
        return None
    if proc.returncode != 0:
        return None
    try:
        state = json.loads(proc.stdout).get("state")
    except (ValueError, TypeError):
        return None
    return True if state == "MERGED" else None


def _ado_pr_view_args(ref: str) -> list[str] | None:
    """Map an **ADO** PR ref to ``az repos pr show`` args, or ``None`` if not ADO.

    Recognizes the classic ``https://<org>.visualstudio.com/.../pullrequest/N``
    and modern ``https://dev.azure.com/<org>/.../pullrequest/N`` URL shapes (the
    odsp-web case). Returns ``["--id", N, "--org", <org-url>]`` -- the org URL
    ``az`` needs to resolve the org-wide-unique PR id (the repo/project in the
    URL is not required by ``az repos pr show``). A GitHub ref, a bare number, or
    any other shape yields ``None`` (spare).
    """
    ref = (ref or "").strip()
    m = _ADO_PR_VSTS.match(ref)
    if m:
        return ["--id", m.group(2), "--org", f"https://{m.group(1)}/"]
    m = _ADO_PR_DEVAZURE.match(ref)
    if m:
        return ["--id", m.group(2), "--org",
                f"https://dev.azure.com/{m.group(1)}/"]
    return None


def _ado_pr_merged(ref: str) -> bool | None:
    """Is an **ADO** PR claim's PR provably **completed** (merged)? (tri-state).

    Shells ``az repos pr show --id N --org <org> --query status -o tsv`` (the
    azure-devops extension) and returns ``True`` only on a definitive
    ``completed`` status; an ``active`` PR (still owed), an ``abandoned`` PR
    (closed unmerged -- never silently reclaimed), a non-ADO ref, a missing
    ``az`` / azure-devops extension, an auth/visibility failure, or any error ->
    ``None`` (spare). Never raises. The ADO analog of :func:`_github_pr_merged`
    -- ``az`` reaches ADO under the operator's ambient ``az login`` (best-effort;
    unauthenticated -> nonzero exit -> spare), keeping the sweep dependency-free
    beyond the CLI the harness already uses for ADO. We project to just the
    ``status`` field (not the full ``-o json`` payload) so a PR title/description
    carrying non-UTF-8 (cp1252) bytes can't crash decoding into a false spare;
    ``errors="replace"`` hardens it further.
    """
    args = _ado_pr_view_args(ref)
    if args is None:
        return None
    az = shutil.which("az")
    if not az:
        return None
    try:
        proc = subprocess.run(
            [az, "repos", "pr", "show", *args, "--query", "status", "-o", "tsv"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=45,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32"
                           else 0),
        )
    except Exception as exc:
        log.debug("az repos pr show for %s degraded: %s", ref, exc)
        return None
    if proc.returncode != 0:
        return None
    return True if (proc.stdout or "").strip() == "completed" else None


def pr_merged(ref: str) -> bool | None:
    """Is a PR claim's PR provably **merged**? (tri-state) -- GitHub **or** ADO.

    Dispatches by ref shape: a GitHub ref (full ``github.com`` URL or the
    ``owner/repo#N`` shorthand) -> :func:`_github_pr_merged` (``gh pr view``); an
    ADO PR URL (``*.visualstudio.com`` / ``dev.azure.com``) ->
    :func:`_ado_pr_merged` (``az repos pr show``). An unrecognized ref (bare
    number / junk), a missing tool, an auth/visibility failure, or any error ->
    ``None`` (spare). Never raises. This is the ``pr``-kind analog of the
    worktree branch-merged check: a merged PR is provably safe AND its obligation
    discharged, so a stale (manually-journaled, never-settled) cross-repo PR
    claim -- GitHub or the real-world **odsp-web ADO** case -- auto-reclaims once
    the PR lands.
    """
    if _github_pr_view_args(ref) is not None:
        return _github_pr_merged(ref)
    if _ado_pr_view_args(ref) is not None:
        return _ado_pr_merged(ref)
    return None


def claim_gone(claim: tracking.ResourceClaim, config: cfg.Config) -> bool | None:
    """Tri-state **gone** verdict, dispatched by claim kind.

    A ``worktree`` claim reuses the same-machine claimant-liveness resolver
    (:func:`gone_of`). A leaseable kind (codespace/container) is *gone* when its
    lease mirror shows the obligation settled (:func:`leaseable_settled`). A
    ``pr`` claim is *gone* when its PR (GitHub **or** ADO) is provably merged
    (:func:`pr_merged`). Every other kind is ``None`` (spare).
    """
    if claim.kind == "worktree":
        return gone_of(claim.ref)
    if claim.kind in _LEASEABLE_KINDS:
        return leaseable_settled(claim, config)
    if claim.kind == "pr":
        return pr_merged(claim.ref)
    return None


def claim_safe(claim: tracking.ResourceClaim, config: cfg.Config) -> bool | None:
    """Tri-state **safe** verdict, dispatched by claim kind.

    A ``worktree`` claim proves safety from the child's record/branch
    (:func:`safe_of`). A leaseable kind proves it from the lease's settled
    disposition mirror (:func:`leaseable_settled`). A ``pr`` claim proves it from
    a provably-merged PR (:func:`pr_merged`). Every other kind is ``None``.
    """
    if claim.kind == "worktree":
        return safe_of(claim, config)
    if claim.kind in _LEASEABLE_KINDS:
        return leaseable_settled(claim, config)
    if claim.kind == "pr":
        return pr_merged(claim.ref)
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
