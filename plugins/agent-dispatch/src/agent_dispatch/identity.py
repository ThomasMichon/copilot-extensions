"""Resolve the calling agent's identity (machine + worktree) and repo from context.

The only durable agent id the facility has is the ``machine`` + ``worktree``
pair, and the authority on "which worktree am I in" is **agent-worktrees**, which
resolves it from the current directory (the way git resolves its repo). So
agent-dispatch *delegates* to the ``agent-worktrees`` CLI when present -- a soft
dependency, like the agent-bridge integration -- rather than reading an env var
or re-implementing the resolution. It degrades to explicit ``--machine`` /
``--worktree`` when agent-worktrees is absent (e.g. outside the facility) or the
caller isn't inside a worktree.

The same delegation resolves the caller's **repo** (the lane). A task belongs to
the repo of the agent that produced it -- repos stay in their own lanes, so an
agent only sees and claims tasks for its own harness repo (cross-repo *code*
targets are handled by that agent via ``working-cross-repo``, never by launching
another repo's harness). The lane key is a **canonical remote** so it is
device-independent (a shared coordinator keys every machine the same) while the
CLI/UX speaks the local repo *name* via the agent-worktrees registry.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache


def _aw_get(key: str) -> str | None:
    """Return `agent-worktrees get <key>` (CWD-resolved), or None if unavailable."""
    exe = shutil.which("agent-worktrees")
    if exe is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "get", key], check=False, capture_output=True, text=True, timeout=15
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def resolve_identity() -> tuple[str | None, str | None]:
    """Resolve the caller's ``(machine, worktree)`` from CWD via agent-worktrees.

    Either element may be ``None`` when agent-worktrees is absent or the caller
    isn't inside a worktree -- callers then supply the value explicitly.
    """
    machine = _aw_get("machine")
    wt_dir = _aw_get("worktree-dir")
    worktree = os.path.basename(wt_dir.rstrip("/\\")) if wt_dir else None
    return (machine, worktree)


def canonicalize_remote(url: str | None) -> str | None:
    """Reduce a git remote URL to a device- and protocol-independent lane key.

    ``https://user@host:443/path/owner/repo.git`` and
    ``git@host:path/owner/repo.git`` both collapse to ``host/path/owner/repo``:
    scheme, userinfo, port, a trailing ``.git`` and surrounding slashes are
    stripped and the host is lowercased (paths are left as-is -- some hosts are
    path-case-sensitive). This makes an ssh and an https clone of the same repo
    match, and keeps the key stable across machines. Returns ``None`` for empty.
    """
    if not url:
        return None
    s = url.strip()
    if not s:
        return None
    # Strip scheme (scheme://) or scp-style (user@host:path).
    if "://" in s:
        s = s.split("://", 1)[1]
        if "@" in s.split("/", 1)[0]:  # userinfo before the first path segment
            s = s.split("@", 1)[1]
        host, _, path = s.partition("/")
    elif "@" in s and ":" in s.split("@", 1)[1]:
        # scp-style git@host:owner/repo
        s = s.split("@", 1)[1]
        host, _, path = s.partition(":")
    else:
        host, _, path = s.partition("/")
    host = host.split(":", 1)[0].lower()  # drop any :port
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    canonical = f"{host}/{path}".strip("/")
    return canonical or None


def resolve_repo() -> str | None:
    """Canonical remote (lane key) for the repo the caller is working in.

    Prefers ``agent-worktrees get repo-remote`` (the registry remote, consistent
    across machines); falls back to ``git remote get-url origin`` in the CWD when
    agent-worktrees is absent. ``None`` when neither resolves -- the CLI then
    requires an explicit ``--repo``.
    """
    raw = _aw_get("repo-remote") or _git_origin()
    return canonicalize_remote(raw)


def _git_origin() -> str | None:
    """`git remote get-url origin` in the CWD, or None."""
    exe = shutil.which("git")
    if exe is None:
        return None
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "remote", "get-url", "origin"],
            check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@lru_cache(maxsize=1)
def _repo_registry() -> tuple[tuple[str, str], ...]:
    """(local_name, canonical_remote) pairs from ``agent-worktrees repos list``.

    Cached per process. Empty when agent-worktrees is absent. Backs the hybrid
    UX: the caller types/reads a local repo *name*, the wire carries the
    canonical remote.
    """
    exe = shutil.which("agent-worktrees")
    if exe is None:
        return ()
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "repos", "list", "--json"],
            check=False, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
        return ()
    import json
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return ()
    pairs: list[tuple[str, str]] = []
    for entry in data.get("repos", []):
        name = entry.get("name")
        canon = canonicalize_remote(entry.get("remote"))
        if name and canon:
            pairs.append((name, canon))
    return tuple(pairs)


def resolve_repo_selector(selector: str | None) -> str | None:
    """Turn a ``--repo`` value (a local *name* or a remote URL) into a lane key.

    A registry name resolves to its canonical remote; anything else is treated
    as a remote URL and canonicalized directly. ``None`` passes through.
    """
    if not selector:
        return None
    for name, canon in _repo_registry():
        if selector == name:
            return canon
    return canonicalize_remote(selector)


def name_for_repo(canonical: str | None) -> str | None:
    """Reverse a canonical remote to its local repo *name* for display, if known."""
    if not canonical:
        return None
    for name, canon in _repo_registry():
        if canon == canonical:
            return name
    return None

