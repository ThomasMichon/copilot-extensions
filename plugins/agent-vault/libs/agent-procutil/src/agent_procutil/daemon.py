"""Resident-daemon launch policy shared by every process-utility consumer."""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path
from collections.abc import Mapping, Sequence

from .daemon_broker import WindowsDaemonProcess


def spawn_brokered_daemon(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
    instance_receipt=None,
) -> WindowsDaemonProcess:
    """Read the installed consumer's adoption; never install or fall back to WMI."""
    import agent_procutil as pu

    try:
        from .windows_task_broker import spawn
    except ModuleNotFoundError as error:
        if error.name != "zdd":
            raise
        raise pu.DaemonSpawnError(
            "Windows launch broker: owning installation requires windows-broker support",
            process_created=False, admission_sent=False,
        ) from error
    return spawn(
        command, cwd=cwd, env=env, creationflags=creationflags,
        log_path=log_path, stderr_log_path=stderr_log_path,
        instance_receipt=instance_receipt,
    )


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


def _resolve_posix_executable(executable: str, cwd: str, environment: Mapping[str, str]) -> Path:
    if os.path.dirname(executable):
        candidates = [Path(executable) if os.path.isabs(executable) else Path(cwd) / executable]
    else:
        candidates = [
            (Path(directory) if os.path.isabs(directory) else Path(cwd) / directory) / executable
            for directory in os.get_exec_path(environment)
        ]
    targets = set()
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            targets.add(candidate.resolve(strict=True))
    if not targets:
        raise FileNotFoundError(f"daemon executable cannot be resolved: {executable}")
    if len(targets) != 1:
        # execvpe may skip an existing candidate after an execution error.
        # Without pinning an explicit target, that provenance is ambiguous.
        raise ValueError("ambiguous daemon executable search; pass an explicit instance")
    return targets.pop()


def _automatic_instance(command: list[str], cwd: str, environment: Mapping[str, str]):
    from .daemon_instances import DaemonInstanceSpec

    if os.name == "nt":
        executable = Path(command[0])
        # CreateProcess does not use the child's cwd/PATH for module search.
        # Do not emulate it using which(), or change what Windows would launch.
        if not executable.is_absolute() or executable.suffix.lower() not in (".exe", ".com"):
            raise ValueError("automatic Windows provenance requires an absolute executable; pass instance")
        if not executable.is_file():
            raise FileNotFoundError(command[0])
        source = executable.resolve(strict=True)
    else:
        source = _resolve_posix_executable(command[0], cwd, environment)
    return DaemonInstanceSpec(Path(command[0]).stem, Path(cwd), source)


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
    instance=None,
) -> subprocess.Popen | WindowsDaemonProcess:
    """Launch a windowless resident daemon from an explicit durable cwd.

    Output is appended to absolute, non-payload log paths with a safe launch
    header. ``env_overrides`` layers over ``env`` (the current environment by
    default). Extra creation flags may add only priority/error-mode policy.

    On Windows, a caller already in a Job or breakaway denial uses its installed
    consumer's adopted finite broker; the returned handle controls the actual daemon. If that
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
    from .daemon_instances import (
        TEST_CONTEXT_ENV, cancel_instance_reservation, instance_environment, reserve_instance,
    )
    from ._daemon_instance_store import bind_process, finish_failed_launch
    from ._daemon_instance_identity import created_process_identity
    if not child_env.get(TEST_CONTEXT_ENV) and os.environ.get(TEST_CONTEXT_ENV):
        child_env[TEST_CONTEXT_ENV] = os.environ[TEST_CONTEXT_ENV]

    automatic = instance is None
    if automatic:
        _automatic_instance(command, cwd_path, child_env)
    from ._daemon_instance_store import _test_context
    if (
        instance is not None and (instance.lease is not None or instance.lifecycle == "finite-worker")
    ) or _test_context(child_env) is not None:
        from ._daemon_instance_child import leased_python_command
        command, python_environment = leased_python_command(command)
        child_env.update(python_environment)
    else:
        from ._daemon_instance_child import normalize_python_command
        command, python_environment = normalize_python_command(command)
        child_env.update(python_environment)
    if automatic:
        instance = _automatic_instance(command, cwd_path, child_env)

    def refused():
        try:
            cancel_instance_reservation(receipt, creation_refused=True)
            return True
        except (OSError, ValueError, KeyError):
            return False

    def failed(identity, *, exited, admission_possible):
        try:
            return finish_failed_launch(
                receipt, identity, exit_confirmed=exited,
                admission_possible=admission_possible,
            )
        except (OSError, ValueError, KeyError):
            return False

    def commit(process):
        identity = created_process_identity(process)
        try:
            if identity is None:
                bind_process(receipt, process.pid)
            else:
                bind_process(receipt, process.pid, observed_birth=identity[1])
        except BaseException as error:
            settled = False
            try:
                process.kill()
                process.wait(timeout=5)
                settled = True
            except (OSError, subprocess.SubprocessError):
                pass
            failed(identity, exited=settled, admission_possible=True)
            failure = pu.DaemonSpawnError(
                "daemon instance commit failed", cleanup_confirmed=settled,
                process_created=True, process_identity=identity,
            )
            failure.instance_receipt = receipt
            raise failure from error
        process.instance_receipt = receipt
        return process
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
            receipt = reserve_instance(instance, environment=child_env)
            child_env.update(instance_environment(receipt))
            try:
                process = subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603
            except OSError as exc:
                if not pu._breakaway_denied(exc, kwargs):
                    refused()
                    raise
                if not refused():
                    failure = pu.DaemonSpawnError(
                        "direct refusal reservation could not be settled",
                        process_created=False, admission_sent=False,
                    )
                    failure.instance_receipt = receipt
                    raise failure from exc
                note = "spawn_daemon: the caller's Job object forbids breakaway"
            else:
                return commit(process)
        flags &= ~pu._CREATE_BREAKAWAY_FROM_JOB
        write_logs(f"{stamp} {note}; trying Windows lifetime broker\n".encode())
        def log_failure(phase, error):
            codes = {
                name: value if type(value := getattr(error, name, None)) is int else None
                for name in ("errno", "winerror")
            }
            write_logs(
                f"{stamp} spawn_daemon: {phase}; type={type(error).__name__}; "
                f"errno={codes['errno']}; winerror={codes['winerror']}\n".encode()
            )

        try:
            receipt = reserve_instance(instance, environment=child_env)
            child_env.update(instance_environment(receipt))
        except Exception as error:
            log_failure("instance-reservation-failed", error)
            raise
        write_logs(f"{stamp} spawn_daemon: instance-reserved; broker-admission-starting\n".encode())
        try:
            process = spawn_brokered_daemon(
                command, cwd=cwd_path, env=child_env, creationflags=flags,
                log_path=log_file, stderr_log_path=err_file,
                instance_receipt=receipt,
            )
            process.instance_receipt = receipt
            return process
        except pu.DaemonSpawnError as broker_error:
            settled = False
            if broker_error.cleanup_confirmed:
                if broker_error.process_created is False:
                    settled = refused()
                elif broker_error.process_created is True:
                    settled = failed(
                        broker_error.process_identity, exited=True,
                        admission_possible=broker_error.admission_sent,
                    )
            else:
                failed(broker_error.process_identity, exited=False, admission_possible=True)
            if not contained_fallback or not settled:
                if not broker_error.cleanup_confirmed:
                    outcome = "partial launch cleanup unconfirmed"
                elif broker_error.process_created is False:
                    outcome = "daemon not started" + ("" if settled else "; reservation held")
                elif settled:
                    outcome = "unadmitted child exit confirmed"
                else:
                    outcome = "creation or startup effects held"
                write_logs(f"{stamp} {note}; broker failed; {outcome}\n".encode())
                if not broker_error.cleanup_confirmed:
                    write_logs(
                        f"{stamp} spawn_daemon: cleanup uncertain; contained retry refused\n".encode()
                    )
                failure = pu.DaemonSpawnError(
                    f"{note}; Windows lifetime broker failed; {outcome}",
                    cleanup_confirmed=broker_error.cleanup_confirmed,
                    process_created=broker_error.process_created,
                    admission_sent=broker_error.admission_sent,
                    process_identity=broker_error.process_identity,
                )
                failure.instance_receipt = receipt
                raise failure from broker_error
        except Exception as error:
            log_failure("broker-admission-failed", error)
            raise
        note += "; broker failed; starting the daemon inside it (contained_fallback)"
        pu.log.warning("%s (%s)", note, command[0])
        write_logs(f"{stamp} {note}\n".encode())
        kwargs["creationflags"] = flags
        receipt = reserve_instance(instance, environment=child_env)
        child_env.update(instance_environment(receipt))
        try:
            process = subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603
        except OSError:
            refused()
            raise
        return commit(process)
