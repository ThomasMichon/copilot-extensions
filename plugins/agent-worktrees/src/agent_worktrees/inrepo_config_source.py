"""Resolve a repo's committed in-repo config independent of the anchor's
working tree.

``config.py``'s ``_load_inrepo_config`` used to read
``.agent-worktrees/config.yaml`` straight off the anchor's checked-out files.
The anchor is a read-only mirror by design (agents never edit it in place --
see the ``anchor-write-guard``), but nothing kept its *checked-out branch* in
sync with the repo's real default: an anchor left on a stale or wrong local
branch silently fed a stale or wrong ``default_branch`` (and every other
in-repo setting) into every command that resolves the repo, including where
``create``/``create-pr`` fork worktrees and target PRs from.

:func:`load_inrepo_config_from_committed_ref` reads the config as actually
**committed**, via ``git show`` -- entirely independent of the anchor's
working tree or index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import git_ops


def parse_yaml_text_safe(text: str) -> dict[str, Any]:
    """Parse a YAML string into a dict, returning ``{}`` on any problem.

    The blob-reading sibling of ``config._load_yaml_safe`` -- for content
    read via ``git show`` (a string), never a file on disk.
    """
    try:
        raw = yaml.safe_load(text)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _offline_remote_head_branch(anchor: Path, remote: str) -> str | None:
    """The branch ``<remote>/HEAD`` currently points at, read offline from the
    local ref store (never a network call) -- or ``None`` if unset/unknown."""
    try:
        proc = git_ops.git(
            "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD",
            cwd=anchor, check=False, capture=True, timeout=3,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    prefix = f"refs/remotes/{remote}/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def _offline_remote_branch_exists(anchor: Path, remote: str, branch: str) -> bool:
    """Cheap, offline existence check for ``<remote>/<branch>`` in the local
    ref store (no network)."""
    try:
        proc = git_ops.git(
            "show-ref", "--verify", "--quiet", f"refs/remotes/{remote}/{branch}",
            cwd=anchor, check=False, capture=True, timeout=3,
        )
    except Exception:
        return False
    return proc.returncode == 0


def _read_committed_blob(
    anchor: Path, remote: str, branch: str, rel_path: Path
) -> str | None:
    """Text content of ``rel_path`` as committed on ``<remote>/<branch>`` --
    read via ``git show``, so it never depends on (or touches) the anchor's
    working tree or index. ``None`` if the ref or path doesn't exist."""
    spec = f"{remote}/{branch}:{rel_path.as_posix()}"
    try:
        proc = git_ops.git("show", spec, cwd=anchor, check=False, capture=True, timeout=5)
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def load_inrepo_config_from_committed_ref(
    anchor: Path, rel_path_candidates: tuple[Path, ...], remote: str = "origin"
) -> dict[str, Any]:
    """The in-repo config as actually **committed** on the resolved default
    branch, checked in ``rel_path_candidates`` order (first match wins) --
    independent of whatever the anchor's working tree has checked out.

    Resolution is two-hop and stays fully offline (no ``ls-remote`` -- this
    sits on the hot ``load_config`` path):

    1. Start from ``<remote>/HEAD``'s branch -- git's own recorded default,
       independent of any local checkout.
    2. Read that branch's committed config. If it declares a *different*
       ``default_branch`` that also exists as ``<remote>/<that name>``, the
       declaring branch is naming a more current source for its own setting
       (the dev/main split this exists for -- ``dev`` is the contribution
       branch, periodically promoted to ``main``) -- hop once more and read
       from there instead.

    Returns ``{}`` when nothing can be resolved this way (no remote, no
    fetched tracking refs, or the anchor isn't a git repo at all); callers
    fall back to the working-tree read in that case.
    """
    head_branch = _offline_remote_head_branch(anchor, remote)
    if head_branch is None:
        return {}

    def _read_config_on(branch: str) -> dict[str, Any] | None:
        for rel_path in rel_path_candidates:
            text = _read_committed_blob(anchor, remote, branch, rel_path)
            if text is not None:
                return parse_yaml_text_safe(text)
        return None

    data = _read_config_on(head_branch)
    if data is None:
        return {}

    declared = data.get("default_branch")
    if (
        isinstance(declared, str)
        and declared
        and declared != head_branch
        and _offline_remote_branch_exists(anchor, remote, declared)
    ):
        hopped = _read_config_on(declared)
        if hopped is not None:
            data = hopped
    return data
