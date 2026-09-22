"""Installer-managed daemon lifecycle CLI for ``agent-bridge``."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


def _core():
    from . import __main__ as core

    return core


def _print_reconcile_status() -> None:
    """Surface the last session-start auto-reconcile attempt, if recorded."""
    core = _core()
    status_path = os.path.join(core._INSTALL_DIR, "reconcile-status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        return
    at = st.get("at", "?")
    frm = st.get("from", "?")
    to = st.get("to", "?")
    log = st.get("log", os.path.join(core._INSTALL_DIR, "reconcile.log"))
    print(f"  Last auto-reconcile: {at}  {frm} -> {to}")
    print(f"    log: {log}")


def _reconcile_service_marker(pid: int, version: str | None) -> None:
    """Point the service-management side files at the post-cutover active daemon."""
    core = _core()
    from .runtime_version import write_running_version

    try:
        with open(core._PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass
    if version:
        write_running_version(pid=pid, version=version)


def _pid_on_port(port: int) -> int | None:
    """Best-effort: find the PID listening on *port* (cross-platform)."""
    import subprocess as sp

    if sys.platform == "win32":
        ps = (
            "(Get-NetTCPConnection -LocalPort {0} -State Listen "
            "-ErrorAction SilentlyContinue | Select-Object -First 1)"
            ".OwningProcess".format(port)
        )
        try:
            out = sp.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True, text=True, timeout=15,
            )
            val = (out.stdout or "").strip()
            return int(val) if val.isdigit() else None
        except (OSError, sp.TimeoutExpired, ValueError):
            return None
    for cmd in (["ss", "-lptnH", f"sport = :{port}"], ["lsof", "-ti", f"tcp:{port}"]):
        try:
            out = sp.run(cmd, capture_output=True, text=True, timeout=15)
        except (OSError, sp.TimeoutExpired):
            continue
        text = out.stdout or ""
        if cmd[0] == "lsof":
            line = text.strip().splitlines()
            if line and line[0].isdigit():
                return int(line[0])
        else:
            import re

            m = re.search(r"pid=(\d+)", text)
            if m:
                return int(m.group(1))
    return None


def _kill_pid(pid: int) -> None:
    import signal as _signal
    import subprocess as sp

    if sys.platform == "win32":
        sp.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, text=True)
    else:
        try:
            os.kill(pid, _signal.SIGTERM)
        except OSError:
            pass


def _force_kill_agent_bridge_tree(pid: int) -> None:
    """Force-kill a verified retired daemon and its process group/tree."""
    import signal as _signal

    if sys.platform == "win32":
        _kill_pid(pid)
        return
    from .procgroup import safe_killpg

    if not safe_killpg(pid, _signal.SIGKILL):
        try:
            os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass


def _ensure_retired_daemon_exited(
    pid: int,
    *,
    graceful_timeout: float = 15.0,
    forced_timeout: float = 10.0,
) -> tuple[bool, bool]:
    """Wait for a retired bridge, then force-reap its verified process tree."""
    import time

    core = _core()
    if pid <= 0 or not core._pid_is_agent_bridge(pid):
        return True, False
    deadline = time.monotonic() + max(0.0, graceful_timeout)
    while time.monotonic() < deadline:
        time.sleep(0.25)
        if not core._pid_is_agent_bridge(pid):
            return True, False

    core._force_kill_agent_bridge_tree(pid)
    deadline = time.monotonic() + max(0.0, forced_timeout)
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if not core._pid_is_agent_bridge(pid):
            return True, True
    return not core._pid_is_agent_bridge(pid), True


def _pid_is_agent_bridge(pid: int, timeout: float = 15.0) -> bool:
    """True if *pid* is a live process running the ``agent_bridge`` module."""
    if pid <= 0:
        return False
    import subprocess as sp

    try:
        if sys.platform == "win32":
            out = sp.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "(Get-CimInstance Win32_Process -Filter "
                    f"'ProcessId={pid}' -ErrorAction SilentlyContinue).CommandLine",
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return "agent_bridge" in (out.stdout or "")
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return b"agent_bridge" in fh.read()
        except OSError:
            out = sp.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return "agent_bridge" in (out.stdout or "")
    except (OSError, sp.TimeoutExpired, ValueError):
        return False


def _reap_abandoned_passive(config_dir_path, record: dict | None) -> dict:
    """Retire a passive daemon stranded by an abandoned cutover."""
    core = _core()
    try:
        from zdd import routing
        from zdd.breadcrumb import reap_abandoned_passive

        table = routing.read_table(config_dir_path) or {}
        active = table.get("active") if isinstance(table, dict) else None
        active_pid = active.get("pid") if isinstance(active, dict) else None

        def _terminate(pid: int) -> bool:
            exited, _forced = core._ensure_retired_daemon_exited(pid)
            return exited

        return reap_abandoned_passive(
            config_dir_path,
            pid_alive=core._pid_is_agent_bridge,
            terminate=_terminate,
            active_pid=int(active_pid) if active_pid else None,
            record=record,
        )
    except Exception as exc:
        return {"reaped": False, "reason": f"reap skipped: {exc}", "pid": None}


def _pid_from_lock(port: int) -> int | None:
    """Holder pid of the singleton lock, if it is a live agent-bridge daemon."""
    core = _core()
    from pathlib import Path

    from .singleton import _read_holder_pid

    lock_path = Path(core._INSTALL_DIR) / f"agent-bridge.{port}.lock"
    pid = _read_holder_pid(lock_path)
    if pid and pid != os.getpid() and core._pid_is_agent_bridge(pid):
        return pid
    return None


def _systemd_available() -> bool:
    unit = os.path.expanduser(f"~/.config/systemd/user/{_core()._SYSTEMD_UNIT}")
    return (
        sys.platform != "win32"
        and shutil.which("systemctl") is not None
        and os.path.exists(unit)
    )


def _win_task_exists() -> bool:
    """True when the ``Agent Bridge`` scheduled task is registered."""
    import subprocess as sp

    try:
        out = sp.run(
            ["schtasks", "/Query", "/TN", _core()._WIN_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, sp.TimeoutExpired):
        return False
    if out.returncode == 0:
        return True
    blob = f"{out.stdout or ''}\n{out.stderr or ''}".casefold()
    return "access is denied" in blob


def _daemon_launch_argv() -> list[str]:
    """Argv that starts the foreground daemon without the Windows ``.cmd`` shim."""
    core = _core()
    if sys.executable:
        return [sys.executable, "-m", "agent_bridge", "start"]
    venv = os.path.join(core._INSTALL_DIR, "venv")
    py = (
        os.path.join(venv, "Scripts", "python.exe")
        if sys.platform == "win32"
        else os.path.join(venv, "bin", "python")
    )
    if os.path.isfile(py):
        return [py, "-m", "agent_bridge", "start"]
    exe = shutil.which("agent-bridge")
    if exe:
        return [exe, "start"]
    return ["agent-bridge", "start"]


def _spawn_via_wmi_broker_pid(argv: list[str]) -> int | None:
    """Launch the daemon through WMI ``Win32_Process.Create``."""
    import base64
    import subprocess as _sp

    core = _core()
    log = os.path.join(core._INSTALL_DIR, "agent-bridge.log")
    err = os.path.join(core._INSTALL_DIR, "agent-bridge-err.log")
    inner = " ".join(f'"{a}"' for a in argv) + f' >> "{log}" 2>> "{err}"'
    cmdline = f'conhost.exe --headless cmd.exe /c "{inner}"'
    ps_cmdline = cmdline.replace("'", "''")
    ps_cwd = core._INSTALL_DIR.replace("'", "''")
    ps = (
        "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
        f"-Arguments @{{ CommandLine = '{ps_cmdline}'; "
        f"CurrentDirectory = '{ps_cwd}' }}; "
        "if ($r.ReturnValue -eq 0) { "
        "[Console]::Out.WriteLine([int]$r.ProcessId) }; "
        "exit [int]$r.ReturnValue"
    )
    encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    try:
        out = _sp.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True,
            text=True,
            timeout=30,
            **core.no_window_kwargs(),
        )
        if out.returncode != 0:
            return None
        try:
            pid = int((out.stdout or "").strip())
        except (TypeError, ValueError):
            return None
        return pid if pid > 0 else None
    except (OSError, _sp.TimeoutExpired):
        return None


def _spawn_via_wmi_broker(argv: list[str]) -> bool:
    return _spawn_via_wmi_broker_pid(argv) is not None


class _BrokeredProcessHandle:
    def __init__(self, pid: int):
        self.pid = pid

    def poll(self) -> int | None:
        from .session_host.osutil import pid_alive

        return None if pid_alive(self.pid) else 0

    def terminate(self) -> None:
        core = _core()
        subprocess.run(
            ["taskkill", "/PID", str(self.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            **core.no_window_kwargs(),
        )


def _spawn_detached_argv(argv: list[str]) -> None:
    """Spawn ``argv`` as a detached, job-surviving process."""
    import subprocess as _sp

    core = _core()
    try:
        logf = open(os.path.join(core._INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(core._INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            argv,
            stdout=logf,
            stderr=errf,
            stdin=_sp.DEVNULL,
            **core.windowless_daemon_kwargs(breakaway=True),
        )
        return
    except OSError:
        if sys.platform == "win32" and core._spawn_via_wmi_broker(argv):
            return
        logf = open(os.path.join(core._INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(core._INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            argv,
            stdout=logf,
            stderr=errf,
            stdin=_sp.DEVNULL,
            **core.windowless_daemon_kwargs(),
        )


def _spawn_detached_daemon() -> None:
    """Spawn ``agent-bridge start`` through the job-surviving launch path."""
    _spawn_detached_argv(_daemon_launch_argv())


def _spawn_watchdog_replacement(
    *,
    delay: float = 1.0,
    start_args: list[str] | None = None,
    active_port: int | None = None,
) -> None:
    """Schedule a fresh daemon after the wedged Windows process releases its lock."""
    code = (
        "import os,sys,time;"
        "time.sleep(float(sys.argv[1]));"
        "os.execv(sys.executable,[sys.executable,'-m','agent_bridge',*sys.argv[2:]])"
    )
    original_args = list(sys.argv[1:] if start_args is None else start_args)
    if "--passive" in original_args and active_port is not None:
        try:
            port_index = original_args.index("--port") + 1
            serving_port = int(original_args[port_index])
        except (ValueError, IndexError):
            serving_port = None
        if serving_port == active_port:
            original_args.remove("--passive")
    argv = [sys.executable, "-c", code, str(delay), *original_args]
    _core()._spawn_detached_argv(argv)


def _watchdog_dead(reason: str, *, active_port: int | None = None) -> None:
    """Restart the Windows frontend promptly, then hard-exit the wedged one."""
    from .watchdog import _force_exit

    try:
        _core()._spawn_watchdog_replacement(active_port=active_port)
        import logging

        logging.getLogger("agent-bridge").error(
            "Self-watchdog: scheduled a detached Windows replacement before exit"
        )
    except Exception as exc:
        import logging

        logging.getLogger("agent-bridge").error(
            "Self-watchdog: could not schedule Windows replacement: %s", exc
        )
    _force_exit(reason)


def _acquire_ensure_lock() -> int | None:
    """Best-effort single-flight lock so concurrent CLI invocations don't each boot."""
    import time

    core = _core()
    try:
        fd = os.open(core._ENSURE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except FileExistsError:
        try:
            age = time.time() - os.path.getmtime(core._ENSURE_LOCK)
        except OSError:
            age = core._ENSURE_BACKOFF_S + 1
        if age > core._ENSURE_BACKOFF_S:
            try:
                os.unlink(core._ENSURE_LOCK)
            except OSError:
                return None
            return _acquire_ensure_lock()
        return None
    except OSError:
        return None


def _release_ensure_lock(fd: int) -> None:
    core = _core()
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(core._ENSURE_LOCK)
    except OSError:
        pass


def _wait_for_ensure_owner() -> bool:
    """Follow a concurrent ensure from process appearance through liveness."""
    import time

    core = _core()
    for _ in range(int(core._ENSURE_BACKOFF_S)):
        if core._service_is_running():
            return True
        if core._service_process_is_live():
            return core._wait_for_service_start()
        if not os.path.exists(core._ENSURE_LOCK):
            return core._wait_for_service_start()
        time.sleep(1)
    return core._service_is_running()


def _ensure_daemon() -> bool:
    """Boot the daemon if it is down, so a daemon-touching command self-heals."""
    import time

    core = _core()
    if os.environ.get("AGENT_BRIDGE_NO_ENSURE") == "1":
        return core._service_is_running()
    if core._service_is_running():
        return True
    if core._reconcile_live_dynamic_daemon():
        return True
    if core._service_process_is_live() and core._wait_for_service_start():
        return True

    now = time.time()
    try:
        last_attempt = os.path.getmtime(core._ENSURE_MARKER)
    except OSError:
        last_attempt = 0.0
    if now - last_attempt < core._ENSURE_BACKOFF_S:
        if core._service_process_is_live():
            return core._wait_for_service_start()
        if os.path.exists(core._ENSURE_LOCK):
            return core._wait_for_ensure_owner()
        return core._service_is_running()

    fd = core._acquire_ensure_lock()
    if fd is None:
        return core._wait_for_ensure_owner()
    lock_held = True
    try:
        if core._service_is_running():
            return True
        if core._service_process_is_live():
            core._release_ensure_lock(fd)
            lock_held = False
            return core._wait_for_service_start()
        try:
            with open(core._ENSURE_MARKER, "w") as fh:
                fh.write(str(now))
        except OSError:
            pass
        core._spawn_detached_daemon()
        for _ in range(20):
            time.sleep(1)
            if core._service_is_running():
                return True
        return False
    finally:
        if lock_held:
            core._release_ensure_lock(fd)


def _service_start() -> None:
    import subprocess as sp

    core = _core()
    if core._service_is_running():
        print(f"[OK] agent-bridge already running (port {core._service_port()})")
        return
    if core._reconcile_live_dynamic_daemon():
        print(f"[OK] agent-bridge recovered dynamic route (port {core._service_port()})")
        return

    used_platform_manager = False
    if core._systemd_available():
        sp.run(["systemctl", "--user", "start", core._SYSTEMD_UNIT])
        used_platform_manager = True
    elif sys.platform == "win32" and core._win_task_exists():
        sp.run(["schtasks", "/Run", "/TN", core._WIN_TASK_NAME], capture_output=True, text=True)
        used_platform_manager = True
    else:
        core._spawn_detached_daemon()

    if core._wait_for_service_start():
        print(f"[OK] agent-bridge started (port {core._service_port()})")
        return

    if used_platform_manager:
        core._spawn_detached_daemon()
        if core._wait_for_service_start():
            print(f"[OK] agent-bridge started (port {core._service_port()})")
            return

    print(
        "[WARN] agent-bridge start issued but health check did not pass yet "
        "-- check ~/.agent-bridge/agent-bridge-err.log",
        file=sys.stderr,
    )


def _service_stop() -> None:
    import subprocess as sp
    import time

    core = _core()
    stopped_any = False

    if core._systemd_available():
        sp.run(["systemctl", "--user", "stop", core._SYSTEMD_UNIT])
        stopped_any = True
    elif sys.platform == "win32" and core._win_task_exists():
        sp.run(["schtasks", "/End", "/TN", core._WIN_TASK_NAME], capture_output=True, text=True)
        stopped_any = True

    port = core._service_port()
    victims = {
        core._read_pid_file(),
        core._pid_on_port(port),
        core._pid_from_lock(port),
        core._pid_from_lock(0),
    }
    victims.discard(None)
    for victim in victims:
        core._kill_pid(victim)
        stopped_any = True
    if victims:
        try:
            os.remove(core._PID_FILE)
        except OSError:
            pass

    if not stopped_any:
        print("[SKIP] agent-bridge does not appear to be running")
        return

    for _ in range(10):
        locks = {core._pid_from_lock(core._service_port()), core._pid_from_lock(0)}
        locks.discard(None)
        live_victims = {victim for victim in victims if core._pid_is_agent_bridge(victim)}
        if not core._service_is_running() and not locks and not live_victims:
            print("[OK] agent-bridge stopped")
            return
        time.sleep(1)
    print("[WARN] agent-bridge stop issued but still responding", file=sys.stderr)


def _cmd_service(args: argparse.Namespace) -> None:
    core = _core()
    action = getattr(args, "service_action", None)
    if action == "start":
        core._service_start()
    elif action == "stop":
        core._service_stop()
    elif action == "restart":
        core._service_stop()
        import time

        time.sleep(3)
        core._service_start()
    elif action == "status":
        core._cmd_status(args)
        pid = core._service_pid()
        if pid:
            print(f"  PID:  {pid}")
        print(f"  Port: {core._service_port()}")
        core._print_reconcile_status()
    else:
        print("Usage: agent-bridge service {start|stop|restart|status}", file=sys.stderr)
        sys.exit(1)


def register_service_control_commands(sub: argparse._SubParsersAction) -> None:
    service_p = sub.add_parser(
        "service",
        help="Control the agent-bridge daemon (start/stop/restart/status)",
    )
    service_sub = service_p.add_subparsers(dest="service_action")
    for _act, _help in (
        ("start", "Start the agent-bridge daemon"),
        ("stop", "Stop the agent-bridge daemon"),
        ("restart", "Restart the agent-bridge daemon"),
        ("status", "Show daemon status, port, and PID"),
    ):
        service_sub.add_parser(_act, help=_help)
    service_p.set_defaults(func=_cmd_service)
