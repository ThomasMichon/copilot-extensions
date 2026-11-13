"""Notify an explicit creation owner without borrowing observer admission."""

from __future__ import annotations

from .owned_process import OwnedCleanupError


def notify(process, job, callback) -> None:
    """Keep the original unexecuted child and Job if refusal cleanup is uncertain."""
    try:
        if callback(process) is not None:
            raise ValueError("Suspended handoff must acknowledge without replacing the child")
    except BaseException as operation_error:
        try:
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
