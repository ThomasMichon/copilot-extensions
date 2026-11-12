"""Worktrees invocation and read-only refusal for the standalone Index reader."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Callable


def _readiness_support():
    bundled = Path(__file__).parent / "configuration-layers" / "src"
    if str(bundled) not in sys.path:
        sys.path.insert(0, str(bundled))
    from configuration_context import readiness

    return readiness


def refuse_unqualified(result: subprocess.CompletedProcess[str]) -> None:
    if os.environ.get("COPILOT_EXTENSIONS_READONLY_PROBE") is not None:
        readiness = _readiness_support()
        if readiness.required() and readiness.unqualified_output(result.stdout.encode("utf-8")):
            raise readiness.ProbeContractError("Same-cell worktrees probe is unqualified.")


def run_state_root(
    command: str, environment: dict[str, str], *, cwd: Path | None,
    project: str | None, argv: Callable[..., list[str]],
) -> subprocess.CompletedProcess[str] | None:
    if os.environ.get("COPILOT_EXTENSIONS_READONLY_PROBE") is not None:
        readiness = _readiness_support()
        if readiness.required(environment):
            raise readiness.ProbeContractError(
                "Legacy worktrees command has no qualified probe provenance; update the owning installation."
            )
    arguments = ("state-root", "--json") if project is None else (
        "--project", project, "state-root", "--json",
    )
    try:
        return subprocess.run(
            argv(command, *arguments),
            cwd=str(cwd) if cwd is not None else None,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
