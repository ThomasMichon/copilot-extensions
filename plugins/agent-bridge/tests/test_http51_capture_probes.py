"""Actual standard-send parser/ASGI/journal evidence, not native SDK proof."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bridge_http51_evidence as evidence
from bridge_http_retained_probes import capture_request, record_observations
from http51_receipt_probes import observe as observe_receipt
from agent_bridge import __main__ as core
from agent_bridge.models import SessionStatus
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from test_live_inbox_runtime import (
    installation as installation, serving as serving,
)
from test_native_send_admission import _command, native_send as native_send
from test_native_send_admission import (
    test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race
    as test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race,
    test_failed_resume_without_a_matching_receipt_remains_a_failure
    as test_failed_resume_without_a_matching_receipt_remains_a_failure,
)

ROOT = Path(__file__).resolve().parents[3]


def test_http51_capture_actual_observations(
    serving, native_send, request, monkeypatch, capsys,
) -> None:
    bound = capture_request(evidence, ROOT)
    receipt_client_factory = core._get_client
    assert HTTP_PROTOCOL_VERSION == evidence.GENERATION
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, client, db, _ = serving
    manager, session, original = native_send
    app.state.session_manager = manager
    native_send = manager, session, original.model_copy(update={"prompt": vector["requests"]["prompt"]})
    # Keep ordinary middleware/installation binding while allowing an actual HTTP 500 response.
    api = TestClient(app, headers=client.headers, raise_server_exceptions=False)
    observed = {"advertised": {
        key: api.get("/health").json()[key]
        for key in ("protocol_version", "min_protocol_version")
    }}
    args, _ = _command(native_send, monkeypatch, api=api)
    args.func(args)
    receipt = json.loads(capsys.readouterr().out)
    observed["receipt"] = {key: receipt[key] for key in vector["expected"]["receipt"]}
    args.func(args)
    observed["same_retry"] = json.loads(capsys.readouterr().out) == receipt
    path = "/api/v1/sessions/bridge-a/turns/native-bound/send"
    body = native_send[2].model_dump()
    observed["changed_input"] = api.post(path, json={**body, "prompt": "changed"}).status_code
    observed["wrong_native"] = api.post(path, json={
        **body, "expected_native_session_id": "replacement",
    }).status_code
    session.status = SessionStatus.RUNNING
    observed["busy"] = api.post(path, json={**body, "invocation_key": "busy"}).status_code
    old, calls = _command(native_send, monkeypatch, generation=50, api=api)
    with pytest.raises(SystemExit) as refused:
        old.func(old)
    result = json.loads(capsys.readouterr().out)
    observed["old_receiver"] = {
        "exit": refused.value.code, "outcome": result["outcome"], "posts": len(calls),
    }

    session.status = SessionStatus.IDLE
    saved_update = manager.db.update_session_status

    def fail_after_commit(*args, **kwargs):
        raise RuntimeError("synthetic response failure after journal commit")

    lost, _ = _command(native_send, monkeypatch, api=api)
    lost.idempotency_key = "lost-reply"
    monkeypatch.setattr(manager.db, "update_session_status", fail_after_commit)
    with pytest.raises(SystemExit) as unknown:
        lost.func(lost)
    result = json.loads(capsys.readouterr().out)
    observed["post_commit_failure"] = {
        "exit": unknown.value.code,
        **{key: result[key] for key in ("outcome", "reason", "idempotency_key")},
    }
    monkeypatch.setattr(manager.db, "update_session_status", saved_update)
    lost.func(lost)
    observed["post_commit_failure"].update({
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    })
    malformed = [True]
    invalid, _ = _command(
        native_send, monkeypatch, api=api,
        transform=lambda value: {} if malformed[0] else value,
    )
    with pytest.raises(SystemExit) as unknown:
        invalid.func(invalid)
    result = json.loads(capsys.readouterr().out)
    malformed[0] = False
    invalid.func(invalid)
    observed["malformed_receipt"] = {
        "exit": unknown.value.code, "outcome": result["outcome"],
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    }
    event, = [
        event for event in session.event_log.get_events(0) if event.event == "user_message"
    ]
    observed["caller_provenance"] = event.data
    observed["no_inbox"] = db.execute_read("SELECT * FROM live_messages") == []
    observed["receipt_only"] = observe_receipt(
        api, manager, session, body, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    api.headers.pop("Authorization")
    observed["unauthenticated"] = api.post(path, json=body).status_code
    api.close()
    assert observed == vector["expected"]
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)
