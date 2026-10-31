"""Actual recovery observations in both installation modes; not SDK evidence."""

from dataclasses import replace
import asyncio
import json
from pathlib import Path

import bridge_http50_evidence as evidence
from bridge_http_retained_probes import capture_request, record_observations
from agent_bridge.db import Database
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.models import SessionStatus
from agent_bridge.runtime_custody import RuntimeCustodyError
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget
from test_live_inbox_runtime import (
    PREFIX, attempt, current, enroll, report,
    installation as installation, serving as serving,
)

ROOT = Path(__file__).resolve().parents[3]


async def idle_custody(db, admission, directory, requests) -> dict:
    manager = SessionManager(
        db, session_host_state_dir=str(directory / "hosts"),
        idle_reap_ttl_seconds=requests["idle_reap_ttl"],
    )

    def session(sid, association=None):
        target = SpawnTarget(type="local", cwd=str(directory))
        retained = Session(sid, sid, target)
        retained.status, retained.turn_count, retained.updated_at = SessionStatus.IDLE, 1, 0
        retained.acp_session_id = "native-" + sid
        db.create_session(
            sid, sid, None, target.cwd, "local", "idle", 1,
            acp_session_id=retained.acp_session_id, target_json=target.to_json(),
            runtime_admission=association,
        )
        manager._sessions[sid] = retained
        return retained

    retained = session("capture-retained", admission)
    observed = {"retained_reaped": await manager.sweep_idle_sessions(now=requests["idle_now"])}
    refused = False
    async with retained._turn_start_lock:
        try:
            await manager.stop_session(retained.session_id, reap_host=True, _automatic_reap=True)
        except RuntimeCustodyError:
            refused = True
    observed["retained_automatic_stop_refused"] = refused
    observed["retained_idle_unchanged"] = retained.status == SessionStatus.IDLE
    legacy = session("capture-legacy")
    async with legacy._turn_start_lock:
        await manager.stop_session(legacy.session_id, reap_host=True, _automatic_reap=True)
    observed["legacy_stopped"] = legacy.status == SessionStatus.STOPPED
    return observed


def test_http50_capture_actual_observations(serving, request, tmp_path) -> None:
    bound = capture_request(evidence, ROOT)
    if bound is not None:
        assert HTTP_PROTOCOL_VERSION == evidence.GENERATION
    else:
        assert HTTP_PROTOCOL_VERSION >= evidence.GENERATION
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, client, db, consumer = serving
    observed = {
        "advertised": {key: client.get("/health").json()[key] for key in (
            "protocol_version", "min_protocol_version",
        )},
        "schema": {
            "version": db.execute_read("SELECT version FROM schema_version")[0][0],
            "pending_index": [row["name"] for row in db.execute_read(
                "PRAGMA index_info(live_messages_handoff_pending)",
            )] == ["session_id", "id"],
        },
    }
    empty = client.get(PREFIX + "/recovery")
    observed["empty_without_initialization"] = (
        empty.status_code == 200 and empty.json() == {"messages": [], "next_after": None}
        and db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    )
    body = enroll(client, consumer)
    admission = app.state.live_inbox_runtime._admission
    assert admission is not None
    observed["idle_custody"] = asyncio.run(idle_custody(db, admission, tmp_path, vector["requests"]))
    ids = []
    for key in ("first", "second"):
        sent = client.post("/api/v1/live-sessions/native-a/messages", json={
            "sender": "synthetic-sender", "body": vector["requests"]["body"],
            "idempotency_key": key,
        })
        assert sent.status_code == 200, sent.text
        ids.append(sent.json()["message_id"])
    payload = {**current(body), "limit": vector["requests"]["poll_limit"]}
    first = client.post(PREFIX + "/poll", json=payload)
    assert first.status_code == 200, first.text
    row, = first.json()["messages"]
    observed["bounded_poll"] = {
        "status": first.status_code, "count": len(first.json()["messages"]),
        "unoffered_untouched": db.execute_read(
            "SELECT handoff_json FROM live_messages WHERE id=?", (ids[1],),
        )[0][0] is None,
        "retry_same": client.post(PREFIX + "/poll", json=payload).json() == first.json(),
    }
    observed["begin"] = [
        client.post(PREFIX + "/begin", json=attempt(body, row)).json() for _ in range(2)
    ]
    enroll(client, replace(consumer, incarnation="successor"))
    response = client.get(PREFIX + f"/recovery/{ids[0]}")
    receipt = response.json()
    legacy = client.get(
        f"/api/v1/live-sessions/native-a/messages/{ids[0]}/receipt",
    ).json()
    observed["recovery"] = {
        "status": response.status_code, "original_consumer": receipt["consumer"] == consumer.document(),
        "same_handoff": receipt["handoff_id"] == row["handoff_id"],
        "stage": receipt["stage"], "body_absent": vector["requests"]["body"] not in response.text,
        "old_receipt_shape": set(legacy) == {
            "session_id", "message_id", "stage", "admitted_at", "sdk_accepted_at", "consumed_at",
        },
        "no_send_permission": client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409,
    }
    owner = app.state.loop_governance.runtime_installation(db)
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="synthetic-close",
        target_assembly="synthetic-retained", predecessor=app.state.live_inbox_runtime._producer,
    )
    db.deregister_live_session(consumer.session_id)
    app.state.ready = False
    app.state.session_manager.is_draining = True
    app.state.live_inbox_runtime = None
    app.state.loop_governance = None
    epoch = db.runtime_epoch(owner)
    passive = Database(db.db_path, read_only=True)
    app.state.db = passive
    try:
        response = client.get(PREFIX + "/recovery", params={
            "limit": vector["requests"]["page_limit"],
        })
        observed["passive_after_close"] = {
            "status": response.status_code,
            "unchanged": response.json() == {"messages": [receipt], "next_after": None},
            "no_epoch_mutation": db.runtime_epoch(owner) == epoch,
        }
    finally:
        app.state.db = db
        passive.close()
    observed["wrong_native"] = client.get(
        PREFIX.replace("native-a", "other") + f"/recovery/{ids[0]}",
    ).status_code
    observed["post_close_begin"] = client.post(PREFIX + "/begin", json=attempt(body, row)).status_code
    observed["outcomes"] = [
        client.post(PREFIX + "/outcome", json=report(body, row, stage)).json()["stage"]
        for stage in ("unknown", "sdk_accepted", "consumed")
    ]
    observed["consumed_single_retained"] = (
        client.get(PREFIX + f"/recovery/{ids[0]}").json()["stage"] == "consumed"
    )
    observed["pending_after_consumption"] = client.get(PREFIX + "/recovery").json()
    client.headers["Forwarded"] = "127.0.0.1"
    observed["proxy"] = client.get(PREFIX + "/recovery").status_code
    client.headers.pop("Forwarded")
    client.headers.pop("Authorization")
    observed["unauthenticated"] = client.get(PREFIX + "/recovery").status_code
    expected = json.loads(json.dumps(vector["expected"]))
    if bound is None:
        expected["advertised"]["protocol_version"] = HTTP_PROTOCOL_VERSION
    assert observed == expected
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)
