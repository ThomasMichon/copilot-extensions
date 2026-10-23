"""Async subprocess support, loaded only by async consumers."""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from . import JobHandle

_procutil = sys.modules[__package__]


async def spawn_in_kill_on_close_job(
    *args: str,
    **kwargs: Any,
) -> tuple[asyncio.subprocess.Process, JobHandle | None]:
    """Spawn a child already contained by a kill-on-close Job Object.

    On Windows the child is created with ``CREATE_SUSPENDED``, assigned to a
    kill-on-close job, and then resumed, with no ``await`` between creation and
    assignment: the child can neither run nor start descendants outside the job.
    One window remains by design: if the owner is hard-killed in the instant
    between ``CreateProcess`` returning and the assignment, the child stays
    suspended (it never runs, so it opens no connection). Closing that window
    would need a hand-rolled ``CreateProcessW`` with ``PROC_THREAD_ATTRIBUTE_JOB_LIST``,
    which ``asyncio``'s subprocess transport (pipes, overlapped I/O) can't use.
    If job creation or assignment fails, the child is still resumed and returned
    with ``None`` so existing cleanup paths remain in charge. Off Windows this is
    a plain ``asyncio.create_subprocess_exec`` call returning ``(process, None)``.
    """
    if not _procutil._is_windows():
        process = await _procutil.asyncio.create_subprocess_exec(*args, **kwargs)
        return process, None

    spawn_kwargs = dict(kwargs)
    spawn_kwargs["creationflags"] = (
        int(spawn_kwargs.get("creationflags", 0)) | _procutil._CREATE_SUSPENDED
    )
    process = await _procutil.asyncio.create_subprocess_exec(*args, **spawn_kwargs)
    job_handle: JobHandle | None = None
    try:
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int):
            _procutil.log.debug("spawned process has no integer pid; cannot assign owner job")
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError("spawned suspended process has no integer pid")
        job_handle = _procutil._assign_suspended_to_kill_on_close_job(pid)
        if not _procutil._resume_suspended_process(pid):
            _procutil.log.debug("killing pid %s because suspended-start resume failed", pid)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            if job_handle is not None:
                job_handle.close()
            raise RuntimeError(f"failed to resume suspended process {pid}")
    except BaseException:
        if job_handle is not None:
            job_handle.close()
        raise
    return process, job_handle


spawn_in_kill_on_close_job.__module__ = __package__
