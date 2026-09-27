"""Shared result types and process-invocation helpers for unattended self-update.

Kept as a dependency-free leaf module (stdlib + ``agent_procutil`` only) so
both ``self_update.py`` (tier orchestration) and ``self_update_dtssh.py``
(dtssh host/mesh liveness) can depend on it without creating an import cycle
between those two.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def output(self) -> str:
        text = "\n".join(
            part.rstrip() for part in (self.stdout, self.stderr) if part and part.strip()
        ).strip()
        return text


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    command: list[str] | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
        }
        if self.detail:
            payload["detail"] = self.detail
        if self.command:
            payload["command"] = self.command
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass
class RunResult:
    tier: str
    status: str
    opted_in: bool
    detail: str = ""
    lock_reclaimed: bool = False
    attempted_at: str | None = None
    success_at: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "noop"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "status": self.status,
            "ok": self.ok,
            "opted_in": self.opted_in,
            "detail": self.detail,
            "lock_reclaimed": self.lock_reclaimed,
            "attempted_at": self.attempted_at,
            "success_at": self.success_at,
            "steps": [step.to_dict() for step in self.steps],
        }


def shutil_which(binary: str) -> str | None:
    return shutil.which(binary)


def default_command_runner(
    argv: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 1800,
) -> CommandResult:
    # Resolve argv[0] through PATH/PATHEXT before invoking: Windows subprocess
    # creation (CreateProcess, used when shell=False) does not apply PATHEXT
    # resolution the way cmd.exe does, so a bare command name that is really a
    # `.cmd`/`.bat` shim (e.g. the `agent-worktrees` binstub) raises
    # FileNotFoundError / WinError 2 even though it is genuinely on PATH.
    # shutil.which() performs the same PATHEXT-aware search cmd.exe does, so
    # resolving here fixes every unattended self-update caller uniformly.
    resolved = argv
    if argv:
        binary = shutil.which(argv[0])
        if binary:
            resolved = [binary, *argv[1:]]
    proc = subprocess.run(  # noqa: S603 - argv list, no shell
        resolved,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_window_kwargs(),
    )
    return CommandResult(
        argv=list(argv), returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr
    )
