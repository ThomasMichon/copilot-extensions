"""Installer-managed daemon lifecycle CLI for ``agent-bridge``."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys


def _core():
    from . import __main__ as core

    return core


#: Memoized result of :func:`_powershell_host`'s ``PATH`` resolution.
_powershell_host_cache: str | None = None


def _powershell_host() -> str:
    """Prefer ``pwsh`` (PowerShell 7); fall back to the legacy Windows
    PowerShell 5.1 binary only when ``pwsh`` isn't on ``PATH``.

    Several call sites in this module poll or spawn via a PowerShell host --
    some in a tight retry loop firing dozens of times per operation. A
    hardcoded ``"powershell"`` sends every one of those through the legacy
    5.1 host even on a machine where ``pwsh`` is installed and preferred.

    Memoized so a tight poll loop (e.g. ``_pid_is_agent_bridge`` retried every
    0.1-0.25s) does not repeat a ``shutil.which`` ``PATH`` scan on every tick.
    """
    global _powershell_host_cache
    if _powershell_host_cache is None:
        _powershell_host_cache = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
    return _powershell_host_cache


def _print_reconcile_status() -> None:
    """Surface background-reconcile staleness/observability.

    Background reconcile is unconditional now (no per-project opt-in --
    agent-bridge-unified-zdd-cutover Phase 0: the opt-in existed only because
    a raw reconcile could race a live daemon/session, and the daemon's own
    update path is always-ZDD, so that justification is gone). What remains
    worth surfacing is *staleness*: how long since the last recorded
    reconcile attempt, so an operator never has to reconstruct that from a
    raw log file.

    The status file is written twice per attempt (see bootstrap-check.sh/
    .ps1): once immediately on launch (``at`` only), then overwritten with
    ``completed_at``/``exit_code``/``success`` once the reconcile actually
    finishes. Reporting must distinguish these -- a launch timestamp alone
    would make a failed or still-wedged reconcile look falsely healthy.
    """
    core = _core()
    print("  Background reconcile: enabled (always-on; no per-project opt-in required)")
    status_path = os.path.join(core._INSTALL_DIR, "reconcile-status.json")
    try:
        with open(status_path, encoding="utf-8") as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        print("  Last auto-reconcile: none recorded yet")
        return
    at = st.get("at", "?")
    frm = st.get("from", "?")
    to = st.get("to", "?")
    log = st.get("log", os.path.join(core._INSTALL_DIR, "reconcile.log"))
    completed_at = st.get("completed_at")
    success = st.get("success")
    if completed_at is None:
        # The launch record hasn't been overwritten with completion info yet
        # -- either still running, or the hook/session exited before the
        # background process could report back.
        age_str = _format_reconcile_age(at)
        started = f"{age_str} ({at})" if age_str else at
        print(f"  Last auto-reconcile: started {started}, still in progress or unreported  {frm} -> {to}")
    else:
        age_str = _format_reconcile_age(completed_at)
        finished = f"{age_str} ({completed_at})" if age_str else completed_at
        outcome = "succeeded" if success else "FAILED"
        print(f"  Last auto-reconcile: {outcome} {finished}  {frm} -> {to}")
    print(f"    log: {log}")


def _format_reconcile_age(at: str) -> str | None:
    """Render an ISO-8601 UTC timestamp (``YYYY-MM-DDTHH:MM:SSZ``) as a
    human "N days/hours ago" string, or ``None`` if it can't be parsed."""
    try:
        recorded = datetime.datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.timezone.utc
        )
    except (ValueError, TypeError):
        return None
    delta = datetime.datetime.now(datetime.timezone.utc) - recorded
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return "just now"
    days = int(total_seconds // 86400)
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''} ago"
    hours = int(total_seconds // 3600)
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    minutes = int(total_seconds // 60)
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    return "just now"


def _reconcile_service_marker(pid: int, version: str | None) -> None:
    """Point the service-management side files at the post-cutover active daemon."""
    core = _core()
    from .runtime_version import consume_pending_generation_id, write_running_version

    try:
        with open(core._PID_FILE, "w", encoding="utf-8") as fh:
            fh.write(str(pid))
    except OSError:
        pass
    if version:
        # The being-PROMOTED daemon's own boot already staged its real
        # generation_id under its own pid (it must be health-gated, hence
        # already running, before promotion ever reaches here) -- consume
        # that staged entry for this CONFIRMED pid (never an abandoned
        # never-promoted passive's) and pass it straight through so this
        # full-payload rewrite carries it rather than dropping it.
        write_running_version(
            pid=pid, version=version, generation_id=consume_pending_generation_id(pid)
        )


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
                [_powershell_host(), "-NoProfile", "-Command", ps],
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


def _identity_termination_available() -> bool:
    """Whether this platform has a genuine identity-bound termination
    primitive at all (a build-time/platform capability, independent of any
    specific pid). Windows always does (ctypes/kernel32); POSIX only when
    both ``os.pidfd_open`` and ``signal.pidfd_send_signal`` are callable
    (Linux 5.3+/Python 3.9+) -- absent on macOS/BSD."""
    if sys.platform == "win32":
        return True
    import signal as _signal

    return callable(getattr(os, "pidfd_open", None)) and callable(
        getattr(_signal, "pidfd_send_signal", None)
    )


def _enumerate_descendant_pids_windows(pid: int) -> list[tuple[int, str]]:
    """Live descendants of *pid* on Windows, each with its own freshly
    captured process-start-time identity token.

    Captured via a single WMI snapshot (all pid/parent-pid pairs) walked as
    a BFS from *pid*, so a per-descendant token is taken close to the kill
    without one round-trip per process.
    """
    import subprocess as sp

    from zdd import diagnostics

    try:
        out = sp.run(
            [
                _powershell_host(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | ForEach-Object "
                '{ "$($_.ProcessId)`t$($_.ParentProcessId)" }',
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, sp.TimeoutExpired):
        return []

    children_of: dict[int, list[int]] = {}
    for line in (out.stdout or "").splitlines():
        if "\t" not in line:
            continue
        pid_s, ppid_s = line.split("\t", 1)
        try:
            child_pid = int(pid_s.strip())
            parent_pid = int(ppid_s.strip())
        except ValueError:
            continue
        children_of.setdefault(parent_pid, []).append(child_pid)

    descendants: list[tuple[int, str]] = []
    seen = {pid}
    frontier = [pid]
    while frontier:
        next_frontier = [
            child_pid
            for parent_pid in frontier
            for child_pid in children_of.get(parent_pid, [])
            if child_pid not in seen
        ]
        for child_pid in next_frontier:
            seen.add(child_pid)
            start = diagnostics.process_start_time(child_pid)
            if start is not None:
                descendants.append((child_pid, start))
        frontier = next_frontier
    return descendants


def _kill_pid_tree_windows_if_identity(pid: int) -> None:
    """Identity-bound Windows termination of *pid* AND its live descendants.

    ``zdd.diagnostics``'s Windows path (``TerminateProcess`` on a verified
    handle) only ever terminates the single named process -- unlike the
    legacy ``taskkill /T`` this replaces, it has no tree semantics of its
    own. Descendants are enumerated and their own identity tokens captured
    *before* the root is killed (so a kill doesn't orphan/rename-away
    anything mid-enumeration), then each -- root and every descendant -- is
    terminated through its own freshly-captured ``(pid, start_time)`` pair,
    never a bare ``taskkill``/PID-only signal.
    """
    from zdd import diagnostics

    descendants = _enumerate_descendant_pids_windows(pid)
    start_time = diagnostics.process_start_time(pid)
    if start_time is not None:
        diagnostics.terminate_pid_if_identity(pid, start_time)
    for child_pid, child_start in descendants:
        diagnostics.terminate_pid_if_identity(child_pid, child_start)


def _kill_pid(pid: int) -> None:
    """Terminate *pid* (and, on Windows, its live descendants), routed
    through an identity-bound OS object whenever this platform has one.

    Re-verifies ``_pid_is_agent_bridge(pid)`` itself, immediately before
    acting, even though every current caller already performed that same
    check beforehand -- a caller's check and this function used to be two
    separate calls, leaving a window in which the OS could reuse *pid* for
    an unrelated process between them. Doing the check again right here,
    directly ahead of capturing the termination token, narrows that window
    to the smallest span this function can control (matching
    ``worktree_manager.mux_daemon_cutover._terminate_mux_daemon_pid``'s
    #5060 pattern of performing the ownership check and the kill inside the
    same function). It still cannot close a theoretical reuse race between
    *this* check and the identity-token capture a few lines below --
    ``terminate_pid_if_identity`` itself is what makes that final, narrow
    window safe, by binding the actual termination to the exact
    ``(pid, start_time)`` pair captured immediately prior.

    Falls back to a legacy, unverified kill **only** when this platform
    has no identity-bound termination primitive available at all (e.g.
    non-Linux POSIX without ``pidfd_open``/``pidfd_send_signal`` --
    ``_identity_termination_available()``), preserving this function's
    prior unconditional-kill behavior there. Windows always has the
    primitive, so it never takes this fallback. A **per-pid** lookup
    failure (the handle/pidfd could not be opened for *this* specific pid,
    its start-time could not be read, or the signal itself failed) is
    different from platform incapability and must never fall back to a
    bare, unverified kill -- a pid reused in exactly that window is the
    hazard this exists to close, so those cases simply skip the kill
    rather than risk signaling a replacement process.
    """
    if not _pid_is_agent_bridge(pid):
        return

    if _identity_termination_available():
        if sys.platform == "win32":
            _kill_pid_tree_windows_if_identity(pid)
            return
        from zdd import diagnostics

        start_time = diagnostics.process_start_time(pid)
        if start_time is not None:
            diagnostics.terminate_pid_if_identity(pid, start_time)
        return

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
        # Known residual PID-reuse window, NOT closed by this function's
        # Windows branch above: unlike `_kill_pid`, this POSIX fallback is
        # a bare, unverified `os.kill` by pid, with no identity-bound
        # recheck immediately beforehand. `zdd.diagnostics
        # .terminate_pid_if_identity` only supports SIGTERM today, not the
        # SIGKILL semantics this force-path needs, so routing through it
        # as-is would change this function's actual behavior, not just its
        # safety -- left open on copilot-extensions#5006 rather than
        # silently claimed closed.
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
                    _powershell_host(),
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
    exe = shutil.which("agent-bridge")  # marketplace-isolation: allow legacy-compatibility
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
            [_powershell_host(), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
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
    # Never the caller's ambient cwd: this is a long-lived supervised daemon
    # that may outlive whatever repo/worktree checkout the caller happened
    # to be running from (service-lifecycle-supervision's "nothing pins the
    # plugin payload" rule, generalized to every deletable checkout). Its
    # own log files already live under the durable install dir -- root cwd
    # there too.
    try:
        logf = open(os.path.join(core._INSTALL_DIR, "agent-bridge.log"), "ab")
        errf = open(os.path.join(core._INSTALL_DIR, "agent-bridge-err.log"), "ab")
        _sp.Popen(
            argv,
            stdout=logf,
            stderr=errf,
            stdin=_sp.DEVNULL,
            cwd=core._INSTALL_DIR,
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
            cwd=core._INSTALL_DIR,
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


#: Waits (seconds) before re-probing a forwarded bridge that did not answer.
_FORWARD_RETRY_DELAYS_S = (1.0, 2.0, 4.0)


def _await_forwarded_bridge(core) -> bool:
    """Re-probe a forwarded bridge with backoff; never start a daemon over it."""
    import time

    for delay in _FORWARD_RETRY_DELAYS_S:
        time.sleep(delay)
        if core._service_is_running():
            return True
    print(
        "[WARN] agent-bridge: the forwarded bridge in active.json is not "
        "answering; not starting a local daemon over it (that would take the "
        "route over from the host).",
        file=sys.stderr,
    )
    return False


def _ensure_daemon() -> bool:
    """Boot the daemon if it is down, so a daemon-touching command self-heals."""
    import time

    core = _core()
    if os.environ.get("AGENT_BRIDGE_NO_ENSURE") == "1":
        return core._service_is_running()
    if core._service_is_running():
        return True
    if core._active_endpoint_is_forward():
        return _await_forwarded_bridge(core)
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
        if core._active_endpoint_is_forward():
            core._release_ensure_lock(fd)
            lock_held = False
            return _await_forwarded_bridge(core)
        if core._service_process_is_live():
            core._release_ensure_lock(fd)
            lock_held = False
            return core._wait_for_service_start()
        try:
            with open(core._ENSURE_MARKER, "w") as fh:
                fh.write(str(now))
        except OSError:
            pass
        if core._active_endpoint_is_forward():
            core._release_ensure_lock(fd)
            lock_held = False
            return _await_forwarded_bridge(core)
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
    if core._active_endpoint_is_forward():
        print(
            "[SKIP] agent-bridge: this machine reaches a host bridge through a "
            "forward (active.json) that is not answering; not starting a local "
            "daemon over it",
        )
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
        "-- check ~/"
        ".agent-bridge/"  # marketplace-isolation: allow deployed-runtime-diagnostics
        "agent-bridge-err.log",
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
    # A local daemon on its configured port (no pid file) may outlive a route
    # rewritten to a forward -- even onto that same port, where it then blocks
    # the forward's bind. Its lock holder is still a victim: the ssh process
    # carrying a forward never holds this lock, and `_pid_from_lock` only
    # returns a live agent-bridge holder.
    fixed_ports = {0, core._configured_port()}
    victims = {core._read_pid_file(), *(core._pid_from_lock(p) for p in fixed_ports)}
    if not core._active_endpoint_is_forward():
        # A forwarded port is held by the ssh session carrying the forward.
        victims |= {core._pid_from_lock(port)}
        port_pid = core._pid_on_port(port)
        if port_pid and core._pid_is_agent_bridge(port_pid):
            victims.add(port_pid)
    victims.discard(None)
    for victim in victims:
        # Identity-verify at the point of termination, not only afterward
        # (afterward only confirms no agent-bridge process remains -- it
        # never proves THIS victim pid was one before it was signaled). A
        # stale pid-file/port/lock entry whose pid has since been reused by
        # an unrelated process must never be killed.
        #
        # `_pid_is_agent_bridge` is a cmdline-substring check, and `_kill_pid`
        # itself now closes the remaining window to its own signal: it
        # re-verifies identity through an OS-object-bound termination
        # (`zdd.diagnostics.terminate_pid_if_identity`) using a start-time
        # token captured immediately before the kill, falling back to a
        # bare signal only where that primitive is unavailable on this
        # platform (never on an actual identity mismatch). Closes
        # copilot-extensions#5006 for this call site.
        if not core._pid_is_agent_bridge(victim):
            continue
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
        forwarded = core._active_endpoint_is_forward()
        lock_ports = fixed_ports if forwarded else {core._service_port(), *fixed_ports}
        locks = {core._pid_from_lock(port) for port in lock_ports}
        locks.discard(None)
        live_victims = {victim for victim in victims if core._pid_is_agent_bridge(victim)}
        local_service_down = forwarded or not core._service_is_running()
        if local_service_down and not locks and not live_victims:
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
        # `restart` is not a distinct behavior: it is the same always-ZDD
        # cutover `deploy` performs (spawn passive -> health-gate -> flip ->
        # drain -> retire). A raw stop-then-start bypasses the health gate
        # and coordinated handoff -- see
        # efforts/active/agent-bridge-unified-zdd-cutover Phase 1.
        from . import venue_cli

        venue_cli._cmd_deploy(args)
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
        (
            "restart",
            "Zero-downtime cutover to a fresh daemon generation (same behavior as `deploy`)",
        ),
        ("stop", "Stop the agent-bridge daemon"),
        ("status", "Show daemon status, port, and PID"),
    ):
        action_p = service_sub.add_parser(_act, help=_help)
        if _act == "restart":
            # Same flags as `venue deploy` -- `restart` calls _cmd_deploy
            # directly, via the shared registration helper so the two never
            # drift apart. `--recover` is excluded: it's a deploy-only
            # maintenance mode that exits without restarting anything, which
            # would make `service restart --recover` silently no-op.
            from . import venue_cli

            venue_cli.add_deploy_cutover_flags(action_p, include_recover=False)
    service_p.set_defaults(func=_cmd_service)
