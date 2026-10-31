"""Finite Windows log writers behind the original shell's private transport."""

from __future__ import annotations

import ntpath
import subprocess
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO

from . import JobHandle, _job_scopes
from .owned_process import OwnedCleanupError

if TYPE_CHECKING:
    from ._windows_caller_owned import _Process


@dataclass(frozen=True)
class NativeOwnedRequest:
    argv: list[str]
    cwd: str
    log: str
    timeout: float
    environment: dict[str, str]


class NativeOwnedFailure(OSError):
    """Keep uncertainty and exact native custody separate from the message."""

    def __init__(
        self, message: str, *, cleanup_confirmed: bool,
        process: _Process | None = None, job: JobHandle | None = None,
    ) -> None:
        super().__init__(message)
        self.cleanup_confirmed = cleanup_confirmed
        self.process, self.job = process, job
        self.output: BinaryIO | None = None
        self.log_error: BaseException | None = None


def _absolute_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\0" in value:
        raise ValueError("native process paths must be fully qualified and normalized")
    drive, tail = ntpath.splitdrive(value)
    normalized = ntpath.normpath(value)
    if value.endswith("\\") and not normalized.endswith("\\"):
        normalized += "\\"
    if not drive or not tail.startswith(("\\", "/")) or normalized.lower() != value.lower():
        raise ValueError("native process paths must be fully qualified and normalized")
    return value


def validate_request(value: object) -> NativeOwnedRequest:
    if not isinstance(value, dict) or set(value) != {
        "executable", "arguments", "cwd", "log", "timeout_ms", "environment",
    }:
        raise ValueError("invalid native owned-process request")
    executable, cwd, log = (
        _absolute_path(value[name]) for name in ("executable", "cwd", "log")
    )
    arguments = value["arguments"]
    if not isinstance(arguments, list) or any(
        not isinstance(argument, str) or "\0" in argument for argument in arguments
    ):
        raise ValueError("native process arguments must be NUL-free strings")
    timeout = value["timeout_ms"]
    if type(timeout) is not int or not 0 < timeout <= 7200000:
        raise ValueError("owned child requires a deadline of at most two hours")
    environment = value["environment"]
    if not isinstance(environment, dict) or any(
        not isinstance(key, str) or not key or "\0" in key or "=" in key[1:]
        or not isinstance(item, str) or "\0" in item
        for key, item in environment.items()
    ):
        raise ValueError("native child environment must contain NUL-free text")
    # Validate the original shell's chain, never the helper's private Job chain.
    environment = _job_scopes.child_environment(environment, inherit_current=False)
    scopes = (*_job_scopes.scope_names(environment), _job_scopes.new_name())
    _job_scopes.scope_names({_job_scopes.ENVIRONMENT_KEY: ";".join(scopes)})
    return NativeOwnedRequest(
        [executable, *arguments], cwd, log, timeout / 1000, dict(environment),
    )


def _spawn(request: NativeOwnedRequest, output: BinaryIO) -> tuple[_Process, JobHandle]:
    from ._windows_caller_owned import spawn

    return spawn(
        request.argv, cwd=request.cwd, env=request.environment, allow_breakaway=False,
        inherit_job_scopes=False, stdin=subprocess.DEVNULL,
        stdout=output, stderr=subprocess.STDOUT,
    )


def _job_empty(job: JobHandle) -> bool:
    from ._windows_caller_owned import _job_pids

    return not _job_pids(job.handle)


def _failure(
    error: BaseException, *, settled: bool,
    process: _Process | None = None, job: JobHandle | None = None,
) -> NativeOwnedFailure:
    message = (
        "native child cleanup could not be attested" if not settled else
        "owned native process tree exceeded its deadline" if isinstance(error, TimeoutError) else
        "native owned process failed: " + type(error).__name__
    )
    return NativeOwnedFailure(message, cleanup_confirmed=settled, process=process, job=job)


def _run_target(request: NativeOwnedRequest, output: BinaryIO) -> int:
    deadline = time.monotonic() + request.timeout
    try:
        process, job = _spawn(request, output)
    except OwnedCleanupError as error:
        raise _failure(error, settled=False, process=error.process, job=error.job) from error
    except BaseException as error:
        raise _failure(error, settled=True) from error
    primary = None
    code = None
    try:
        while not _job_empty(job):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("owned native process tree exceeded its deadline")
            time.sleep(min(.01, remaining))
        code = process.wait(timeout=0)
        if type(code) is not int or not 0 <= code < 2**32:
            raise RuntimeError("native root exit status is unconfirmed")
        if time.monotonic() >= deadline:
            raise TimeoutError("owned native process tree exceeded its deadline")
    except BaseException as error:
        primary = error
    try:
        process.retire_job(job, 10)
        process.wait(timeout=0)
        process.close()
    except BaseException as error:
        failure = _failure(error, settled=False, process=process, job=job)
        failure.primary_error = primary
        raise failure from error
    if primary is not None:
        raise _failure(primary, settled=True) from primary
    if code is None:
        raise NativeOwnedFailure("native root exit status is unconfirmed", cleanup_confirmed=True)
    return code if code < 2**31 else code - 2**32


def run(value: object) -> int:
    """Wait for every writer, not only the root; errors attest actual retirement."""
    try:
        request = validate_request(value)
    except (TypeError, ValueError) as error:
        raise NativeOwnedFailure(str(error), cleanup_confirmed=True) from error
    try:
        output = open(request.log, "ab", buffering=0)
    except OSError as error:
        raise _failure(error, settled=True) from error
    result = None
    failure = None
    cause = None
    try:
        result = _run_target(request, output)
    except BaseException as error:
        if isinstance(error, NativeOwnedFailure):
            failure = error
        else:
            cause = error
            failure = _failure(
                error, settled=False,
                process=getattr(error, "process", None), job=getattr(error, "job", None),
            )
    finally:
        try:
            output.close()
        except BaseException as error:
            message = "native owned log close failed: " + type(error).__name__
            if failure is None:
                cause = error
                failure = NativeOwnedFailure(message, cleanup_confirmed=result is not None)
            else:
                failure.args = (str(failure) + "; " + message,)
            failure.output, failure.log_error = output, error
    if failure is not None:
        if cause is not None:
            raise failure from cause
        raise failure
    if result is None:
        raise NativeOwnedFailure("native root exit status is unconfirmed", cleanup_confirmed=False)
    return result
