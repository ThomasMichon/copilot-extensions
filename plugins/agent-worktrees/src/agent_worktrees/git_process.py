"""Private process composition for the Git wrapper, retaining caller-owned seams."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
import subprocess
from typing import TypedDict, cast

ProcessResult = subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
ProcessRunner = Callable[..., ProcessResult]
TextRunner = Callable[..., subprocess.CompletedProcess[str]]


class GitProbeOptions(TypedDict, total=False):
    timeout: float
    kill_tree: bool
    observation_deadline: float


class NoHooksOptions(TypedDict, total=False):
    no_hooks: bool


class _BoundedOptions(TypedDict, total=False):
    observation_deadline: float


def current_branch(
    cwd: str | Path, *, timeout: float | None, kill_tree: bool,
    observation_deadline: float | None, run: TextRunner,
) -> str | None:
    options: GitProbeOptions = {}
    if timeout is not None:
        options["timeout"] = timeout
    if kill_tree:
        options["kill_tree"] = True
    if observation_deadline is not None:
        options["observation_deadline"] = observation_deadline
    result = run("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd, check=False, **options)
    if result.returncode != 0:
        return None
    name = result.stdout.strip()
    return None if name == "HEAD" else name


def execute(
    command: list[str], *, cwd: str | Path | None, env: dict[str, str],
    capture: bool, timeout: float | None, kill_tree: bool,
    input_data: bytes | None, observation_deadline: float | None,
    run: ProcessRunner, bounded_run: ProcessRunner,
    window_options: Callable[[], Mapping[str, object]],
    soft_timeout: float | None = None,
) -> ProcessResult:
    if soft_timeout is not None and observation_deadline is None:
        if input_data is not None or not capture:
            raise ValueError("soft timeout requires captured text output without input")
        from agent_procutil.owned_process import run_owned

        return cast(ProcessResult, run_owned(
            command, cwd=cwd, env=env, timeout=timeout, soft_timeout=soft_timeout,
        ))
    if observation_deadline is not None or (kill_tree and timeout is not None):
        bounded: _BoundedOptions = {}
        if observation_deadline is not None:
            bounded["observation_deadline"] = observation_deadline
        if input_data is not None:
            return bounded_run(
                command, cwd=cwd, env=env, timeout=timeout,
                binary=True, input=input_data, **bounded,
            )
        return bounded_run(command, cwd=cwd, env=env, timeout=timeout, **bounded)
    if input_data is not None:
        return run(
            command, cwd=cwd, capture_output=capture, env=env, timeout=timeout,
            input=input_data, **window_options(),
        )
    return run(
        command, cwd=cwd, capture_output=capture, env=env, timeout=timeout,
        text=True, encoding="utf-8", errors="replace", **window_options(),
    )
