"""Optional repository-account integration without shared credential state."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import os
import subprocess
from typing import Any

from . import procutil
from .registrar import RegistrarError

_TIMEOUT_SECONDS = 30


def run_repository_command(
    argv: list[str], *, timeout: float = _TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None, **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Forward execution context; the capture runner owns pipes and tree flags."""
    fixed_options = {
        "capture_output": True, "text": True, "check": False,
        **procutil.no_window_kwargs(),
    }
    if any(key not in fixed_options or value != fixed_options[key]
           for key, value in kwargs.items()):
        raise ValueError("Repository GitHub command received unsupported process options")
    try:
        result = procutil.run_background_capture(argv, timeout=timeout, env=env, cwd=cwd)
    except (OSError, subprocess.SubprocessError, RuntimeError):
        raise RuntimeError("Repository GitHub command failed") from None
    if result is None:
        raise RuntimeError("Repository GitHub command unavailable or timed out")
    return result


def validate_auth(forge: Mapping[str, Any]) -> dict[str, str]:
    if "auth" not in forge:
        return {}
    auth = forge["auth"]
    if not isinstance(auth, str) or auth not in {"ambient", "repository"}:
        raise RegistrarError(
            "repository-issue-loop forge.auth: expected ambient or repository"
        )
    if forge.get("provider") != "github":
        raise RegistrarError("repository-issue-loop forge.auth: only supported for github")
    return {"auth": auth}


def _single_value(value: Any) -> str | None:
    text = value if isinstance(value, str) else ""
    text = text.strip()
    return text if text and not any(char.isspace() for char in text) else None


def repository_environment(
    repository: str, expected_login: str, *, runner: Callable[..., Any],
) -> dict[str, str]:
    prefix = procutil.agent_worktrees_launch_prefix()
    if prefix is None:
        raise RuntimeError("Repository GitHub authentication requires agent-worktrees")
    env = procutil.agent_worktrees_environment()

    def request(argv: list[str], *, operation: str) -> Any:
        try:
            result = runner(
                argv, capture_output=True, text=True, check=False,
                timeout=_TIMEOUT_SECONDS, env=env, **procutil.no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError, RuntimeError):
            raise RuntimeError(
                f"Repository GitHub authentication failed during {operation}"
            ) from None
        # Token commands may fall back to ambient credentials with a warning.
        # Never echo their output or use that fallback for a forge operation.
        if int(result.returncode) != 0 or str(result.stderr or "").strip():
            raise RuntimeError(f"Repository GitHub authentication failed during {operation}")
        return result

    account = _single_value(request(
        [*prefix, "repos", "account-for", repository], operation="account selection",
    ).stdout)
    if account is None or account.casefold() != expected_login.casefold():
        raise RuntimeError("Repository GitHub account does not match forge.producer_login")
    credential = _single_value(request(
        [*prefix, "repos", "gh", repository, "--", "auth", "token"],
        operation="credential retrieval",
    ).stdout)
    if credential is None:
        raise RuntimeError("Repository GitHub credential retrieval returned no single credential")
    env = {**env, "GH_TOKEN": credential}
    login = _single_value(request(
        ["gh", "api", "user", "--jq", ".login"], operation="credential identity verification",
    ).stdout)
    if login is None or login.casefold() != expected_login.casefold():
        raise RuntimeError("Repository GitHub credential identity does not match forge.producer_login")
    return env
