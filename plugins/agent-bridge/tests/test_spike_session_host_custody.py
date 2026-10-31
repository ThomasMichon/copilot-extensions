"""Spike workload custody: real owner loss during an active, detached turn."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes" / "session-host"


@pytest.fixture
def spike_host(monkeypatch):
    monkeypatch.syspath_prepend(str(SPIKE))
    spec = importlib.util.spec_from_file_location("custody_spike_host", SPIKE / "session_host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_posix_workload_inherits_host_retirement_group(spike_host, monkeypatch, tmp_path):
    monkeypatch.setattr(spike_host.osutil, "IS_WIN", False)
    monkeypatch.setattr(spike_host, "os", SimpleNamespace(getpid=lambda: 42, getpgrp=lambda: 42))
    launch = Mock()
    monkeypatch.setattr(spike_host.subprocess, "Popen", launch)
    host = spike_host.SessionHost(["workload"], 0, tmp_path / "host.json")
    host.spawn_child()
    assert not launch.call_args.kwargs.get("start_new_session", False)


def test_posix_workload_refuses_unowned_group(spike_host, monkeypatch, tmp_path):
    monkeypatch.setattr(spike_host.osutil, "IS_WIN", False)
    monkeypatch.setattr(spike_host, "os", SimpleNamespace(getpid=lambda: 42, getpgrp=lambda: 99))
    launch = Mock()
    monkeypatch.setattr(spike_host.subprocess, "Popen", launch)
    host = spike_host.SessionHost(["workload"], 0, tmp_path / "host.json")
    with pytest.raises(RuntimeError, match="must own"):
        host.spawn_child()
    launch.assert_not_called()


@pytest.mark.parametrize("group", [42, 99])
def test_posix_terminate_targets_only_own_pinned_group(spike_host, monkeypatch, tmp_path, group):
    monkeypatch.setattr(spike_host.osutil, "IS_WIN", False)
    kill = Mock()
    monkeypatch.setattr(
        spike_host, "os",
        SimpleNamespace(getpid=lambda: 42, getpgrp=lambda: group, killpg=kill),
    )
    host = spike_host.SessionHost(["workload"], 0, tmp_path / "host.json")
    host.proc = Mock(pid=12345)
    host.proc.poll.return_value = None
    if group == 42:
        host._terminate_child()
        kill.assert_called_once_with(42, spike_host.signal.SIGTERM)
    else:
        with pytest.raises(RuntimeError, match="no longer owns"):
            host._terminate_child()
        kill.assert_not_called()
    host.proc.terminate.assert_not_called()


def _wait_json(path, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            time.sleep(0.05)
    raise AssertionError(f"bounded readiness not observed: {path.name}")


def _runner(root, spike):
    """Only this process renews the host; killing it is actual runner loss."""
    from agent_procutil import direct_python, no_window_kwargs
    from agent_procutil._daemon_instance_identity import created_process_identity
    from agent_procutil.daemon_instances import DaemonLeasePolicy, daemon_test_lifecycle

    child_code = (
        "import json,os,sys,time\nfrom pathlib import Path\n"
        "from agent_procutil._daemon_instance_identity import birth_identity\n"
        "root=Path(sys.argv[1])\n"
        "(root/'child.json').write_text(json.dumps({'pid':os.getpid(),"
        "'birth':birth_identity(os.getpid())}))\n"
        "i=0\nwhile True:\n"
        " i+=1;(root/'progress.json').write_text(json.dumps(i))\n"
        " try: print(json.dumps({'frame':i}),flush=True)\n"
        " except BrokenPipeError: pass\n"
        " time.sleep(.05)\n"
    )
    with daemon_test_lifecycle(
        owner=f"spike-custody-runner:{os.getpid()}",
        registry_root=root / "registry",
        lease=DaemonLeasePolicy(2, 30),
    ) as context:
        python, python_environment = direct_python(sys.executable)
        environment = {**os.environ, **context, **python_environment}
        # This is a fresh owner, not an attempted adoption of pytest's instance.
        environment.pop("COPILOT_EXTENSIONS_DAEMON_INSTANCE", None)
        host = subprocess.Popen(
            [python, str(spike / "session_host.py"), "--port", "0",
             "--state-file", str(root / "host.json"), "--child-cmd",
             json.dumps([python, "-c", child_code, str(root)])],
            env=environment, start_new_session=(os.name != "nt"),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **no_window_kwargs(),
        )
        identity = created_process_identity(host)
        assert identity is not None
        (root / "custody.json").write_text(json.dumps({"pid": identity[0], "birth": identity[1]}))
        host.wait(timeout=25)


@pytest.mark.parametrize("end", ["runner-loss", "terminate"])
def test_active_workload_retires_with_host(tmp_path, monkeypatch, end):
    from agent_procutil import direct_python, no_window_kwargs
    from agent_procutil._daemon_instance_identity import identity_state, stop_identity

    monkeypatch.syspath_prepend(str(SPIKE))
    import wire

    python, python_environment = direct_python(sys.executable)
    runner = subprocess.Popen(
        [python, "-c",
         "import runpy,sys;from pathlib import Path;"
         "m=runpy.run_path(sys.argv[1]);m['_runner'](Path(sys.argv[2]),Path(sys.argv[3]))",
         str(Path(__file__).resolve()), str(tmp_path), str(SPIKE)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env={**os.environ, **python_environment}, **no_window_kwargs(),
    )
    identities = []
    terminating_front = None
    try:
        host_identity = _wait_json(tmp_path / "custody.json")
        identities.append(host_identity)
        child = _wait_json(tmp_path / "child.json")
        identities.append(child)
        host = _wait_json(tmp_path / "host.json")
        assert host["host_pid"] == host_identity["pid"]
        assert host["child_pid"] == child["pid"]
        with socket.create_connection(("127.0.0.1", host["port"]), timeout=3) as front:
            wire.send_msg(front, wire.ATTACH, wire.pack_u64(0))
            assert wire.recv_msg(front)[0] == wire.HELLO
        before = _wait_json(tmp_path / "progress.json")
        # Outlive the TTL with the owner present and no front: work must survive.
        time.sleep(2.5)
        assert _wait_json(tmp_path / "progress.json") > before
        assert identity_state(child["pid"], child["birth"]) == "matching"
        if end == "runner-loss":
            runner.kill()
            runner.wait(timeout=5)
        else:
            # Keep the socket open through retirement: closing with unread
            # replay frames can reset the connection and discard TERMINATE.
            terminating_front = socket.create_connection(("127.0.0.1", host["port"]), timeout=3)
            wire.send_msg(terminating_front, wire.ATTACH, wire.pack_u64(0))
            assert wire.recv_msg(terminating_front)[0] == wire.HELLO
            wire.send_msg(terminating_front, wire.TERMINATE)
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            if all(identity_state(row["pid"], row["birth"]) == "dead" for row in identities):
                break
            time.sleep(0.05)
        assert identity_state(host_identity["pid"], host_identity["birth"]) == "dead"
        assert identity_state(child["pid"], child["birth"]) == "dead", (
            "active child escaped the expired host's custody"
        )
    finally:
        from plugin_test_containment import UnreapedProcessError

        if terminating_front is not None:
            terminating_front.close()
        try:
            for path in (tmp_path / "custody.json", tmp_path / "child.json"):
                if path.exists():
                    row = _wait_json(path)
                    state = identity_state(row["pid"], row["birth"])
                    if state == "dead":
                        continue
                    settled = False
                    if state == "matching":
                        try:
                            settled = stop_identity(row["pid"], row["birth"])
                        except (OSError, subprocess.SubprocessError):
                            settled = identity_state(row["pid"], row["birth"]) == "dead"
                    if not settled:
                        raise UnreapedProcessError("spike child custody remains unknown")
        finally:
            if runner.poll() is None:
                runner.kill()
            runner.wait(timeout=5)
