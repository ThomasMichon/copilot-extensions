"""Companion observations over the existing engine process boundary."""

from __future__ import annotations

import os
import subprocess

from . import engine_client as ec
from . import engine_process
from .engine_environment import _engine_environment


def worktree_status_bundle(
    project: str | None, worktree_id: str, *, runner=None,
) -> dict:
    """Read the owning engine's independently marked status facts."""
    obj = ec.run_json(
        project,
        ["worktree-status-bundle", "--worktree", worktree_id, "--json"],
        runner=runner,
    )
    if (
        not isinstance(obj, dict)
        or obj.get("worktree_id") != worktree_id
        or not isinstance(obj.get("facts"), dict)
    ):
        raise ec.EngineError("The status bundle does not describe the selected worktree.")
    return obj


def cancellable_read_runner(cancelled, *, cwd: str):
    """Own reads in the selected worktree, retaining the two-argument runner protocol."""
    def run(cmd, timeout):
        if cancelled.is_set():
            raise InterruptedError("owned engine read cancelled")
        return engine_process.run(
            cmd, timeout=timeout, cancelled=cancelled,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=_engine_environment(), stdin=subprocess.DEVNULL, cwd=cwd,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )

    return run
