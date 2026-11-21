"""Passive inventory through the attributable Worktrees installation."""

from __future__ import annotations

import asyncio

from agent_procutil import no_window_kwargs, spawn_in_kill_on_close_job


async def read(project: str, args: list[str], *, timeout: float) -> tuple[str | None, str]:
    from ..session_lifecycle_cli import (
        _agent_worktrees_environment, _agent_worktrees_launch_prefix,
    )
    from .worktrees import _kill_process_tree

    try:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
        if not prefix:
            return None, "worktree_owner_unavailable"
        proc, job = await spawn_in_kill_on_close_job(
            *prefix, "--project", project, *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=_agent_worktrees_environment(),
            start_new_session=True, require_job=True, **no_window_kwargs(),
        )
    except (OSError, ValueError, RuntimeError) as error:
        return None, type(error).__name__
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            stdout.decode("utf-8") if proc.returncode == 0 else None,
            stderr.decode("utf-8", errors="replace"),
        )
    except (asyncio.TimeoutError, asyncio.CancelledError) as error:
        if job is not None:
            job.close()
            job = None
        _kill_process_tree(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=5)
        except Exception:
            pass
        if isinstance(error, asyncio.CancelledError):
            raise
        return None, "worktree_owner_timeout"
    finally:
        if job is not None:
            job.close()
