"""Status-bar render surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path

from . import config as cfg
from . import git_ops, prune, sessions, tracking


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = getattr(_core(), name, None)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "status-segment",
        help="Print a tmux/psmux status-bar segment for the worktree at cwd",
    )
    p.add_argument(
        "--path", default=None, help="Worktree path to classify (default: current directory)"
    )
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch before classifying (refreshes behind-counts; slower)",
    )
    p.add_argument(
        "--plain", action="store_true", help="Plain text without tmux #[style] directives"
    )
    p.add_argument(
        "--no-title",
        action="store_true",
        help="Omit the worktree title; show only the state block",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit structured JSON (id/path/repo/branch/state/ahead/behind/"
            "dirty/closure) instead of the rendered bar string. Same cheap, "
            "non-daemon single-worktree classify pass -- unlike `list --json "
            "--classify --worktree-id`, which still pays the resident "
            "classify daemon's whole-fleet negotiation cost."
        ),
    )

    p = sub.add_parser(
        "status-context",
        help="Print a tmux/psmux left status segment (machine, env, repo:id)",
    )
    p.add_argument(
        "--path", default=None, help="Worktree path to describe (default: current directory)"
    )
    p.add_argument(
        "--plain", action="store_true", help="Plain text without tmux #[style] directives"
    )


def _normalize_path(path: str) -> str:
    return _core()._normalize_path(path)


def _apply_tracking_override(record, info):
    return _core()._apply_tracking_override(record, info)


def _sync_status_tag(info: git_ops.WorktreeStateInfo) -> str:
    return _core()._sync_status_tag(info)


def _local_claimant_alive(owner_ref: str) -> bool | None:
    return _core()._local_claimant_alive(owner_ref)


def _json_output(value) -> None:
    _core()._json_output(value)


def _find_repo_dir():
    return _core()._find_repo_dir()


# status-segment -- one styled line for a tmux/psmux status bar
# ═══════════════════════════════════════════════════════════════════════════

# Git state -> (256-color background, short label) for the status-bar block.
# CONVO is the session-derived refinement of UNUSED (see
# git_ops.refine_state_with_session): a clean, commit-less worktree whose
# session held conversation turns reads as a distinct teal block, not grey
# UNUSED.  Both the status bar and `list --json --classify` resolve to this
# same WorktreeState set.
_SEGMENT_STYLE: dict[git_ops.WorktreeState, tuple[str, str]] = {
    git_ops.WorktreeState.DIRTY: ("colour160", "DIRTY"),  # red
    git_ops.WorktreeState.WIP: ("colour178", "WIP"),  # amber
    git_ops.WorktreeState.COMPLETED: ("colour034", "FINAL"),  # green
    git_ops.WorktreeState.UNUSED: ("colour244", "UNUSED"),  # grey
    git_ops.WorktreeState.CONVO: ("colour037", "CONVO"),  # teal
    git_ops.WorktreeState.ORPHAN: ("colour129", "ORPHAN"),  # magenta
    git_ops.WorktreeState.ACTIVE: ("colour039", "ACTIVE"),  # blue
    git_ops.WorktreeState.GONE: ("colour238", "GONE"),  # dark grey
    git_ops.WorktreeState.UNKNOWN: ("colour238", "?"),  # dark grey
}

# worktree-finality-and-obligations (Phase 5): background color per
# `prune.ClosureDescriptor.style` -- the canonical style token every
# descriptor-driven surface (list JSON, mux, Picker) shares. Reuses
# `_SEGMENT_STYLE`'s existing palette for the base states it mirrors 1:1
# (dirty/wip/unused/convo/orphan/gone/unknown/active), and adds two tokens
# `_SEGMENT_STYLE` never needed because it never distinguished them:
# `final` (a *refreshed*, claim-free, follow-up-free COMPLETED -- the exact
# green FINAL block) vs `merged-blocked` (COMPLETED but not yet safe to prune
# -- held claims, open follow-ups, cached/fetch-free evidence, or any other
# live blocker -- rendered amber/orange, distinct from WIP's amber so "work
# landed but not closed out" never reads as "still being written").
_DESCRIPTOR_STYLE_BG: dict[str, str] = {
    "final": "colour034",  # green -- same as legacy FINAL
    "merged-blocked": "colour208",  # orange -- landed but blocked
    "active": "colour039",
    "dirty": "colour160",
    "wip": "colour178",
    "unused": "colour244",
    "convo": "colour037",
    "orphan": "colour129",
    "gone": "colour238",
    "unknown": "colour238",
}

_SEGMENT_TITLE_MAX = 48


def _find_record_for_path(path: str) -> tracking.WorktreeRecord | None:
    """Return the tracking record whose worktree path matches ``path``."""
    override = _core_helper("_find_record_for_path", _find_record_for_path)
    if override is not _find_record_for_path:
        return override(path)
    try:
        norm = _normalize_path(path)
        for r in tracking.list_records(cfg.tracking_dir()):
            if r.worktree_path and _normalize_path(r.worktree_path) == norm:
                return r
    except Exception:
        pass
    return None


def _resolve_remote_default_branch(
    path: str,
    remote: str,
    *,
    config_default: str | None = None,
    allow_remote: bool = False,
) -> str | None:
    """Resolve a repo's default branch from the REMOTE's configuration, never a
    (possibly stale) local branch. Returns ``None`` if nothing resolves.

    Order:
      1. ``config_default`` if ``<remote>/<config_default>`` still exists (honor
         a valid explicit hint).
      2. The local ``<remote>/HEAD`` symbolic ref -- the remote's own default
         when a clone / ``git remote set-head`` recorded it (fast, offline).
      3. (``allow_remote`` only) ``git ls-remote --symref <remote> HEAD`` --
         asks the remote directly, so it works even when the local
         ``<remote>/HEAD`` was never set. Authoritative source of truth.
      4. First of ``main`` / ``master`` present as a *remote-tracking* ref
         (``<remote>/<cand>``) -- main-first, never a local head.

    Network is used only when ``allow_remote=True`` (step 3), so hot/pollable
    callers stay cheap and offline by leaving it ``False``. See dotfiles#1046.
    """
    override = _core_helper("_resolve_remote_default_branch", _resolve_remote_default_branch)
    if override is not _resolve_remote_default_branch:
        return override(
            path,
            remote,
            config_default=config_default,
            allow_remote=allow_remote,
        )

    def _has(ref: str) -> bool:
        r = git_ops.git("rev-parse", "--verify", "--quiet", ref, cwd=path, check=False)
        return r.returncode == 0

    if config_default and _has(f"{remote}/{config_default}"):
        return config_default

    head = git_ops.git("symbolic-ref", f"refs/remotes/{remote}/HEAD", cwd=path, check=False)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().rsplit("/", 1)[-1]

    if allow_remote:
        try:
            ls = git_ops.git(
                "ls-remote", "--symref", remote, "HEAD", cwd=path, check=False, timeout=10
            )
        except Exception:
            ls = None
        if ls is not None and ls.returncode == 0:
            for line in ls.stdout.splitlines():
                # Format: "ref: refs/heads/<branch>\tHEAD"
                line = line.strip()
                if line.startswith("ref:") and "HEAD" in line:
                    ref = line[len("ref:") :].split("\t", 1)[0].strip()
                    if ref.startswith("refs/heads/"):
                        return ref.rsplit("/", 1)[-1]

    for cand in ("main", "master"):
        if _has(f"{remote}/{cand}"):
            return cand

    return None


def _detect_upstream_branch(
    path: str,
    remote: str,
    config_default: str | None,
) -> str | None:
    """Detect the repo's upstream default branch (``main``/``master``/...).

    Thin, **offline** wrapper over ``_resolve_remote_default_branch`` for the
    status segment, which runs in arbitrary repos and polls frequently -- so it
    must not hit the network and cannot trust the ambient config's default
    (e.g. a ``master`` project binstub polling a ``main`` repo). Falls back to
    the config default as a last-resort hint (may be stale) when nothing else
    resolves.
    """
    override = _core_helper("_detect_upstream_branch", _detect_upstream_branch)
    if override is not _detect_upstream_branch:
        return override(path, remote, config_default)
    return (
        _resolve_remote_default_branch(
            path,
            remote,
            config_default=config_default,
            allow_remote=False,
        )
        or config_default
    )


def _resolve_segment_title(
    rec: tracking.WorktreeRecord | None,
    path: str,
    info: git_ops.WorktreeStateInfo,
    ctx: sessions.SessionContext | None = None,
) -> str:
    """Resolve a worktree's display title cheaply (single-record scan).

    Priority: explicit tracking title -> latest session summary -> last
    commit subject.  Returns "" when nothing is available.  Truncated to
    keep the status bar readable.  Pass a precomputed ``ctx`` (from
    :func:`sessions.scan_sessions_fast`) to avoid a second scan.
    """
    title = ""
    if rec and rec.title and rec.title != "null":
        title = rec.title
    if not title and rec is not None:
        try:
            if ctx is None:
                ctx = sessions.scan_sessions_fast([rec])
            title = ctx.latest_summary.get(_normalize_path(path), "") or ""
        except Exception:
            title = ""
    if not title:
        title = info.title or ""
    if len(title) > _SEGMENT_TITLE_MAX:
        title = title[: _SEGMENT_TITLE_MAX - 1].rstrip() + "\u2026"
    return title


def _persist_segment_title(
    rec: tracking.WorktreeRecord,
    path: str,
    ctx: sessions.SessionContext | None,
) -> None:
    """Persist the live session overall-summary into the worktree's ``title``.

    The ``title`` field is the single slot the Picker reads, so the
    status-updater -- which already resolves the title every tick -- lands it
    there instead of only painting the mux status bar.  This keeps the overall
    summary alive after the Copilot session-state directory is cleaned up
    (when the live ``latest_summary`` is no longer derivable).

    Distinct from the live "latest action" disposition (DIRTY/WIP/CONVO),
    which stays ephemeral in ``@aw_seg`` -- this only persists the slow,
    overall summary.

    Only the session summary is persisted (never the commit-subject fallback,
    which would lock in a poor title), and a finalized/completed worktree's
    curated PR/squash title is left untouched.  An AGENT-ASSERTED title
    (``agent-worktrees status --title``) is likewise authoritative and never
    clobbered.  A no-op when nothing changed, so per-tick writes don't churn the
    YAML.
    """
    if ctx is None:
        return
    if getattr(rec, "title_asserted", False):
        return  # agent-asserted --title is authoritative -- don't clobber
    if (rec.status or "").lower() in ("finalized", "complete", "completed"):
        return  # curated title -- don't clobber
    summary = ctx.latest_summary.get(_normalize_path(path), "")
    if not summary or summary == "null":
        return
    if (rec.title or "") == summary:
        return
    try:
        rec.title = summary
        tracking.save_record(rec)
    except Exception:
        pass


def _render_status_segment(
    path: str | None = None,
    fetch: bool = False,
    plain: bool = False,
    no_title: bool = False,
    persist_title: bool = False,
) -> str:
    """Render one styled status-bar segment for the worktree at the path/cwd.

    Returns the segment string (empty outside a git worktree).  The
    ``status-updater`` loop calls this in-process to refresh a session's
    ``@aw_seg`` option; ``cmd_status_segment`` is the thin print wrapper.

    Historically polled directly from a multiplexer status line::

        set -g status-right '#(agent-worktrees status-segment)'

    -- but that spawns a process per render, which psmux runs synchronously
    in the paint path (no #() caching like tmux), tanking responsiveness.
    The status bar now reads a precomputed ``#{@aw_seg}`` instead, refreshed
    off the paint path by ``status-updater``.

    Classifies the worktree's git disposition relative to its upstream
    default branch -- independent of any live session -- and prints::

        <title> #[bg=<color>] <STATE><markers><sync> #[default]

    States: ``DIRTY`` (uncommitted changes or commits ahead of upstream),
    ``FINAL`` (COMPLETED, and -- when a tracking record exists --
    genuinely claim-free/follow-up-free evidence refreshed via a successful
    ``--fetch``), ``MERGED`` (COMPLETED but not (yet) provably FINAL: no
    tracking record, a fetch-free/cached poll, a requested ``--fetch`` that
    itself failed, held claims, or open follow-ups -- see
    ``prune.assemble_closure_descriptor``), ``UNUSED`` (clean, no work and
    no conversation since the fork point), ``CONVO`` (clean, no commits but
    the session held conversation turns -- annotated with the turn count),
    ``WIP`` (clean, commits ahead whose content is not yet upstream),
    ``ORPHAN`` (no merge base with upstream). ``<markers>`` is the
    descriptor's compact `` C<N>``/`` F<N>`` suffix -- held-claim / open-
    follow-up counts, present on ANY state (not just MERGED) when a
    tracking record has them. ``<sync>`` is the picker's ``↑ahead``/
    ``↓behind`` tag.

    Fetch-free by default so it is cheap enough to poll on a short
    ``status-interval``; pass ``--fetch`` to refresh behind-counts from the
    remote AND to make a genuine ``FINAL`` reachable at all (a fetch-free
    poll can only ever report ``MERGED`` for a completed worktree, per
    design.md's cached-evidence-never-authorizes-FINAL rule -- and a
    requested ``--fetch`` that itself fails degrades the same way, never
    silently upgrading stale local refs to FINAL). Prints nothing (exit 0)
    outside a git worktree so a misconfigured status line never spams
    errors into the bar.
    """
    override = _core_helper("_render_status_segment", _render_status_segment)
    if override is not _render_status_segment:
        return override(
            path,
            fetch=fetch,
            plain=plain,
            no_title=no_title,
            persist_title=persist_title,
        )
    target = str(Path(path).resolve()) if path else os.getcwd()

    # Remote / default-branch.  The config gives a hint, but the segment may
    # run in any repo, so the real upstream branch is detected from git
    # (a `master` project binstub must still classify a `main` repo).
    remote, config_default = "origin", None
    try:
        repo = cfg.load_config(include_control_plane_related_pr=False).default_repo
        remote, config_default = repo.remote, repo.default_branch
    except Exception:
        pass
    default_branch = (
        _detect_upstream_branch(target, remote, config_default) or config_default or "master"
    )

    rec = _find_record_for_path(target)
    branch = rec.branch if rec else (git_ops._get_current_branch_safe(target) or "HEAD")

    try:
        info = git_ops.classify_worktree(
            target,
            branch,
            fetch=bool(fetch),
            remote=remote,
            default_branch=default_branch,
            active_paths=None,  # raw git disposition -- never ACTIVE
        )
    except Exception:
        return ""  # not a worktree / git failure -> empty bar, no noise

    if info.state == git_ops.WorktreeState.GONE:
        return ""

    if rec is not None:
        info = _apply_tracking_override(rec, info)

    # Session activity: scan once and reuse for both the turn-count
    # refinement and the title.  An UNUSED worktree (no commits) that held
    # conversation turns is "conversation-only" -- surface it distinctly so
    # it isn't mistaken for an idle/unused tree.
    ctx = None
    turns = 0
    if rec is not None:
        try:
            ctx = sessions.scan_sessions_fast([rec])
            turns = ctx.turn_count.get(_normalize_path(target), 0)
        except Exception:
            ctx, turns = None, 0

    sync = _sync_status_tag(info)
    refined_state = git_ops.refine_state_with_session(info.state, turns)
    refined_info = (
        dataclasses.replace(info, state=refined_state)
        if refined_state != info.state else info
    )

    if rec is not None:
        # worktree-finality-and-obligations (Phase 5): the mux/PSMux status
        # bar now consumes the same canonical closure descriptor list JSON
        # already publishes (Phase 4), instead of re-deriving its own
        # label/color from the raw git state -- so a worktree with a held
        # claim or an open follow-up renders that fact here too (`C<N>`/
        # `F<N>` markers), and a COMPLETED worktree is `FINAL` only when
        # genuinely claim-free/follow-up-free evidence says so, `MERGED`
        # otherwise. Fetch-free polling (the default) never reports FINAL --
        # matches design.md's "cached evidence never authorizes FINAL/safe".
        # "refreshed" requires the fetch to have both been REQUESTED and
        # actually attempted (`info.fetch_requested`) and succeeded (not
        # `info.fetch_failed`) -- a requested fetch that failed (network
        # down, remote unreachable) or a classification that short-circuited
        # before ever reaching the fetch step must not be treated as
        # authoritative current-state evidence either.
        held_claims = sum(1 for c in rec.resources if c.is_live)
        open_follow_ups = tracking.effective_open_follow_up_count(rec)
        disposition = prune.cleanup_disposition(
            rec, refined_info, turn_count=turns,
            claimant_alive=_local_claimant_alive,
            paired_sibling_final=prune.default_paired_sibling_final,
        )
        _fetch_fresh = info.fetch_requested and not info.fetch_failed
        if _fetch_fresh:
            # Phase 9: share this fetch's freshness with every other
            # worktree of this repo via the repo-scoped ledger.
            tracking.record_repo_fetch_confirmed(rec.repo)
        descriptor = prune.assemble_closure_descriptor(
            rec, refined_info, disposition,
            held_claims=held_claims, open_follow_ups=open_follow_ups,
            evidence_mode="refreshed" if _fetch_fresh else "cached",
            turn_count=turns,
            repo_fetch_fresh=tracking.is_repo_fetch_fresh(rec.repo),
        )
        bg = _DESCRIPTOR_STYLE_BG.get(descriptor.style, "colour238")
        block_label = descriptor.compact
        tag = f" {turns}\U0001f4ac" if refined_state == git_ops.WorktreeState.CONVO else sync
    else:
        # No tracking record -- held claims/follow-ups/disposition are
        # unknowable, so this falls back to the legacy raw-state label. A
        # COMPLETED worktree renders MERGED here, never FINAL: FINAL is a
        # claim-free/follow-up-free *proof*, and without a record there is no
        # evidence to prove it with (#discussion_r4008048471's sibling finding
        # -- an untracked completed worktree must not read as more settled
        # than a tracked one ever could without --fetch).
        if refined_state == git_ops.WorktreeState.CONVO:
            bg, block_label = _SEGMENT_STYLE[refined_state]
            tag = f" {turns}\U0001f4ac"
        elif refined_state == git_ops.WorktreeState.COMPLETED:
            bg, block_label = _DESCRIPTOR_STYLE_BG["merged-blocked"], "MERGED"
            tag = sync
        else:
            bg, block_label = _SEGMENT_STYLE.get(
                refined_state, ("colour238", refined_state.value.upper())
            )
            tag = sync

    if plain:
        block = f"[{block_label}{tag}]"
    else:
        block = f"#[bg={bg},fg=colour015,bold] {block_label}{tag} #[default]"

    parts: list[str] = []
    if not no_title:
        title = _resolve_segment_title(rec, target, info, ctx)
        if persist_title and rec is not None:
            _persist_segment_title(rec, target, ctx)
        if title:
            parts.append(title)
    parts.append(block)
    return " ".join(parts)


def _status_segment_json(path: str | None = None, fetch: bool = False) -> dict | None:
    """Cheap, single-worktree JSON snapshot of the status-segment's own facts.

    Mirrors :func:`_render_status_segment`'s classify pass -- the same
    non-daemon, fetch-free-by-default :func:`git_ops.classify_worktree` call
    -- but returns structured data instead of a rendered bar string. Built
    for a caller (the Mux Companion) that wants the current worktree's id,
    state, sync counts, and closure descriptor WITHOUT paying the resident
    classify daemon's whole-fleet negotiation cost that ``list --json
    --classify --worktree-id`` incurs even when scoped to one id (the daemon
    protocol has no per-id request shape, only project-wide filters, so a
    single-id caller still waits on/falls through a full-fleet round trip).

    Kept as a deliberate sibling of ``_render_status_segment`` rather than a
    shared refactor of it -- that function renders the live,
    every-``status-interval`` mux bar, so duplicating its (already carefully
    reviewed) classify prefix here is safer than reshaping it. Returns
    ``None`` outside a git worktree, exactly like the rendered segment
    returns an empty string.
    """
    override = _core_helper("_status_segment_json", _status_segment_json)
    if override is not _status_segment_json:
        return override(path, fetch=fetch)
    target = str(Path(path).resolve()) if path else os.getcwd()

    remote, config_default = "origin", None
    try:
        repo_cfg = cfg.load_config(include_control_plane_related_pr=False).default_repo
        remote, config_default = repo_cfg.remote, repo_cfg.default_branch
    except Exception:
        pass
    default_branch = (
        _detect_upstream_branch(target, remote, config_default) or config_default or "master"
    )

    rec = _find_record_for_path(target)
    branch = rec.branch if rec else (git_ops._get_current_branch_safe(target) or "HEAD")

    try:
        info = git_ops.classify_worktree(
            target,
            branch,
            fetch=bool(fetch),
            remote=remote,
            default_branch=default_branch,
            active_paths=None,
        )
    except Exception:
        return None

    if info.state == git_ops.WorktreeState.GONE:
        return None

    if rec is not None:
        info = _apply_tracking_override(rec, info)

    turns = 0
    if rec is not None:
        try:
            ctx = sessions.scan_sessions_fast([rec])
            turns = ctx.turn_count.get(_normalize_path(target), 0)
        except Exception:
            turns = 0

    refined_state = git_ops.refine_state_with_session(info.state, turns)
    refined_info = (
        dataclasses.replace(info, state=refined_state)
        if refined_state != info.state else info
    )

    closure = None
    if rec is not None:
        held_claims = sum(1 for c in rec.resources if c.is_live)
        open_follow_ups = tracking.effective_open_follow_up_count(rec)
        disposition = prune.cleanup_disposition(
            rec, refined_info, turn_count=turns,
            claimant_alive=_local_claimant_alive,
            paired_sibling_final=prune.default_paired_sibling_final,
        )
        _fetch_fresh = info.fetch_requested and not info.fetch_failed
        if _fetch_fresh:
            # Phase 9: share this fetch's freshness with every other
            # worktree of this repo via the repo-scoped ledger.
            tracking.record_repo_fetch_confirmed(rec.repo)
        descriptor = prune.assemble_closure_descriptor(
            rec, refined_info, disposition,
            held_claims=held_claims, open_follow_ups=open_follow_ups,
            evidence_mode="refreshed" if _fetch_fresh else "cached",
            turn_count=turns,
            repo_fetch_fresh=tracking.is_repo_fetch_fresh(rec.repo),
        )
        closure = descriptor.to_dict()

    return {
        "id": rec.worktree_id if rec is not None else None,
        "path": target,
        "repo": rec.repo if rec is not None else None,
        "branch": branch,
        "state": refined_state.value,
        "ahead": refined_info.ahead,
        "behind": refined_info.behind,
        "dirty": refined_info.dirty,
        "turn_count": turns,
        "status": rec.status if rec is not None else None,
        "closure": closure,
    }


def cmd_status_segment(args: argparse.Namespace) -> int:
    """Print the worktree status-bar segment (thin wrapper over the renderer)."""
    if getattr(args, "json", False):
        data = _status_segment_json(args.path, fetch=bool(args.fetch))
        _json_output(data if data is not None else {"error": "not a tracked git worktree"})
        return 0
    line = _render_status_segment(
        args.path,
        fetch=bool(args.fetch),
        plain=bool(args.plain),
        no_title=bool(args.no_title),
    )
    if line:
        print(line)
    return 0


def _platform_short(platform: str) -> str:
    """Map a stored platform name to its short worktree-id code.

    Mirrors the ``plat_short`` used when minting worktree ids
    (``windows`` -> ``win``; ``wsl`` / ``linux`` unchanged) so the status
    bar's environment label matches the id on disk.
    """
    return "win" if platform == "windows" else platform


# Environment badge background by OS type (darker colors -- white text on
# top stays readable).  Keyed on the short platform code from
# ``_platform_short``; unknown environments fall back to dark grey.
_ENV_BG: dict[str, str] = {
    "win": "colour025",  # Windows -- dark blue
    "wsl": "colour055",  # WSL -- purple
    "linux": "colour130",  # Linux -- dark orange
}


def _resolve_machine_alias(machine: str, repo: str | None) -> str:
    """Normalize a raw/tracked machine value through machines.yaml's alias
    resolution, mirroring ``cmd_machine_context``'s own resolution.

    A worktree's tracking record freezes ``machine`` at registration time (an
    older raw hostname/COMPUTERNAME), and ``cfg.detect_machine()`` returns the
    raw hostname when called with no ``repo_dir``. Neither path re-resolves
    through ``machines.yaml``'s ``hostname:``/``alias:`` mapping the way
    ``machine-context`` does, so a box whose COMPUTERNAME differs from its
    canonical mesh key/alias (dotfiles machines.yaml's decoupled-hostname
    convention) never gets its friendly name rendered here even though live
    detection resolves it correctly elsewhere. Fails open to the raw value on
    any error -- this must never break the status segment.
    """
    if not machine:
        return machine
    try:
        from . import repos as repos_mod

        repo_dir = repos_mod.resolve_path(repo) if repo else None
        if not repo_dir:
            repo_dir = _find_repo_dir()
        if not repo_dir:
            return machine
        entries = cfg.load_machines_yaml(repo_dir)
        entry = cfg.find_machine_entry(entries, machine)
        if entry is None:
            return machine
        return cfg.machine_name(entry)
    except Exception:
        return machine


def _render_status_context(path: str | None = None, plain: bool = False) -> str:
    """Render the left status-bar segment: machine, environment, repo:id.

    Returns the identity string (empty when no fields resolve).  Static for
    a session's lifetime, so ``status-updater`` renders it once into
    ``@aw_ctx``; ``cmd_status_context`` is the thin print wrapper.

    Renders three identity fields for the worktree the path is in::

        <machine>  <env-badge>  <repo>:<id4>

    where ``<machine>`` is the host designation (black text), ``<env>`` is
    the platform short code (``win``/``wsl``/``linux``, matching the
    worktree id) rendered as a colored badge keyed on OS type, and
    ``<id4>`` is the worktree id's 4-char suffix (its "last 4 digits").
    Values come from the worktree's tracking record when the path is
    inside a tracked worktree, falling back to live host detection. Either
    source is normalized through machines.yaml's alias resolution (see
    ``_resolve_machine_alias``) so a decoupled hostname/alias mapping (a
    shared-pool box whose COMPUTERNAME differs from its mesh key) renders
    its friendly name here too, matching ``machine-context``.
    """
    override = _core_helper("_render_status_context", _render_status_context)
    if override is not _render_status_context:
        return override(path, plain=plain)
    target = str(Path(path).resolve()) if path else os.getcwd()
    rec = _find_record_for_path(target)

    machine = (rec.machine if rec and rec.machine else "") or cfg.detect_machine()
    machine = _resolve_machine_alias(machine, rec.repo if rec else None)
    platform = (rec.platform if rec and rec.platform else "") or cfg.detect_platform()
    env = _platform_short(platform)

    repo = rec.repo if rec else ""
    suffix = rec.worktree_id.rsplit("-", 1)[-1] if rec and rec.worktree_id else ""
    locus = f"{repo}:{suffix}" if repo and suffix else (repo or "")

    fields = [f for f in (machine, env, locus) if f]
    if not fields:
        return ""

    if plain:
        return "  ".join(fields)

    bg = _ENV_BG.get(env, "colour238")
    styled: list[str] = []
    if machine:
        styled.append(f"#[fg=colour016,nobold]{machine}#[default]")
    if env:
        styled.append(f"#[bg={bg},fg=colour015,bold] {env} #[default]")
    if locus:
        styled.append(f"#[fg=colour016,bold]{locus}#[default]")
    # Lead with a style directive so the 1-char left padding is not trimmed.
    return "#[default] " + " ".join(styled)


def cmd_status_context(args: argparse.Namespace) -> int:
    """Print the left identity segment (thin wrapper over the renderer)."""
    line = _render_status_context(args.path, plain=bool(args.plain))
    if line:
        print(line)
    return 0
