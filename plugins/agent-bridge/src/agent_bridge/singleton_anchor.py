"""Singleton-repo anchor-key helpers for worktree-shaped session ownership."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_flags

from .agent_registry import _agent_worktrees_bin, _normalize_repo_basename

log = logging.getLogger("agent-bridge.singleton-anchor")

ANCHOR_SUFFIX = "@anchor"
_QUERY_TIMEOUT_S = 15.0
_SCRUBBED_ENV_KEYS = (
    "VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH",
)


@dataclass(frozen=True)
class SingletonRepo:
    """A singleton-class repo plus its local anchor path, when known."""

    name: str
    path: str = ""


def anchor_session_key(repo_name: str) -> str:
    """Stable pseudo-worktree id for a singleton repo's anchor checkout."""
    return f"{repo_name}{ANCHOR_SUFFIX}"


def repo_name_from_anchor_key(key: str) -> str | None:
    """Return the repo portion of ``<repo>@anchor``, or None."""
    if not key or not key.endswith(ANCHOR_SUFFIX):
        return None
    repo = key[: -len(ANCHOR_SUFFIX)].strip()
    return repo or None


def _choose_local_path(raw_paths: object) -> str:
    """Pick the best local path from a repos-list ``paths`` payload."""
    candidates: list[str] = []
    if isinstance(raw_paths, dict):
        candidates.extend(
            str(v).strip()
            for v in raw_paths.values()
            if isinstance(v, str) and str(v).strip()
        )
    elif isinstance(raw_paths, str) and raw_paths.strip():
        candidates.append(raw_paths.strip())
    for raw in candidates:
        path = Path(raw).expanduser()
        if path.exists():
            return str(path)
    if candidates:
        return str(Path(candidates[0]).expanduser())
    return ""


def parse_singleton_repos_payload(stdout: str | None) -> list[SingletonRepo]:
    """Parse ``agent-worktrees repos list --class singleton --json`` output."""
    try:
        doc = json.loads(stdout or "{}")
    except (TypeError, ValueError):
        log.debug("agent-worktrees repos list --class singleton emitted non-JSON")
        return []
    repos = doc.get("repos") if isinstance(doc, dict) else None
    if not isinstance(repos, list):
        return []
    out: list[SingletonRepo] = []
    for entry in repos:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        out.append(SingletonRepo(name=name, path=_choose_local_path(entry.get("paths"))))
    return out


def list_singleton_repos() -> list[SingletonRepo]:
    """Live-query agent-worktrees for singleton-class repos (fail open)."""
    exe = _agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- no singleton repo registry")
        return []
    child_env = {
        k: v for k, v in os.environ.items() if k not in _SCRUBBED_ENV_KEYS
    }
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "repos", "list", "--class", "singleton", "--json"],
            capture_output=True,
            text=True,
            timeout=_QUERY_TIMEOUT_S,
            check=False,
            creationflags=no_window_flags(),
            env=child_env,
        )
    except Exception as exc:
        log.warning("agent-worktrees singleton repo query failed: %s", exc)
        return []
    if proc.returncode != 0:
        log.debug(
            "agent-worktrees repos list --class singleton exited %s",
            proc.returncode,
        )
        return []
    return parse_singleton_repos_payload(proc.stdout)


def find_singleton_repo(target: str) -> SingletonRepo | None:
    """Resolve a repo key/name to a singleton-class repo, or None."""
    want = _normalize_repo_basename(target)
    for repo in list_singleton_repos():
        if _normalize_repo_basename(repo.name) == want:
            return repo
    return None
