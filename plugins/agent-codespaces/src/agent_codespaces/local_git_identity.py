"""Bounded local repository identity probes before remote preparation."""
from __future__ import annotations
import subprocess
from pathlib import Path
from agent_procutil import no_window_kwargs
_GIT_PROBE_TIMEOUT = 10.0

def cwd_repo_root() -> Path | None:
    """The git repo root for the current directory, or ``None`` when not in one.

    Backs config **auto-discovery**: a CLI run inside a repo that carries a
    ``.copilot-extensions/agent-codespaces/config.yaml`` picks it up without a manual ``config
    adopt`` (the adoption manifest remains for extra/multi repos and for the
    detached daemon paths, which pass ``include_cwd=False``).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=Path.cwd(), capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=_GIT_PROBE_TIMEOUT,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Local Git root discovery timed out; repository configuration is unverified") from exc
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    return Path(result.stdout.strip()).resolve()


def _git_origin_remote(repo_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=_GIT_PROBE_TIMEOUT,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Local Git origin discovery timed out; repository identity is unverified") from exc
    except (OSError, subprocess.SubprocessError):
        return None
    remote = (result.stdout or "").strip()
    return remote if result.returncode == 0 and remote else None

