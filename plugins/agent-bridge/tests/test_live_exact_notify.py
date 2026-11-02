"""Exact informational notifications refuse aliases before and after admission."""

import argparse
import json
import time

import pytest

from agent_bridge import live_exact_notify as contract
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.send_outcome import SendRefused
from tests import test_live_keyed_receipt as controls

api = controls.api
binding = controls.binding
rollover = controls.rollover


def path(session="live-1"):
    return f"/api/v1/live-sessions/{session}/messages/exact-notify/v1"


def test_probe_then_rollover_refuses_notify_without_successor_effect(api, tmp_db):
    assert tmp_db.get_live_session("live-1")["session_id"] == "live-1"
    rollover(tmp_db)
    response = api.post(path(), json=binding())
    assert response.status_code == 409
    assert not tmp_db.execute_read("SELECT * FROM live_messages")
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")


def test_notify_admission_then_rollover_retains_informational_native_provenance(api, tmp_db):
    response = api.post(path(), json=binding())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["protocol"] == contract.PROTOCOL
    mid = result["admission"]["message_id"]
    repeated = api.post(path(), json=binding())
    assert repeated.json()["admission"]["duplicate"]
    assert repeated.json()["admission"]["message_id"] == mid
    rollover(tmp_db)
    assert api.get("/api/v1/live-sessions/successor/messages").json()["messages"] == []
    row = tmp_db.execute_read("SELECT * FROM live_messages")[0]
    assert row["session_id"] == "live-1" and row["exact_session"] == 1
    assert row["kind"] == "notify" and row["delivery"] == "steer"
    assert tmp_db.ack_live_messages("successor", [mid], time.time()) == 0
    assert api.post(path("successor"), json=binding(expected_session_id="successor")).status_code == 409
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")


@pytest.mark.parametrize("supported", [True, False])
def test_cli_uses_distinct_capability_and_never_ensures_or_downgrades(monkeypatch, capsys, supported):
    calls, ensures = [], []

    class Client:
        _base = "http://test"
        _connect_grace = 0

        def _request(self, method, url, *args, **kwargs):
            calls.append((method, url, args, kwargs))
            if url == "/health":
                return {
                    "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                    "capabilities": {contract.CAPABILITY: 1} if supported else {},
                }
            return {
                "protocol": contract.PROTOCOL, "version": 1, "binding": binding(),
                "admission": {"session_id": "live-1", "message_id": 1},
            }

    from agent_bridge import __main__ as cli
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: ensures.append(kwargs) or Client())
    monkeypatch.setattr("agent_bridge.session_targeting_cli._resolve_prompt",
                        lambda *args, **kwargs: binding()["body"])
    args = argparse.Namespace(
        json=True, target="live-1", expected_session_id="live-1", idempotency_key="retry-1",
        sender="supervisor", live_exact_notify=True, kind="notify",
    )
    if supported:
        contract.send(args)
        assert json.loads(capsys.readouterr().out)["protocol"] == contract.PROTOCOL
        assert calls[-1][1] == path() and calls[-1][3]["allow_retry"] is False
    else:
        with pytest.raises(SendRefused):
            contract.send(args)
        assert len(calls) == 1 and calls[0][1] == "/health"
    assert ensures == [{"ensure": False}]


@pytest.mark.parametrize("field,value", [
    ("live_exact", True), ("live_receipt_only", True), ("kind", "prompt"),
    ("queue", True), ("expected_session_id", "other"), ("idempotency_key", None),
])
def test_cli_refuses_mixed_or_work_bearing_modes_before_client(monkeypatch, field, value):
    from agent_bridge import __main__ as cli
    monkeypatch.setattr(cli, "_get_client", lambda **_: pytest.fail("invalid options must not connect"))
    args = argparse.Namespace(
        json=True, target="live-1", expected_session_id="live-1", idempotency_key="retry-1",
        sender="supervisor", live_exact_notify=True, kind="notify",
    )
    setattr(args, field, value)
    with pytest.raises(SendRefused):
        contract.send(args)
