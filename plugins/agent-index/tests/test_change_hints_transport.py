"""Receiver-first HTTP intake and truthful old/new/rollback negotiation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_index import server
from agent_index.client import AgentIndexClient
from agent_index.indexing import engine
from agent_index.indexing.change_hints import ChangeHints, RefRange
from agent_index.indexing.change_hints_contract import (
    CONTRACT_ID, execution_versions, intake_contract,
)
from agent_index.indexing.task_store import TaskStore


def _hints(**kwargs):
    return ChangeHints(**{
        "source": "git:sample", "canonical_family": "git",
        "account_id": "account-a", "item_ids": ("a",), **kwargs,
    })


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "_missing_indexing_dependencies", lambda: [])
    monkeypatch.setattr(engine, "run_reindex", lambda **_: pytest.fail("intake must not execute"))
    store = TaskStore(tmp_path / "tasks.db")
    app = server.build_app()
    notifications = []
    app.state.task_store = store
    app.state.task_runner = SimpleNamespace(notify=lambda: notifications.append("notify"))
    return TestClient(app), store, notifications


def _connected_api(client):
    calls = []
    api = AgentIndexClient("http://fixture")

    def request(method, path, json=None, params=None):
        calls.append((method, path))
        response = client.request(method, path, json=json, params=params)
        response.raise_for_status()
        return response.json()

    api._request = request
    return api, calls


def test_old_source_only_caller_unchanged_and_hinted_burst_unions(service):
    client, store, notifications = service
    plain = client.post("/reindex", json={"source": "git:sample"}).json()
    assert plain["accepted"]
    assert "change_hints" not in plain["task"]
    api, calls = _connected_api(client)
    first = api.reindex(source="git:sample", change_hints=_hints())
    other = _hints(item_ids=("b",), ref_ranges=(RefRange("a", "b"),))
    second = api.reindex(source="git:sample", change_hints=other)
    assert first["task"]["id"] == second["task"]["id"]
    assert first["task"]["id"] != plain["task"]["id"]
    assert ChangeHints.from_dict(second["task"]["change_hints"]) == _hints().union(other)
    assert calls == [("GET", "/health"), ("POST", "/reindex/hints")] * 2
    assert len(notifications) == 3
    assert store.get_pending_count() == 2
    assert store.dequeue_next().id == plain["task"]["id"]
    assert store.dequeue_next() is None


@pytest.mark.parametrize("versions", [(), (2,), (True,), None, 1, "1"])
def test_execution_capability_is_explicit_and_callable_bound(monkeypatch, versions):
    def legacy(**kwargs):
        pytest.fail("a capability read must not invoke indexing")

    monkeypatch.setattr(engine, "run_reindex", legacy)
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    legacy.change_hints_versions = versions
    assert execution_versions(legacy) == []
    assert intake_contract() == {
        "id": CONTRACT_ID, "intake_versions": [1], "execution_versions": [],
    }


def test_health_uses_exact_current_callable_and_never_inherits_module_capability(service, monkeypatch):
    client, _, _ = service

    def capable(**kwargs):
        pytest.fail("health must not execute indexing")

    capable.change_hints_versions = (1,)
    monkeypatch.setattr(engine, "run_reindex", capable)
    assert client.get("/health").json()["indexing_contract"]["execution_versions"] == [1]
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: None)
    health = client.get("/health").json()
    assert health["indexing_contract"]["intake_versions"] == [1]
    assert health["indexing_contract"]["execution_versions"] == []


@pytest.mark.parametrize("change", [
    {"source": "git:other"}, {"source": "all"}, {"full": True},
    {"extra": True}, {"change_hints": {}},
    {"change_hints": {**_hints().to_dict(), "version": 2}},
    {"change_hints": {**_hints().to_dict(), "item_ids": "a"}},
    {"change_hints": {**_hints().to_dict(), "snapshot_complete": True}},
])
def test_bad_scope_unknown_fields_and_versions_do_not_enqueue(service, change):
    client, store, notifications = service
    payload = {"source": "git:sample", "change_hints": _hints().to_dict(), **change}
    response = client.post("/reindex/hints", json=payload)
    assert response.status_code == 422
    assert store.get_queue() == []
    assert notifications == []


@pytest.mark.parametrize("payload", [
    '{"source":"git:sample","source":"git:other","change_hints":{}}',
    '{"source":"git:sample","change_hints":{"version":1,"version":2}}',
    "null", "{",
])
def test_duplicate_or_corrupt_json_does_not_enqueue(service, payload):
    client, store, _ = service
    assert client.post("/reindex/hints", content=payload).status_code == 422
    assert store.get_pending_count() == 0


def test_legacy_endpoint_refuses_required_hints_instead_of_silently_dropping(service):
    client, store, _ = service
    assert client.post("/reindex", json={
        "source": "git:sample", "change_hints": _hints().to_dict(),
    }).status_code == 422
    assert store.get_queue() == []


def test_body_limit_and_drain_refuse_without_queue_effects(service, monkeypatch):
    client, store, _ = service
    from agent_index import reindex_intake

    monkeypatch.setattr(reindex_intake, "MAX_HINT_REQUEST_BYTES", 10)
    assert client.post("/reindex/hints", json={
        "source": "git:sample", "change_hints": _hints().to_dict(),
    }).status_code == 413
    client.app.state.drain_gate.set_draining(True)
    assert client.post("/reindex/hints", json={}).status_code == 503
    assert store.get_queue() == []


@pytest.mark.parametrize("health", [
    {}, {"indexing_contract": None},
    {"indexing_contract": {"id": CONTRACT_ID, "intake_versions": [2]}},
    {"indexing_contract": {"id": CONTRACT_ID, "intake_versions": [True]}},
    {"indexing_contract": {"id": "other", "intake_versions": [1]}},
])
def test_new_caller_old_or_unadvertised_provider_refuses_before_post(health):
    api = AgentIndexClient("http://fixture")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        assert method == "GET"
        return health

    api._request = request
    with pytest.raises(RuntimeError, match="does not advertise"):
        api.reindex(source="git:sample", change_hints=_hints())
    assert calls == [("GET", "/health")]


def test_provider_rollback_after_negotiation_never_falls_back_to_legacy_post():
    legacy = FastAPI()
    legacy_posts = []

    @legacy.get("/health")
    def health():
        # Discovery succeeded immediately before the provider rolled back.
        return {"indexing_contract": intake_contract()}

    @legacy.post("/reindex")
    def reindex(body: dict):
        legacy_posts.append(body)
        return {"accepted": True}

    api, calls = _connected_api(TestClient(legacy))
    with pytest.raises(Exception, match="404") as refused:
        api.reindex(source="git:sample", change_hints=_hints())
    assert refused.value.response.status_code == 404
    assert calls == [("GET", "/health"), ("POST", "/reindex/hints")]
    assert legacy_posts == []


@pytest.mark.parametrize("receipt", [
    {"accepted": True, "task": {"source": "git:sample", "full": False}},
    {"accepted": "true"}, {},
    {"accepted": True, "task": {
        "source": "git:sample", "full": False,
        "change_hints": _hints(item_ids=()).to_dict(),
    }},
])
def test_missing_acceptance_receipt_is_unknown_not_replayed(receipt):
    api = AgentIndexClient("http://fixture")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        if method == "GET":
            return {"indexing_contract": intake_contract()}
        return receipt

    api._request = request
    with pytest.raises(RuntimeError, match="outcome unknown"):
        api.reindex(source="git:sample", change_hints=_hints())
    assert calls == [("GET", "/health"), ("POST", "/reindex/hints")]


def test_ordinary_client_request_does_not_require_negotiation(service):
    client, _, _ = service
    api, calls = _connected_api(client)
    assert api.reindex(source="git:sample", full=True)["accepted"]
    assert calls == [("POST", "/reindex")]
