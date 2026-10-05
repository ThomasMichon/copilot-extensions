"""Shared result types and process-invocation helpers for unattended self-update.

Kept as a dependency-free leaf module (stdlib + ``agent_procutil`` only) so
both ``self_update.py`` (tier orchestration) and ``self_update_dtssh.py``
(dtssh host/mesh liveness) can depend on it without creating an import cycle
between those two.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

log = logging.getLogger("agent-machines.self-update")

# Conventional shell "command timed out" exit code (matches `timeout(1)` on
# POSIX); used so a timed-out step is distinguishable from a genuine
# subprocess failure in status/log output without inventing a new sentinel.
TIMEOUT_RETURNCODE = 124


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
    process, job_handle = spawn_sync_in_kill_on_close_job(
        resolved,
        cwd=str(cwd) if cwd is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **no_window_kwargs(),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return CommandResult(
            argv=list(argv), returncode=process.returncode, stdout=stdout, stderr=stderr
        )
    except subprocess.TimeoutExpired:
        # A plain `process.kill()` only terminates the immediate child. A
        # `.cmd`/`.bat` binstub (agent-worktrees, agent-ssh, ...) commonly
        # re-execs through several layers (cmd.exe -> pwsh.exe -> python.exe
        # -> git.exe/further shims); any grandchild left alive after the
        # immediate child dies can keep inherited stdout/stderr pipes open
        # indefinitely, which then blocks `communicate()` forever even though
        # the top-level process is already gone. Closing the kill-on-close Job
        # (when one was assigned) terminates every process still in it, not
        # just the immediate child, before we drain whatever partial output
        # remains buffered.
        tree_killed = job_handle is not None
        if job_handle is not None:
            job_handle.close()
        else:
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                log.debug("process already gone after timeout", exc_info=True)
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired as drain_exc:
            # A surviving descendant can still hold the pipes open even after
            # the tree-kill (or plain kill()) above; preserve whatever output
            # the exception itself already collected rather than discarding it.
            log.warning(
                "command still undrained after termination; returning partial "
                "output: %r",
                resolved,
            )
            stdout = drain_exc.output or ""
            stderr = drain_exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
        termination = "tree-killed via Job Object" if tree_killed else "killed (no Job assigned)"
        detail = f"command timed out after {timeout}s and was terminated ({termination}): {resolved!r}"
        return CommandResult(
            argv=list(argv),
            returncode=TIMEOUT_RETURNCODE,
            stdout=stdout,
            stderr="\n".join(part for part in (stderr, detail) if part),
        )
    finally:
        if job_handle is not None:
            job_handle.close()

