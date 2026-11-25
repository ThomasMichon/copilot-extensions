"""Passive inventory through the attributable Worktrees installation."""

from __future__ import annotations

import asyncio
import os
import subprocess
import threading
from typing import cast

from agent_procutil import no_window_kwargs, spawn_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError, run_owned


async def _read_posix(
    argv: list[str], *, timeout: float, env: dict[str, str],
) -> tuple[str | None, str]:
    # This callback requests confirmed group retirement, not only signal delivery.
    settled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(
        run_owned, argv, timeout=timeout, env=env, on_settled=settled.set,
    ))
    try:
        result = await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Do not abandon the finite owner before its unreaped group is settled.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            worker.result()
        except OwnedCleanupError:
            raise
        except Exception:
            pass
        raise
    except subprocess.TimeoutExpired:
        return None, "worktree_owner_timeout"
    except (OSError, ValueError, RuntimeError) as error:
        return None, type(error).__name__
    if not settled.is_set():
        return None, "worktree_owner_cleanup_unconfirmed"
    return (
        cast(str, result.stdout) if result.returncode == 0 else None,
        cast(str, result.stderr),
    )


async def read(project: str, args: list[str], *, timeout: float) -> tuple[str | None, str]:
    from ..session_lifecycle_cli import (
        _agent_worktrees_environment, _agent_worktrees_launch_prefix,
    )
    from .worktrees import _kill_process_tree

    try:
        prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
        if not prefix:
            return None, "worktree_owner_unavailable"
        environment = _agent_worktrees_environment()
        if os.name != "nt":
            return await _read_posix(
                [*prefix, "--project", project, *args], timeout=timeout, env=environment,
            )
        proc, job = await spawn_in_kill_on_close_job(
            *prefix, "--project", project, *args,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=environment,
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
