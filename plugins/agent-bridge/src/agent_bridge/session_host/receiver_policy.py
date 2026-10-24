"""Dependency-free policy adapter for the portable Session Host bundle."""

from __future__ import annotations

import json
import os
import shutil
import subprocess

from agent_procutil import no_window_flags


def require_path_hosting(cwd: str | os.PathLike[str] | None, *, operation: str = "launch") -> None:
    """The guest's attributed owner resolves its own validated marketplace context."""
    if not cwd:
        raise ValueError("Repository hosting receiver requires an actual execution directory")
    owner = shutil.which("agent-worktrees")
    if owner is None:
        raise ValueError(
            "Repository hosting receiver is unavailable; install an enforcing owner "
            "or use an eligible external harness for source-only work"
        )
    try:
        result = subprocess.run(
            [owner, "agent-policy", "check", "--path", os.fspath(cwd),
             "--operation", operation, "--json"],
            capture_output=True, text=True, timeout=30, creationflags=no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Repository hosting receiver check is unavailable: {exc}") from exc
    if result.returncode:
        raise ValueError(
            "Repository hosting receiver refused: "
            + (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        )
    try:
        answer = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise ValueError("Repository hosting receiver returned invalid JSON") from exc
    if (
        not isinstance(answer, dict) or type(answer.get("version")) is not int
        or answer["version"] != 1 or answer.get("allowed") is not True
        or "error" in answer or "code" in answer
        or not isinstance(answer.get("repository"), str) or not answer["repository"].strip()
        or "expose_agent" not in answer
        or (answer["expose_agent"] is not None and answer["expose_agent"] is not True)
        or not isinstance(answer.get("sources"), list)
        or any(not isinstance(path, str) or not path.strip() for path in answer["sources"])
        or answer.get("denied_by") != []
    ):
        raise ValueError("Repository hosting receiver did not return an explicit version-1 allow")
