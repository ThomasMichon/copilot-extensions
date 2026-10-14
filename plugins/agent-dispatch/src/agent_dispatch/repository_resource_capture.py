"""Caller-owned finite registry probes, including captured-pipe teardown."""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
from time import monotonic, sleep

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from .procutil import agent_worktrees_environment, agent_worktrees_launch_prefix
from .registrar import RegistrarError


def _retire_posix_group(proc: subprocess.Popen[str]) -> None:
    if proc.returncode is not None:
        raise RegistrarError("registered repository probe process-group custody was already released")
    try:
        # WNOWAIT retains the original child PID even after leader exit.
        # Unlike a queried start token, this kernel custody cannot race PID reuse.
        os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except OSError as exc:
        raise RegistrarError("registered repository probe process-group custody is unconfirmed") from exc
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _communicate_unreaped(
    proc: subprocess.Popen[str], *, deadline: float,
) -> tuple[str, str]:
    if proc.stdout is None or proc.stderr is None:
        raise RegistrarError("registered repository probe requires captured output pipes")
    streams = (proc.stdout, proc.stderr)
    output = (bytearray(), bytearray())
    with selectors.DefaultSelector() as selector:
        for index, stream in enumerate(streams):
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(proc.args, 0)
            for key, _mask in selector.select(remaining):
                chunk = os.read(key.fd, 65536)
                if chunk:
                    output[key.data].extend(chunk)
                else:
                    selector.unregister(key.fileobj)
    while os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is None:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(proc.args, 0)
        sleep(min(.01, remaining))
    stdout, stderr = (
        data.decode(stream.encoding, stream.errors).replace("\r\n", "\n").replace("\r", "\n")
        for data, stream in zip(output, streams, strict=True)
    )
    return stdout, stderr


def run_agent_worktrees_capture(
    *args: str, timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    deadline = monotonic() + timeout
    argv = [*prefix, *args]
    kwargs = no_window_kwargs()
    if os.name != "nt":
        if not all(hasattr(os, key) for key in ("waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT")):
            raise RegistrarError("registered repository probe requires unreaped POSIX process custody")
        kwargs["start_new_session"] = True
    try:
        proc, job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=agent_worktrees_environment(), **kwargs,
        )
    except (OSError, RuntimeError) as exc:
        raise RegistrarError("registered repository probe could not acquire process custody") from exc
    retired = False

    def retire_tree() -> None:
        nonlocal job, retired
        if retired:
            return
        retired = True
        if job is not None:
            held_job, job = job, None
            held_job.close()
        elif os.name != "nt":
            _retire_posix_group(proc)

    try:
        if os.name == "nt":
            stdout, stderr = proc.communicate(timeout=max(0, deadline - monotonic()))
        else:
            stdout, stderr = _communicate_unreaped(proc, deadline=deadline)
            retire_tree()
            proc.wait(timeout=1)
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        retire_tree()
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired as exc:
            raise RegistrarError("registered repository probe teardown remains unconfirmed") from exc
        return None
    finally:
        retire_tree()
