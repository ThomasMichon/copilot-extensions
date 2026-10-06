"""Root-chain resolution for PR source attribution (codename mode).

A worktree's ``owner_ref`` is a BACKWARD link (see
``tracking.WorktreeRecord.owner_ref``) to the worktree that owns it as a
cross-repo resource -- e.g. a worktree in this repo spawned to do work
requested by a session in a DIFFERENT control-plane repo carries that
caller's worktree as its ``owner_ref``. Walking that chain to its ROOT
answers "which worktree really kicked off this work?" without manual
stack-unwinding (the exact pain the ``pr-attribution-codenames`` effort's
root-chain-capture slice exists to remove).

This module resolves that root PURELY in terms of each hop's own already
public-safe **codename** -- never raw machine/worktree-id/session
identifiers -- so folding a ``root=<codename>`` field into the existing
codename marker (:func:`agent_worktrees.providers.attribution.build_codename_marker`)
never widens what a public PR already exposes under ``codename`` mode. A
repo that has opted into the full raw marker doesn't need this at all: its
marker already names the immediate worktree in full.

Same-machine only for now: a chain that steps onto a different machine
degrades to ``None`` (no root annotation) rather than guessing or probing
over SSH -- unlike :mod:`agent_worktrees.claimant`, there is no live/dead
question to answer here, only a best-effort annotation, so an unresolvable
hop simply means the PR marker carries no ``root=`` field.

Kept as its own module rather than growing ``pr_ops.py`` or ``tracking.py``
further -- both are at their ``tools/check-module-size.py`` ceiling (see
``tools/module-size-baseline.json``).
"""

from __future__ import annotations

from . import config as cfg
from . import tracking

#: Defensive cap on chain depth -- protects against a corrupted/cyclic
#: owner_ref graph (hand-edited YAML, a bug elsewhere) looping forever.
_MAX_CHAIN_DEPTH = 32


def _load_local_record(
    project: str | None, worktree_id: str,
) -> tracking.WorktreeRecord | None:
    """Load a same-machine worktree record by ``(project, worktree_id)``.

    Returns ``None`` if the project/tracking dir can't be resolved, the
    record file is absent, or it fails to parse -- every failure mode
    degrades to "chain unresolved here", never an exception.
    """
    try:
        path = cfg.project_dir(project) / "worktrees" / f"{worktree_id}.yaml"
    except Exception:
        return None
    if not path.exists():
        return None
    try:
        return tracking.load_record(path)
    except Exception:
        return None


def _load_root_config(root_project: str | None):
    """Load *root_project*'s own layered config, or ``None`` on any failure."""
    try:
        return (
            cfg.load_project_config(root_project)
            if root_project else cfg.load_config()
        )
    except Exception:
        return None


def _ensure_publishable_root_codename(
    root_record: tracking.WorktreeRecord,
    root_project: str | None,
    *,
    ensure: bool,
) -> str | None:
    """Return *root_record*'s own codename, backfilling + gating it with
    ITS OWN project's config/wordlist/allocation-policy -- never the
    caller's. Returns ``None`` when the codename is missing and ``ensure``
    is False, when backfill fails (best-effort: this is an annotation, not
    the primary PR-open policy gate -- a
    ``codename_tracking.CodenameAttributionPolicyError`` here is swallowed,
    unlike at the primary allocation site), or when the root's own
    provenance/explicit-opt-in check says it is not safe to publish.
    """
    from . import codename as codename_mod
    from . import codename_tracking
    from .providers import attribution as attr

    if not root_record.codename and ensure:
        root_config = _load_root_config(root_project)
        if root_config is not None:
            try:
                codename_tracking.ensure_codename(
                    root_record,
                    cfg.tracking_dir(root_project),
                    codename_tracking.wordlist_for_repo(root_config),
                    **codename_tracking.allocation_policy_kwargs_for_repo(
                        root_config
                    ),
                )
            except Exception:
                pass

    codename = root_record.codename
    if not isinstance(codename, str) or not codename_mod.is_valid_handle(codename):
        return None

    root_config = _load_root_config(root_project)
    explicit = bool(
        codename_tracking.allocation_policy_kwargs_for_repo(root_config)[
            "source_attribution_configured"
        ]
    ) if root_config is not None else False
    if not attr.may_publish_codename(
        codename_source=root_record.codename_source,
        source_attribution_configured=explicit,
    ):
        return None
    return codename


def resolve_root_codename(
    record: tracking.WorktreeRecord,
    *,
    project: str | None = None,
    this_machine: str | None = None,
    ensure: bool = True,
) -> str | None:
    """Walk *record*'s ``owner_ref`` chain to its root, returning the root
    worktree's own public-safe codename -- or ``None`` when there is no
    chain (no ``owner_ref`` at all), the chain steps onto a different
    machine, the chain is cyclic/exceeds the depth cap, an intermediate
    owner record can't be loaded, or the resolved root's own codename can't
    be determined/isn't safe to publish.

    *project* is *record*'s own project identity (defaults to the active
    project when omitted) -- used only to resolve a BARE (same-repo)
    ``owner_ref`` at the first hop; each subsequent bare hop resolves within
    its own immediate parent's project, never the caller's.

    Never returns *record*'s own codename: a childless/root worktree has no
    chain worth annotating -- the caller's existing ``codename=`` marker
    field already names it.
    """
    if this_machine is None:
        try:
            this_machine = cfg.load_config().machine
        except Exception:
            this_machine = None
    if project is None:
        try:
            project = cfg.project_name()
        except Exception:
            project = None

    current = record
    current_project = project
    seen: set[tuple[str | None, str]] = set()
    for _ in range(_MAX_CHAIN_DEPTH):
        parsed = current.owner_claim_ref
        if parsed is None:
            break
        if parsed.machine and this_machine and parsed.machine != this_machine:
            return None  # chain leaves this machine; not resolvable here
        parent_project = parsed.project or current_project
        key = (parent_project, parsed.worktree_id)
        if key in seen:
            return None  # cyclic owner graph; refuse to loop
        seen.add(key)
        parent = _load_local_record(parent_project, parsed.worktree_id)
        if parent is None:
            return None  # owner record gone/unreadable; chain unresolved
        current = parent
        current_project = parent_project
    else:
        return None  # exceeded depth cap; treat as unresolved

    if current is record:
        return None  # no chain at all -- nothing to annotate

    return _ensure_publishable_root_codename(current, current_project, ensure=ensure)


def root_codename_for_marker(record: tracking.WorktreeRecord, config) -> str | None:
    """``resolve_root_codename`` convenience wrapper for a PR-marker publish
    site: resolves *record*'s own repo project name off *config* and
    swallows any error -- a best-effort annotation must never block or alter
    whether the PRIMARY codename marker publishes.
    """
    try:
        repo_name = getattr(config.default_repo, "repo_name", None) or None
        return resolve_root_codename(record, project=repo_name)
    except Exception:
        return None


def build_codename_marker_with_root(
    codename: str, record: tracking.WorktreeRecord, config,
) -> str:
    """One-call convenience for a PR-marker publish site: builds the
    ``codename`` marker augmented with a best-effort ``root=<codename>``
    field from :func:`root_codename_for_marker`. Never raises -- a
    resolution failure just omits the ``root`` field, matching the marker
    shape from before this field existed.
    """
    from .providers import attribution as attr

    return attr.build_codename_marker(
        codename, root=root_codename_for_marker(record, config),
    )


def compose_codename_body(
    body: str | None,
    codename: str | None,
    marker_published: bool,
    record: tracking.WorktreeRecord | None,
    config,
) -> str:
    """Compose a PR body's ``codename``-mode source marker in one call.

    Collapses "build+append the (root-augmented) codename marker when
    publishable, else strip any stale marker" into a single call, so a
    publish-site caller (``pr_ops._open_via_provider``) needs no local
    multi-line ternary. Never leaves a stale/caller-supplied source marker
    in place when publication is skipped -- it could still carry raw
    identifiers from some other source (a copy-pasted body, an older
    template). *record* may be ``None`` (an untracked worktree): publication
    is then never requested by the caller, but this degrades safely either
    way by treating it the same as "no root to resolve".
    """
    from .providers import attribution as attr

    if not marker_published:
        return attr.strip_marker(body or "")
    root = root_codename_for_marker(record, config) if record is not None else None
    return attr.append_marker(body or "", attr.build_codename_marker(codename, root=root))
