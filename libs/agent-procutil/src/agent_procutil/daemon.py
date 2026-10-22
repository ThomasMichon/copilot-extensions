"""Resident-daemon launch policy shared by every process-utility consumer."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from collections.abc import Mapping, Sequence

from .daemon_broker import WindowsDaemonProcess, spawn_brokered_daemon


def _validated_daemon_argv(argv: object) -> list[str]:
    # A bare string (or path) is itself a Sequence: iterating it would launch
    # its first character as the executable.
    if isinstance(argv, (str, bytes, bytearray, os.PathLike)):
        raise TypeError(
            "spawn_daemon argv must be a sequence of arguments, not a single "
            f"{type(argv).__name__}; pass [executable, *args]"
        )
    command = [os.fspath(part) for part in argv]  # type: ignore[union-attr]
    if not command:
        raise ValueError("spawn_daemon requires a non-empty argv")
    return command


def spawn_daemon(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    log_path: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    env_overrides: Mapping[str, str] | None = None,
    console_children: bool = False,
    breakaway: bool = True,
    contained_fallback: bool = False,
    extra_creationflags: int = 0,
    display: str | None = None,
    stderr_log_path: str | os.PathLike[str] | None = None,
) -> subprocess.Popen | WindowsDaemonProcess:
    """Launch a windowless resident daemon from an explicit durable cwd.

    Output is appended to absolute, non-payload log paths with a safe launch
    header. ``env_overrides`` layers over ``env`` (the current environment by
    default). Extra creation flags may add only priority/error-mode policy.

    On Windows, a caller already in a Job or breakaway denial triggers a bounded WMI bootstrap outside the
    caller's Job; the returned handle controls the actual daemon. If that also
    fails, raise ``DaemonSpawnError`` unless ``contained_fallback`` explicitly
    accepts the caller's shorter lifetime. ``breakaway=False`` and contained
    test mode never invoke the broker. POSIX uses a new session.
    """
    import agent_procutil as pu

    command = _validated_daemon_argv(argv)
    extra_creationflags = pu._validated_extra_creationflags(extra_creationflags)
    cwd_path = pu._validated_daemon_cwd(cwd)
    log_file = pu._validated_daemon_log_path(log_path)
    err_file = None
    if stderr_log_path is not None:
        err_file = pu._validated_daemon_log_path(stderr_log_path, "stderr_log_path")
        same = [os.path.normcase(os.path.normpath(p)) for p in (err_file, log_file)]
        if same[0] == same[1]:
            err_file = None

    child_env = dict(os.environ if env is None else env)
    if env_overrides:
        child_env.update(env_overrides)
    kwargs = pu._daemon_popen_kwargs(
        console_children=console_children,
        breakaway=breakaway,
        extra_creationflags=extra_creationflags,
    )
    with contextlib.ExitStack() as stack:
        log_fh = stack.enter_context(pu._open_daemon_log(log_file))
        err_fh = stack.enter_context(pu._open_daemon_log(err_file)) if err_file else None
        logs = [fh for fh in (log_fh, err_fh) if fh is not None]

        def write_logs(data: bytes) -> None:
            for fh in logs:
                fh.write(data)
                fh.flush()

        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_logs(pu._daemon_log_header(stamp, cwd_path, command[0], display))
        popen_kwargs = {
            "cwd": cwd_path,
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fh,
            "stderr": subprocess.STDOUT if err_fh is None else err_fh,
            "close_fds": True,
        }
        flags = kwargs.get("creationflags", 0)
        needs_broker = False
        if flags & pu._CREATE_BREAKAWAY_FROM_JOB:
            from .daemon_broker import caller_in_job

            needs_broker = caller_in_job()
        if needs_broker:
            note = "spawn_daemon: caller Job lifetime requires verified outside-Job launch"
        else:
            try:
                return subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603
            except OSError as exc:
                if not pu._breakaway_denied(exc, kwargs):
                    raise
                note = "spawn_daemon: the caller's Job object forbids breakaway"
        flags &= ~pu._CREATE_BREAKAWAY_FROM_JOB
        write_logs(f"{stamp} {note}; trying Windows lifetime broker\n".encode())
        try:
            return spawn_brokered_daemon(
                command, cwd=cwd_path, env=child_env, creationflags=flags,
                log_path=log_file, stderr_log_path=err_file,
            )
        except pu.DaemonSpawnError as broker_error:
            if not contained_fallback or not broker_error.cleanup_confirmed:
                outcome = (
                    "daemon not started" if broker_error.cleanup_confirmed
                    else "partial launch cleanup unconfirmed"
                )
                write_logs(f"{stamp} {note}; broker failed; {outcome}\n".encode())
                if not broker_error.cleanup_confirmed:
                    write_logs(
                        f"{stamp} spawn_daemon: cleanup uncertain; contained retry refused\n".encode()
                    )
                raise pu.DaemonSpawnError(
                    f"{note}; Windows lifetime broker failed; {outcome}",
                    cleanup_confirmed=broker_error.cleanup_confirmed,
                ) from broker_error
        note += "; broker failed; starting the daemon inside it (contained_fallback)"
        pu.log.warning("%s (%s)", note, command[0])
        write_logs(f"{stamp} {note}\n".encode())
        kwargs["creationflags"] = flags
        return subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603
