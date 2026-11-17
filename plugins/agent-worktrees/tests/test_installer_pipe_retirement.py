"""Confirmed Job retirement does not depend on unrelated pipe-handle custody."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import pytest

from agent_procutil import JobHandle, no_window_kwargs, spawn_sync_in_kill_on_close_job
from agent_procutil.streaming import SyncProcessOutput
from agent_worktrees import installer_run

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(os.name != "nt", reason="Windows captured pipe retirement"),
]


def test_strict_empty_job_does_not_require_unrelated_write_handle_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, retained_writer = os.pipe()
    captures: list[SyncProcessOutput] = []
    spawned: list[tuple[subprocess.Popen[Any], JobHandle]] = []
    lines: list[str] = []

    def spawn(
        argv: Sequence[str], cwd: Path | None, env: Mapping[str, str], capture: bool,
        *, require_tree_exit: bool,
    ) -> tuple[subprocess.Popen[Any], JobHandle]:
        assert capture and require_tree_exit
        proc, job = spawn_sync_in_kill_on_close_job(
            list(argv), cwd=cwd, env=dict(env), require_job=True, allow_breakaway=False,
            stdout=retained_writer, stderr=retained_writer, **no_window_kwargs(),
        )
        assert job is not None
        # A foreign retained write handle is not an active owned-Job producer.
        proc.stdout = os.fdopen(read_fd, "rb")
        spawned.append((proc, job))
        return proc, job

    def capture(process: subprocess.Popen[Any], **kwargs: Any) -> SyncProcessOutput:
        result = SyncProcessOutput(process, **kwargs)
        captures.append(result)
        return result

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    monkeypatch.setattr(installer_run, "SyncProcessOutput", capture)
    monkeypatch.setattr(installer_run, "_REAP_GRACE_S", 1)
    started = time.monotonic()
    try:
        result = installer_run.run_installer(
            [sys.executable, "-c", "print('retired-output', flush=True)"],
            cwd=tmp_path, env=os.environ, deadline_s=5, log=lines.append,
            require_tree_exit=True,
        )
        assert result.status == "OK"
        assert lines == ["retired-output"]
        assert time.monotonic() - started < 5
        assert all(proc.poll() is not None and job.handle is None for proc, job in spawned)
        os.fstat(retained_writer)
        assert all(not reader.is_alive() for output in captures for reader in output._readers)
    finally:
        os.close(retained_writer)
        for proc, job in spawned:
            if job.handle:
                job.terminate_and_wait(5)
            proc.wait(timeout=5)
        for output in captures:
            if not output._finished:
                output.finish(2)
        if not spawned:
            os.close(read_fd)
