"""Process-level rehearsal for the real status-monitor drain/cutover path."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from agent_worktrees import locks
from agent_worktrees import status_monitor_cutover as smc

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def _write_fake_mux(fake_bin: Path) -> None:
    script = """#!/usr/bin/env bash
set -eu
case "${1:-}" in
  list-sessions) printf 'wt-test:0:@1:1\\n' ;;
  has-session|set-option) exit 0 ;;
  *) exit 0 ;;
esac
"""
    for name in ("tmux", "psmux"):
        path = fake_bin / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)


def _wait_for(predicate, *, timeout: float = 20.0, interval: float = 0.05, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _active_pid(routing_path: Path) -> int | None:
    if not routing_path.exists():
        return None
    active = _read_json(routing_path).get("active")
    if not isinstance(active, dict):
        return None
    pid = active.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _send_delayed_hook(lock_path: Path, *, delay_s: float) -> dict:
    data = _read_json(lock_path)
    host, port_s = str(data["hook_endpoint"]).split(":", 1)
    body = json.dumps(
        {
            "version": 1,
            "token": data["hook_token"],
            "kind": "preToolUse",
            "payload": {"__test_delay_s": delay_s},
            "deadline": time.time() + 30.0,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    with socket.create_connection((host, int(port_s)), timeout=10) as sock:
        sock.settimeout(10)
        sock.sendall(body)
        sock.shutdown(socket.SHUT_WR)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process rehearsal")
@pytest.mark.timeout(180)
def test_real_status_monitor_cutover_drains_live_classify_request(monkeypatch) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aw-cutover-process-"))
    home = tmp / "home"
    fake_bin = tmp / "fake-bin"
    fake_bin.mkdir()
    _write_fake_mux(fake_bin)
    tracked = tmp / "tracked"
    tracked.mkdir()
    subprocess.run(["git", "init", "-q", str(tracked)], check=True)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AGENT_HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
    env["PYTEST_CURRENT_TEST"] = "status-monitor-cutover-process"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", env["PYTEST_CURRENT_TEST"])
    # Never spawn a REAL, 120s-delayed detached subprocess during this test
    # (copilot-extensions#5453 async backstop) -- it has its own dedicated
    # unit tests, and this test's tmp HOME is torn down long before any
    # such process would fire.
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda *a, **k: None,
    )

    install_dir = home / ".agent-worktrees"
    registry = install_dir / "status-monitor.d"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "wt-test").write_text(str(tracked), encoding="utf-8")
    lock_path = install_dir / "status-monitor.lock"
    routing_path = install_dir / "status-monitor-routing" / "active.json"

    monitor = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "agent_worktrees", "status-monitor", "--interval", "1"],
        cwd=tmp,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids_to_kill = {monitor.pid}
    try:
        _wait_for(
            lambda: lock_path.exists() and "hook_endpoint" in _read_json(lock_path),
            message="status-monitor never published its hook endpoint",
        )
        old_pid = _wait_for(
            lambda: _active_pid(routing_path),
            message="status-monitor never published its routed endpoint",
        )
        pids_to_kill.add(old_pid)
        hook_response: dict[str, object] = {}
        hook_thread = threading.Thread(
            target=lambda: hook_response.update(_send_delayed_hook(lock_path, delay_s=3.0)),
            daemon=True,
        )
        hook_thread.start()
        time.sleep(0.3)

        result: dict[str, object] = {}
        cutover_thread = threading.Thread(
            target=lambda: result.update(smc.activate_after_update(runtime_python=sys.executable)),
            daemon=True,
        )
        cutover_thread.start()

        new_pid = _wait_for(
            lambda: (pid := _active_pid(routing_path)) and pid != old_pid and pid,
            message="cutover never published a successor route",
        )
        pids_to_kill.add(new_pid)
        assert hook_thread.is_alive(), "delayed hook finished before the route flipped"
        cutover_thread.join(timeout=20)
        assert not cutover_thread.is_alive(), "cutover thread did not finish"
        assert result["action"] == "cutover", result
        assert result["result"]["ok"] is True
        hook_thread.join(timeout=20)
        assert not hook_thread.is_alive(), "delayed hook did not drain to completion"
        assert hook_response.get("version") == 1
        assert locks.pid_alive(new_pid)
        new_lock = _read_json(lock_path)
        assert "hook_endpoint" in new_lock and "classify_endpoint" in new_lock
        drain_events = [
            json.loads(line)
            for line in (routing_path.parent / "lifecycle.log").read_text().splitlines()
            if json.loads(line).get("action") == "monitor-drain"
        ]
        assert any(
            event["detail"]["control_action"] == "promote"
            and event["detail"]["phase"] == "complete"
            for event in drain_events
        )
        assert any(
            event["detail"]["role"] == "daemon"
            and event["detail"]["control_action"] == "drain"
            and event["detail"]["phase"] == "complete"
            for event in drain_events
        )
    finally:
        for pid in pids_to_kill:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


@pytest.mark.timeout(300)
@pytest.mark.parametrize("failure", ["none", "successor", "installer", "installer-started"])
def test_install_process_returns_before_resident_finishes_accepted_work(tmp_path, failure):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import classify_daemon
    from agent_worktrees.status_monitor_cli import _SELF_RETIRE_MAX_GRACE_S
    from zdd import breadcrumb, diagnostics, routing

    home = tmp_path / "home"
    home.mkdir()
    root = home / ".agent-worktrees"
    started, release = tmp_path / "started", tmp_path / "release"
    second_started, second_release = tmp_path / "second-started", tmp_path / "second-release"
    second_loop = second_started.with_suffix(".loop-release")
    third_started = tmp_path / "third-started"
    third_loop = third_started.with_suffix(".loop-release")
    promotion_started, promotion_release = tmp_path / "promotion-started", tmp_path / "promotion-release"
    output = tmp_path / "result.json"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
               AGENT_WORKTREES_STATUS_MONITOR="1")
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
                "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR"):
        env.pop(key, None)
    baseline = """
import runpy, sys, threading
from pathlib import Path
from agent_worktrees import classify_daemon, repository_map, status_monitor_cli
status_monitor_cli._SELF_RETIRE_MAX_GRACE_S = 0.2
started, release = map(Path, sys.argv[1:3])
options = sys.argv[3:]
original_start = repository_map.RepositoryMapRuntime.start
def paused_start(self, interval):
    original_start(self, interval)
    barrier = (
        started.with_suffix(".loop-release") if "--passive" in options
        else started.parent / "promotion-started" if started.name == "started"
        else None
    )
    for _ in range(2400):
        if barrier is None or barrier.exists():
            return
        threading.Event().wait(0.1)
    raise TimeoutError("resident loop was not released")
repository_map.RepositoryMapRuntime.start = paused_start
original = classify_daemon.start_server
def start_server(compute, **kwargs):
    def held(kind, payload):
        if payload.get("async_install_probe"):
            started.touch()
            for _ in range(2400):
                if release.exists():
                    return {"completed": True}
                threading.Event().wait(0.1)
            raise TimeoutError("probe was not released")
        return compute(kind, payload)
    return original(held, **kwargs)
classify_daemon.start_server = start_server
sys.argv = ["agent_worktrees", "status-monitor", "--interval", "2", *options]
runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
"""
    installation = """
import json, os, sys
from contextlib import ExitStack
from pathlib import Path
from agent_worktrees import status_monitor_cutover as cutover, status_monitor_runtime as runtime
# Preserve this source packet's import closure for the actual resident child.
runtime._daemon_environment = lambda: dict(os.environ)
spawn_monitor = cutover.spawn_monitor
def controlled_spawn(argv, env, **kwargs):
    options = argv[argv.index("status-monitor") + 1:]
    return spawn_monitor(
        [argv[0], "-c", sys.argv[4], sys.argv[2], sys.argv[3], *options], env, **kwargs,
    )
cutover.spawn_monitor = controlled_spawn
if len(sys.argv) > 7 and sys.argv[7] in {"installer", "installer-started"}:
    from agent_worktrees import runtime_monitor_retirements
    def owner_exits_before_grant(*args, **kwargs):
        Path(sys.argv[5]).touch()
        import threading
        while not Path(sys.argv[6]).exists():
            threading.Event().wait(0.1)
        os._exit(29)
    if sys.argv[7] == "installer":
        runtime_monitor_retirements.commit = owner_exits_before_grant
    else:
        from zdd import breadcrumb
        write_breadcrumb = breadcrumb.write_breadcrumb
        def owner_exits_before_flipped(*args, **kwargs):
            if kwargs["state"] == "flipped":
                owner_exits_before_grant()
            return write_breadcrumb(*args, **kwargs)
        breadcrumb.write_breadcrumb = owner_exits_before_flipped
if len(sys.argv) > 6:
    started, release = map(Path, sys.argv[5:7])
    make_client = cutover._make_client
    def controlled_client(base_url):
        client = make_client(base_url)
        promote = client.promote
        def held_promotion():
            import threading
            started.touch()
            for _ in range(2400):
                if release.exists():
                    return promote()
                threading.Event().wait(0.1)
            raise TimeoutError("promotion was not released")
        client.promote = held_promotion
        return client
    cutover._make_client = controlled_client
with ExitStack() as custody:
    guard = cutover._acquire_cutover_lock(cutover.routing_dir())
    custody.callback(guard.release)
    result = cutover.activate_after_update(
        runtime_python=sys.executable, cutover_guard=guard,
        spawned_handles=[], transition_custody=custody, asynchronous_retirement=True,
    )
    Path(sys.argv[1]).write_text(json.dumps(result), encoding="utf-8")
"""
    identities = {}
    request_result = {}
    second_result = {}
    last_original = {}
    requester = None
    second_requester = None
    first_installer = None
    restarted = None
    with (tmp_path / "process.log").open("w", encoding="utf-8") as log:
        old = subprocess.Popen(
            [sys.executable, "-B", "-c", baseline, str(started), str(release)],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **no_window_kwargs(),
        )
        try:
            record = _wait_for(
                lambda: (
                    value if "classify_endpoint" in (
                        value := locks.read_lock(root / "status-monitor.lock") or {}
                    ) else None
                ), message="baseline did not publish its request endpoint",
            )
            old_pid = record["pid"]
            original_endpoint = routing.read_active_endpoint(root / "status-monitor-routing")
            identities[old_pid] = diagnostics.process_start_time(old_pid)
            host, port, token = classify_daemon.endpoint_from_rendezvous(record)

            def request(endpoint, result, key):
                try:
                    result.update(classify_daemon.wcs_client.request(
                        *endpoint, kind=classify_daemon.KIND, key=key,
                        payload={"async_install_probe": True}, request_deadline_s=240,
                    ))
                except classify_daemon.wcs_client.DaemonUnavailable as error:
                    result["error"] = str(error)

            first_installer = subprocess.Popen(
                [sys.executable, "-B", "-c", installation, str(output),
                 str(second_started), str(second_release), baseline,
                 str(promotion_started), str(promotion_release), failure],
                env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, **no_window_kwargs(),
            )
            _wait_for(promotion_started.exists, timeout=60, message="successor promotion did not reach its barrier")
            pending_endpoint = routing.read_active_endpoint(root / "status-monitor-routing")
            pending_health = smc.ControlClient(
                pending_endpoint.base_url,
                token=(root / "status-monitor-control.token").read_text().strip(),
            ).health()
            assert pending_health["published"] is False
            assert pending_health["worktrees"]["status"] == "unready"
            time.sleep(4)
            assert old.poll() is None
            assert smc.ControlClient(
                original_endpoint.base_url,
                token=(root / "status-monitor-control.token").read_text().strip(),
            ).health()["status"] == "ready"
            previous_client = smc.ControlClient(
                original_endpoint.base_url,
                token=(root / "status-monitor-control.token").read_text().strip(),
            )
            previous_client.worktrees_read({"contract_version": 1, "project": "fixture"})
            assert previous_client.health()["worktrees"]["status"] == "ready"
            assert previous_client._request("repos-map-read", {
                "repository_map_version": 1, "scope": str(tmp_path.resolve()),
            }).get("error", {}).get("code") != "superseded"
            requester = threading.Thread(
                target=request, args=((host, port, token), request_result, "first-accepted"),
                daemon=True,
            )
            requester.start()
            _wait_for(started.exists, message="baseline did not accept the held request")
            promotion_release.touch()
            exit_code = first_installer.wait(timeout=60)
            if failure in {"installer", "installer-started"}:
                assert exit_code == 29
                assert breadcrumb.read_breadcrumb(root / "status-monitor-routing")["state"] == (
                    "started" if failure == "installer-started" else "committed"
                )
                second_loop.touch()
                _wait_for(
                    lambda: (locks.read_lock(root / "status-monitor.lock") or {}).get("pid") != old_pid,
                    message="selected resident did not complete its departed installer's publication",
                )
                _wait_for(
                    lambda: breadcrumb.read_breadcrumb(root / "status-monitor-routing") is None,
                    message="completed resident did not retire the recovered publication witness",
                )
            else:
                assert exit_code == 0, (tmp_path / "process.log").read_text()
                result = _read_json(output)["result"]
                assert result["ok"] and result["committed"] and result["retirement_pending"]
            assert requester.is_alive() and old.poll() is None, request_result
            successor = routing.read_active_endpoint(root / "status-monitor-routing")
            assert successor is not None and successor.pid != old_pid
            identities[successor.pid] = diagnostics.process_start_time(successor.pid)
            second = locks.read_lock(root / "status-monitor.lock")
            assert second["pid"] == successor.pid
            second_requester = threading.Thread(
                target=request, args=(
                    classify_daemon.endpoint_from_rendezvous(second), second_result, "second-accepted",
                ), daemon=True,
            )
            second_requester.start()
            _wait_for(second_started.exists, message="second resident did not accept held work")
            third_loop.touch()
            installed = subprocess.run(
                [sys.executable, "-B", "-c", installation, str(output),
                 str(third_started), str(tmp_path / "third-release"), baseline],
                env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, timeout=60, **no_window_kwargs(),
            )
            assert installed.returncode == 0, (tmp_path / "process.log").read_text()
            assert _read_json(output)["result"]["retirement_pending"]
            third = routing.read_active_endpoint(root / "status-monitor-routing")
            assert third is not None and third.pid not in (old_pid, successor.pid)
            identities[third.pid] = diagnostics.process_start_time(third.pid)
            assert requester.is_alive() and second_requester.is_alive()
            second_loop.touch()
            control_token = (root / "status-monitor-control.token").read_text().strip()
            _wait_for(
                lambda: smc.ControlClient(
                    successor.base_url, token=control_token,
                ).health()["status"] == "draining",
                message="second resident forgot its generation before admitting work",
            )
            time.sleep(8)
            health = smc.ControlClient(
                third.base_url, token=(root / "status-monitor-control.token").read_text().strip(),
            ).health()
            assert health["published"] is True and health["status"] == "ready"
            assert health["predecessorRetirement"]["pending"] is True
            assert requester.is_alive() and second_requester.is_alive()
            if failure == "successor":
                _wait_for(
                    lambda: previous_client.health()["status"] == "draining",
                    timeout=_SELF_RETIRE_MAX_GRACE_S + 15,
                    message="first resident never reached the ready-successor drain boundary",
                )
                killed = diagnostics.terminate_pid_if_identity(
                    third.pid, identities[third.pid], exit_timeout=10,
                )
                assert killed.get("exit_confirmed") is True
                assert requester.is_alive() and second_requester.is_alive()
                restarted = subprocess.Popen(
                    [sys.executable, "-B", "-c", baseline, str(third_started), str(tmp_path / "restart-release")],
                    env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, **no_window_kwargs(),
                )
                _wait_for(
                    lambda: (
                        ((routing.read_table(root / "status-monitor-routing") or {}).get("active") or {}).get("pid")
                        not in (None, third.pid, old_pid, successor.pid)
                    ),
                    message="old cold-start lease blocked replacement after successor death",
                )
                restarted_endpoint = routing.read_active_endpoint(root / "status-monitor-routing")
                identities[restarted_endpoint.pid] = diagnostics.process_start_time(restarted_endpoint.pid)
            retirement_deadline = time.monotonic() + _SELF_RETIRE_MAX_GRACE_S + 15
            release.touch()
            second_release.touch()
            second_loop.touch()
            third_loop.touch()
            requester.join(timeout=15)
            second_requester.join(timeout=15)
            assert not requester.is_alive() and request_result.get("completed") is True
            assert not second_requester.is_alive() and second_result.get("completed") is True
            def original_exited():
                if old.poll() is not None:
                    return True
                try:
                    last_original.update(previous_client.health())
                except (OSError, ValueError):
                    pass
                return False

            _wait_for(
                original_exited, timeout=max(1, retirement_deadline - time.monotonic()),
                message=f"original resident did not retire after accepted work; last health={last_original}",
            )
            assert old.wait(timeout=10) == 0
            _wait_for(
                lambda: not diagnostics._pid_alive(successor.pid),
                timeout=max(1, retirement_deadline - time.monotonic()),
                message="second resident did not independently retire",
            )
        except (AssertionError, subprocess.TimeoutExpired) as error:
            log.flush()
            table = routing.read_table(root / "status-monitor-routing")
            health = {}
            for label in ("active", "previous"):
                endpoint = (table or {}).get(label)
                if endpoint:
                    try:
                        health[label] = smc.ControlClient(
                            routing.Endpoint.from_dict(endpoint).base_url,
                            token=(root / "status-monitor-control.token").read_text().strip(),
                        ).health()
                    except (OSError, ValueError) as unavailable:
                        health[label] = str(unavailable)
            for label, endpoint in (("original", original_endpoint),):
                try:
                    health[label] = smc.ControlClient(
                        endpoint.base_url,
                        token=(root / "status-monitor-control.token").read_text().strip(),
                    ).health()
                except (OSError, ValueError) as unavailable:
                    health[label] = str(unavailable)
            pytest.fail(
                f"{error}\nhealth={health}\nlast_original={last_original}\n"
                f"restart={None if restarted is None else (restarted.pid, restarted.poll())}\n"
                f"discovery-pid={(locks.read_lock(root / 'status-monitor.lock') or {}).get('pid')}\n"
                f"singleton={(root / 'status-monitor-singleton.lock').read_text() if (root / 'status-monitor-singleton.lock').exists() else None}\n"
                f"{(tmp_path / 'process.log').read_text(encoding='utf-8')}"
            )
        finally:
            promotion_release.touch()
            release.touch()
            second_release.touch()
            second_loop.touch()
            third_loop.touch()
            if requester is not None:
                requester.join(timeout=15)
            if second_requester is not None:
                second_requester.join(timeout=15)
            if first_installer is not None:
                first_installer.wait(timeout=60)
            for pid, identity in identities.items():
                if identity is not None:
                    diagnostics.terminate_pid_if_identity(pid, identity, exit_timeout=10)
            if old.poll() is None:
                old.terminate()
            old.wait(timeout=10)
            if restarted is not None:
                restarted.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only windowless-parent contract")
def test_spawn_passive_is_windowless_from_pythonw_parent(tmp_path: Path) -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = Path(sys.executable).with_name("python.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw.exe is unavailable")
    if not python.is_file():
        pytest.skip("python.exe is unavailable")
    parent = tmp_path / "windowless_parent.py"
    output = tmp_path / "result.json"
    parent.write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "from agent_worktrees import status_monitor_cutover as smc\n"
        "from agent_worktrees import status_monitor_runtime as smr\n"
        "root = Path(sys.argv[1])\n"
        "runtime_python = str(Path(sys.argv[3]))\n"
        "smr._daemon_environment = lambda: dict(os.environ)\n"
        "smr._daemon_cwd = lambda: str(root)\n"
        "pids = []\n"
        "for port in (49151, 49152):\n"
        " handle = smc.spawn_passive(runtime_python, port=port)\n"
        " pids.append(getattr(handle, 'pid', None))\n"
        " time.sleep(2.5)\n"
        " handle.terminate()\n"
        " time.sleep(0.5)\n"
        "Path(sys.argv[2]).write_text(json.dumps(pids), encoding='utf-8')\n",
        encoding="utf-8",
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    def visible_windows():
        windows = set()

        @callback_type
        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                windows.add(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        return windows

    def process_name(hwnd):
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

    baseline = visible_windows()
    foreground = user32.GetForegroundWindow()
    process_names = {
        "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
        "openconsole.exe", "windowsterminal.exe",
    }
    surfaced = set()
    focus_changes = set()
    parent_log = tmp_path / "windowless-parent.log"
    with parent_log.open("w", encoding="utf-8") as stream, subprocess.Popen(
        [str(pythonw), "-I", str(parent), str(tmp_path), str(output), str(python)],
        stdout=stream, stderr=stream,
    ) as proc:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            surfaced.update(
                hwnd for hwnd in visible_windows() - baseline
                if process_name(hwnd) in process_names
            )
            current = user32.GetForegroundWindow()
            if current != foreground and current not in baseline and process_name(current) in process_names:
                focus_changes.add(current)
            time.sleep(0.02)
        if proc.poll() is None:
            proc.terminate()
            pytest.fail("windowless passive-monitor probe exceeded its deadline")
        assert proc.returncode == 0, parent_log.read_text(encoding="utf-8", errors="replace")
    pids = json.loads(output.read_text(encoding="utf-8"))
    assert len(pids) == 2
    assert all(isinstance(pid, int) and pid > 0 for pid in pids)
    assert surfaced == focus_changes == set()
def test_departed_installer_prepublication_reservation_has_surviving_recovery(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import runtime_monitor_retirements as retirements
    from zdd import breadcrumb, routing

    root = tmp_path / "runtime"
    root.mkdir()
    start = locks.process_start_time(os.getpid())
    assert isinstance(start, str) and start
    routing.publish_active(
        root / "status-monitor-routing", bind="127.0.0.1", port=61001,
        pid=os.getpid(), process_start_time=start,
    )
    script = """
import os, sys
from pathlib import Path
from agent_worktrees import runtime_monitor_retirements as retirements
from agent_worktrees.status_monitor_cutover import _acquire_cutover_lock
root = Path(sys.argv[1])
guard = _acquire_cutover_lock(root / "status-monitor-routing")
retirements.reserve(root, {
    "pid": int(sys.argv[2]), "start": sys.argv[3], "slot": None, "release_claim": None,
})
os._exit(29)
"""
    child = subprocess.run(
        [sys.executable, "-B", "-c", script, str(root), str(os.getpid()), start],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert child.returncode == 29, child.stderr
    assert retirements._records(root)[0][1]["state"] == "prepared"
    assert breadcrumb.read_breadcrumb(root / "status-monitor-routing") is None
    surviving_owner = retirements.ResidentRetirements(root)
    surviving_owner.refresh()
    assert not surviving_owner.pending and surviving_owner.error is None
    assert not retirements._records(root)


@pytest.mark.skipif(sys.platform not in {"win32", "linux"}, reason="native passive termination contract")
@pytest.mark.parametrize("phase", ["initial", "spawn-unrecorded", "passive", "dead-selected"])
@pytest.mark.parametrize("owner", ["resident", "next-installer"])
def test_marked_interrupted_publication_has_native_recovery(tmp_path, phase, owner):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import runtime_monitor_completion as completion
    from agent_worktrees import runtime_monitor_retirements as retirements
    from agent_worktrees import status_monitor_recovery_contract as recovery
    from zdd import breadcrumb, diagnostics, routing

    root = tmp_path / "runtime"
    root.mkdir()
    directory = root / "status-monitor-routing"
    token = "synthetic-native-recovery-token"
    smc.control_token_path(root).write_text(token, encoding="utf-8")
    old_pid, old_start = os.getpid(), locks.process_start_time(os.getpid())
    assert old_start
    generation = "native-recovery-generation"
    def control(action, payload):
        if action == "health":
            return {
                "pid": old_pid, "status": "ready", "published": True,
                "runtime_generation": generation, "capabilities": [recovery.CAPABILITY],
                "worktrees": {"status": "ready"}, "repositories": {"admitting": True},
            }
        assert action == recovery.ACTION
        return recovery.handle(
            payload, directory=directory, generation=generation, port=server.port,
            reopen=lambda: {"draining": False}, close_on_failure=lambda: None, lock_timeout=1,
        )

    server = smc.ControlServer(control, token=token)
    server.start()
    old = routing.publish_active(
        directory, bind="127.0.0.1", port=server.port, pid=old_pid, process_start_time=old_start,
    )
    passive = """
import json, os, sys, time
from pathlib import Path
from agent_worktrees.status_monitor_cutover import ControlServer
from agent_worktrees.runtime_monitor_preparation import register
from zdd import diagnostics
root = Path(sys.argv[1])
assert register(root, int(sys.argv[2]))
server = ControlServer(lambda action, payload: {
    "pid": os.getpid(), "status": "ready", "published": False,
}, token="synthetic-native-recovery-token", port=int(sys.argv[2]))
server.start()
(root / "candidate.json").write_text(json.dumps({
    "pid": os.getpid(), "start": diagnostics.process_start_time(os.getpid()), "port": server.port,
}))
time.sleep(120)
"""
    installer = """
import json, os, subprocess, sys, time
from pathlib import Path
from agent_procutil import no_window_kwargs
from agent_worktrees import runtime_monitor_retirements as retirements
from agent_worktrees.status_monitor_cutover import _acquire_cutover_lock
from agent_worktrees.status_monitor_cutover import pick_free_port
from agent_worktrees.runtime_monitor_preparation import launch_environment
from zdd import breadcrumb, diagnostics, routing
from zdd.cutover_lock import CutoverLock
root, phase = Path(sys.argv[1]), sys.argv[2]
directory = root / "status-monitor-routing"
guard = _acquire_cutover_lock(directory)
with CutoverLock(directory):
    old = routing.read_active_endpoint(directory, verify_listener=False)
    retirements.reserve(root, {
        "pid": old.pid, "start": old.process_start_time, "slot": None, "release_claim": None,
    })
    witness = routing.publication_witness(directory, deadline=time.monotonic() + 3)
    port = pick_free_port()
    breadcrumb.write_breadcrumb(directory, state="started", old=old.to_dict(),
        new_port=port, publication_witness=witness, self_retirement=True)
    candidate = {}
    if phase != "initial":
        env = os.environ.copy()
        launch_environment(directory, env)
        subprocess.Popen([sys.executable, "-B", "-c", sys.argv[3], str(root), str(port)], env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs())
        deadline = time.monotonic() + 15
        while not (root / "candidate.json").exists():
            assert time.monotonic() < deadline, "passive did not publish identity"
            time.sleep(0.05)
        candidate = json.loads((root / "candidate.json").read_text())
    if phase == "spawn-unrecorded":
        os._exit(29)
    breadcrumb.write_breadcrumb(directory, state="started", old=old.to_dict(),
        new_pid=candidate.get("pid"), new_process_start_time=candidate.get("start"),
        new_port=candidate.get("port", port), publication_witness=witness, self_retirement=True)
    if phase == "dead-selected":
        routing.publish_active(directory, bind="127.0.0.1", port=candidate["port"],
            pid=candidate["pid"], process_start_time=candidate["start"], demote_existing=True)
        result = diagnostics.terminate_pid_if_identity(candidate["pid"], candidate["start"], exit_timeout=3)
        assert result.get("identity_verified") is True and result.get("exit_confirmed") is True
    os._exit(29)
"""
    try:
        ended = subprocess.run(
            [sys.executable, "-B", "-c", installer, str(root), phase, passive],
            stdin=subprocess.DEVNULL, capture_output=True, timeout=30, **no_window_kwargs(),
        )
        assert ended.returncode == 29, ended.stderr
        witness = breadcrumb.read_breadcrumb(directory)
        assert witness["state"] == "started" and witness["self_retirement"] is True
        assert not diagnostics._pid_alive(witness["pid"])
        if owner == "resident":
            completion.recover(
                root, promote=lambda: pytest.fail("failed candidate must not promote"), ready=lambda: False,
            )
        else:
            script = """
import sys
from pathlib import Path
from agent_worktrees.runtime_monitor_abort import recover_abandoned
from agent_worktrees.status_monitor_cutover import _acquire_cutover_lock
from zdd.cutover_lock import CutoverLock
root = Path(sys.argv[1])
guard = _acquire_cutover_lock(root / "status-monitor-routing")
with CutoverLock(root / "status-monitor-routing"):
    assert recover_abandoned(root)
guard.release()
"""
            recovered = subprocess.run(
                [sys.executable, "-B", "-c", script, str(root)],
                stdin=subprocess.DEVNULL, capture_output=True, timeout=15, **no_window_kwargs(),
            )
            assert recovered.returncode == 0, recovered.stderr
        assert breadcrumb.read_breadcrumb(directory) is None
        assert not retirements._records(root)
        active = routing.read_active_endpoint(directory, verify_listener=False)
        assert (active.pid, active.process_start_time, active.port) == (old.pid, old.process_start_time, old.port)
        if witness.get("new_pid") is not None:
            assert not diagnostics._pid_alive(witness["new_pid"])
    finally:
        server.close()
