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

import json

from . import config as cfg
from . import tracking

#: Defensive cap on chain depth -- protects against a corrupted/cyclic
#: owner_ref graph (hand-edited YAML, a bug elsewhere) looping forever. A
#: chain of up to this many ANCESTORS is accepted; only a longer one is
#: rejected.
_MAX_CHAIN_DEPTH = 32

#: Sidecar suffix for a per-worktree frozen root-codename decision (see
#: ``_load_frozen_root``/``_freeze_root``). Kept alongside the worktree's
#: own tracking YAML, never inside it -- this is a NEW, independently-added
#: concern that doesn't need ``tracking.py``'s own schema/migration rigor
#: (that module is at its line-count ceiling; see module docstring).
_FREEZE_SUFFIX = ".root-attribution.json"


def _is_safe_path_component(value: str | None) -> bool:
    """True when *value* is safe to join as a single filesystem path
    segment -- no path separators, no ``..``/``.`` traversal, no NUL.
    ``project``/``worktree_id`` fields of a parsed
    :class:`~agent_worktrees.tracking_claims.ClaimRef` are sourced from
    persisted YAML and are **not** validated by ``parse_claim_ref`` itself
    (a three-or-more-component ref is simply accepted as qualified) -- a
    corrupted or hand-edited ``owner_ref`` must be rejected here before
    ever being joined into a real path, or evaluated under the WRONG
    project's provenance policy. Same guard as
    ``claims_transitive_cli._is_safe_path_component`` (duplicated rather
    than imported -- a small, private, path-safety helper each module owns
    independently).
    """
    if not value or "\x00" in value:
        return False
    if "/" in value or "\\" in value:
        return False
    return value not in (".", "..")


def _freeze_path(project: str | None, worktree_id: str):
    return cfg.tracking_dir(project) / f"{worktree_id}{_FREEZE_SUFFIX}"


def _freeze_identity(record: tracking.WorktreeRecord) -> tuple[str, str] | None:
    """The ``(owner_ref, creation_nonce)`` pair a frozen decision is bound
    to, or ``None`` when *record* predates ``creation_nonce`` (never
    backfilled -- such a record simply can't participate in freezing; every
    call recomputes live, which is always safe, just less efficient).

    ``owner_ref`` is NOT actually immutable for a worktree's whole life --
    accepting a worktree claim handoff rewrites it (``claim_handoffs.py``).
    ``creation_nonce`` is a random token stamped ONCE, at actual creation,
    by ``create_new_record`` -- unlike ``started_at`` (only second-
    precision, so a same-second reap+recreate of the same id could collide),
    this uniquely identifies THIS incarnation of the worktree id. A frozen
    decision is therefore valid only while BOTH still match the record's
    current values -- a handoff or an id-reuse after a hard delete each
    invalidate it, causing a fresh (consistent) resolution under whatever
    is current, rather than silently publishing a stale root forever or
    leaking a predecessor's decision into a reused id. This doubles as the
    lifecycle-cleanup fix a dedicated sidecar-removal hook at every
    tracking-record deletion call site would otherwise need
    (``handoff_trace.remove_trace``'s own contract) -- self-invalidating
    beats having to wire into ``retire_record``/``reap_cli``/the cleanup
    command individually, and needs no changes to any of those (two of
    which are at their own line-count ceiling).
    """
    if not record.creation_nonce:
        return None
    return (record.owner_ref or "", record.creation_nonce)


def _load_frozen_root(
    project: str | None, record: tracking.WorktreeRecord,
) -> tuple[bool, str | None]:
    """Return ``(frozen, codename)``: ``frozen`` is True only when a prior
    resolution was persisted AND is still bound to *record*'s CURRENT
    ``_freeze_identity`` (``codename`` may legitimately be ``None`` --
    "frozen: this worktree has no publishable root"). ``(False, None)``
    means never resolved/frozen yet, or the stored identity no longer
    matches (a handoff rewrote ``owner_ref``, or this id was reaped and
    recreated) -- a fresh walk is needed either way.

    Validates the EXACT sidecar schema before accepting a ``None``: a
    missing ``root_codename`` key (an empty/truncated/malformed object) is
    NOT the same as an explicit JSON ``null`` and must not be silently
    accepted as an intentional "frozen: no root" decision -- ``dict.get``
    alone can't tell those apart, so the key's presence is checked first.
    A non-``None`` value is further validated with the same
    :func:`agent_worktrees.codename.is_valid_handle` gate every OTHER
    codename interpolated into the marker goes through: this sidecar is a
    plain JSON file, not a validated tracking record, and a malformed or
    hand-edited one must never publish arbitrary text (including a stray
    ``-->``) straight into the HTML comment. Any invalid/ambiguous shape
    degrades to "never frozen" so the normal provenance path re-runs and
    re-freezes a clean result.
    """
    try:
        path = _freeze_path(project, record.worktree_id)
        if not path.exists():
            return False, None
        identity = _freeze_identity(record)
        if identity is None:
            return False, None
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or "root_codename" not in data:
            return False, None
        stored_identity = (data.get("owner_ref"), data.get("creation_nonce"))
        if stored_identity != identity:
            return False, None
        codename = data["root_codename"]
        if codename is None:
            return True, None
        from . import codename as codename_mod
        if isinstance(codename, str) and codename_mod.is_valid_handle(codename):
            return True, codename
        return False, None
    except Exception:
        return False, None


def _freeze_root(
    project: str | None, record: tracking.WorktreeRecord, codename: str | None,
) -> None:
    """Persist *codename* (or ``None``) as this worktree's root-codename
    decision, BOUND to its current ``_freeze_identity`` (``owner_ref`` +
    ``creation_nonce``), so a LATER change to the root's own repo config
    (e.g. toggling ``source_attribution_configured``) can never retroactively
    expose a previously-withheld custom-wordlist codename, or hide one
    already published -- the same ``unconfigured-attribution-never-leaks``
    "freeze once, never re-derive from live config" guarantee this plugin's
    own PR-attribution design already applies to the PRIMARY codename
    (``tracking.PRRecord.attribution_mode``/``attribution_explicit``) --
    while still invalidating itself the moment the bound identity changes
    (see :func:`_freeze_identity`). A ``None`` identity (no
    ``creation_nonce`` -- a pre-existing record) is a no-op: such a record
    never participates in freezing. Callers must hold this worktree's
    freeze lock (see :func:`resolve_root_codename`) across the read-check
    -> compute -> this write; never raises on its own: a failed write just
    means the next call re-resolves live.
    """
    identity = _freeze_identity(record)
    if identity is None:
        return
    owner_ref, creation_nonce = identity
    try:
        _freeze_path(project, record.worktree_id).write_text(json.dumps({
            "root_codename": codename,
            "owner_ref": owner_ref,
            "creation_nonce": creation_nonce,
        }))
    except Exception:
        pass


def _load_local_record(
    project: str | None, worktree_id: str,
) -> tracking.WorktreeRecord | None:
    """Load a same-machine worktree record by ``(project, worktree_id)``.

    Returns ``None`` if either component is unsafe to join as a path
    segment (see :func:`_is_safe_path_component`), the project/tracking dir
    can't be resolved, the record file is absent, or it fails to parse --
    every failure mode degrades to "chain unresolved here", never an
    exception.
    """
    if not _is_safe_path_component(worktree_id):
        return None
    if project is not None and not _is_safe_path_component(project):
        return None
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


def _walk_to_root(
    record: tracking.WorktreeRecord,
    *,
    project: str | None,
    this_machine: str | None,
) -> tuple[tracking.WorktreeRecord, str | None] | None:
    """Walk *record*'s ``owner_ref`` chain to its root. Returns ``(root,
    root_project)``, or ``None`` when there is no chain, the chain steps
    onto a different machine, is cyclic, exceeds the depth cap, or an
    intermediate owner record can't be loaded/is an unsafe path component.
    """
    current = record
    current_project = project
    seen: set[tuple[str | None, str]] = set()
    # _MAX_CHAIN_DEPTH + 1: a chain of exactly the documented cap's worth of
    # ANCESTORS needs one more iteration than that to actually CONFIRM the
    # last one has no further owner (the iteration that loads ancestor N
    # is distinct from the one that observes ancestor N is the root) --
    # without the +1, a chain of exactly the cap's depth would be rejected
    # as if it exceeded it, one short of the documented boundary.
    for _ in range(_MAX_CHAIN_DEPTH + 1):
        parsed = current.owner_claim_ref
        if parsed is None:
            break
        if parsed.machine:
            # Fail CLOSED when this machine's own identity can't be
            # resolved: an unresolved `this_machine` must never be treated
            # as "matches any qualified owner machine" -- that would let a
            # cross-machine ref masquerade as same-machine and load
            # whatever happens to live locally under that project/worktree
            # id. Only a POSITIVELY confirmed match proceeds.
            if not this_machine or parsed.machine != this_machine:
                return None
        parent_project = parsed.project or current_project
        if not _is_safe_path_component(parsed.worktree_id) or (
            parent_project is not None and not _is_safe_path_component(parent_project)
        ):
            # A corrupted/hand-edited owner_ref could otherwise escape the
            # intended tracking directory, or have its codename evaluated
            # under the WRONG project's provenance policy.
            return None
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
    return current, current_project


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

    The result is FROZEN (when *ensure* is True) and reused on every later
    call with the SAME ``owner_ref``/``creation_nonce`` identity -- see
    :func:`_freeze_root`/:func:`_freeze_identity` -- so a later change to
    the root's own repo config can never retroactively expose or hide a
    previously-decided codename. A worktree claim handoff rewriting
    ``owner_ref``, or this worktree id being reaped and recreated (a fresh
    ``creation_nonce``), each invalidate the stale freeze automatically,
    rather than requiring explicit cleanup at every tracking-record
    deletion call site. A record predating ``creation_nonce`` never
    freezes at all (always computed live -- safe, just less efficient).
    The read-check -> compute -> write is itself serialized under this
    worktree's own tracking record lock (:class:`tracking._RecordLock`)
    so two concurrent publish/finalize processes can't both observe "not
    frozen," independently derive different decisions, and clobber each
    other -- every caller gets back the single WINNING persisted value,
    never a locally-computed one that lost the race.
    """
    frozen, frozen_codename = _load_frozen_root(project, record)
    if frozen:
        return frozen_codename

    if this_machine is None:
        try:
            this_machine = cfg.load_config().machine
        except Exception:
            this_machine = None
    resolved_project = project
    if resolved_project is None:
        try:
            resolved_project = cfg.project_name()
        except Exception:
            resolved_project = None

    def _compute() -> str | None:
        walked = _walk_to_root(
            record, project=resolved_project, this_machine=this_machine,
        )
        if walked is None:
            return None
        root, root_project = walked
        return _ensure_publishable_root_codename(root, root_project, ensure=ensure)

    if not ensure or _freeze_identity(record) is None:
        # A diagnostic/peek caller (ensure=False) must never lock in a
        # premature decision that then blocks the real publish call later.
        # A record with no `creation_nonce` (predates this mechanism) can't
        # be bound to a reliable identity at all -- compute fresh, no lock,
        # no persistence, every time.
        return _compute()

    if not _is_safe_path_component(record.worktree_id) or (
        project is not None and not _is_safe_path_component(project)
    ):
        # Can't safely build/lock a sidecar path for this ref shape --
        # degrade to an unfrozen computation each call rather than raise.
        return _compute()

    with tracking._RecordLock(
        _freeze_path(project, record.worktree_id), require_sidecar=True,
    ):
        # Re-check INSIDE the lock: another process may have frozen this
        # worktree's decision while we were resolving `this_machine`/
        # `project` above.
        frozen, frozen_codename = _load_frozen_root(project, record)
        if frozen:
            return frozen_codename
        result = _compute()
        _freeze_root(project, record, result)
        return result


def root_codename_for_marker(record: tracking.WorktreeRecord, config) -> str | None:
    """``resolve_root_codename`` convenience wrapper for a PR-marker publish
    site: resolves *record*'s own repo project name off *config* and
    swallows any error -- a best-effort annotation must never block or alter
    whether the PRIMARY codename marker publishes.
    """
    try:
        repo_name = getattr(config, "repo_name", None) or None
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
