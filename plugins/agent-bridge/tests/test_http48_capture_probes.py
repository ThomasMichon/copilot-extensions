"""Observe the real local ASGI adapter, not an extension or native SDK."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import time
from typing import Any

import pytest

import bridge_http48_evidence as evidence
import bridge_http47_runtime as binding
from test_live_inbox_runtime import (
    PREFIX, attempt, current, report, request as consumer_request,
    installation as installation, serving as serving,
)

ROOT = Path(__file__).resolve().parents[3]


def capture_request() -> dict | None:
    import os

    if binding.REQUEST not in os.environ:
        return None
    path, value = binding.load_request()
    if value.get("capture_generation") != 48:
        raise ValueError("HTTP-48 probe cannot join another capture")
    if (value["root"] != str(ROOT) or value["report"] != str(path.parent / "observed.json")
            or value["input_sha256"] != evidence.digest(
                evidence.safe(ROOT, evidence.INPUT).read_bytes(),
            )):
        raise ValueError("HTTP-48 input/report binding changed")
    for name, identity in value["source_proof"].items():
        if evidence.previous.git_evidence.sha256_bytes(
            evidence.safe(ROOT, name).read_bytes(),
        ) != identity["sha256"]:
            raise ValueError(f"HTTP-48 probe source changed: {name}")
    return value


def test_http48_capture_actual_observations(
    serving: tuple, request: pytest.FixtureRequest,
) -> None:
    bound = capture_request()
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, client, db, consumer = serving
    history_index, = db.execute_read(
        "SELECT sql FROM sqlite_master WHERE name='bridge_inbox_consumers_session'",
    )
    retained_index, = db.execute_read(
        "SELECT sql FROM sqlite_master WHERE name='live_messages_retained_session'",
    )
    observed_schema = {
        "version": db.execute_read("SELECT version FROM schema_version")[0][0],
        "history_index": (
            "WHERE" not in history_index["sql"].upper()
            and [row["name"] for row in db.execute_read(
                "PRAGMA index_info(bridge_inbox_consumers_session)",
            )] == ["session_id"]
        ),
        "retained_history_index": (
            "WHERE RUNTIME_CUSTODY_JSON IS NOT NULL OR HANDOFF_JSON IS NOT NULL"
            in " ".join(retained_index["sql"].upper().split())
            and [row["name"] for row in db.execute_read(
                "PRAGMA index_info(live_messages_retained_session)",
            )] == ["session_id"]
        ),
    }
    assert db.create_cli_mode_reservation("synthetic-worktree", now=time.time())
    registration = {
        "machine": "machine-a", "worktree_id": "synthetic-worktree",
        "pid": consumer.pid, "process_started_at": consumer.process_started_at,
    }
    registered = client.post("/api/v1/live-sessions", json={
        **registration, "session_id": consumer.session_id,
    })
    assert registered.status_code == 200, registered.text
    observed: dict[str, Any] = {"consumer_schema": observed_schema}
    health = client.get("/health")
    assert health.status_code == 200
    observed["advertised"] = {
        key: health.json()[key] for key in ("protocol_version", "min_protocol_version")
    }
    payload = consumer_request(consumer)
    invalid = {**payload, "contract": vector["requests"]["unsupported_contract"]}
    observed["unsupported_contract"] = client.post(PREFIX + "/negotiate", json=invalid).status_code
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    negotiation = client.post(PREFIX + "/negotiate", json=payload)
    assert negotiation.status_code == 200, negotiation.text
    body = {**payload, **negotiation.json()}
    owner = app.state.loop_governance.runtime_installation(db)
    observed["negotiate"] = {
        "status": negotiation.status_code, "contract": body["contract"],
        "exact_installation": body["binding"]["installation"] == owner.document(),
        "epoch": body["binding"]["epoch"],
        "expected_incarnation": body["expected_incarnation"],
    }
    observed["enroll"] = client.post(PREFIX + "/enroll", json=body).status_code
    ordinary = f"/api/v1/live-sessions/{consumer.session_id}/messages"
    message = {
        "sender": "synthetic-sender", "body": vector["requests"]["body"],
        "delivery": "queue", "idempotency_key": "synthetic-original-operation",
    }
    rejected = client.post(ordinary, json={**message, "delivery": "interrupt"})
    observed["unsupported_interrupt"] = rejected.status_code
    admitted = client.post(ordinary, json=message)
    assert admitted.status_code == 200, admitted.text
    mid = admitted.json()["message_id"]
    replay = client.post(ordinary, json=message)
    assert replay.status_code == 200, replay.text
    observed["ordinary_producer"] = {
        "status": admitted.status_code,
        "stage": admitted.json()["receipt"]["stage"],
        "same_keyed_retry": replay.json()["duplicate"] and replay.json()["message_id"] == mid,
        "legacy_inbox_empty": db.list_pending_live_messages(consumer.session_id) == [],
    }
    first = client.post(PREFIX + "/poll", json=current(body))
    assert first.status_code == 200, first.text
    row, = first.json()["messages"]
    retry = client.post(PREFIX + "/poll", json=current(body))
    stored, = db.execute_read("SELECT * FROM live_messages")
    observed["poll"] = {
        "status": first.status_code, "body": row["body"], "handoff_stage": row["handoff_stage"],
        "same_message": row["id"] == mid,
        "same_handoff": row["handoff_id"] == json.loads(stored["handoff_json"])["id"],
        "unchanged_retry": retry.status_code == 200 and retry.json() == first.json(),
    }
    wrong = copy.deepcopy(current(body))
    wrong["binding"]["epoch"] += 1
    observed["wrong_epoch"] = client.post(PREFIX + "/poll", json=wrong).status_code
    observed["begin"] = []
    for _ in range(2):
        reply = client.post(PREFIX + "/begin", json=attempt(body, row))
        assert reply.status_code == 200, reply.text
        observed["begin"].append(reply.json())
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    for successor in ("native-b", "native-c"):
        renamed = client.post("/api/v1/live-sessions", json={
            **registration, "session_id": successor,
        })
        assert renamed.status_code == 200, renamed.text
    original_retry = client.post(ordinary, json=message)
    original_receipt = client.get(f"{ordinary}/{mid}/receipt")
    successor_url = "/api/v1/live-sessions/native-c/messages"
    observed["native_rename"] = {
        "original_row_unchanged": dict(db.execute_read("SELECT * FROM live_messages")[0]) == original,
        "original_begin_refused": client.post(PREFIX + "/begin", json=attempt(body, row)).status_code,
        "original_retry_receipt": (
            original_retry.status_code == 200 and original_retry.json()["receipt_current"]
            and original_retry.json()["session_id"] == consumer.session_id
            and original_retry.json()["receipt"] == original_receipt.json()
        ),
        "original_receipt_status": original_receipt.status_code,
        "successor_retry": client.post(successor_url, json=message).status_code,
        "successor_receipt": client.get(f"{successor_url}/{mid}/receipt").status_code,
        "original_pending_visible": [
            item["message_id"] for item in db.inspect_live_inbox(consumer.session_id, 10)
        ] == [mid],
        "successor_pending_empty": db.inspect_live_inbox("native-c", 10) == [],
        "new_key_refused": client.post(
            ordinary, json={**message, "idempotency_key": "synthetic-new-operation"},
        ).status_code,
    }
    readiness = {
        "owner": "bridge", "cwd": str(db.db_path.parent), "plugins": [], "timeout_seconds": 0.05,
    }
    original_base = ordinary.removesuffix("/messages")
    observed["alias_control_refusals"] = {
        "startup": client.post(ordinary, json={
            **message, "idempotency_key": "synthetic-startup-operation", "plugin_readiness": readiness,
        }).status_code,
        "readiness": client.post(original_base + "/plugin-readiness", json=readiness).status_code,
        "mode-ready": client.post(original_base + "/mode", json={
            "mode": "autopilot", "plugin_readiness": readiness,
        }).status_code,
        "mode": client.post(original_base + "/mode", json={
            "mode": "autopilot", "wait_timeout": 1.0,
        }).status_code,
        "no_new_control": len(db.execute_read("SELECT * FROM live_messages")) == 1,
    }
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="synthetic-transition",
        target_assembly="synthetic-retained", predecessor=app.state.live_inbox_runtime._producer,
    )
    assert db.deregister_live_session("native-c")
    observed["native_rename"]["aliases_removed"] = (
        db.execute_read("SELECT * FROM live_session_aliases") == []
    )
    app.state.ready = False
    app.state.session_manager.is_draining = True
    app.state.loop_governance = None
    replay = client.post(ordinary, json=message)
    observed["post_close_keyed_retry"] = {
        "status": replay.status_code, "duplicate": replay.json()["duplicate"],
        "same_message": replay.json()["message_id"] == mid,
    }
    observed["changed_keyed_input"] = client.post(
        ordinary, json={**message, "body": "changed input"},
    ).status_code
    observed["outcomes_after_close"] = []
    for stage in vector["requests"]["outcomes"]:
        reply = client.post(PREFIX + "/outcome", json=report(body, row, stage))
        assert reply.status_code == 200, reply.text
        observed["outcomes_after_close"].append(reply.json())
    duplicate = client.post(PREFIX + "/outcome", json=report(body, row, "consumed"))
    assert duplicate.status_code == 200, duplicate.text
    observed["duplicate_outcome"] = duplicate.json()
    stored, = db.execute_read("SELECT * FROM live_messages")
    observed["retained"] = {
        "body": stored["body"], "delivered": stored["delivered_at"] is not None,
        "consumed": stored["consumed_at"] is not None,
    }
    observed["post_close_begin"] = client.post(
        PREFIX + "/begin", json=attempt(body, row),
    ).status_code
    client.headers["X-Forwarded-For"] = "127.0.0.1"
    observed["proxy"] = client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).status_code
    client.headers.pop("X-Forwarded-For")
    client.headers.pop("Authorization")
    observed["unauthenticated"] = client.post(
        PREFIX + "/outcome", json=report(body, row, "consumed"),
    ).status_code
    assert observed == vector["expected"]
    if bound is not None:
        path = Path(bound["report"])
        if path.is_symlink() or (path.exists() and path.stat().st_nlink != 1):
            raise ValueError("HTTP-48 report is aliased")
        observations = json.loads(path.read_bytes()) if path.exists() else {}
        case = request.node.callspec.params["installation"]
        if case in observations:
            raise ValueError("HTTP-48 observation case was duplicated")
        observations[case] = observed
        path.write_bytes(evidence.encode(observations))
