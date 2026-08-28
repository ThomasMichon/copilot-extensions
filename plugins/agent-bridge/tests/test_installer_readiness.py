from __future__ import annotations

import json

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.installer_readiness import emit, evaluate


def test_healthy_service_is_ready_and_requires_no_external_restart(capsys):
    result = evaluate(True)

    assert result["state"] == "ready"
    assert "no separate restart is required" in result["detail"]
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_unavailable_or_unhealthy_service_is_failed(capsys):
    result = evaluate(False)

    assert result["state"] == "failed"
    assert "service is unavailable or unhealthy" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_payload_command_uses_non_starting_health_probe(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_service_is_running", lambda: True)
    monkeypatch.setattr(
        cli,
        "_ensure_daemon",
        lambda: pytest.fail("readiness must not start the bridge"),
    )

    cli._cmd_installer_readiness(None)

    assert json.loads(capsys.readouterr().out)["state"] == "ready"


def test_failed_payload_probe_exits_nonzero_without_starting(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_service_is_running", lambda: False)
    monkeypatch.setattr(
        cli,
        "_ensure_daemon",
        lambda: pytest.fail("readiness must not start the bridge"),
    )

    with pytest.raises(SystemExit) as raised:
        cli._cmd_installer_readiness(None)

    assert raised.value.code == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"
