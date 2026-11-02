"""Bounded finite capture over the existing native shell lifetime transport."""

from __future__ import annotations

from . import _job_scopes
from ._native_owned import NativeOwnedFailure, _absolute_path
from .owned_process import _run_owned


def run(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "executable", "arguments", "cwd", "timeout_ms", "environment", "limit",
    }:
        raise NativeOwnedFailure("invalid native query request", cleanup_confirmed=True)
    try:
        executable = _absolute_path(value["executable"])
        cwd = _absolute_path(value["cwd"], allow_unc_root=True)
        arguments = value["arguments"]
        if not isinstance(arguments, list) or any(
            not isinstance(argument, str) or "\0" in argument for argument in arguments
        ):
            raise ValueError("native query arguments must be NUL-free strings")
        timeout, limit = value["timeout_ms"], value["limit"]
        if type(timeout) is not int or not 0 < timeout <= 30000:
            raise ValueError("native query requires a deadline of at most 30 seconds")
        if type(limit) is not int or not 0 < limit <= 65536:
            raise ValueError("native query requires a capture limit of at most 65536 bytes")
        environment = value["environment"]
        if not isinstance(environment, dict) or any(
            not isinstance(key, str) or not key or "\0" in key or "=" in key[1:]
            or not isinstance(item, str) or "\0" in item
            for key, item in environment.items()
        ):
            raise ValueError("native query environment must contain NUL-free text")
        environment = _job_scopes.child_environment(environment, inherit_current=False)
    except (TypeError, ValueError) as error:
        raise NativeOwnedFailure(str(error), cleanup_confirmed=True) from error
    settled = False

    def on_settled() -> None:
        nonlocal settled
        settled = True

    try:
        result = _run_owned(
            [executable, *arguments], cwd=cwd, env=environment,
            timeout=timeout / 1000, capture_limits=(limit, limit),
            on_settled=on_settled,
        )
    except BaseException as error:
        raise NativeOwnedFailure(
            "native query failed: " + type(error).__name__, cleanup_confirmed=settled,
            process=getattr(error, "process", None), job=getattr(error, "job", None),
        ) from error
    if not settled:
        raise NativeOwnedFailure("native query cleanup is unconfirmed", cleanup_confirmed=False)
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
