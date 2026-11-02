"""Exact CLI-native admission and read-only keyed recovery over the actual inbox."""

import argparse
import json
import time

import pytest

from agent_bridge import live_keyed_receipt as contract
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.send_outcome import SendRefused
from tests import test_live_message_receipts as controls

api = controls.api


def binding(**changes):
    return {
        "version": 1, "expected_session_id": "live-1", "idempotency_key": "retry-1",
        "sender": "supervisor", "body": "assigned task acceptance", **changes,
    }


def path(session="live-1", operation="keyed-receipt"):
    return f"/api/v1/live-sessions/{session}/messages/{operation}/v1"


def test_missing_receipt_does_not_enqueue_or_move_inbox_cursor(api, tmp_db):
    before = tmp_db.execute_read("SELECT * FROM live_messages")
    result = api.post(path(), json=binding())
    assert result.status_code == 200 and result.json()["receipt"] is None
    assert result.json()["binding"] == binding()
    assert tmp_db.execute_read("SELECT * FROM live_messages") == before


def test_exact_busy_native_send_and_receipt_recovery_are_one_original_message(api, tmp_db):
    first = api.post(path(operation="keyed-exact"), json=binding())
    assert first.status_code == 200, first.text
    mid = first.json()["receipt"]["message_id"]
    assert first.json()["receipt"]["stage"] == "admitted"
    recovered = api.post(path(), json=binding())
    assert recovered.json()["receipt"] == first.json()["receipt"]
    repeated = api.post(path(operation="keyed-exact"), json=binding())
    assert repeated.status_code == 200 and repeated.json()["admission"]["duplicate"]
    assert repeated.json()["receipt"]["message_id"] == mid
    controls.record_consumption(tmp_db, "live-1", [controls._consumed(mid)])
    assert api.post(path(), json=binding()).json()["receipt"]["stage"] == "consumed"
    rows = tmp_db.execute_read("SELECT * FROM live_messages")
    assert len(rows) == 1 and rows[0]["delivery"] == "steer"
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")


@pytest.mark.parametrize("operation", ["keyed-exact", "keyed-receipt"])
@pytest.mark.parametrize("changes", [
    {"version": True}, {"version": 2}, {"unexpected": 1},
    {"expected_session_id": ""}, {"idempotency_key": ""}, {"body": ""},
])
def test_strict_distinct_routes_refuse_invalid_bodies_without_effect(api, tmp_db, operation, changes):
    result = api.post(path(operation=operation), json=binding(**changes))
    assert result.status_code == 422
    assert not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.parametrize("operation", ["keyed-exact", "keyed-receipt"])
def test_different_session_or_keyed_prompt_never_observes_or_changes_original(
    api, tmp_db, operation,
):
    assert api.post(path(operation="keyed-exact"), json=binding()).status_code == 200
    before = [dict(row) for row in tmp_db.execute_read("SELECT * FROM live_messages")]
    assert api.post(path("other", operation), json=binding()).status_code == 409
    assert api.post(path(operation=operation), json=binding(body="different")).status_code == 409
    assert [dict(row) for row in tmp_db.execute_read("SELECT * FROM live_messages")] == before


def test_raw_native_fence_does_not_follow_legacy_session_alias(api, tmp_db):
    now = time.time()
    tmp_db.register_live_session(
        "successor", machine=None, cwd=None, worktree_id="other", repo=None,
        branch=None, pid=None, role=None, now=now,
    )
    tmp_db.execute_write(
        "INSERT INTO live_session_aliases(alias_session_id,target_session_id,created_at) VALUES (?,?,?)",
        ("live-1", "successor", now),
    )
    result = api.post(path(operation="keyed-exact"), json=binding())
    assert result.status_code == 409
    assert not tmp_db.execute_read("SELECT * FROM live_messages")
    assert api.post(path(), json=binding()).json()["receipt"] is None


@pytest.mark.parametrize("receipt_only", [True, False])
@pytest.mark.parametrize("supported", [True, False])
def test_cli_negotiates_actual_peer_and_never_ensures_or_falls_back(
    monkeypatch, capsys, receipt_only, supported,
):
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
            return {"protocol": contract.PROTOCOL, "version": 1, "binding": binding(), "receipt": None}

    from agent_bridge import __main__ as cli
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: ensures.append(kwargs) or Client())
    monkeypatch.setattr("agent_bridge.session_targeting_cli._resolve_prompt",
                        lambda *args, **kwargs: binding()["body"])
    args = argparse.Namespace(
        json=True, target="live-1", expected_session_id="live-1", idempotency_key="retry-1",
        sender="supervisor", live_receipt_only=receipt_only, live_exact=not receipt_only,
    )
    if supported:
        contract.send(args)
        assert json.loads(capsys.readouterr().out)["binding"] == binding()
        assert calls[-1][1] == path(operation="keyed-receipt" if receipt_only else "keyed-exact")
        assert calls[-1][3]["allow_retry"] is False
    else:
        with pytest.raises(SendRefused):
            contract.send(args)
        assert len(calls) == 1 and calls[0][1] == "/health"
    assert ensures == [{"ensure": False}]
