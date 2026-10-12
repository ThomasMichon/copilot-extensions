from __future__ import annotations

import json

from agent_dispatch import __main__ as cli
from agent_dispatch.client import DispatchError
from agent_dispatch.installer_readiness import emit, evaluate


def test_healthy_coordinator_is_ready(capsys):
    result = evaluate(lambda: {"status": "ok", "version": "1.2.3"})

    assert result["state"] == "ready"
    assert "version 1.2.3" in result["detail"]
    assert emit(result) == 0
    assert json.loads(capsys.readouterr().out) == result


def test_unavailable_coordinator_is_failed_without_starting_it(capsys):
    calls = 0

    def probe():
        nonlocal calls
        calls += 1
        raise DispatchError(503, "offline")

    result = evaluate(probe)

    assert calls == 1
    assert result["state"] == "failed"
    assert "restart the coordinator service" in result["detail"]
    assert emit(result) == 1
    assert json.loads(capsys.readouterr().out)["state"] == "failed"


def test_non_ready_health_state_is_failed():
    result = evaluate(lambda: {"status": "draining"})

    assert result["state"] == "failed"
    assert "graceful cutover" in result["detail"]


def test_payload_command_does_not_autostart_coordinator(monkeypatch, capsys):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def health(self):
            return {"status": "ok"}

    ensure_values = []

    def fake_client(_args, *, ensure=True):
        ensure_values.append(ensure)
        return FakeClient()

    monkeypatch.setattr(cli, "_client", fake_client)

    assert cli.main(["installer-readiness"]) == 0
    assert ensure_values == [False]
    assert json.loads(capsys.readouterr().out)["state"] == "ready"
