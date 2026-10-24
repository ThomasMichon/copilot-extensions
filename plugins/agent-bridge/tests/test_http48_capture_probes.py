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
    observed: dict[str, Any] = {}
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
    mid = db.enqueue_live_message(
        consumer.session_id, "synthetic-sender", vector["requests"]["body"], time.time(),
        runtime_admission=app.state.live_inbox_runtime._admission,
    )
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
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="synthetic-transition",
        target_assembly="synthetic-retained", predecessor=app.state.live_inbox_runtime._producer,
    )
    db.deregister_live_session(consumer.session_id)
    app.state.ready = False
    app.state.session_manager.is_draining = True
    app.state.loop_governance = None
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
