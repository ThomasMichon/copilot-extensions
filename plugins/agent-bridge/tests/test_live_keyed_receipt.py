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


def rollover(tmp_db):
    now = time.time()
    tmp_db.register_live_session(
        "live-1", machine="host", cwd="/workspace", worktree_id="wt-1", repo="repo",
        branch="dev", pid=42, role=None, process_started_at=now - 60, now=now,
    )
    tmp_db.execute_write("UPDATE live_sessions SET cli_mode=1 WHERE session_id='live-1'")
    assert tmp_db.register_live_session(
        "successor", machine="host", cwd="/workspace", worktree_id="wt-1", repo="repo",
        branch="dev", pid=42, role=None, process_started_at=now - 60, now=now + 1,
    ) == "live"
    assert tmp_db.get_live_session("live-1")["session_id"] == "successor"
    assert not tmp_db.execute_read("SELECT 1 FROM live_sessions WHERE session_id='live-1'")


def test_admitted_exact_prompt_survives_actual_rollover_without_successor_delivery(api, tmp_db):
    sent = api.post(path(operation="keyed-exact"), json=binding())
    assert sent.status_code == 200, sent.text
    mid = sent.json()["receipt"]["message_id"]
    legacy = tmp_db.enqueue_live_message("live-1", "peer", "legacy", time.time())
    rollover(tmp_db)
    row = tmp_db.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]
    assert row["session_id"] == "live-1" and row["exact_session"] == 1
    assert [item["id"] for item in tmp_db.list_pending_live_messages("successor")] == [legacy]
    assert [item["id"] for item in tmp_db.list_pending_live_messages("live-1")] == [legacy]
    for session in ("live-1", "successor"):
        pending = api.get(f"/api/v1/live-sessions/{session}/messages")
        assert pending.status_code == 200
        assert [item["id"] for item in pending.json()["messages"]] == [legacy]
    assert api.post(path(), json=binding()).json()["receipt"]["message_id"] == mid
    assert api.get(f"/api/v1/live-sessions/successor/messages/{mid}/receipt").status_code == 404
    assert tmp_db.consume_live_message("successor", mid, time.time()) == 0
    assert api.post("/api/v1/live-sessions/successor/messages/ack", json={"ids": [mid]}).json()["acked"] == 0
    assert tmp_db.ack_live_messages("live-1", [mid], time.time()) == 1
    assert tmp_db.consume_live_message("live-1", mid, time.time()) == 1
    assert api.post(path(), json=binding()).json()["receipt"]["stage"] == "consumed"
    assert api.post(path("successor", "keyed-exact"),
                    json=binding(expected_session_id="successor")).status_code == 409
    assert len(tmp_db.execute_read("SELECT * FROM live_messages")) == 2


def test_legacy_key_cannot_be_recovered_as_exact_provenance(api, tmp_db):
    tmp_db.enqueue_live_message("live-1", binding()["sender"], binding()["body"], time.time(),
                                delivery="steer", idempotency_key=binding()["idempotency_key"])
    for operation in ("keyed-exact", "keyed-receipt"):
        assert api.post(path(operation=operation), json=binding()).status_code == 409
    assert len(tmp_db.execute_read("SELECT * FROM live_messages")) == 1


def test_schema_38_migration_preserves_legacy_rows_and_requires_writable_preparation(tmp_db):
    from agent_bridge.db import Database

    mid = tmp_db.enqueue_live_message("live-1", "peer", "legacy", time.time())
    tmp_db.execute_write("ALTER TABLE live_messages DROP COLUMN exact_session")
    tmp_db.execute_write("UPDATE schema_version SET version=38")
    with pytest.raises(ValueError, match="writable preparation"):
        Database(tmp_db.db_path, read_only=True)
    migrated = Database(tmp_db.db_path)
    try:
        row = migrated.execute_read("SELECT * FROM live_messages WHERE id=?", (mid,))[0]
        assert row["session_id"] == "live-1" and row["exact_session"] == 0
        assert migrated.execute_read("SELECT version FROM schema_version")[0]["version"] == 39
        readonly = Database(tmp_db.db_path, read_only=True)
        readonly.close()
    finally:
        migrated.close()


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
