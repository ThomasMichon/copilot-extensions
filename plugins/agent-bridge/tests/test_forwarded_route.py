"""A venue's forwarded bridge route is never taken over by a local daemon.

On a CodeSpace/container/SSH venue, the launcher points ``active.json`` at the
host bridge's forwarded port so the sessions there report to the host. A local
daemon started over that route publishes itself in its place: the sessions then
heartbeat the local daemon, and the host expires them while they keep running.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_bridge import __main__ as m
from agent_bridge import service_process_cli
from agent_bridge.self_retire import is_superseded


def _route(tmp_path, monkeypatch, active):
    (tmp_path / "config.yaml").write_text("port: 0\n", encoding="utf-8")
    (tmp_path / "active.json").write_text(json.dumps({"active": active}), encoding="utf-8")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))


FORWARD = {"bind": "127.0.0.1", "port": 62254, "forwarded": True}
LEGACY_FORWARD = {"port": 62254}  # what launchers wrote before "bind"/"forwarded"
DAEMON = {"bind": "127.0.0.1", "port": 39881, "pid": 350677, "version": "0.4.4", "generation": 1}


def test_a_forwarded_route_is_recognized_and_resolved(tmp_path, monkeypatch):
    for active in (FORWARD, LEGACY_FORWARD):
        _route(tmp_path, monkeypatch, active)
        assert m._active_endpoint_is_forward()
        assert m._service_port() == 62254  # not the default port


def test_a_daemon_published_route_is_not_a_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, DAEMON)
    assert not m._active_endpoint_is_forward()
    (tmp_path / "active.json").unlink()
    assert not m._active_endpoint_is_forward()


def _ensure_setup(monkeypatch, tmp_path, *, answers):
    import time as _t

    monkeypatch.setattr(_t, "sleep", lambda *_a: None)
    monkeypatch.setattr(m, "_ENSURE_LOCK", str(tmp_path / ".ensure.lock"))
    monkeypatch.setattr(m, "_ENSURE_MARKER", str(tmp_path / ".ensure-attempt"))
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    seq = iter(answers)
    monkeypatch.setattr(m, "_service_is_running", lambda: next(seq, False))
    spawned = []
    monkeypatch.setattr(m, "_spawn_detached_daemon", lambda: spawned.append(1))
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: spawned.append("reconcile"))
    return spawned


def test_ensure_never_starts_a_daemon_over_a_forwarded_route(tmp_path, monkeypatch, capsys):
    _route(tmp_path, monkeypatch, FORWARD)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False] * 10)
    assert m._ensure_daemon() is False
    assert spawned == []
    assert "not starting a local daemon" in capsys.readouterr().err


def test_ensure_rides_out_a_blip_on_the_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, LEGACY_FORWARD)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False, False, True])
    assert m._ensure_daemon() is True
    assert spawned == []


def test_ensure_still_boots_a_local_daemon_without_a_forward(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, DAEMON)
    spawned = _ensure_setup(monkeypatch, tmp_path, answers=[False, False, True])
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: False)
    monkeypatch.setattr(m, "_service_process_is_live", lambda: False)
    monkeypatch.setattr(m, "_acquire_ensure_lock", lambda: 7)
    monkeypatch.setattr(m, "_release_ensure_lock", lambda _fd: None)
    assert m._ensure_daemon() is True
    assert spawned == [1]


def test_the_retry_waits_back_off():
    assert list(service_process_cli._FORWARD_RETRY_DELAYS_S) == sorted(
        service_process_cli._FORWARD_RETRY_DELAYS_S
    )


def _superseded(active, *, listening=True, my_pid=350677):
    return is_superseded(
        "/unused", my_pid=my_pid, my_generation=1,
        read_table=lambda _d: {"active": active},
        is_listening=lambda _h, _p: listening,
    )


def test_a_daemon_whose_route_a_live_forward_replaced_retires():
    assert _superseded(FORWARD)


def test_a_daemon_stays_when_the_forward_is_not_explicit_live_or_pid_free():
    assert not _superseded(FORWARD, listening=False)
    assert not _superseded(LEGACY_FORWARD)  # never retire on an ambiguous entry
    assert not _superseded({**FORWARD, "pid": 42, "generation": 9}, listening=False)
    assert not _superseded(DAEMON)  # its own route

# -- the service verbs never act on the host bridge through the forward -------

def _forward_down(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, FORWARD)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)

    def boom(*_a, **_k):
        raise AssertionError("must not act over a forwarded route")

    return boom


def test_service_start_does_not_start_a_daemon_over_a_forward(tmp_path, monkeypatch, capsys):
    boom = _forward_down(tmp_path, monkeypatch)
    for name in ("_reconcile_live_dynamic_daemon", "_systemd_available", "_spawn_detached_daemon"):
        monkeypatch.setattr(m, name, boom)
    m._service_start()
    assert "not starting a local daemon" in capsys.readouterr().out


def _stop_setup(monkeypatch, killed, *, is_bridge=True):
    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_from_lock", lambda _port: None)
    monkeypatch.setattr(m, "_pid_on_port", lambda _port: 4242)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid, *_a: is_bridge)
    monkeypatch.setattr(m, "_kill_pid", killed.append)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)


def test_service_stop_never_kills_the_forwards_listener(tmp_path, monkeypatch):
    _forward_down(tmp_path, monkeypatch)
    killed = []
    _stop_setup(monkeypatch, killed)  # 4242 is the ssh session holding the forward
    m._service_stop()
    assert killed == []
    assert m._service_pid() is None  # nor is it reported as the bridge


def test_service_stop_kills_a_port_listener_only_if_it_is_a_bridge(tmp_path, monkeypatch):
    _route(tmp_path, monkeypatch, {"bind": "127.0.0.1", "port": 39881, "generation": 1})
    killed = []
    _stop_setup(monkeypatch, killed, is_bridge=False)
    m._service_stop()
    assert killed == []
    _stop_setup(monkeypatch, killed, is_bridge=True)
    m._service_stop()
    assert killed == [4242]


def test_deploy_never_cuts_over_the_host_bridge(tmp_path, monkeypatch, capsys):
    boom = _forward_down(tmp_path, monkeypatch)
    import zdd.cutover

    monkeypatch.setattr(zdd.cutover, "CutoverOrchestrator", boom)
    from agent_bridge import venue_cli

    venue_cli._cmd_deploy(None)
    assert "no local daemon to deploy" in capsys.readouterr().out


_INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


@pytest.mark.skipif(os.name == "nt", reason="a POSIX bash environment is needed")
def test_install_sh_start_does_not_start_a_daemon_over_a_forward(tmp_path):
    home = tmp_path / "home"
    (home / ".agent-bridge").mkdir(parents=True)
    (home / ".agent-bridge" / "active.json").write_text(json.dumps({"active": FORWARD}))
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "curl").write_text("#!/bin/sh\nexit 7\n")  # the forward is down
    (fake_bin / "curl").chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(["bash", str(_INSTALL_SH), "start"], capture_output=True,
                            text=True, env=env, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert "not starting a local daemon" in result.stdout
    assert not (home / ".agent-bridge" / "agent-bridge.pid").exists()

