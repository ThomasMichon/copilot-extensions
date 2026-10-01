"""Worktree-creation core: the paired-knowledge carve and ``_create_worktree_core``.

Componentized out of ``__main__.py``'s launch core (module-size split,
``module-componentization-discipline`` effort). This owns the full "create a
new worktree" side-effect sequence -- codename allocation, owner-claim
journaling, the git worktree/branch/tracking-record write, and the paired
citadel knowledge-repo carve (#957) -- used by ``create``/``run``/``embody``'s
new-worktree path and by the registrar/pool declaration flow.

``__main__.py`` stays the compatibility/composition root: it imports and
re-exports every name defined here (several sibling modules --
``resolve_launch_cli.py``, ``worktree_ops_cli.py`` -- already call
``_create_worktree_core`` via their own ``_core()`` proxy) so every existing
caller and every test's ``monkeypatch.setattr(m, "<name>", ...)`` keep working
unchanged. Every cross-call between the functions below -- and every call out
to a helper that still lives in ``__main__.py`` proper (launch-plan building,
launch preflight, worktree-record serialization) -- goes through ``_core()``
(the live ``__main__`` module object) rather than a bare local name, so a
test's monkeypatch on ``m.<name>`` is observed no matter which function in
this band makes the call, exactly as the handoff-cutover slice's own
``_core()`` idiom already established for this same file.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

from . import (
    activity,
    codename_tracking,
    git_ops,
    local_cache_refresh,
    obligations,
    permissions,
    profile_assignment,
    state_root as state_root_mod,
    tracking,
)
from . import config as cfg


def _core():
    from . import __main__ as core

    return core


def _paired_knowledge_allocation_preflight(config: cfg.Config) -> None:
    """Preflight the paired-knowledge codename-allocation policy with NO
    side effect (round-6 review finding).

    Mirrors the same is-this-a-bound-worktree-class-stateless-harness
    resolution `_carve_paired_knowledge` performs, purely to decide
    whether that function's eventual allocation would be policy-blocked
    -- so `_create_worktree_core` can call this BEFORE it creates the
    harness's own worktree/branch/record, closing the gap where a
    paired-knowledge policy violation only surfaced after those harness
    side effects already existed (with no rollback).

    Raises :class:`codename_tracking.CodenameAttributionPolicyError` on a
    genuine policy violation. Any OTHER failure (state-root resolution,
    config load, an unregistered knowledge repo) is swallowed -- this is
    advisory-only pre-validation; `_carve_paired_knowledge`'s own
    preflight and second (lock-held) revalidation remain the actual
    fail-closed gate for the allocation itself.
    """
    if os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return
    try:
        res = state_root_mod.resolve_state_root(config)
    except Exception:
        return
    if not (res.requires_external and res.bound and res.path):
        return

    from . import repos as repos_mod

    entry = repos_mod.find_repo(res.repo)
    is_worktree_class = bool(entry) and repos_mod.normalize_class(entry.repo_class) in (
        "worktree", "knowledge",
    )
    if not is_worktree_class:
        return
    try:
        knowledge_config = cfg.load_config(project=res.repo)
        knowledge_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
            knowledge_config
        )
    except Exception:
        return
    codename_tracking.check_allocation_policy(**knowledge_policy_kwargs)


def _carve_paired_knowledge(
    config: cfg.Config,
    *,
    harness_id: str,
    timestamp: str,
    suffix: str,
    plat: str,
    plat_short: str,
) -> dict | None:
    """Carve/stamp the citadel knowledge-repo PAIR for a stateless harness (#957).

    When the launch repo is a **stateless harness bound to a knowledge repo**
    (``resolve_state_root`` reports ``requires_external`` + ``bound``), the
    knowledge repo's state is carved and tracked **together** with the harness
    worktree so the agent never has to remember to carve it separately:

    * **Worktree-class knowledge repo** -> carve its own worktree (shared
      ``<ts>-<suffix>`` pair stub, parallel ``worktree/<id>`` branch), write its
      tracking record cross-stamped back to the harness, and return the
      ``pair_*`` stamp (``pair_kind="worktree"``) for the HARNESS record.
    * **Non-worktree-class knowledge repo** (singleton / reference) -> no second
      worktree; return an ``anchor`` pairing stamp so the pair is recorded and
      ``state-root --pair`` resolves to the knowledge anchor.

    Returns the ``pair_*`` dict to stamp on the harness record, or ``None`` when
    no pairing applies (a normal self-hosted repo, or an unbound harness).
    **Fail-safe:** any error degrades to ``None`` (the harness carve is never
    affected) with a one-line stderr note. Set ``AGENT_WORKTREES_NO_PAIR`` to
    disable the carve-both behavior entirely.
    """
    core = _core()
    if os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return None
    try:
        res = state_root_mod.resolve_state_root(config)
    except Exception:
        return None
    if not (res.requires_external and res.bound and res.path):
        return None

    from . import repos as repos_mod

    knowledge_name = res.repo
    knowledge_anchor = res.path
    pair_id = f"{timestamp}-{suffix}"
    harness_ref = tracking.format_claim_ref(config.machine, config.repo_name or "?", harness_id)
    entry = repos_mod.find_repo(knowledge_name)
    is_worktree_class = bool(entry) and repos_mod.normalize_class(entry.repo_class) in (
        "worktree", "knowledge",
    )
    remote = git_ops.resolve_remote_name(
        (entry.remote or "origin") if entry else "origin",
        cwd=knowledge_anchor,
    )
    default_branch = (entry.default_branch or "main") if entry else "main"
    prepared = core._prepare_worktree_source(
        knowledge_anchor,
        remote=remote,
        default_branch=default_branch,
        fast_forward_anchor=getattr(config, "auto_fast_forward", True),
        label=f"paired knowledge repo '{knowledge_name}'",
    )

    if not is_worktree_class:
        # Non-worktree-class knowledge -> operate on the anchor (no 2nd carve).
        anchor_ref = tracking.format_claim_ref(
            config.machine,
            knowledge_name,
            os.path.basename(knowledge_anchor.rstrip("/\\")) or "anchor",
        )
        print(
            f"Paired knowledge repo '{knowledge_name}' is not worktree-class; "
            f"pairing at its anchor (no separate worktree).",
            file=sys.stderr,
        )
        return {
            "pair_id": pair_id, "pair_role": "harness", "pair_ref": anchor_ref,
            "pair_kind": "anchor",
        }

    # Worktree-class knowledge -> carve its paired worktree.
    knowledge_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}-k"
    knowledge_branch = f"worktree/{knowledge_id}"
    knowledge_wt_root = cfg.derive_worktree_root(knowledge_anchor)
    knowledge_wt_path = str(Path(knowledge_wt_root) / knowledge_id)
    knowledge_ref = tracking.format_claim_ref(config.machine, knowledge_name, knowledge_id)
    knowledge_tracking_path = cfg.project_dir(knowledge_name) / "worktrees"

    # pr-attribution-codenames Phase 2 (#2838): assign the paired knowledge worktree's own
    # codename (scoped to its own project's tracking directory and wordlist, not a share of
    # the harness's) BEFORE the git worktree/branch is created -- an allocation failure (an
    # exhausted finite configured wordlist) must never leave an orphaned checkout with no
    # tracking record. Assignment + the eventual record-write share one allocation lock
    # (see codename_tracking.py) so a concurrent carve can never pick the same candidate.
    #
    # codename-attribution-by-default (round-10/11 findings): the allocation-policy check
    # below is deliberately OUTSIDE this config-load try/except -- an earlier design had it
    # inside, where a bare `except Exception` swallowed the policy violation into a silent
    # `knowledge_wordlist = None` fallback, letting this path allocate a codename (with
    # codename_source="built-in") for exactly the custom-wordlist/unconfigured-
    # source_attribution repo the policy exists to block. A config-load failure degrades
    # ONLY the wordlist resolution (matching this function's existing fail-safe posture);
    # it never silently authorizes an otherwise-blocked allocation.
    try:
        knowledge_config = cfg.load_config(project=knowledge_name)
        knowledge_wordlist = codename_tracking.wordlist_for_repo(knowledge_config)
        knowledge_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
            knowledge_config
        )
    except Exception:
        knowledge_wordlist = None
        knowledge_policy_kwargs = {
            "codename_source": "built-in", "pr_enabled": False,
            "source_attribution_configured": False,
        }

    # Preflight (round-12/13 finding): validate BEFORE any side effect this
    # function performs (worktree, branch, or record) -- the `create` flow
    # already persisted the HARNESS worktree/branch/record before calling
    # this function, so failing here does not roll those back (no rollback
    # protocol is implemented; see the second revalidation below for the
    # orphan-naming backstop).
    codename_tracking.check_allocation_policy(**knowledge_policy_kwargs)

    with codename_tracking.allocation_lock(knowledge_tracking_path):
        # Second revalidation (round-13/14 finding, sharpened by a PR
        # #3037 review finding) immediately before the first side effect
        # below -- RECOMPUTES the policy kwargs from a freshly-loaded
        # config, never reuses the pre-lock snapshot above: a config
        # change landing in the window between the preflight and this
        # point must be observed here, or the "shrinks the TOCTOU window"
        # claim is hollow (the earlier code re-checked the SAME stale
        # values, which can never disagree with themselves). If hit, the
        # harness worktree/branch created by the caller before this
        # function ran is now orphaned (no automatic rollback) -- name it
        # explicitly so the operator can remove it.
        try:
            fresh_knowledge_config = cfg.load_config(project=knowledge_name)
            fresh_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
                fresh_knowledge_config
            )
            knowledge_wordlist = codename_tracking.wordlist_for_repo(
                fresh_knowledge_config
            )
        except Exception:
            # PR #3037 review finding: a reload failure here must NEVER
            # silently fall back to the pre-lock snapshot -- if config
            # changed from an explicit opt-in to an unconfigured custom
            # wordlist in the interval, reusing the (permissive) stale
            # snapshot would still authorize an allocation the fresh state
            # would have blocked. Degrade to the conservative,
            # ALWAYS-non-authorizing state instead (never the reverse):
            # unconditionally forces `check_allocation_policy` to raise,
            # so a transient reload failure fails the operation closed
            # rather than silently authorizing it.
            fresh_policy_kwargs = {
                "codename_source": "custom", "pr_enabled": True,
                "source_attribution_configured": False,
            }
        try:
            codename_tracking.check_allocation_policy(**fresh_policy_kwargs)
        except codename_tracking.CodenameAttributionPolicyError as exc:
            harness_worktree_path = str(
                Path(config.default_repo.worktree_root) / harness_id
            )
            raise codename_tracking.CodenameAttributionPolicyError(
                f"{exc} A harness worktree/branch was already created "
                "before this policy violation was detected and is left in "
                f"place (no automatic rollback): worktree="
                f"{harness_worktree_path!r} branch={f'worktree/{harness_id}'!r}. "
                "Remove it manually with `git worktree remove` if unwanted."
            ) from exc
        knowledge_codename = codename_tracking.assign_new_codename(
            knowledge_tracking_path, knowledge_wordlist
        )

        Path(knowledge_wt_root).mkdir(parents=True, exist_ok=True)
        print(
            f"Creating paired knowledge worktree on branch {knowledge_branch}...",
            file=sys.stderr,
        )
        git_ops.create_worktree(
            knowledge_anchor,
            knowledge_wt_path,
            knowledge_branch,
            prepared.start_point,
        )

        tracking.create_new_record(
            worktree_id=knowledge_id,
            branch=knowledge_branch,
            worktree_path=knowledge_wt_path,
            repo=knowledge_name,
            machine=config.machine,
            platform_name=plat,
            tracking_path=knowledge_tracking_path,
            pair_id=pair_id,
            pair_role="knowledge",
            pair_ref=harness_ref,
            pair_kind="worktree",
            codename=knowledge_codename,
            codename_source=fresh_policy_kwargs["codename_source"],
        )
    # Best-effort permissions + trust for the knowledge worktree.
    try:
        permissions.clone_permissions(knowledge_anchor, knowledge_wt_path)
        permissions.add_trusted_folder(knowledge_wt_path)
        permissions.ensure_extension_permission_approvals(knowledge_wt_path)
    except Exception:
        pass
    activity.log_event(
        "paired_knowledge_worktree_created",
        worktree_id=knowledge_id,
        branch=knowledge_branch,
    )
    return {
        "pair_id": pair_id, "pair_role": "harness", "pair_ref": knowledge_ref,
        "pair_kind": "worktree",
    }


def _stamp_and_compose_paired_knowledge(
    config: cfg.Config,
    record: tracking.WorktreeRecord,
    worktree_path: str,
    pair_stamp: dict | None,
) -> dict | None:
    """Persist a new pair before composing its launch-time plugin overlay."""
    if not pair_stamp:
        return None
    record.pair_id = pair_stamp.get("pair_id")
    record.pair_role = pair_stamp.get("pair_role")
    record.pair_ref = pair_stamp.get("pair_ref")
    record.pair_kind = pair_stamp.get("pair_kind")
    tracking.save_record(record)

    from . import knowledge_plugins

    try:
        return knowledge_plugins.compose_from_pair(cwd=worktree_path, config=config)
    except knowledge_plugins.KnowledgePluginError as exc:
        # The pair is already durable. Keep create's resource identity usable
        # and let the normal launcher retry its strict pre-Copilot preflight.
        print(
            f"paired-knowledge plugin composition failed; the launch preflight will retry: {exc}",
            file=sys.stderr,
        )
        return {"action": "error", "error": str(exc)}


def _journal_owner_reciprocal_claim(
    config: cfg.Config,
    worktree_id: str,
    owner_ref: str | None,
    *,
    owner_locked: bool = False,
) -> bool:
    """Journal the reciprocal ``worktree`` claim onto an owner's ledger (Ph3c).

    A worktree created with an ``owner_ref`` is another worktree's outbound
    resource. This writes the **forward** half of that bidirectional link -- a
    ``worktree``-kind :class:`ResourceClaim` on the *owner*'s record whose ``ref``
    is this worktree's qualified ClaimRef -- so the owner's finalize gate sees
    the obligation and the child's finalize ``_settle_parent_obligation`` has a
    matching claim to settle. Without it the link is half-formed (backward
    ``owner_ref`` set, but the owner holds no claim), so settlement is a silent
    no-op.

    Same-machine owner -> written here; a **cross-machine** owner defers to the
    lease mirror (``_resolve_owner_ref_record_path`` returns no local path).
    Fully best-effort: any failure returns ``False`` and never raises (so it
    can never break a worktree carve). Idempotent -- ``add_resource_claim``
    dedups by ref, so a ``run``-driven create that also journals is harmless.
    Returns ``True`` only when a claim was written.
    """
    if not owner_ref:
        return False
    core = _core()
    # Stage D: real implementation lives in claims_cli (cluster-free).
    from . import claims_cli as _claims_cli

    _resolve_owner_ref_record_path = core._self_override("_resolve_owner_ref_record_path", _claims_cli._resolve_owner_ref_record_path)

    try:
        owner_path, _owner_wt, _err = _resolve_owner_ref_record_path(owner_ref, config,)
        if owner_path is None or not owner_path.exists():
            return False
        child_ref = tracking.format_claim_ref(
            config.machine,
            config.repo_name,
            worktree_id,
        )

        def _write_claim() -> None:
            owner_rec = tracking.load_record(owner_path)
            tracking.add_resource_claim(
                owner_rec,
                tracking.ResourceClaim(
                    kind="worktree",
                    ref=child_ref,
                    created_at=tracking._now_iso(),
                    state=obligations.ACTIVE,
                ),
                save=False,
            )
            tracking.save_record(owner_rec, owner_path)

        if owner_locked:
            _write_claim()
        else:
            with tracking._RecordLock(owner_path, require_sidecar=True):
                _write_claim()
        print(f"Journaled this worktree as an obligation on {owner_ref}.", file=sys.stderr,)
        return True
    except Exception as exc:  # never let journaling break the carve
        print(f"owner-claim journaling failed (non-fatal): {exc}", file=sys.stderr)
        return False


def _prepare_worktree_source(
    anchor: str | Path,
    *,
    remote: str,
    default_branch: str,
    fast_forward_anchor: bool,
    label: str,
) -> git_ops.WorktreeBaseResult:
    """Prepare one source checkout and report degraded freshness honestly."""
    print(f"Fetching latest from {remote} for {label}...", file=sys.stderr)
    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote=remote,
        default_branch=default_branch,
        fast_forward_anchor=fast_forward_anchor,
    )
    if prepared.fetch_error:
        print(
            f"Note: fetch failed for {label}; using last-known "
            f"'{prepared.start_point}' ({prepared.fetch_error}).",
            file=sys.stderr,
        )
    if prepared.anchor.updated:
        print(
            f"Fast-forwarded {label} anchor by {prepared.anchor.behind} commit(s).",
            file=sys.stderr,
        )
    elif prepared.anchor.reason in {
        "dirty",
        "ahead",
        "diverged",
        "detached",
        "non-default-branch",
        "ff-failed",
    }:
        print(
            f"Note: {label} anchor is {prepared.anchor.reason}; left untouched.",
            file=sys.stderr,
        )
    if prepared.start_point != f"{remote}/{default_branch}":
        print(
            f"Note: '{remote}/{default_branch}' not found for {label}; "
            f"branching from '{prepared.start_point}' instead.",
            file=sys.stderr,
        )
    return prepared


def _creation_parent_session(
    explicit: str | None,
    *,
    inherit_ambient: bool,
) -> str | None:
    if explicit is not None:
        return explicit
    if inherit_ambient:
        return os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    return None


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    profile_is_explicit: bool = True,
    no_mux: bool = False,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
    name: str | None = None,
    parent_session: str | None = None,
    inherit_parent_session: bool = True,
    caller_worktree: str | None = None,
    owner_ref: str | None = None,
    dispatch_attempt: dict[str, object] | None = None,
    launch_preflight: object | None = None,
    recovery: bool = False,
    bound_agent: str | None = None,
    no_pair: bool = False,
) -> dict:
    """Create a new worktree and return a dict with worktree info + launch plan.

    Performs the side-effects (fetch, git worktree add, tracking YAML,
    permissions) but does NOT launch copilot.  Returns a dict suitable
    for JSON serialization.

    ``kind="system"`` marks the worktree as daemon-owned (hidden from the
    Picker, exempt from routine cleanup); ``owner``/``name`` label it for the
    System-menu browse view.

    ``no_pair`` opts THIS creation out of the paired-knowledge carve
    regardless of ``origin`` -- for a registrar/pool declaration that has no
    bound knowledge repo to give every worker (e.g. a portable pool). It is
    a narrower, per-call sibling of the blunt whole-host
    ``AGENT_WORKTREES_NO_PAIR`` env var; either one skips the carve.

    Raises ``RuntimeError`` on failure.
    """
    core = _core()
    # Stage D: local-shadow deferred names via _self_override (keeps create/worktree_ops_cli cluster-free).
    from . import claims_cli as _claims_cli
    from . import resolve_launch_cli as _resolve_launch_cli
    from . import worktree_ops_cli as _worktree_ops_cli

    _coordination_readiness_for_owner_ref = core._self_override("_coordination_readiness_for_owner_ref", _claims_cli._coordination_readiness_for_owner_ref)
    CoordinationReadinessFailure = core._self_override("CoordinationReadinessFailure", _claims_cli.CoordinationReadinessFailure)
    _resolve_owner_ref_record_path = core._self_override("_resolve_owner_ref_record_path", _claims_cli._resolve_owner_ref_record_path)
    _validate_profile_assignment_config = core._self_override("_validate_profile_assignment_config", _resolve_launch_cli._validate_profile_assignment_config)
    _apply_assignment_env = core._self_override("_apply_assignment_env", _resolve_launch_cli._apply_assignment_env)
    _launch_profile_selection = core._self_override("_launch_profile_selection", _resolve_launch_cli._launch_profile_selection)
    _reflect_assignment = core._self_override("_reflect_assignment", _resolve_launch_cli._reflect_assignment)
    _slugify = core._self_override("_slugify", _worktree_ops_cli._slugify)

    repo = config.default_repo
    if kind != "system" and getattr(repo, "knowledge_only", False):
        raise RuntimeError(
            f"'{config.repo_name}' is a knowledge-only companion repo "
            "(knowledge_only: true) -- it cannot be driven directly. "
            "It is carved automatically as a paired '-k' worktree when the "
            "stateless harness bound to it (see its knowledge_repo config) "
            "creates its own worktree; work there instead."
        )
    plat = cfg.detect_platform()
    plat_short = "win" if plat == "windows" else plat

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    if kind == "system":
        # Recognizable id for daemon worktrees: sys-<name>-<ts>-<suffix>.
        slug = _slugify(name or owner or "daemon")
        worktree_id = f"sys-{slug}-{timestamp}-{suffix}"
    else:
        worktree_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}"
    branch = f"worktree/{worktree_id}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)

    if owner_ref:
        parsed_owner = tracking.parse_claim_ref(owner_ref)
        if parsed_owner is None or not parsed_owner.is_qualified:
            raise RuntimeError(
                f"--owner-ref must be qualified as machine/project/worktree_id (got {owner_ref!r})"
            )
        if parsed_owner.machine != config.machine:
            raise RuntimeError(
                f"cross-machine owner {owner_ref} cannot synchronously accept "
                "this worktree obligation; use a dispatch/lease flow that "
                "persists remote ownership before creation"
            )
        readiness = _coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            raise CoordinationReadinessFailure(readiness)

    _validate_profile_assignment_config(config)
    launches_copilot = kind != "system"
    fake_args = None
    if launches_copilot:
        fake_args = argparse.Namespace(
            copilot_args=[],
            recovery=recovery,
            no_mux=no_mux,
            no_resume=False,
            profile=(profile.name if profile is not None and profile_is_explicit else None),
        )
        launch_preflight = launch_preflight or core._preflight_launch(
            config,
            fake_args,
            worktree_path,
        )
        if launch_preflight.error:
            raise core.LaunchPreflightError(launch_preflight.error)

    # Round-6 review finding: preflight the PAIRED-KNOWLEDGE allocation policy here, BEFORE
    # any side effect below (owner claim, harness git worktree/branch, tracking record) --
    # the ONLY preflight this policy previously had lived inside `_carve_paired_knowledge`,
    # which `_create_worktree_core` calls AFTER the harness worktree/branch/record already
    # exist, so a violation there still left those harness side effects behind (and without
    # the orphan-path context the later revalidation adds). This mirrors the harness's own
    # preflight-then-revalidate-under-lock shape immediately below;
    # `_carve_paired_knowledge`'s own preflight/revalidation stay in place as defense in
    # depth for the narrower TOCTOU window between here and its own carve.
    #
    # Pairing applies to every `kind == "session"` worktree regardless of `origin`
    # (#catch-22 follow-up to #3207): a delegate/system-origin dispatch worker needs its
    # own paired knowledge sibling exactly as much as an interactive operator does --
    # reciprocal disposal (see `terminal_conclusion.conclude_disposable_worktree`) is what
    # lets a dispatch attempt's conclusion release both halves together, so origin is no
    # longer a reason to skip the carve. `no_pair` is the intentional, explicit per-call
    # opt-out for a registrar/pool with no bound knowledge repo to hand its workers.
    if kind == "session" and not no_pair:
        core._paired_knowledge_allocation_preflight(config)

    # Ensure root exists
    Path(repo.worktree_root).mkdir(parents=True, exist_ok=True)

    prepared = core._prepare_worktree_source(
        repo.anchor,
        remote=repo.remote,
        default_branch=repo.default_branch,
        fast_forward_anchor=config.auto_fast_forward,
        label="repository",
    )

    # pr-attribution-codenames Phase 2 (#2838): assign the codename FIRST --
    # before the owner claim is journaled and before the git worktree/branch
    # is created. An allocation failure (an exhausted finite configured
    # wordlist) must never leave the owner ledger with a live obligation
    # pointing at a worktree that was never created, nor an orphaned
    # checkout with no tracking record. The whole sequence below (allocate
    # -> acquire the owner record lock -> journal owner claim -> create the
    # git worktree -> write the record) is held under one cross-process
    # allocation lock so two concurrent `create` calls can never observe the
    # same existing codename set and choose the same candidate.
    #
    # Lock order is always this module's `allocation_lock` (outer) then any
    # per-record lock (inner) -- here, the owner's -- never the reverse.
    # `codename_tracking.ensure_codename` (the resume/status backfill path)
    # acquires them in this same order; reversing it here (owner lock, then
    # allocation lock, as an earlier revision did) is a real cross-process
    # deadlock hazard against a concurrent backfill of that same owner
    # worktree's own codename.
    #
    # codename-attribution-by-default (round-21 finding): preflight the
    # allocation-time policy BEFORE any create side effect below (owner
    # claim journal, git worktree/branch, tracking record) -- a distinct,
    # more severe gap than the paired-knowledge path since this is the
    # ordinary `create` path every worktree goes through.
    new_codename_source = codename_tracking.classify_codename_source(
        getattr(repo, "codename", None)
    )
    codename_tracking.check_allocation_policy(
        pr_enabled=bool(getattr(repo.pr, "enabled", False)),
        codename_source=new_codename_source,
        source_attribution_configured=bool(
            getattr(repo.pr, "source_attribution_configured", False)
        ),
    )
    tracking_path = cfg.tracking_dir()
    tracking_path.mkdir(parents=True, exist_ok=True)
    with codename_tracking.allocation_lock(tracking_path):
        # Second revalidation (round-13/14 finding, sharpened by a PR
        # #3037 review finding) immediately before the first side effect
        # below -- RECOMPUTES from a freshly-loaded config, never reuses
        # the pre-lock `repo`/`new_codename_source` snapshot above: a
        # config change landing in the window between the preflight and
        # this point must be observed here, or re-checking the SAME
        # stale values (which can never disagree with themselves) makes
        # the claimed TOCTOU-window shrink hollow.
        try:
            fresh_config = cfg.load_config(project=config.repo_name)
            fresh_repo = fresh_config.default_repo
            fresh_codename_source = codename_tracking.classify_codename_source(
                getattr(fresh_repo, "codename", None)
            )
            fresh_pr_enabled = bool(getattr(fresh_repo.pr, "enabled", False))
            fresh_sac = bool(
                getattr(fresh_repo.pr, "source_attribution_configured", False)
            )
            fresh_wordlist_config = fresh_config
        except Exception:
            # PR #3037 review finding: never fall back to the pre-lock
            # snapshot on a reload failure -- degrade to the
            # conservative, ALWAYS-non-authorizing state instead (see the
            # matching fix in _carve_paired_knowledge for the full
            # rationale).
            fresh_codename_source = "custom"
            fresh_pr_enabled = True
            fresh_sac = False
            fresh_wordlist_config = config
        codename_tracking.check_allocation_policy(
            pr_enabled=fresh_pr_enabled,
            codename_source=fresh_codename_source,
            source_attribution_configured=fresh_sac,
        )
        new_codename = codename_tracking.assign_new_codename(
            tracking_path, codename_tracking.wordlist_for_repo(fresh_wordlist_config)
        )

        # Creator ownership is established before the resource exists, and
        # its ledger lock stays held until the child tracking record is
        # durable. Thus finalize cannot hand off/clean a not-yet-created
        # child reference.
        owner_guard = None
        if owner_ref:
            owner_path, _owner_id, owner_err = _resolve_owner_ref_record_path(owner_ref, config)
            if owner_err or owner_path is None or not owner_path.exists():
                raise RuntimeError(owner_err or f"owner ledger is missing: {owner_ref}")
            owner_guard = tracking._RecordLock(owner_path, require_sidecar=True)
            owner_guard.__enter__()

        try:
            if owner_guard is not None and not core._journal_owner_reciprocal_claim(
                config, worktree_id, owner_ref, owner_locked=True
            ):
                raise RuntimeError(f"owner {owner_ref} cannot accept a new worktree obligation")

            print(f"Creating worktree on branch {branch}...", file=sys.stderr)
            git_ops.create_worktree(
                repo.anchor,
                worktree_path,
                branch,
                prepared.start_point,
            )

            # Write tracking YAML
            record = tracking.create_new_record(
                worktree_id=worktree_id,
                branch=branch,
                worktree_path=worktree_path,
                repo=config.repo_name,
                machine=config.machine,
                platform_name=plat,
                tracking_path=tracking_path,
                kind=kind,
                owner=owner,
                interface=interface,
                origin=origin,
                codename=new_codename,
                codename_source=fresh_codename_source,
                dispatch_attempt=(
                    tracking.DispatchAttempt(
                        task_id=str(dispatch_attempt["task_id"]),
                        reservation_key=str(dispatch_attempt["reservation_key"]),
                        attempt=int(dispatch_attempt["attempt"]),
                        driver=str(dispatch_attempt["driver"]),
                        supervisor=str(dispatch_attempt["supervisor"]),
                        creator_machine=config.machine,
                    )
                    if dispatch_attempt is not None
                    else None
                ),
                # #1029: link the new worktree back to the session that spawned it, so a
                # later resume (esp. a PR/feedback worktree with no sessions of its own)
                # restores context instead of cold-starting.
                parent_session=core._creation_parent_session(
                    parent_session,
                    inherit_ambient=inherit_parent_session,
                ),
                # #2178: for a bridge spawn, record the caller worktree so the Picker can
                # jump back to it.
                caller_worktree=caller_worktree or None,
                # resource-claims: the qualified backward owner link, for a worktree
                # spun up as another worktree's outbound resource (stamped by `run` /
                # an explicit --owner-ref). Absent = unclaimed.
                owner_ref=owner_ref or None,
                bound_agent=bound_agent or None,
            )
        finally:
            if owner_guard is not None:
                owner_guard.__exit__(None, None, None)

    # Clone permissions
    if permissions.clone_permissions(repo.anchor, worktree_path):
        print("Copied Copilot permissions to worktree path.", file=sys.stderr)

    activity.log_event(
        "worktree_created",
        worktree_id=worktree_id,
        branch=branch,
    )

    # Trust the new worktree path
    if permissions.add_trusted_folder(worktree_path):
        print("Added worktree path to trustedFolders.", file=sys.stderr)

    # Pre-approve the facility's own extension-permission-access gate so the
    # first launch here never blocks on an interactive prompt no one is
    # necessarily present to answer (github/copilot-agent-runtime#22266).
    if permissions.ensure_extension_permission_approvals(worktree_path):
        print("Pre-approved facility extension permissions for worktree path.", file=sys.stderr)

    # Worktree-scoped dynamic guidance (docs/patterns/worktree-scoped-
    # dynamic-guidance.md): refresh every enabled source's gitignored
    # *.local.instructions.md sibling now, before the first session here
    # even starts, so a directory-scanning harness may pick it up with no
    # reliance on the repo-wide catch-all. Best-effort and silent -- see
    # local_cache_refresh's own docstring.
    local_cache_refresh.refresh_local_cache(worktree_path)

    # citadel paired -harness/-knowledge worktree lifecycle (#957): when this is
    # a stateless harness bound to a knowledge repo, carve/stamp the knowledge
    # pair together with this worktree and cross-stamp the linkage. Only for
    # plain session worktrees (never system/bridge), and fully fail-safe -- a
    # pairing failure never breaks the harness carve. Applies regardless of
    # `origin` (see the preflight comment above for why); `no_pair` is the
    # explicit per-call opt-out.
    if kind == "session" and not no_pair:
        try:
            pair_stamp = core._carve_paired_knowledge(
                config,
                harness_id=worktree_id,
                timestamp=timestamp,
                suffix=suffix,
                plat=plat,
                plat_short=plat_short,
            )
        except codename_tracking.CodenameAttributionPolicyError:
            # codename-attribution-by-default (round-10/11 finding): this is
            # NOT an ordinary pairing glitch -- it is the paired-knowledge
            # allocation policy correctly refusing to leak a custom
            # vocabulary term. Re-raise (abort `create` outright) rather
            # than degrading to a logged warning and a silent `pair_stamp =
            # None`, which would defeat the policy entirely.
            raise
        except Exception as exc:  # pragma: no cover - defensive
            print(f"paired-knowledge carve failed (non-fatal): {exc}", file=sys.stderr)
            pair_stamp = None
        core._stamp_and_compose_paired_knowledge(config, record, worktree_path, pair_stamp)

    # Copilot discovers repository settings before sessionStart. A committed
    # relative-path directory marketplace source (this repo's own) resolves
    # live against whichever checkout is active, so no local override seeding
    # is needed here (#marketplace-override-retirement).

    result = {"worktree": core._worktree_to_dict(record)}
    if not launches_copilot:
        return result

    # Build launch command (for caller to use).
    assert fake_args is not None
    assert launch_preflight is not None
    selection = _launch_profile_selection(
        config,
        fake_args,
        record,
        lane="new",
        generation_key=f"new:{worktree_id}",
        ordinary_profile=profile,
        explicit_profile=profile if profile_is_explicit else None,
    )
    _reflect_assignment(record, selection)
    launch_cmd = core._build_launch_cmd(
        config,
        fake_args,
        worktree_path,
        profile=selection.profile,
        preflight=launch_preflight,
    )
    env = _apply_assignment_env(
        core._build_env(
            selection.profile,
            core._repo_session_env(config, worktree_path),
            work_dir=worktree_path,
        ),
        selection,
    )
    result["worktree"] = core._worktree_to_dict(record)
    result["launch"] = {
        "action": "exec",
        "work_dir": worktree_path,
        "cmd": launch_cmd,
        "env": env,
        "worktree_id": worktree_id,
        "post_exit": True,
        "no_mux": no_mux,
        # Authoritative for downstream out-of-process calls (e.g. the
        # launcher's `execution-leg get`), which must scope to the
        # project that actually owns this worktree -- not whatever ambient
        # project the launcher itself started with, nor the mutable
        # process-global `cfg.active_project()` (#2338).
        "project": config.repo_name,
    }
    if selection.assignment is not None:
        result["launch"]["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    return result
