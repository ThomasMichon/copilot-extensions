"""Notify an explicit creation owner without borrowing observer admission."""

from __future__ import annotations

import agent_procutil as procutil

from .owned_process import OwnedCleanupError


def spawn(argv, require_job, allow_breakaway, callback, **kwargs):
    """Keep actual creation custody through publication, resume and failed settlement."""
    if (
        not callable(callback) or not procutil._is_windows()
        or require_job is not True or allow_breakaway is not False
    ):
        raise ValueError("Suspended handoff requires Windows, required custody and no breakaway")
    name = procutil._new_job_name()
    options = dict(kwargs)
    options["env"] = procutil._job_child_environment(options.get("env"), append=name)
    options["creationflags"] = int(options.get("creationflags", 0)) | procutil._CREATE_SUSPENDED
    try:
        # headless-guard: allow: the existing owner supplies headless flags or its interactive pane
        process = procutil.subprocess.Popen(argv, **options)
    except OSError as error:
        kind = (
            procutil.SyncProcessNotFoundError
            if isinstance(error, FileNotFoundError) else procutil.SyncProcessLaunchError
        )
        raise kind(error) from error
    job = None
    try:
        job = procutil._assign_suspended_to_kill_on_close_job(
            process.pid, allow_breakaway=False, name=name,
        )
        if job is None:
            raise RuntimeError("Windows descendant Job assignment failed before child resume")
        if callback(process) is not None:
            raise ValueError("Suspended handoff must acknowledge without replacing the child")
        if not procutil._resume_suspended_process(process.pid):
            raise RuntimeError(f"failed to resume suspended process {process.pid}")
        return process, job
    except BaseException as operation_error:
        try:
            if job is None:
                process.kill()
            else:
                job.terminate_and_wait(5)
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        except BaseException as cleanup_error:
            failure = OwnedCleanupError(process, job)
            failure.operation_error = operation_error
            failure.cleanup_error = cleanup_error
            raise failure from operation_error
        raise
