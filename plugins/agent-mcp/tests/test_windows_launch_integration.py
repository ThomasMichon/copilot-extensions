"""Live Windows regression for agent-mcp's headless-launch primitives.

Per ``docs/patterns/windows-background-process-launch.md`` § Review and
validation: a mocked ``creationflags`` assertion proves wiring, not
behavior, and a genuine check needs a real console child launched from a
real windowless parent. This launches a real ``pythonw.exe`` parent process
that itself runs the ``CommandInjector`` auth-mint spawn (a real console
child with real, caller-influenced args) across two cycles, observing
actual Win32 window/foreground state from the pytest side -- not just
captured kwargs, and not from a console-attached thread inside pytest's own
process, which cannot distinguish "flag applied" from "flag absent" when
the parent already owns a console.

Windows-only; skipped everywhere else. Deliberately excluded from the fast
required lane (it needs a real Windows host and takes real wall-clock time
for repeated cycles) -- run it explicitly on a Windows box alongside a
Windows launch-path change. A whole-suite run on ``windows-latest`` (the
daily Windows coverage rotation) executes it too, which is how the resident
``serve`` host's ``agent_procutil.spawn_daemon`` launch gets hosted Windows
coverage: see :func:`test_serve_host_spawn_is_headless_and_lifetime_matches_its_log`.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

_SURFACED_PROCESS_NAMES = {
    "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
    "openconsole.exe", "windowsterminal.exe", "cmd.exe",
}

# The child script run by the real `pythonw.exe` parent below: constructs the
# same `CommandInjector` auth-mint config this PR changed and runs it for two
# real cycles, exactly as `agent_mcp.auth.injectors` does in production.
_CHILD_SCRIPT = """
import asyncio, json, sys
sys.path.insert(0, sys.argv[2])
from agent_mcp.auth import build_injector
from agent_mcp.config import parse_config

cfg = parse_config({
    "server": {"type": "stdio", "command": "npx"},
    "auth": {
        "kind": "command",
        "command": [sys.executable.replace("pythonw.exe", "python.exe"),
                    "-c", "print('token=abc')"],
        "target_env": "API_KEY",
    },
})
inj = build_injector(cfg)

async def cycles():
    out = []
    for _ in range(2):
        inj._cached = None
        out.append(await inj.child_env())
    return out

results = asyncio.run(cycles())
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(results, fh)
"""


def _configure_win32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    return user32, kernel32, callback_type


def _visible_windows(user32, callback_type):
    windows = set()

    @callback_type
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            windows.add(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return windows


def _process_name(user32, kernel32, hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    try:
        length = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(length.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            return Path(name.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def test_command_injector_mint_spawn_never_surfaces_a_window(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw.exe is unavailable on this interpreter")

    repo_src = str(Path(__file__).resolve().parents[1] / "src")
    child = tmp_path / "windowless_child.py"
    child.write_text(_CHILD_SCRIPT, encoding="utf-8")
    output_path = tmp_path / "result.json"

    user32, kernel32, callback_type = _configure_win32()
    baseline = _visible_windows(user32, callback_type)
    foreground = user32.GetForegroundWindow()
    surfaced: set[int] = set()
    focus_changes: set[int] = set()

    with subprocess.Popen(
        [str(pythonw), "-I", str(child), str(output_path), repo_src],
    ) as proc:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            surfaced.update(
                hwnd for hwnd in _visible_windows(user32, callback_type) - baseline
                if _process_name(user32, kernel32, hwnd) in _SURFACED_PROCESS_NAMES
            )
            current = user32.GetForegroundWindow()
            if (
                current != foreground and current not in baseline
                and _process_name(user32, kernel32, current) in _SURFACED_PROCESS_NAMES
            ):
                focus_changes.add(current)
            time.sleep(0.02)
        if proc.poll() is None:
            proc.kill()
            pytest.fail("windowless child spawn exceeded its deadline")
        returncode = proc.returncode

    assert returncode == 0
    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert results == [{"API_KEY": "abc"}, {"API_KEY": "abc"}]
    assert surfaced == set()
    assert focus_changes == set()


async def test_command_injector_timeout_reaps_the_full_process_tree(tmp_path):
    # Force the timeout path and prove the COMPLETE spawned process tree
    # exits, not just the direct child -- `auth.command` is caller-configured
    # and can itself be a multi-process wrapper, so `_run_command`'s timeout
    # path now reaps via the tree-aware `_terminate_tree` (same as other
    # injectors already use for a known multi-process helper), not the
    # direct-child-only `_terminate_proc`. The mint command here spawns its
    # own nested child and writes both PIDs before sleeping, so this proves
    # the nested descendant is reaped too, not just the immediate child.
    from agent_mcp.auth import build_injector
    from agent_mcp.auth import injectors as injectors_module
    from agent_mcp.config import parse_config

    pid_file = tmp_path / "pids.json"
    child_code = (
        "import json, subprocess, sys, time\n"
        f"nested = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        f"with open(r'{pid_file}', 'w', encoding='utf-8') as fh:\n"
        "    json.dump({'direct': __import__('os').getpid(), 'nested': nested.pid}, fh)\n"
        "time.sleep(30)\n"
    )
    cfg = parse_config({
        "server": {"type": "stdio", "command": "npx"},
        "auth": {
            "kind": "command",
            "command": [sys.executable, "-c", child_code],
            "target_env": "API_KEY",
        },
    })
    inj = build_injector(cfg)
    inj._timeout = 2.0

    live_procs: list = []
    orig_term = injectors_module._terminate_tree

    async def spy(proc):
        if proc is not None:
            live_procs.append(proc)
        await orig_term(proc)

    injectors_module._terminate_tree = spy
    try:
        assert await inj.child_env() == {}  # timed out -> no token
    finally:
        injectors_module._terminate_tree = orig_term

    assert live_procs, "expected the timed-out child to be captured for reaping"
    direct_proc = live_procs[0]

    # Wait (bounded) for the mint script to have written both PIDs before the
    # tree-kill below; it writes them well within the 2s timeout above.
    deadline = time.monotonic() + 5.0
    pids: dict | None = None
    while time.monotonic() < deadline and pids is None:
        if pid_file.exists():
            try:
                pids = json.loads(pid_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        if pids is None:
            time.sleep(0.05)
    assert pids is not None, "mint script never wrote its PID file"
    # `direct_proc.pid` (asyncio's own view of the spawned process) and the
    # script's self-reported `os.getpid()` are not asserted equal: a venv's
    # `python.exe` can itself be a relocatable launcher that re-execs the
    # real interpreter as a further child (the same class of trampoline
    # documented for `pythonw.exe` elsewhere in this plugin) -- check every
    # PID either side names, so this test holds either way.
    tracked_pids = {"asyncio-tracked": direct_proc.pid, **pids}

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    still_active = 259  # STILL_ACTIVE

    def has_exited(pid: int) -> bool:
        handle = kernel32.OpenProcess(0x0400, False, pid)  # PROCESS_QUERY_INFORMATION
        if not handle:
            return True  # can't even open it -> already gone
        try:
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value != still_active
        finally:
            kernel32.CloseHandle(handle)

    deadline = time.monotonic() + 5.0
    exited = {key: False for key in tracked_pids}
    while time.monotonic() < deadline and not all(exited.values()):
        for key, pid in tracked_pids.items():
            if not exited[key] and has_exited(pid):
                exited[key] = True
        time.sleep(0.05)
    still_running = {k: v for k, v in tracked_pids.items() if not exited[k]}
    assert not still_running, f"still running after tree-kill: {still_running}"



# -- serve host: spawn_daemon through the forwarder's real launch path --------

_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_SERVE_CYCLES = 2
_CONTAINED_FALLBACK_NOTE = "starting the daemon inside it (contained_fallback)"

# Runs under a windowless pythonw.exe inside a kill-on-close Job, the way a
# forwarder launched by a Copilot session runs: starts the resident serve
# host through ``forward._spawn_serve_host`` (the production launch path,
# unmodified) and exits without waiting. The only instrumentation is a
# pass-through wrapper that records the pid ``spawn_daemon`` returned.
_SERVE_PARENT_SCRIPT = """
import json, sys, traceback
spec = json.loads(sys.argv[1])
sys.path.insert(0, spec["src"])
result = {}
try:
    import agent_procutil
    real_spawn_daemon = agent_procutil.spawn_daemon

    def recording_spawn_daemon(*args, **kwargs):
        proc = real_spawn_daemon(*args, **kwargs)
        result["pid"] = proc.pid
        return proc

    agent_procutil.spawn_daemon = recording_spawn_daemon
    from agent_mcp import forward
    forward._spawn_serve_host(spec["socket"])
except BaseException:
    result["error"] = traceback.format_exc()
with open(spec["result"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
"""


class _WindowObserver:
    """Polls visible top-level windows and foreground changes in the background,
    using the same EnumWindows/foreground helpers as the mint-spawn test."""

    def __init__(self, user32, kernel32, callback_type):
        self._user32, self._kernel32, self._callback_type = user32, kernel32, callback_type
        self.baseline = _visible_windows(user32, callback_type)
        self.foreground = user32.GetForegroundWindow()
        self.surfaced: dict[int, str] = {}
        self.focus_changes: dict[int, str] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _attributable(self, hwnd) -> str:
        name = _process_name(self._user32, self._kernel32, hwnd)
        return name if name in _SURFACED_PROCESS_NAMES else ""

    def _run(self):
        while not self._stop.is_set():
            for hwnd in _visible_windows(self._user32, self._callback_type) - self.baseline:
                if hwnd not in self.surfaced and (name := self._attributable(hwnd)):
                    self.surfaced[hwnd] = name
            current = self._user32.GetForegroundWindow()
            if current and current != self.foreground and current not in self.baseline:
                if current not in self.focus_changes and (name := self._attributable(current)):
                    self.focus_changes[current] = name
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def _serve_log(home: Path) -> str:
    try:
        return (home / "serve.log").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<no serve.log>"


def _wait_for_endpoint(socket_path: Path, home: Path, timeout: float = 45.0) -> int:
    from agent_mcp import sockio

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        endpoint = sockio._read_endpoint(socket_path)
        if endpoint is not None:
            return endpoint["port"]
        time.sleep(0.05)
    pytest.fail(f"the serve host never published its endpoint; serve.log:\n{_serve_log(home)}")


def _accepts(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            return True
    except OSError:
        return False


def _run_serve_cycle(tmp_path: Path, cycle: int, kernel32) -> str:
    """One launch/observe/stop cycle; returns ``"survived"`` or ``"contained"``."""
    import agent_procutil as pu

    root = tmp_path / f"serve-cycle-{cycle}"
    home = root / "home"
    home.mkdir(parents=True)
    socket_path = home / "serve.sock"
    parent_script = root / "parent.py"
    parent_script.write_text(_SERVE_PARENT_SCRIPT, encoding="utf-8")
    result_path = root / "parent-result.json"
    spec = {
        "src": str(Path(__file__).resolve().parents[1] / "src"),
        "socket": str(socket_path),
        "result": str(result_path),
    }

    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe"):
        pytest.skip("pythonw.exe is unavailable on this interpreter")
    env = os.environ.copy()
    env.update(pu.windowless_python_env(sys.executable))
    env["AGENT_MCP_HOME"] = str(home)
    env.pop("AGENT_MCP_SERVE_SOCKET", None)
    # Exercise the forwarder's production flags (breakaway requested): this
    # test owns the host it observes and terminates it below, whichever
    # lifetime the platform grants.
    env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)

    parent, job = pu.spawn_sync_in_kill_on_close_job(
        [pythonw, str(parent_script), json.dumps(spec)], env=env,
    )
    host_handle = None
    try:
        assert job is not None, "could not place the windowless parent in a Job"
        assert parent.wait(timeout=60) == 0
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert "pid" in result, result.get("error") or _serve_log(home)
        host_pid = result["pid"]
        host_handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE,
            False, host_pid,
        )
        assert host_handle, f"serve host {host_pid} exited before it could be observed"

        port = _wait_for_endpoint(socket_path, home)
        assert _accepts(port), f"serve host is not accepting; serve.log:\n{_serve_log(home)}"
        log_text = _serve_log(home)
        assert f"spawn_daemon cwd={home}" in log_text
        assert "display=agent_mcp serve" in log_text

        in_job = wintypes.BOOL()
        assert kernel32.IsProcessInJob(host_handle, job.handle, ctypes.byref(in_job))
        contained = _CONTAINED_FALLBACK_NOTE in log_text
        # Survival is conditional: a host broke away unless the fallback is
        # logged, and a logged fallback means it runs inside the caller's Job.
        assert bool(in_job.value) is contained, log_text

        # The windowless parent has exited; now close the caller's containment.
        job.close()
        job = None
        if contained:
            assert kernel32.WaitForSingleObject(host_handle, 10000) == _WAIT_OBJECT_0, (
                "a host started through the logged contained fallback dies with the Job"
            )
            return "contained"

        time.sleep(1.0)
        assert kernel32.WaitForSingleObject(host_handle, 0) == _WAIT_TIMEOUT, (
            f"the serve host must outlive its caller's Job; serve.log:\n{_serve_log(home)}"
        )
        assert _accepts(port), "the surviving serve host stopped accepting connections"
        return "survived"
    finally:
        if host_handle:
            if kernel32.WaitForSingleObject(host_handle, 0) != _WAIT_OBJECT_0:
                kernel32.TerminateProcess(host_handle, 1)
                kernel32.WaitForSingleObject(host_handle, 10000)
            kernel32.CloseHandle(host_handle)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if job is not None:
            job.close()


def test_serve_host_spawn_is_headless_and_lifetime_matches_its_log(tmp_path):
    """The forwarder's real ``spawn_daemon`` launch of the resident serve host,
    from a windowless parent inside a kill-on-close Job, across two cycles:
    no window or foreground change, and the host either outlives the closed
    Job or -- only when ``serve.log`` records the contained fallback (the Job
    forbade breakaway) -- runs inside it and dies with it."""
    user32, kernel32, callback_type = _configure_win32()
    with _WindowObserver(user32, kernel32, callback_type) as observer:
        outcomes = [_run_serve_cycle(tmp_path, cycle, kernel32) for cycle in range(_SERVE_CYCLES)]
        time.sleep(0.5)

    assert observer.surfaced == {}, f"visible windows surfaced: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
    assert len(set(outcomes)) == 1, f"lifetime differed between cycles: {outcomes}"
    if outcomes[0] == "contained":
        # Not a failure: the enclosing Job (for instance a test runner's)
        # forbids breakaway. Record it so a run that never proves survival
        # is visible as such.
        print("serve host ran inside the caller's Job (breakaway denied); survival not exercised")
