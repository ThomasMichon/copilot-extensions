"""Captured mux operations own their Windows descendants through completion."""

from __future__ import annotations

import logging
import os
import subprocess

from agent_procutil import spawn_sync_in_kill_on_close_job


def run(
    argv: list[str], *, capture_output: bool = True, text: bool = False,
    timeout: float, **kwargs: object,
) -> subprocess.CompletedProcess:
    if os.name != "nt":
        return subprocess.run(
            argv, capture_output=capture_output, text=text, timeout=timeout, **kwargs,
        )
    process, job = spawn_sync_in_kill_on_close_job(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=text, require_job=True, **kwargs,
    )
    try:
        if job is None:
            raise RuntimeError("Mux operation could not acquire a Windows descendant Job")
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except BaseException:
        if job is None:
            process.kill()
        logging.getLogger(__name__).warning("Mux operation failed or timed out: %s", argv[:2])
        raise
    finally:
        if job is not None:
            job.close()
        process.communicate(timeout=5)
