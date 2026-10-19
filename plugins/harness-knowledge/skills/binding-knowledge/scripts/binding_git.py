"""Git-root and origin reads for ordinary and supported bare binding anchors."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_CONTEXT = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES", "GIT_COMMON_DIR",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_GRAFT_FILE", "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE", "GIT_INTERNAL_SUPER_PREFIX", "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS", "GIT_OBJECT_DIRECTORY", "GIT_PREFIX",
    "GIT_QUARANTINE_PATH", "GIT_REPLACE_REF_BASE", "GIT_SHALLOW_FILE", "GIT_WORK_TREE",
}


def repository_env() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in _CONTEXT
        and not key.upper().startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def command(path: str) -> list[str]:
    argv = ["git", "-C", path]
    if (Path(path) / "HEAD").is_file() and not (Path(path) / ".git").exists():
        argv.extend(["--git-dir", path])
    return argv


def run(path: str, *args: str, timeout: float = 5) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*command(path), *args], capture_output=True, text=True, timeout=timeout,
            env=repository_env(),
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("registered control-plane Git root could not be checked") from exc


def validate_checkout(path: str) -> None:
    """Accept actual Git roots, including bare anchors, not stale directories."""
    result = run(path, "rev-parse", "--is-bare-repository", "--absolute-git-dir")
    values = result.stdout.splitlines()
    if result.returncode or len(values) != 2 or values[0] not in {"true", "false"}:
        raise RuntimeError("registered control-plane checkout is not a Git repository")
    checkout = Path(path).resolve()
    metadata = checkout / ".git"
    if values[0] == "false" and metadata.is_file():
        root = run(path, "rev-parse", "--show-toplevel")
        if root.returncode or Path(root.stdout.strip()).resolve() != checkout:
            raise RuntimeError("registered control-plane path is not its Git root")
        return
    expected = metadata if metadata.is_dir() else checkout
    if values[0] == "false" and not metadata.is_dir():
        raise RuntimeError("registered control-plane path is not its Git root")
    if Path(values[1]).resolve() != expected:
        raise RuntimeError("registered control-plane path is not its Git root")


def origin(path: str) -> str:
    validate_checkout(path)
    result = run(path, "remote", "get-url", "origin")
    return result.stdout.strip() if result.returncode == 0 else ""


def default_branch(knowledge_path: str) -> str:
    path = Path(knowledge_path).resolve()
    if not knowledge_path or not path.is_dir():
        return ""
    try:
        result = run(str(path), "ls-remote", "--symref", "origin", "HEAD", timeout=10)
    except RuntimeError:
        result = None
    if result is not None and result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("ref:") and "HEAD" in line:
                ref = line[len("ref:"):].split("\t", 1)[0].strip()
                if ref.startswith("refs/heads/"):
                    return ref.removeprefix("refs/heads/")
    try:
        result = run(
            str(path), "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD",
        )
    except RuntimeError:
        return ""
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip().removeprefix("origin/")
    for candidate in ("main", "master"):
        try:
            result = run(
                str(path), "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}",
            )
        except RuntimeError:
            return ""
        if result.returncode == 0:
            return candidate
    return ""
