"""Service liveness/port resolution follows the dynamic routing table (#1713).

Post-#694 the daemon binds an OS-assigned **dynamic** port advertised in
``active.json``; ``config.yaml``'s ``port`` is 0 (the dynamic sentinel). A
port/liveness resolver that reads only ``config.yaml`` therefore probes the
legacy 9280 and falsely reports the daemon down while it is healthy on a dynamic
port -- the #1713 liveness bug (``service status`` FAILing on 9280; ``service
start`` warning "health check did not pass" against a daemon that came up).
``_service_port`` must prefer the live port from the routing table.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_bridge import __main__ as m
from agent_bridge.models import default_port


def _write_active(install_dir, port, pid=4321):
    (install_dir / "active.json").write_text(
        json.dumps(
            {
                "active": {
                    "bind": "127.0.0.1",
                    "port": port,
                    "pid": pid,
                    "version": "0.0.0",
                    "generation": 1,
                }
            }
        ),
        encoding="utf-8",
    )


def test_service_port_prefers_dynamic_active_endpoint(monkeypatch, tmp_path):
    # config pins the dynamic sentinel (port: 0) -- the legacy resolver would
    # fall back to default_port(); the live routing table says otherwise.
    (tmp_path / "config.yaml").write_text("port: 0\n", encoding="utf-8")
    _write_active(tmp_path, 54321)
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    assert m._service_port() == 54321
    assert m._service_port() != default_port()


def test_service_pid_prefers_routed_pid_over_stale_pid_file(
    monkeypatch, tmp_path
):
    (tmp_path / "config.yaml").write_text("port: 0\n", encoding="utf-8")
    _write_active(tmp_path, 54321, pid=222)
    (tmp_path / "agent-bridge.pid").write_text("111", encoding="utf-8")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_PID_FILE", str(tmp_path / "agent-bridge.pid"))
    monkeypatch.setattr(m, "_pid_on_port", lambda _port: 333)

    assert m._service_pid() == 222


def test_service_status_prints_routed_pid(monkeypatch, capsys):
    monkeypatch.setattr(m, "_cmd_status", lambda _args: None)
    monkeypatch.setattr(m, "_service_pid", lambda: 222)
    monkeypatch.setattr(m, "_service_port", lambda: 54321)
    monkeypatch.setattr(m, "_print_reconcile_status", lambda: None)

    m._cmd_service(SimpleNamespace(service_action="status"))

    output = capsys.readouterr().out
    assert "PID:  222" in output
    assert "Port: 54321" in output


def test_service_port_falls_back_to_config_fixed_port(monkeypatch, tmp_path):
    # No routing table (fixed-port or never-started deployment) -> honor the
    # config-pinned fixed port.
    (tmp_path / "config.yaml").write_text("port: 9299\n", encoding="utf-8")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    assert m._active_endpoint_port() is None
    assert m._service_port() == 9299


def test_service_port_defaults_when_nothing_known(monkeypatch, tmp_path):
    # No routing table and no config -> the platform default.
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    assert m._service_port() == default_port()


def test_is_running_probes_the_dynamic_port(monkeypatch, tmp_path):
    # _service_is_running must health-probe the dynamic port from active.json,
    # not the legacy default -- so a healthy dynamic-port daemon reads as up.
    (tmp_path / "config.yaml").write_text("port: 0\n", encoding="utf-8")
    _write_active(tmp_path, 61234)
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))

    probed: dict[str, str] = {}

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(url, timeout=0):
        probed["url"] = url
        return _Resp()

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert m._service_is_running() is True
    assert "61234" in probed["url"]
