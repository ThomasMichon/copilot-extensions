"""Recover a healthy dynamic daemon stranded behind stale routing."""

from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
from types import SimpleNamespace

from agent_bridge import __main__ as m


def test_reconcile_adopts_and_undrains_dynamic_singleton(monkeypatch):
    draining = {"value": True}
    published = {}
    reconciled = {}

    monkeypatch.setattr(
        m, "_pid_from_lock", lambda port: 222 if port == 0 else None
    )
    monkeypatch.setattr(
        m, "_listening_ports_for_pid", lambda pid: [9857, 55231]
    )

    def _health(port, **_kwargs):
        if port != 55231:
            return None
        return {
            "status": "ok",
            "service": "agent-bridge",
            "ready": True,
            "draining": draining["value"],
            "version": "0.4.0-dev417",
        }

    monkeypatch.setattr(m, "_service_health_on_port", _health)
    monkeypatch.setattr(
        m,
        "_undrain_service_at",
        lambda port: draining.__setitem__("value", False) or True,
    )
    monkeypatch.setattr(
        m,
        "_active_endpoint",
        lambda: SimpleNamespace(
            bind="127.0.0.1",
            port=52118,
            pid=111,
            version="0.4.0-dev416",
            generation=7,
        ),
    )

    from zdd import routing

    @contextmanager
    def _lock(_config_dir):
        yield

    monkeypatch.setattr(routing, "_routing_lock", _lock)
    monkeypatch.setattr(
        routing,
        "read_table",
        lambda _config_dir: {
            "active": {
                "bind": "127.0.0.1",
                "port": 52118,
                "pid": 111,
                "version": "0.4.0-dev416",
                "generation": 7,
            }
        },
    )

    def _publish(config_dir, **kwargs):
        published.update({"config_dir": config_dir, **kwargs})
        return SimpleNamespace(version=kwargs["version"]), None

    monkeypatch.setattr(routing, "_publish_active_unlocked", _publish)
    monkeypatch.setattr(
        m,
        "_reconcile_service_marker",
        lambda pid, version: reconciled.update(
            {"pid": pid, "version": version}
        ),
    )

    assert m._reconcile_live_dynamic_daemon() is True
    assert published == {
        "config_dir": m._INSTALL_DIR,
        "bind": "127.0.0.1",
        "port": 55231,
        "pid": 222,
        "version": "0.4.0-dev417",
    }
    assert reconciled == {"pid": 222, "version": "0.4.0-dev417"}


def test_reconcile_refuses_to_publish_daemon_that_stays_drained(monkeypatch):
    monkeypatch.setattr(m, "_pid_from_lock", lambda _port: 222)
    monkeypatch.setattr(m, "_listening_ports_for_pid", lambda _pid: [55231])
    monkeypatch.setattr(
        m,
        "_service_health_on_port",
        lambda _port: {
            "status": "ok",
            "service": "agent-bridge",
            "ready": True,
            "draining": True,
        },
    )
    monkeypatch.setattr(m, "_undrain_service_at", lambda _port: False)

    assert m._reconcile_live_dynamic_daemon() is False


def test_undrain_connection_failure_returns_false(monkeypatch):
    from agent_bridge import client

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def undrain(self):
            raise client.BridgeConnectionError("connection reset")

    monkeypatch.setattr(client, "BridgeClient", _Client)
    monkeypatch.setattr(
        "agent_bridge.config.load_or_create_auth_token",
        lambda: "token",
    )

    assert m._undrain_service_at(55231) is False


def test_undrain_truncated_json_returns_false(monkeypatch):
    from agent_bridge import client

    class _Client:
        def __init__(self, *_args, **_kwargs):
            pass

        def undrain(self):
            raise json.JSONDecodeError("truncated", "{", 1)

    monkeypatch.setattr(client, "BridgeClient", _Client)
    monkeypatch.setattr(
        "agent_bridge.config.load_or_create_auth_token",
        lambda: "token",
    )

    assert m._undrain_service_at(55231) is False


def test_health_partial_response_returns_none(monkeypatch):
    class _Response:
        status = 200

        def read(self):
            raise http.client.IncompleteRead(b"", 1)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(),
    )

    assert m._service_health_on_port(55231) is None


def test_reconcile_does_not_overwrite_concurrent_publication(monkeypatch):
    stale = SimpleNamespace(
        bind="127.0.0.1",
        port=52118,
        pid=111,
        version="0.4.0-dev416",
        generation=7,
    )
    monkeypatch.setattr(m, "_active_endpoint", lambda: stale)
    monkeypatch.setattr(m, "_pid_from_lock", lambda _port: 222)
    monkeypatch.setattr(m, "_listening_ports_for_pid", lambda _pid: [55231])
    undrained = {"called": False}
    monkeypatch.setattr(
        m,
        "_undrain_service_at",
        lambda _port: undrained.__setitem__("called", True) or True,
    )
    monkeypatch.setattr(
        m,
        "_service_health_on_port",
        lambda port, **_kwargs: (
            {
                "status": "ok",
                "service": "agent-bridge",
                "ready": True,
                "draining": False,
                "version": "0.4.0-dev417",
            }
            if port == 55231
            else None
        ),
    )

    from zdd import routing

    @contextmanager
    def _lock(_config_dir):
        yield

    monkeypatch.setattr(routing, "_routing_lock", _lock)
    monkeypatch.setattr(
        routing,
        "read_table",
        lambda _config_dir: {
            "active": {
                "bind": "127.0.0.1",
                "port": 60000,
                "pid": 333,
                "version": "0.4.0-dev418",
                "generation": 8,
            }
        },
    )
    monkeypatch.setattr(
        routing,
        "_publish_active_unlocked",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a concurrent active generation must not be replaced")
        ),
    )

    assert m._reconcile_live_dynamic_daemon() is False
    assert undrained["called"] is False


def test_service_start_recovers_before_platform_manager(monkeypatch, capsys):
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: True)
    monkeypatch.setattr(m, "_service_port", lambda: 55231)
    monkeypatch.setattr(
        m,
        "_systemd_available",
        lambda: (_ for _ in ()).throw(
            AssertionError("platform manager must not run after adoption")
        ),
    )
    monkeypatch.setattr(
        m,
        "_spawn_detached_daemon",
        lambda: (_ for _ in ()).throw(
            AssertionError("adoption must not spawn a duplicate")
        ),
    )

    m._service_start()

    assert "recovered dynamic route (port 55231)" in capsys.readouterr().out


def test_ensure_daemon_recovers_before_wait_or_spawn(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_NO_ENSURE", raising=False)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_reconcile_live_dynamic_daemon", lambda: True)
    monkeypatch.setattr(
        m,
        "_service_process_is_live",
        lambda: (_ for _ in ()).throw(
            AssertionError("recovered daemon must not enter startup wait")
        ),
    )
    monkeypatch.setattr(
        m,
        "_spawn_detached_daemon",
        lambda: (_ for _ in ()).throw(
            AssertionError("recovered daemon must not spawn")
        ),
    )

    assert m._ensure_daemon() is True


def test_service_process_liveness_includes_dynamic_singleton(monkeypatch):
    monkeypatch.setattr(m, "_active_endpoint", lambda: None)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(
        m, "_pid_from_lock", lambda port: 222 if port == 0 else None
    )
    monkeypatch.setattr(
        m,
        "_pid_is_agent_bridge",
        lambda pid, probe_timeout: pid == 222,
    )

    assert m._service_process_is_live() is True


def test_windows_listener_census_returns_unique_ports(monkeypatch):
    monkeypatch.setattr(m.sys, "platform", "win32")

    class _Result:
        stdout = "55231\n9857\n55231\n"

    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: _Result())

    assert m._listening_ports_for_pid(222) == [9857, 55231]


def test_service_stop_does_not_succeed_while_port_zero_holder_lives(
    monkeypatch, capsys
):
    import time

    monkeypatch.setattr(m, "_systemd_available", lambda: False)
    monkeypatch.setattr(m.sys, "platform", "win32")
    monkeypatch.setattr(m, "_win_task_exists", lambda: False)
    monkeypatch.setattr(m, "_service_port", lambda: 52118)
    monkeypatch.setattr(m, "_read_pid_file", lambda: None)
    monkeypatch.setattr(m, "_pid_on_port", lambda _port: None)
    monkeypatch.setattr(
        m, "_pid_from_lock", lambda port: 222 if port == 0 else None
    )
    monkeypatch.setattr(m, "_kill_pid", lambda _pid: None)
    monkeypatch.setattr(m, "_service_is_running", lambda: False)
    monkeypatch.setattr(m, "_pid_is_agent_bridge", lambda pid: pid == 222)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    m._service_stop()

    output = capsys.readouterr()
    assert "[OK] agent-bridge stopped" not in output.out
    assert "still responding" in output.err
