"""Git-evidence resolution helpers for ``check-agent-bridge-contracts.py``.

Split out to keep the main checker under its line-count cap (see
``check-module-size.py``). These are pure Git plumbing: commit/blob
resolution (opportunistic across a squash-merge-orphaned commit), a source
file's hash at a historical revision, and an integer module-level constant
at a historical revision.

Commit-based lookups return ``None``/``False`` once a commit is genuinely
unresolvable -- callers treat that as "evidence unavailable", falling back
to the content-addressed blob helpers below rather than failing outright.
"""
from __future__ import annotations

import ast
import hashlib
from functools import lru_cache
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_LOCAL_GIT_TIMEOUT = 10


@lru_cache(maxsize=1)
def require_local_object_reads() -> None:
    result = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=True,
        env=clean_git_environment(), timeout=_LOCAL_GIT_TIMEOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    match = re.search(r"\b(\d+)\.(\d+)(?:\.(\d+))?", result.stdout)
    if match is None or tuple(map(int, match.groups(default="0"))) < (2, 45, 0):
        raise RuntimeError(
            "Local-only contract validation requires Git 2.45+ "
            "(GIT_NO_LAZY_FETCH support); no object probes were performed."
        )


def clean_git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def sha256_bytes(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        canonical = data
    else:
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@lru_cache(maxsize=512)
def git(*args: str) -> subprocess.CompletedProcess[str]:
    require_local_object_reads()
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_git_environment(),
        timeout=_LOCAL_GIT_TIMEOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def ensure_commit_available(commit: str) -> bool:
    # Validation is read-only. Missing historical commits are opportunistic
    # evidence, not permission to fetch through a different credential context.
    return git("cat-file", "-e", f"{commit}^{{commit}}").returncode == 0


def git_blob(commit: str, path: str) -> str | None:
    if not ensure_commit_available(commit):
        return None
    result = git("rev-parse", "--verify", f"{commit}:{path}")
    value = result.stdout.strip()
    return value if result.returncode == 0 and _GIT_OBJECT_RE.fullmatch(value) else None


@lru_cache(maxsize=256)
def git_file_sha256(commit: str, path: str) -> str | None:
    require_local_object_reads()
    if not ensure_commit_available(commit):
        return None
    result = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
        env=clean_git_environment(),
        timeout=_LOCAL_GIT_TIMEOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        return None
    return sha256_bytes(result.stdout)


@lru_cache(maxsize=256)
def blob_sha256(blob: str) -> str | None:
    """Hash a Git blob object's content directly by its own object id --
    content-addressed, so resolvable even when the commit that captured it
    is orphaned (e.g. by a squash merge)."""
    require_local_object_reads()
    result = subprocess.run(
        ["git", "-C", str(REPO), "cat-file", "-p", f"{blob}^{{blob}}"],
        capture_output=True,
        check=False,
        env=clean_git_environment(),
        timeout=_LOCAL_GIT_TIMEOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        return None
    return sha256_bytes(result.stdout)


def plugin_version_at(commit: str) -> str | None:
    if not ensure_commit_available(commit):
        return None
    result = git("show", f"{commit}:plugins/agent-bridge/plugin.json")
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def integer_constant_at(commit: str, path: str, name: str) -> int | None:
    if not ensure_commit_available(commit):
        return None
    result = git("show", f"{commit}:{path}")
    if result.returncode != 0:
        return None
    try:
        tree = ast.parse(result.stdout, filename=f"{commit}:{path}")
    except SyntaxError:
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            return node.value.value
    return None
