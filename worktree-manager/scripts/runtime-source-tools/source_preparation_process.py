"""Adapt source preparation to the existing native process and permission owners."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time
from types import ModuleType
from typing import BinaryIO


def procutil_module(name: str) -> ModuleType:
    source = Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src"
    if not source.is_dir():
        source = Path(__file__).resolve().parent.parent / "runtime-input-provider/libs/agent-procutil/src"
    previous = sys.path.copy()
    try:
        sys.path.insert(0, str(source))
        from agent_procutil import (
            _daemon_instance_owner, owned_budget, owned_process, python_cache_permissions,
        )

        module = {
            "_daemon_instance_owner": _daemon_instance_owner,
            "owned_budget": owned_budget,
            "owned_process": owned_process,
            "python_cache_permissions": python_cache_permissions,
        }[name]
    finally:
        sys.path[:] = previous
    if Path(module.__file__).resolve() != (source / "agent_procutil" / (name + ".py")).resolve():
        raise ImportError("source preparation requires its repository process utilities")
    return module


def run(
    argv: list[str], *, deadline: float, cwd: str | Path | None = None,
    env: dict[str, str] | None = None, input: str | bytes | None = None,
    text: bool = False, encoding: str = "utf-8", errors: str = "strict",
    stdout: BinaryIO | None = None, capture_output: bool = True, stderr: int | None = None,
    timeout: float | None = None, check: bool = False, creationflags: int = 0,
) -> subprocess.CompletedProcess:
    """Reserve native settlement inside, never after, the caller's deadline."""
    from math import isfinite

    if type(deadline) not in (int, float) or not isfinite(deadline):
        raise ValueError("source preparation requires a finite deadline")
    owner = procutil_module("owned_process")
    budgets = procutil_module("owned_budget")
    started = time.monotonic()
    end = deadline if timeout is None else min(deadline, started + timeout)
    remaining = end - started
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, 0)
    result = owner.run_caller_owned(
        list(argv), cwd=cwd, env=env, timeout=remaining, binary=True,
        input_bytes=input.encode(encoding) if isinstance(input, str) else input,
        max_stdout_bytes=1 << 30, max_stderr_bytes=16 << 20,
        budget=budgets.OwnedExecutionBudget(end, min(5, remaining / 5)),
    )
    if stdout is not None:
        view = memoryview(result.stdout)
        for offset in range(0, len(view), 1 << 20):
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, remaining)
            stdout.write(view[offset:offset + (1 << 20)])
        result.stdout = None
    if text:
        result.stdout = result.stdout.decode(encoding, errors) if result.stdout is not None else None
        result.stderr = result.stderr.decode(encoding, errors) if result.stderr is not None else None
    if check:
        result.check_returncode()
    if time.monotonic() >= deadline:
        raise subprocess.TimeoutExpired(argv, remaining)
    return result
