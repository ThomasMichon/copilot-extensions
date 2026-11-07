"""Exact native question admission and durable outcomes through real HTTP."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.live_questions import CAPABILITY, LiveQuestions
from agent_bridge.routes.live_questions import router


@pytest.fixture
def api(tmp_db):
    tmp_db.register_live_session(
        "native-1", machine=None, cwd=None, worktree_id=None, repo=None, branch=None,
        pid=123, process_started_at=1.0, role=None, now=time.time(),
    )
    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_questions = LiveQuestions()
    app.add_middleware(BearerAuthMiddleware, token="synthetic-secret")
    app.include_router(router)
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-secret"}
    assert headers["Authorization"].endswith("synthetic-secret")
    with TestClient(app, headers=headers) as client:
        yield SimpleNamespace(client=client, db=tmp_db, state=app.state)


def observation(family="elicitation"):
    return {
        "generation": "extension-1", "pid": 123, "process_started_at": 1.0,
        "requests": [{
            "request_id": "question-1", "tool_call_id": "tool-1", "family": family,
            "message": "Choose", "answerable": True,
            "requested_schema": {"type": "object", "properties": {"color": {"type": "string"}}},
        }],
    }


def answer(family="elicitation"):
    return {
        "request_id": "question-1", "generation": "extension-1", "family": family,
        "sender": "delegated-controller", "content": (
            {"answer": "blue"} if family == "user_input" else {"color": "blue"}
        ), "wait_timeout": 0,
    }


BASE = "/api/v1/live-sessions/native-1/questions"


def test_live_question_provider_advertises_actual_capability(api, monkeypatch):
    from agent_bridge.app_routes import configure_serving_app
    from agent_bridge.models import ServiceConfig

    app = configure_serving_app(
        ServiceConfig(db_path=str(api.db.db_path)), "synthetic-secret", lifespan=None,
    )
    app.state.db = api.db
    app.state.session_manager = SimpleNamespace(is_draining=False)
    monkeypatch.setattr("agent_bridge.routes.health.observe_worktree_writer", lambda: {})
    with TestClient(app, headers=api.client.headers) as client:
        assert client.get("/health").json()["capabilities"][CAPABILITY] == 1
        assert client.get(BASE).json()["availability"] == "unavailable"


@pytest.mark.parametrize("family,length,limit,included", [
    ("elicitation", 512, 256, False),
    ("user_input", 50, 256, False),
    ("elicitation", 4, 256, True),
    ("user_input", 512, 6000, True),
])
def test_passive_native_correlation_is_exact_and_charged_to_its_budget(family, length, limit, included):
    from agent_bridge.events import EventLog
    from agent_bridge.result_snapshot import build_represented_result_snapshot

    def snapshot(correlated):
        events = EventLog()
        data = {"tool_call_id": "tool", "message": "Q", "read_only": True}
        if correlated:
            data.update(request_id="r" * length, request_family=family)
        events.append("ask_user_request", data)
        return build_represented_result_snapshot(
            registration={"session_id": "native-budget", "status": "live", "turn_state": "running"},
            event_log=events, requested_ref="native-budget", position=None,
            max_text_chars=limit,
        )

    baseline = snapshot(False)
    current = snapshot(True)
    pending = current.state.pending_input.value[0]
    assert current.limits.used_text_chars <= limit
    if included:
        assert pending["request_id"] == "r" * length
        assert pending["request_family"] == family
        assert current.limits.used_text_chars - baseline.limits.used_text_chars == length + len(family)
    else:
        assert "request_id" not in pending and "request_family" not in pending
        assert current.limits.used_text_chars == baseline.limits.used_text_chars


@pytest.mark.parametrize("family", ["elicitation", "user_input"])
def test_current_native_roundtrip_and_durable_outcome(api, family):
    assert api.client.get(BASE).json()["availability"] == "unavailable"
    assert api.client.post(BASE + "/poll", json=observation(family)).status_code == 200
    current = api.client.get(BASE).json()
    assert current["generation"] == "extension-1"
    assert current["requests"][0]["request_id"] == "question-1"
    admitted = api.client.post(BASE + "/answers", json=answer(family))
    assert admitted.status_code == 200
    result = admitted.json()
    assert result["accepted"] is None and result["state"] == "in_flight"
    control_id = result["answer_id"]
    # Neither a message consumer nor the generic mode consumer can take this answer.
    assert api.db.list_pending_live_messages("native-1") == []
    assert api.db.claim_live_controls("native-1", time.time(), 130) == []
    controls = api.client.post(BASE + "/poll", json=observation(family)).json()["controls"]
    assert len(controls) == 1 and controls[0]["id"] == control_id
    assert controls[0]["sender"] == "delegated-controller"
    assert controls[0]["body"]["session_id"] == "native-1"
    assert api.client.post(BASE + "/poll", json=observation(family)).json()["controls"] == []
    assert api.db.ack_live_messages(
        "native-1", [control_id], time.time(), controls=True,
    ) == 0
    ack = {
        "generation": "extension-1", "request_id": "question-1",
        "state": "accepted", "detail": "SDK accepted",
    }
    assert api.client.post(BASE + f"/answers/{control_id}/ack", json=ack).json() == {"acked": 1}
    assert api.client.get(BASE + f"/answers/{control_id}").json()["accepted"] is True
    assert api.client.post(BASE + f"/answers/{control_id}/ack", json=ack).json() == {"acked": 0}
    api.state.live_questions = LiveQuestions()
    assert api.client.get(BASE).json()["availability"] == "unavailable"
    assert api.client.get(BASE + f"/answers/{control_id}").json()["accepted"] is True
    api.db.get_live_session = lambda _: {"session_id": "native-successor"}
    assert api.client.get(BASE + f"/answers/{control_id}").json()["accepted"] is True
    assert api.client.get(
        "/api/v1/live-sessions/native-successor/questions/answers/" + str(control_id),
    ).status_code == 404


def test_authentication_mismatch_and_single_use_refusals(api):
    assert api.client.post(BASE + "/poll", json=observation()).status_code == 200
    assert api.client.post(
        BASE + "/answers", json=answer(), headers={"Authorization": "invalid"},
    ).status_code == 401
    for fields in (
        {"generation": "old"}, {"request_id": "other"}, {"family": "permission"},
        {"content": {"color": {"nested": "invalid"}}}, {"action": "cancel", "content": {"x": 1}},
        {"content": {"unknown": "invalid"}},
    ):
        response = api.client.post(BASE + "/answers", json={**answer(), **fields})
        assert response.status_code in {409, 422}, response.text
    assert api.db.execute_read("SELECT id FROM live_messages") == []
    first = api.client.post(BASE + "/answers", json=answer()).json()
    duplicate = api.client.post(BASE + "/answers", json=answer())
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["answer_id"] == first["answer_id"]
    assert len(api.db.execute_read("SELECT id FROM live_messages")) == 1
    wrong = {"generation": "old", "request_id": "question-1", "state": "accepted"}
    assert api.client.post(
        BASE + f"/answers/{first['answer_id']}/ack", json=wrong,
    ).status_code == 409
    # An unclaimed answer cannot acquire an outcome through acknowledgment.
    wrong["generation"] = "extension-1"
    assert api.client.post(
        BASE + f"/answers/{first['answer_id']}/ack", json=wrong,
    ).json()["acked"] == 0


def test_terminal_completion_restart_and_process_replacement_revoke_observation(api):
    api.client.post(BASE + "/poll", json=observation())
    empty = {**observation(), "requests": []}
    api.client.post(BASE + "/poll", json=empty)
    assert api.client.post(BASE + "/answers", json=answer()).status_code == 409
    api.client.post(BASE + "/poll", json=observation())
    snapshot = api.state.live_questions._snapshots["native-1"]
    api.state.live_questions._snapshots["native-1"] = type(snapshot)(
        snapshot.observation, snapshot.observed_at - 16,
    )
    assert api.client.post(BASE + "/answers", json=answer()).status_code == 409
    assert api.client.post(
        BASE + "/poll", json={**observation(), "pid": 999},
    ).status_code == 409
    api.state.live_questions = LiveQuestions()
    assert api.client.post(BASE + "/answers", json=answer()).status_code == 409
    # A still-current extension can republish actual in-memory requests after a bridge restart.
    api.client.post(BASE + "/poll", json=observation())
    assert api.client.post(BASE + "/answers", json=answer()).status_code == 200


def test_question_answers_do_not_follow_session_aliases(api):
    api.db.get_live_session = lambda _: {"session_id": "native-successor"}
    assert api.client.get(BASE).status_code == 409
    assert api.client.post(BASE + "/answers", json=answer()).status_code == 409


@pytest.mark.parametrize("state,accepted", [("rejected", False), ("unknown", None)])
def test_native_rejection_and_uncertainty_are_not_acceptance(api, state, accepted):
    api.client.post(BASE + "/poll", json=observation())
    admitted = api.client.post(BASE + "/answers", json=answer()).json()
    api.client.post(BASE + "/poll", json=observation())
    result = api.client.post(BASE + f"/answers/{admitted['answer_id']}/ack", json={
        "generation": "extension-1", "request_id": "question-1", "state": state,
    })
    assert result.json()["acked"] == 1
    result = api.client.get(BASE + f"/answers/{admitted['answer_id']}").json()
    assert result["state"] == state and result["accepted"] is accepted


def test_unclaimed_timeout_withdraws_and_missing_required_field_refuses(api):
    snapshot = observation()
    snapshot["requests"][0]["requested_schema"]["required"] = ["color"]
    api.client.post(BASE + "/poll", json=snapshot)
    assert api.client.post(BASE + "/answers", json={**answer(), "content": {}}).status_code == 422
    result = api.client.post(BASE + "/answers", json={**answer(), "wait_timeout": 0.001}).json()
    assert result["state"] == "withdrawn" and result["accepted"] is False
    assert api.client.post(BASE + "/poll", json=snapshot).json()["controls"] == []


def test_old_provider_refuses_before_mutation_and_new_provider_gets_pinned_shape():
    client = BridgeClient("http://127.0.0.1:1", "synthetic-secret")
    sent = []
    capable = False

    def request(method, path, **kwargs):
        if path == "/health":
            return {"protocol_version": 52, "min_protocol_version": 1,
                    "capabilities": {CAPABILITY: 1} if capable else {}}
        sent.append((method, path, kwargs))
        return {"accepted": True, "state": "accepted", "answer_id": 1}

    client._request = request
    with pytest.raises(BridgeClientError, match=CAPABILITY):
        client.answer_live_question(
            "native-1", "question-1", "extension-1", {"color": "blue"},
            family="elicitation", sender="delegated-controller",
        )
    assert sent == []
    capable = True
    assert client.answer_live_question(
        "native-1", "question-1", "extension-1", {"color": "blue"},
        family="elicitation", sender="delegated-controller",
    )["accepted"] is True
    assert sent[-1][2]["allow_retry"] is False
    assert sent[-1][2]["body"]["request_id"] == "question-1"
    capable = False
    with pytest.raises(BridgeClientError):
        client.live_questions("native-1")
    assert len(sent) == 1


def test_cli_live_branch_preserves_owned_answer_and_permission_boundaries(monkeypatch, capsys):
    from agent_bridge import __main__ as core
    from agent_bridge.session_lifecycle_cli import _cmd_answer

    client = MagicMock()
    client.live_questions.return_value = {
        "generation": "extension-1", "requests": [{"request_id": "question-1", "family": "elicitation"}],
    }
    client.answer_live_question.return_value = {"accepted": True, "state": "accepted"}
    def selected(**kwargs):
        assert kwargs == {"ensure": False}
        return client
    monkeypatch.setattr(core, "_get_client", selected)
    monkeypatch.setattr(core, "_caller_id_for", lambda _: "controller")
    monkeypatch.setattr(core, "_live_sender_label", lambda _: "delegated-controller")
    args = core.build_parser().parse_args([
        "answer", "native-1", "--request-id", "question-1",
        "--generation", "extension-1", "--field", "color=blue",
    ])
    _cmd_answer(args)
    assert json.loads(capsys.readouterr().out)["accepted"] is True
    client.answer_ask_user.assert_not_called()
    client.answer_permission.assert_not_called()
    client.get_session_status.assert_not_called()
    args.option_id = "approve"
    with pytest.raises(SystemExit) as exc:
        _cmd_answer(args)
    assert exc.value.code == 2
