"""Resident-daemon launch policy shared by every process-utility consumer."""

from __future__ import annotations

import contextlib
import importlib
import os
import subprocess
import sys
import time
from pathlib import Path
from collections.abc import Mapping, Sequence

from .daemon_broker import WindowsDaemonProcess


def spawn_brokered_daemon(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
    instance_receipt=None,
) -> WindowsDaemonProcess:
    """Read the installed consumer's Task adoption; never install one.

    Unrelated to :func:`spawn_daemon`'s WMI durable launch in ``daemon_broker``.
    """
    import agent_procutil as pu

    try:
        # Minimal remote hosts omit this installed-only legacy adapter.
        spawn = importlib.import_module(".windows_task_broker", __package__).spawn
    except ModuleNotFoundError as error:
        if error.name not in {"zdd", f"{__package__}.windows_task_broker"}:
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
    python_bootstrap_root: str | os.PathLike[str] | None = None,
    stdio_fds: tuple[int, int, int] | None = None,
) -> subprocess.Popen | WindowsDaemonProcess:
    """Launch a windowless resident daemon from an explicit durable cwd.

    Output is appended to absolute, non-payload log paths with a safe launch
    header. ``env_overrides`` layers over ``env`` (the current environment by
    default). Extra creation flags may add only priority/error-mode policy.

    Windows durable children (``breakaway=True`` outside contained test mode)
    are launched through WMI ``Win32_Process.Create`` (#12117): they start
    outside every caller Job and process tree, so installer or caller
    termination, a tree-kill or a kill-on-close Job never ends them. Use this
    only for daemons that must outlive their launcher (installer service
    start, daemon self-restart, supervisor restart, a CLI ensuring its own
    backing daemon); bounded and tool-to-tool children use normal creation.
    A blocked WMI launch raises :class:`DaemonSpawnError`; only
    ``contained_fallback`` explicitly accepts the caller's shorter lifetime
    afterwards. ``breakaway=False`` and contained test mode stay native and
    contained. Finite workers keep native suspended breakaway verified against
    tracked launcher-owned Jobs and are refused under a strict caller Job.
    POSIX uses a new session; durable Linux
    launches also leave a systemd caller's cleanup unit through a user scope.
    ``python_bootstrap_root`` selects an explicitly retained agent_procutil
    source root for leased Python admission, rather than the launcher's copy.
    """
    creation = [False]
    try:
        return _spawn_daemon(
            argv, cwd=cwd, log_path=log_path, env=env, env_overrides=env_overrides,
            console_children=console_children, breakaway=breakaway,
            contained_fallback=contained_fallback, extra_creationflags=extra_creationflags,
            display=display, stderr_log_path=stderr_log_path, instance=instance,
            python_bootstrap_root=python_bootstrap_root, _creation=creation,
            stdio_fds=stdio_fds,
        )
    except BaseException as error:
        if not creation[0]:
            error.process_created = False
            error.cleanup_confirmed = True
            error.admission_sent = False
        raise


def _spawn_daemon(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    log_path: str | os.PathLike[str],
    env: Mapping[str, str] | None,
    env_overrides: Mapping[str, str] | None,
    console_children: bool,
    breakaway: bool,
    contained_fallback: bool,
    extra_creationflags: int,
    display: str | None,
    stderr_log_path: str | os.PathLike[str] | None,
    instance,
    python_bootstrap_root: str | os.PathLike[str] | None = None,
    stdio_fds: tuple[int, int, int] | None = None,
    _creation: list[bool],
) -> subprocess.Popen | WindowsDaemonProcess:
    import agent_procutil as pu

    command = _validated_daemon_argv(argv)
    extra_creationflags = pu._validated_extra_creationflags(extra_creationflags)
    cwd_path = pu._validated_daemon_cwd(cwd)
    log_file = pu._validated_daemon_log_path(log_path)
    if stdio_fds is not None:
        if (
            type(stdio_fds) is not tuple or len(stdio_fds) != 3
            or any(type(fd) is not int or fd < 0 for fd in stdio_fds)
            or stderr_log_path is not None
        ):
            raise ValueError("stdio_fds requires three open descriptors and no stderr_log_path")
        for descriptor in stdio_fds:
            os.fstat(descriptor)
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
        command, python_environment = leased_python_command(
            command, bootstrap_root=python_bootstrap_root,
        )
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
            if not settled:
                failure.process = process
            raise failure from error
        process.instance_receipt = receipt
        return process
    # A contained-test child environment keeps the launch contained even when
    # the launcher itself is not in contained test mode.
    if child_env.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        breakaway = False
    kwargs = pu._daemon_popen_kwargs(
        console_children=console_children,
        breakaway=breakaway,
        extra_creationflags=extra_creationflags,
    )
    @contextlib.contextmanager
    def diagnostic_stream(path):
        manager = pu._open_daemon_log(path)
        stream = manager.__enter__()
        try:
            yield stream
        finally:
            try:
                manager.__exit__(*sys.exc_info())
            except OSError:
                pass

    with contextlib.ExitStack() as stack:
        log_fh = stack.enter_context(diagnostic_stream(log_file))
        err_fh = stack.enter_context(diagnostic_stream(err_file)) if err_file else None
        logs = [fh for fh in (log_fh, err_fh) if fh is not None]

        def write_logs(data: bytes) -> None:
            for fh in logs:
                fh.write(data)
                fh.flush()

        def report_logs(data: bytes) -> None:
            try:
                write_logs(data)
            except OSError:
                pass

        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_logs(pu._daemon_log_header(stamp, cwd_path, command[0], display))

        def reserve():
            try:
                return reserve_instance(instance, environment=child_env)
            except Exception as error:
                code = getattr(error, "errno", None)
                report_logs(
                    f"{stamp} spawn_daemon: instance-reservation-failed; "
                    f"type={type(error).__name__}; errno={code if type(code) is int else None}\n".encode()
                )
                raise

        popen_kwargs = {
            "cwd": cwd_path,
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fh,
            "stderr": subprocess.STDOUT if err_fh is None else err_fh,
            "close_fds": True,
        }
        if stdio_fds is not None:
            popen_kwargs.update(zip(("stdin", "stdout", "stderr"), stdio_fds))
        flags = kwargs.get("creationflags", 0)
        durable_windows = bool(flags & pu._CREATE_BREAKAWAY_FROM_JOB)
        if pu._is_windows():
            from . import _job_scopes

            scope_environment = _job_scopes.child_environment(child_env)
            child_env = scope_environment.copy()
            popen_kwargs["env"] = child_env
        # Finite workers are bounded work, not #12117 durable daemons: they
        # keep native suspended breakaway and never escape a strict caller Job.
        finite = instance is not None and instance.lifecycle == "finite-worker"
        wmi_durable = durable_windows and not finite
        if durable_windows:
            child_env.pop(_job_scopes.ENVIRONMENT_KEY, None)
        if durable_windows and not wmi_durable:
            kwargs["creationflags"] = flags | pu._CREATE_SUSPENDED
        receipt = reserve()
        if durable_windows:
            try:
                write_logs(f"{stamp} spawn_daemon: instance-reserved\n".encode())
            except BaseException:
                refused()
                raise
        child_env.update(instance_environment(receipt))
        process = None
        creation_attempted = False
        creation_refused = False

        def create():
            nonlocal creation_attempted, creation_refused
            launch_command = command
            if (
                not pu._is_windows() and breakaway and not pu.contained_test_mode()
                and child_env.get("COPILOT_EXTENSIONS_TEST_CONTAINED") != "1"
            ):
                from .linux_daemon_scope import scope_command

                launch_command = scope_command(command, receipt.instance_id, child_env, cwd=cwd_path)
            creation_attempted, creation_refused = True, False
            _creation[0] = True
            try:
                return subprocess.Popen(launch_command, **popen_kwargs, **kwargs)  # noqa: S603
            except (OSError, ValueError, TypeError) as error:
                creation_refused = True
                error.process_created = False
                error.cleanup_confirmed = True
                error.admission_sent = False
                raise

        resume_attempted = False
        if not wmi_durable:
            try:
                if durable_windows:
                    # Query handles must close before resume: inherited handles
                    # would defeat the caller's KILL_ON_JOB_CLOSE ownership.
                    with _job_scopes.query_scopes(scope_environment) as verify:
                        report_logs(f"{stamp} spawn_daemon: creating-suspended\n".encode())
                        process = create()
                        report_logs(f"{stamp} spawn_daemon: created-suspended\n".encode())
                        verify(process._handle)
                        report_logs(f"{stamp} spawn_daemon: caller-jobs-verified\n".encode())
                    resume_attempted = True
                    if not pu._resume_suspended_process(process.pid):
                        raise OSError("failed to resume verified daemon")
                    report_logs(f"{stamp} spawn_daemon: resumed\n".encode())
                else:
                    process = create()
            except BaseException as error:
                if process is None and creation_attempted and not creation_refused:
                    report_logs(
                        f"{stamp} spawn_daemon: creation-outcome-unknown; "
                        f"type={type(error).__name__}; reservation retained\n".encode()
                    )
                    raise
                if not durable_windows:
                    refused()
                    raise
                identity = created_process_identity(process) if process is not None else None
                cleanup_confirmed = process is None
                if process is not None:
                    try:
                        process.kill()
                        process.wait(timeout=5)
                        cleanup_confirmed = True
                    except (OSError, subprocess.SubprocessError):
                        pass
                    settled = failed(
                        identity, exited=cleanup_confirmed, admission_possible=resume_attempted,
                    )
                else:
                    settled = refused()
                if (
                    process is None and creation_attempted and settled
                    and not pu._breakaway_denied(error, kwargs)
                ):
                    raise
                if not isinstance(error, (OSError, ValueError)) and cleanup_confirmed and settled:
                    raise
                denied = (
                    process is None and creation_attempted and pu._breakaway_denied(error, kwargs)
                )
                reason = (
                    "the caller's Job object forbids breakaway; daemon not started"
                    if denied else "native daemon launch could not escape launcher-owned cleanup Jobs"
                )
                report_logs(
                    f"{stamp} spawn_daemon: {reason}; "
                    f"cleanup_confirmed={cleanup_confirmed}; reservation_settled={settled}\n".encode()
                )
                if not contained_fallback or not cleanup_confirmed or not settled:
                    failure = pu.DaemonSpawnError(
                        reason,
                        cleanup_confirmed=cleanup_confirmed, process_created=process is not None,
                        admission_sent=resume_attempted, process_identity=identity,
                    )
                    failure.instance_receipt = receipt
                    if not cleanup_confirmed:
                        failure.process = process
                    raise failure from error
                note = "spawn_daemon: explicit contained_fallback accepts caller-owned lifetime"
                pu.log.warning("%s (%s)", note, command[0])
                report_logs(f"{stamp} {note}\n".encode())
                child_env.update(scope_environment)
                kwargs["creationflags"] = flags & ~pu._CREATE_BREAKAWAY_FROM_JOB
                receipt = reserve()
                child_env.update(instance_environment(receipt))
                try:
                    process = create()
                except OSError:
                    refused()
                    raise
            process = commit(process)
            if durable_windows:
                report_logs(f"{stamp} spawn_daemon: instance-bound\n".encode())
            return process

        # Windows durable launch (#12117): always through WMI, never a native
        # breakaway child that a caller tree-kill or outer Job could still end.
        report_logs(f"{stamp} spawn_daemon: wmi-durable-launch\n".encode())
        _creation[0] = True
        handles = None
        if stdio_fds is not None:
            import msvcrt

            handles = tuple(msvcrt.get_osfhandle(fd) for fd in stdio_fds)
        try:
            process = _durable_windows_launch(
                command, cwd=cwd_path, env=dict(child_env), creationflags=flags,
                log_path=log_file, stderr_log_path=err_file,
                instance_receipt=receipt, stdio_handles=handles,
            )
        except BaseException as error:
            if not isinstance(error, pu.DaemonSpawnError):
                report_logs(
                    f"{stamp} spawn_daemon: creation-outcome-unknown; "
                    f"type={type(error).__name__}; reservation retained\n".encode()
                )
                raise
            created = getattr(error, "process_created", None)
            cleanup_confirmed = getattr(error, "cleanup_confirmed", False) is True
            identity = getattr(error, "process_identity", None)
            if created is False and cleanup_confirmed:
                settled = refused()
            elif created is None and not cleanup_confirmed:
                settled = False
            else:
                settled = failed(
                    identity, exited=cleanup_confirmed,
                    admission_possible=getattr(error, "admission_sent", True) is not False,
                )
            reason = str(error) or "Windows durable WMI launch failed; daemon not started"
            report_logs(
                f"{stamp} spawn_daemon: durable WMI launch failed; daemon not started; "
                f"cleanup_confirmed={cleanup_confirmed}; reservation_settled={settled}\n".encode()
            )
            if not contained_fallback or not cleanup_confirmed or not settled:
                failure = pu.DaemonSpawnError(
                    reason, cleanup_confirmed=cleanup_confirmed,
                    process_created=created, admission_sent=getattr(error, "admission_sent", True),
                    process_identity=identity,
                )
                failure.instance_receipt = receipt
                if getattr(error, "process", None) is not None:
                    failure.process = error.process
                raise failure from error
            note = "spawn_daemon: explicit contained_fallback accepts caller-owned lifetime"
            pu.log.warning("%s (%s)", note, command[0])
            report_logs(f"{stamp} {note}\n".encode())
            child_env.update(scope_environment)
            kwargs["creationflags"] = flags & ~pu._CREATE_BREAKAWAY_FROM_JOB
            receipt = reserve()
            child_env.update(instance_environment(receipt))
            try:
                process = create()
            except OSError:
                refused()
                raise
            return commit(process)
        process = commit(process)
        report_logs(f"{stamp} spawn_daemon: instance-bound\n".encode())
        return process


def _durable_windows_launch(command, **kwargs) -> WindowsDaemonProcess:
    from . import daemon_broker

    return daemon_broker.spawn_brokered_daemon(command, **kwargs)
