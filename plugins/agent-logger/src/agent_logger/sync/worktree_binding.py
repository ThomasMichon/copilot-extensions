"""Worktree-binding derivation + sidecar marking (session-worktree-archive
-linkout Phase 3, aperture-labs).

Mirrors :mod:`agent_logger.sync.origin` exactly: a per-session-dir sidecar
(``worktree.json``) that syncs with the session, so a downstream consumer
(Permanent Record's archive-side worktree correlation) can read an
**authoritative** ``worktree_id`` written at sync time -- while the live
``agent-worktrees`` binding for that session was still available on the
machine that ran it -- rather than reconstructing one after the fact from a
best-effort CWD-pattern match against an archived, possibly-gone worktree.

This closes the loop for **future** sessions only. Permanent Record's own
``correlate_cwd()`` reconstruction pass
(``efforts/active/session-worktree-archive-linkout`` Phase 1, aperture-labs)
remains the one-time (plus ongoing best-effort) backfill for **past**
sessions synced before this module existed.

``agent-worktrees`` is a **soft** dependency, resolved lazily and only at the
point of use -- mirroring the same optional cross-plugin import
``agent_codespaces.config._registered_repo_paths`` already uses for
``agent_worktrees.repos``. agent-logger's own ``pyproject.toml`` does not (and
should not) declare a hard dependency on a sibling plugin; a machine that
happens to have both installed (the normal facility case) gets proactive
worktree binding, one that has only agent-logger degrades to a no-op sidecar
absence, not an error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .origin import _read_workspace_paths

WORKTREE_SIDECAR = "worktree.json"
SCHEMA_VERSION = 1

# The confidence label Permanent Record's own correlation schema reserves for
# a proactively-written (not reconstructed) binding -- see this effort's
# Phase 1 doc (`processed_sessions.worktree_confidence`).
PROACTIVE_CONFIDENCE = "proactive"

# workspace.yaml keys that may carry the session's own cwd, in match
# precedence -- mirrors ``origin._ORIGIN_KEYS`` but ``cwd`` is tried first
# here: it is the most literal match for `find_worktree_id_by_cwd`, whereas
# `git_root`/`repository` are origin-derivation's own precedence (a repo
# identity match, not necessarily the exact worktree-scoped directory).
_CWD_KEYS = ("cwd", "git_root", "repository")


def _find_worktree_lookup() -> tuple[Any, Any] | None:
    """Lazily resolve agent-worktrees' two binding-lookup functions.

    Returns ``None`` when ``agent-worktrees`` is not installed alongside
    agent-logger in the current environment -- the caller then treats every
    session as unresolvable (no proactive binding), never an error.
    """
    try:
        from agent_worktrees.tracking import (
            find_worktree_id_by_cwd,
            find_worktree_id_by_session,
        )
    except ImportError:
        return None
    return find_worktree_id_by_cwd, find_worktree_id_by_session


def derive_worktree_binding(
    session_dir: Path, origin: dict | None,
) -> dict | None:
    """Derive a session's worktree binding, or ``None`` if unresolvable.

    ``origin`` is the session's already-derived origin dict (see
    :func:`agent_logger.sync.origin.derive_origin`) -- its ``source_repo`` is
    the harness project name this binding is scoped to. A session with no
    resolved harness (a machine-only origin) has no project to search and is
    always unresolvable here: agent-worktrees' tracking is per-project, and
    guessing a project would violate the same reconstruction-honesty
    discipline Permanent Record's own correlation pass follows.

    Resolution order per session, first hit wins:
    1. ``find_worktree_id_by_cwd`` against each of ``cwd`` / ``git_root`` /
       ``repository`` recorded in ``workspace.yaml`` (in that order).
    2. ``find_worktree_id_by_session`` keyed by the session directory's own
       name (the session id) -- the identity fallback for a resumed session
       whose recorded cwd no longer reflects its true worktree (mirrors the
       same fallback the sessionStart hook itself uses).
    """
    if not origin:
        return None
    source_repo = origin.get("source_repo")
    if not source_repo:
        return None
    lookup = _find_worktree_lookup()
    if lookup is None:
        return None
    find_by_cwd, find_by_session = lookup

    # ``_read_workspace_paths`` returns pairs in ITS OWN precedence order
    # (git_root, repository, cwd) -- re-key and walk `_CWD_KEYS`' own order
    # (cwd first) instead, since a cwd-based worktree match is the most
    # literal fit for `find_worktree_id_by_cwd`.
    found = dict(_read_workspace_paths(session_dir))
    for key in _CWD_KEYS:
        value = found.get(key)
        if not value:
            continue
        worktree_id = find_by_cwd(value, project=source_repo)
        if worktree_id:
            return {
                "schema_version": SCHEMA_VERSION,
                "worktree_id": worktree_id,
                "confidence": PROACTIVE_CONFIDENCE,
                "basis": f"cwd:{key}",
            }

    session_id = session_dir.name
    worktree_id = find_by_session(session_id, project=source_repo)
    if worktree_id:
        return {
            "schema_version": SCHEMA_VERSION,
            "worktree_id": worktree_id,
            "confidence": PROACTIVE_CONFIDENCE,
            "basis": "session_id",
        }
    return None


def _payload(binding: dict) -> str:
    return json.dumps(binding, indent=2, sort_keys=True) + "\n"


def read_worktree_sidecar(session_dir: Path) -> dict | None:
    """Read a session's ``worktree.json`` sidecar; return its dict or ``None``.

    ``None`` means no resolvable *recorded* binding -- either the sidecar is
    absent, unreadable, or malformed.
    """
    path = session_dir / WORKTREE_SIDECAR
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_worktree_sidecar(session_dir: Path, binding: dict) -> bool:
    """Write ``worktree.json`` into ``session_dir``. Idempotent: returns
    ``False`` when the file already holds the identical payload, ``True``
    when written."""
    path = session_dir / WORKTREE_SIDECAR
    payload = _payload(binding)
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == payload:
                return False
        except OSError:
            pass
    try:
        path.write_text(payload, encoding="utf-8")
    except OSError:
        return False
    return True


def mark_all_worktrees(source: Path, *, dry_run: bool = False) -> dict:
    """Ensure every local session-state dir carries a worktree-binding
    sidecar, where one is resolvable.

    Must run **after** :func:`agent_logger.sync.origin.mark_all` in the same
    sync pass -- each session's own ``origin.json`` (already on disk) supplies
    the harness-project scope this function resolves worktree bindings
    against. A session whose origin sidecar is missing or machine-only is
    skipped, not guessed at.

    Returns ``{total, marked, unresolved}``. ``dry_run`` derives and counts
    without writing. Fully no-op (all sessions counted ``unresolved``, never
    an error) when ``agent-worktrees`` is not installed alongside
    agent-logger in the current environment.
    """
    from .origin import read_origin_sidecar

    ss = source / "session-state"
    summary: dict = {"total": 0, "marked": 0, "unresolved": 0}
    if not ss.is_dir():
        return summary
    for entry in sorted(ss.iterdir()):
        if not entry.is_dir():
            continue
        summary["total"] += 1
        origin = read_origin_sidecar(entry)
        binding = derive_worktree_binding(entry, origin)
        if binding is None:
            summary["unresolved"] += 1
            continue
        if not dry_run and write_worktree_sidecar(entry, binding):
            summary["marked"] += 1
    return summary
