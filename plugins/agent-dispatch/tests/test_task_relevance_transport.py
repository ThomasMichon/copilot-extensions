"""Current/prior HTTP relevance boundaries without production callers or processes."""

from __future__ import annotations

import copy

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.overrides import logical_override_id, set_override
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.queue import Status
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests.test_pool_catalog_transport import _headers
from tests.test_task_relevance_registration import SPEC, emitted, emitter, q as q, report


@pytest.fixture
def api(q, tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr("agent_dispatch.remote_dispatch.local_machine", lambda: "host-a")
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "default")
    monkeypatch.setenv("AGENT_DISPATCH_OVERRIDES", str(tmp_path / "overrides.json"))
    monkeypatch.setattr(
        "agent_dispatch.relevance_source.read_supervisor_declarations",
        lambda *_args, **_kwargs: DeclarationRead((), True),
    )
    return TestClient(create_app(
        q, token="data", control_token="control", enable_mcp=False,
        verification_interval=0,
    ))


def body(q, task_id: str) -> dict:
    values = report(q, task_id)
    scope = values.pop("scope")
    values.pop("reporter")
    return {
        "version": 1, "registration_id": scope.registration_id,
        "registration_revision": scope.revision, **values,
    }


def transport(api: TestClient, calls: list, contract=...) -> httpx.MockTransport:
    def send(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        response = api.request(
            request.method, str(request.url), headers=request.headers, content=request.content,
        )
        if request.url.path == "/health" and contract is not ...:
            value = response.json()
            if contract is None:
                value.pop("task_relevance", None)
            else:
                value["task_relevance"] = contract
            return httpx.Response(response.status_code, json=value)
        return httpx.Response(response.status_code, content=response.content)
    return httpx.MockTransport(send)


def test_registered_wire_acceptance_and_receipt_recovery_after_policy_hold(q, api) -> None:
    emitter(q)
    t = emitted(q)
    calls = []
    with DispatchClient(
        "http://testserver", token="data", control_token="control", transport=transport(api, calls),
    ) as client:
        snapshot = client.relevance_snapshot(t.id, registration_id="source-a")
        assert snapshot["registration_id"] == "source-a"
        request = body(q, t.id)
        result = client.report_task_relevance(t.id, report=request)
        assert result["decision"] == "abandoned"
        q.set_registration_status("source-a", "paused")
        assert client.report_task_relevance(t.id, report=request) == result
        assert client.relevance_receipt(
            t.id, request["request_id"], registration_id="source-a",
        )["acceptance"] == result
        with pytest.raises(DispatchError):
            client.relevance_receipt(t.id, request["request_id"], registration_id="foreign")


@pytest.mark.parametrize("credential", [None, "data", "wrong"])
@pytest.mark.parametrize("method,path", [
    ("GET", "/task-relevance/contract"),
    ("GET", "/task-relevance/tasks/task-a?registration_id=source-a"),
    ("POST", "/task-relevance/tasks/task-a"),
    ("GET", "/task-relevance/tasks/task-a/receipts/request-a?registration_id=source-a"),
])
def test_relevance_requires_control_not_ordinary_data_authority(q, api, credential, method, path) -> None:
    emitter(q)
    t = emitted(q)
    response = api.request(
        method, path, headers={} if credential is None else _headers(credential),
        json=body(q, t.id) if method == "POST" else None,
    )
    assert response.status_code in {401, 403}
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("control", [None, ""])
def test_unconfigured_control_is_unsupported_even_on_an_open_data_server(q, control) -> None:
    api = TestClient(create_app(
        q, token=None, control_token=control, enable_mcp=False, verification_interval=0,
    ))
    assert api.get("/task-relevance/contract", headers=_headers()).status_code == 503
    assert api.get("/health").json()["task_relevance"]["control_authority_configured"] is False


def test_task_authority_cannot_promote_itself_to_source_control(q, api) -> None:
    token = mint_task_scope_token(
        "control", task_id="task-a", worker_id="worker-a", owner_session_id="native-a",
        epoch=0, generation=1,
    )
    response = api.get("/task-relevance/contract", headers={**_headers(), TASK_SCOPE_HEADER: token})
    assert response.status_code == 403


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"expected_revision": True},
    {"source": "foreign"}, {"scope": {"source": "foreign"}}, {"reporter": "foreign"},
])
def test_strict_wire_rejects_unknown_scope_and_invalid_generation_before_effects(q, api, change) -> None:
    emitter(q)
    t = emitted(q)
    response = api.post(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), json={**body(q, t.id), **change},
    )
    assert response.status_code == 422
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("override", ["source-a", logical_override_id("local", "review-source")])
def test_actual_override_owner_prevents_report_admission(q, api, monkeypatch, override) -> None:
    from agent_dispatch.config import overrides_path

    emitter(q)
    t = emitted(q)
    request = body(q, t.id)
    set_override(overrides_path(), override, disabled=True, reason="operator hold")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=request)
    assert response.status_code == 409
    assert "operator override" in response.json()["detail"]
    assert q.get(t.id).status == Status.QUEUED


def test_draining_receiver_refuses_new_decisions(q, api) -> None:
    emitter(q)
    t = emitted(q)
    api.app.state.drain_gate.set_draining(True)
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=body(q, t.id))
    assert response.status_code == 409
    assert q.get(t.id).status == Status.QUEUED


@pytest.mark.parametrize("contract", [
    None, {}, {"protocol": "task_relevance/v2", "supported_versions": [2]},
    {"protocol": "task_relevance/v1", "supported_versions": [True],
     "registration_scopes": ["local-direct-command-emitter"], "control_authority_configured": True},
])
def test_prior_invalid_or_rollback_receiver_gets_no_relevance_effect(q, api, contract) -> None:
    emitter(q)
    t = emitted(q)
    calls = []
    with DispatchClient(
        "http://testserver", token="data", control_token="control",
        transport=transport(api, calls, contract),
    ) as client:
        with pytest.raises(DispatchUpgradeRequired, match="running coordinator lacks"):
            client.report_task_relevance(t.id, report=body(q, t.id))
        assert client.get(t.id)["status"] == Status.QUEUED
    assert not any(method == "POST" for method, _path in calls)
    assert q.relevance_receipt(t.id, "closed-object") is None


def test_capability_is_additive_for_old_clients_and_unknown_future_fields(q, api) -> None:
    from agent_dispatch.task_relevance_contract import relevance_contract

    emitter(q)
    t = emitted(q)
    assert api.get("/tasks/" + t.id, headers=_headers("data")).status_code == 200
    contract = copy.deepcopy(relevance_contract("control"))
    contract["future_optional_property"] = {"unused": True}
    with DispatchClient(
        "http://testserver", token="data", control_token="control",
        transport=transport(api, [], contract),
    ) as client:
        assert client.report_task_relevance(t.id, report=body(q, t.id))["decision"] == "abandoned"


def test_lost_report_response_recovers_original_receipt_without_another_effect(q, api) -> None:
    emitter(q)
    t = emitted(q)
    calls = []
    normal = transport(api, calls)

    def lose_response(request: httpx.Request) -> httpx.Response:
        response = normal.handle_request(request)
        if request.method == "POST":
            raise httpx.ReadTimeout("original report response lost", request=request)
        return response

    with DispatchClient(
        "http://testserver", token="data", control_token="control",
        transport=httpx.MockTransport(lose_response),
    ) as client:
        values = {**body(q, t.id), "request_id": "object/closed-2"}
        with pytest.raises(httpx.ReadTimeout):
            client.report_task_relevance(t.id, report=values)
        q.remove_registration("source-a")
        recovered = client.relevance_receipt(
            t.id, values["request_id"], registration_id="source-a",
        )
    assert recovered["acceptance"]["decision"] == "abandoned"
    assert sum(method == "POST" for method, _path in calls) == 1
    assert q.get(t.id).status == Status.ABANDONED


def test_unreadable_policy_is_not_treated_as_no_overrides(q, api) -> None:
    from agent_dispatch.config import overrides_path

    emitter(q)
    t = emitted(q)
    overrides_path().write_text("{invalid", encoding="utf-8")
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=body(q, t.id))
    assert response.status_code == 409
    assert q.get(t.id).status == Status.QUEUED


def test_fresh_abandon_emits_existing_lifecycle_once_not_on_report_replay(q, api, monkeypatch) -> None:
    emitter(q)
    t = emitted(q)
    events, releases, telemetry = [], [], []
    monkeypatch.setattr(api.app.state.bus, "publish", events.append)
    monkeypatch.setattr(
        "agent_dispatch.handoff_claim_release.release_if_handoff",
        lambda task, **options: releases.append((task, options)),
    )
    monkeypatch.setattr("agent_dispatch.telemetry.emit", telemetry.append)
    request = body(q, t.id)
    for _attempt in range(2):
        response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=request)
        assert response.status_code == 200
    assert len(events) == len(releases) == len(telemetry) == 1
    assert events[0]["type"] == "task.abandoned"
    assert events[0]["task"]["id"] == t.id
    assert events[0]["task"]["status"] == Status.ABANDONED
    assert events[0]["task"]["has_result"] is False
    assert "result" not in events[0]["task"]
    assert telemetry[0]["event"] == events[0]["type"]
    assert telemetry[0]["to"] == Status.ABANDONED


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("override", [None, "declared:example:review-source", "logical:example:review-source"])
def test_unmirrored_declaration_supersedes_direct_even_when_its_source_is_disabled(
    q, api, monkeypatch, bound, override,
) -> None:
    from agent_dispatch.config import overrides_path
    from agent_dispatch.registrar import load_declaration

    emitter(q)
    t = emitted(q)
    if bound:
        q.claim_one("host/worker", task_id=t.id)
        q.start(t.id, "host/worker", owner_session_id="original-native")
    values = body(q, t.id)
    declaration = load_declaration({
        "name": "review-source", "owner": "example", "kind": "emitter", "spec": SPEC,
    })
    monkeypatch.setattr(
        "agent_dispatch.relevance_source.read_supervisor_declarations",
        lambda *_args, **_kwargs: DeclarationRead((declaration,), True),
    )
    assert q.get_registration("declared:example:review-source") is None
    if override is not None:
        set_override(overrides_path(), override, reason="source held")
    before = q.get(t.id)
    snapshot = api.get(
        f"/task-relevance/tasks/{t.id}", headers=_headers(), params={"registration_id": "source-a"},
    )
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=values)
    assert snapshot.status_code == response.status_code == 409
    assert "superseded" in response.json()["detail"]
    assert q.get(t.id) == before
    assert q.steer_log(t.id) == q.list_wakes(t.id) == []


def test_unconfirmed_registrar_does_not_prove_direct_source_eligibility(q, api, monkeypatch) -> None:
    emitter(q)
    t = emitted(q)
    monkeypatch.setattr(
        "agent_dispatch.relevance_source.read_supervisor_declarations",
        lambda *_args, **_kwargs: DeclarationRead((), False, "source unavailable"),
    )
    response = api.post(f"/task-relevance/tasks/{t.id}", headers=_headers(), json=body(q, t.id))
    assert response.status_code == 409
    assert "incomplete" in response.json()["detail"]
    assert q.get(t.id).status == Status.QUEUED
