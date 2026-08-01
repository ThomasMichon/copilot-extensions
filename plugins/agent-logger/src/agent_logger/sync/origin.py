"""Session origin derivation + sidecar marking.

Each session records its **origin** -- the source *harness* repo it was worked
in (matched against the machine's configured harness repos) plus the machine, or
a **machine-only** marker when no harness resolves. The mark is a per-session-dir
sidecar (``origin.json``) that syncs with the session, so any downstream logger
daemon can route by origin without re-parsing ``workspace.yaml``.

This is the foundation of origin-routed filing (aperture-labs effort
``origin-routed-logging``; visions: agent-logger ``origin-routed-filing`` /
``derive-the-origin-never-guess`` and permanent-record ``origin-faithful
routing``). Deriving-not-guessing: the origin comes from the session's own
recorded ``workspace.yaml`` paths, or falls back to the machine default
explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path

ORIGIN_SIDECAR = "origin.json"
SCHEMA_VERSION = 1

# workspace.yaml keys carrying a filesystem origin, in match precedence.
_ORIGIN_KEYS = ("git_root", "repository", "cwd")


def _read_workspace_paths(session_dir: Path) -> list[tuple[str, str]]:
    """Return ``(basis, value)`` pairs from a session's ``workspace.yaml``.

    Only the origin-bearing keys (``git_root`` / ``repository`` / ``cwd``) are
    returned, in that precedence order. Missing file or a read error yields an
    empty list (the caller then falls back to the machine default).
    """
    ws = session_dir / "workspace.yaml"
    if not ws.is_file():
        return []
    found: dict[str, str] = {}
    try:
        with open(ws, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                for key in _ORIGIN_KEYS:
                    prefix = f"{key}:"
                    if line.startswith(prefix):
                        val = line[len(prefix):].strip()
                        if val and key not in found:
                            found[key] = val
    except OSError:
        return []
    return [(k, found[k]) for k in _ORIGIN_KEYS if k in found]


def derive_origin(session_dir: Path, machine: str,
                  harness_repos: list[str]) -> dict:
    """Derive a session's origin as ``{machine, source_repo|None, basis}``.

    ``source_repo`` is the first configured harness repo whose name appears
    (case-insensitive substring) in the session's ``git_root`` / ``repository``
    / ``cwd`` (worktree-safe: a path like ``.../aperture-labs.worktrees/...``
    still resolves to ``aperture-labs``). When none matches -- no
    ``workspace.yaml``, no path, or an unrecognized (non-harness) repo -- the
    origin falls back to machine-only (``source_repo=None``,
    ``basis='machine-default'``). It never guesses which machine ran the sync.
    """
    for basis, value in _read_workspace_paths(session_dir):
        low = value.lower()
        for repo in harness_repos:
            if repo and repo.lower() in low:
                return {
                    "schema_version": SCHEMA_VERSION,
                    "machine": machine,
                    "source_repo": repo,
                    "basis": basis,
                }
    return {
        "schema_version": SCHEMA_VERSION,
        "machine": machine,
        "source_repo": None,
        "basis": "machine-default",
    }


def _payload(origin: dict) -> str:
    return json.dumps(origin, indent=2, sort_keys=True) + "\n"


def write_origin_sidecar(session_dir: Path, origin: dict) -> bool:
    """Write ``origin.json`` into ``session_dir``. Idempotent: returns ``False``
    when the file already holds the identical payload, ``True`` when written."""
    path = session_dir / ORIGIN_SIDECAR
    payload = _payload(origin)
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


def mark_all(source: Path, machine: str, harness_repos: list[str],
             *, dry_run: bool = False) -> dict:
    """Ensure every local session-state dir carries an origin sidecar.

    Returns ``{total, marked, by_repo}``. Marks **all** local sessions (not just
    the synced/allowlisted ones), so the origin is available locally to
    distinguish personal vs work regardless of what syncs. ``dry_run`` derives
    and counts without writing.
    """
    ss = source / "session-state"
    summary: dict = {"total": 0, "marked": 0, "by_repo": {}}
    if not ss.is_dir():
        return summary
    for entry in sorted(ss.iterdir()):
        if not entry.is_dir():
            continue
        summary["total"] += 1
        origin = derive_origin(entry, machine, harness_repos)
        key = origin["source_repo"] or "(machine-only)"
        summary["by_repo"][key] = summary["by_repo"].get(key, 0) + 1
        if not dry_run and write_origin_sidecar(entry, origin):
            summary["marked"] += 1
    return summary
