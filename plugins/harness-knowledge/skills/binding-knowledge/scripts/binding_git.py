"""Git-root and origin reads for ordinary and supported bare binding anchors."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def command(path: str) -> list[str]:
    argv = ["git", "-C", path]
    if (Path(path) / "HEAD").is_file() and not (Path(path) / ".git").exists():
        argv.extend(["--git-dir", path])
    return argv


def run(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*command(path), *args], capture_output=True, text=True, timeout=5,
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
