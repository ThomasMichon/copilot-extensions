"""Worktree identity resolution -- map CWD / a raw ID to a canonical worktree ID.

Extracted from ``__main__.py`` (module-size-baseline split, copilot-extensions
#2614): these functions have no dependency on the CLI entry point itself, only
on ``git_ops``/``tracking``/``config``/``output``, so they live here instead of
being re-imported back from ``__main__`` by sibling CLI modules (``pr_cli.py``,
``pr_merge_cli.py``, ``session_tracking_cli.py``) -- a ``from . import __main__``
reverse-reference from a non-entry-point module is unsafe: when the real entry
point is executed via ``python -m agent_worktrees``, that reverse import forces
a *second*, independent execution of ``__main__.py`` under the distinct module
name ``agent_worktrees.__main__`` (Python never treats the ``__main__``-run
script and an explicit ``package.__main__`` import as the same module object),
which can observe (and crash on) the first execution's own not-yet-defined
late bindings.  See the fix for copilot-extensions#2614's regression.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config as cfg
from . import git_ops, output, tracking


def _worktree_id_from_git(cwd: Path) -> str | None:
    """Return the git-internal worktree name if CWD is inside a *linked*
    worktree, else None.

    Git has no notion of a shared "worktree root": ``git worktree add <path>``
    places a worktree at an arbitrary folder and records its admin data at
    ``<main-repo>/.git/worktrees/<name>/``. From anywhere inside a linked
    worktree, ``git rev-parse --git-dir`` therefore resolves to
    ``<main-repo>/.git/worktrees/<name>`` -- and ``<name>`` is exactly the
    agent-worktrees ID (we name the git worktree after the ID). The *main*
    worktree's git-dir is plain ``.git`` (no ``worktrees/`` parent), which
    yields no id. This is layout-independent, so it survives a ``worktree_root``
    change (copilot-extensions#59).
    """
    try:
        result = git_ops.git("rev-parse", "--git-dir", cwd=str(cwd), check=False, timeout=10)
    except Exception:
        return None
    if result.returncode != 0:
        return None
    git_dir = (result.stdout or "").strip()
    if not git_dir:
        return None
    p = Path(git_dir)
    if not p.is_absolute():
        p = (cwd / p).resolve()
    # A linked worktree's git-dir is ``.../.git/worktrees/<name>``.
    if p.parent.name == "worktrees" and p.name and p.name != "worktrees":
        return p.name
    return None


def _linked_worktree_root(cwd: str | Path) -> Path | None:
    """Return git's actual linked-worktree root, never a configured projection."""
    candidate = Path(cwd).resolve()
    if not _worktree_id_from_git(candidate):
        return None
    try:
        result = git_ops.git(
            "rev-parse",
            "--show-toplevel",
            cwd=str(candidate),
            check=False,
            timeout=10,
        )
    except Exception:
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _worktree_path_for_id(
    config: cfg.Config,
    worktree_id: str | None,
    *,
    cwd: str | Path,
) -> str:
    """Resolve a worktree ID to its authoritative path."""
    if not worktree_id:
        return ""
    linked_root = _linked_worktree_root(cwd)
    if linked_root is not None and _worktree_id_from_git(linked_root) == worktree_id:
        return str(linked_root)
    record = tracking.load_record_by_id(worktree_id)
    if record is not None and record.worktree_path:
        return record.worktree_path
    return str(Path(config.default_repo.worktree_root) / worktree_id)


def _adopt_linked_worktree(cwd: str | Path) -> str | None:
    """Track a linked worktree created by an external session host."""
    root = _linked_worktree_root(cwd)
    worktree_id = _worktree_id_from_git(root) if root is not None else None
    if root is None or not worktree_id or not cfg.active_project():
        return None
    try:
        config = cfg.load_config()
        anchor = Path(config.default_repo.anchor).resolve()
        linked_anchor = git_ops.resolve_to_anchor(root).resolve()
        if linked_anchor != anchor or git_ops._normalize_wt_path(
            str(root)
        ) == git_ops._normalize_wt_path(str(anchor)):
            return None
        branch = git_ops.current_branch(root)
        if not branch:
            return None
        record, _created = tracking.create_new_record_if_absent(
            worktree_id=worktree_id,
            branch=branch,
            worktree_path=str(root),
            repo=config.repo_name,
            machine=config.machine,
            platform_name=config.platform,
            tracking_path=cfg.tracking_dir(),
            interface="cli",
            origin="user",
            checkout_managed=False,
        )
    except Exception:
        return None
    if record.repo != config.repo_name or git_ops._normalize_wt_path(
        record.worktree_path
    ) != git_ops._normalize_wt_path(str(root)):
        return None
    return worktree_id


def _infer_worktree_id_from_worktree_root(config: cfg.Config | None, cwd: Path) -> str | None:
    """Legacy fallback: derive the ID from the first path component under the
    configured ``worktree_root``.

    Superseded by git-based + tracked-path resolution in
    :func:`_infer_worktree_id_from_cwd`; retained only as a last resort. This
    single-root assumption is exactly what copilot-extensions#59 fixed (it
    silently failed for worktrees created under a *previous* ``worktree_root``
    layout), so do not rely on it as the primary path.
    """
    try:
        if config is None:
            config = cfg.load_config()
        wt_root = Path(config.default_repo.worktree_root).resolve()
    except Exception:
        return None

    try:
        rel = cwd.relative_to(wt_root)
    except ValueError:
        return None

    if not rel.parts:
        return None  # CWD is exactly worktree_root

    candidate = rel.parts[0]

    # Validate: a tracking YAML should exist for this candidate
    yaml_path = cfg.tracking_dir() / f"{candidate}.yaml"
    if yaml_path.exists():
        return candidate

    # Even without a tracking file, if the directory exists under
    # worktree_root and has a .git entry it's a valid worktree
    wt_dir = wt_root / candidate
    if wt_dir.is_dir() and (wt_dir / ".git").exists():
        return candidate

    return None


def _infer_worktree_id_from_cwd(
    config: cfg.Config | None = None,
) -> str | None:
    """Derive the worktree ID from the current working directory.

    Identity is resolved the way git itself resolves a linked worktree --
    **independent of any configured ``worktree_root``**. Because git records a
    worktree at an arbitrary path (there is no shared "root" in git's model),
    the current worktree is identifiable from anywhere inside it, regardless of
    where it lives on disk. This keeps inference correct across a
    ``worktree_root`` layout change: worktrees created under an older root are
    still resolved (copilot-extensions#59).

    Resolution order (each root-independent; the legacy single-root scan is
    only a last resort):
      1. **git's own identity** -- ``git rev-parse --git-dir`` under a linked
         worktree is ``.../.git/worktrees/<name>``; ``<name>`` is the tracking
         ID. Authoritative even when the tracking YAML is briefly absent. When
         no tracking YAML exists at all (a worktree `git worktree add`-ed by an
         external host -- a GitHub-App/coding-agent session, a hand-run git
         command, or any environment where agent-worktrees' own sessionStart
         hook never ran), auto-adopts it on the spot (:func:`_adopt_linked_worktree`)
         so every caller -- not just the hook -- can bind ownership on first
         use. A worktree is never "unadoptable" merely because a prior session
         in a different environment created it without our tooling's help.
      2. **tracked-path match** -- match CWD against each record's recorded
         ``worktree_path`` (:func:`tracking.find_worktree_id_by_cwd`,
         deepest-match wins).
      3. **legacy ``worktree_root`` prefix scan** -- the pre-fix single-root
         assumption, kept only for safety.
    """
    cwd = Path.cwd().resolve()
    tdir = cfg.tracking_dir()

    # 1. Ask git. A linked worktree's git-dir names the worktree directly.
    git_id = _worktree_id_from_git(cwd)
    if git_id:
        if (tdir / f"{git_id}.yaml").exists():
            return git_id
        # Tracking YAML missing: this may be a linked worktree that was never
        # created through `agent-worktrees create` -- e.g. a GitHub-App/coding
        # -agent session, or any external host that ran `git worktree add`
        # directly. Auto-adopt it now (best-effort) so every caller of this
        # shared inference (create-pr, pr-status, finalize, push-changes, ...)
        # can bind ownership on first use, rather than depending on the
        # sessionStart hook having already run in that environment -- which it
        # never will when agent-worktrees isn't even installed there. Falls
        # through to the pre-existing path-match-or-git_id behavior when
        # adoption isn't possible (no active project, foreign anchor, etc.).
        adopted_id = _adopt_linked_worktree(cwd)
        if adopted_id:
            return adopted_id
        # Tracking YAML briefly missing: prefer a path match if one exists,
        # else trust git's authoritative identity.
        return tracking.find_worktree_id_by_cwd(str(cwd)) or git_id

    # 2. Match CWD against recorded worktree paths (root-independent).
    path_id = tracking.find_worktree_id_by_cwd(str(cwd))
    if path_id:
        return path_id

    # 3. Legacy single-root scan (last resort; the #59 failure mode).
    return _infer_worktree_id_from_worktree_root(config, cwd)


def _resolve_worktree_id(raw_id: str) -> str:
    """Canonicalize a worktree ID, resolving short suffixes.

    If ``raw_id`` matches a tracking file directly, return as-is.
    Otherwise, search for tracking files whose stem ends with the
    given suffix.  Raises ``SystemExit`` on ambiguous or invalid IDs.
    """
    # Reject IDs with path-traversal or glob metacharacters
    if re.search(r"[/\\]|\.\.", raw_id):
        output.err(f"Invalid worktree ID: {raw_id}")
        raise SystemExit(1)

    tdir = cfg.tracking_dir()

    # Exact match -- fast path
    if (tdir / f"{raw_id}.yaml").exists():
        return raw_id

    # Suffix match: iterate tracking files whose stems end with raw_id
    matches = [p.stem for p in tdir.glob("*.yaml") if p.stem.endswith(raw_id)]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        short_list = ", ".join(sorted(m[-12:] for m in matches))
        output.err(f"Ambiguous short ID '{raw_id}' matches {len(matches)} worktrees: {short_list}")
        raise SystemExit(1)

    # No tracking match -- return as-is (caller will fail on missing YAML)
    return raw_id
