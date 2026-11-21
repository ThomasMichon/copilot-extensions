"""Receiver-first HTTP intake and truthful old/new/rollback negotiation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

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


Service = tuple[TestClient, TaskStore, list[str]]


def _hints(**kwargs: Any) -> ChangeHints:
    return ChangeHints(**{
        "source": "git:sample", "canonical_family": "git",
        "account_id": "account-a", "item_ids": ("a",), **kwargs,
    })


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(server, "_missing_indexing_dependencies", lambda: [])
    monkeypatch.setattr(engine, "run_reindex", lambda **_: pytest.fail("intake must not execute"))
    store = TaskStore(tmp_path / "tasks.db")
    app = server.build_app()
    notifications: list[str] = []
    app.state.task_store = store
    app.state.task_runner = SimpleNamespace(notify=lambda: notifications.append("notify"))
    return TestClient(app), store, notifications


def _connected_api(client: TestClient) -> tuple[AgentIndexClient, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    api = AgentIndexClient("http://fixture")

    def request(
        method: str, path: str, json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls.append((method, path))
        response = client.request(method, path, json=json, params=params)
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

    setattr(api, "_request", request)
    return api, calls


def test_old_source_only_caller_unchanged_and_hinted_burst_unions(service: Service) -> None:
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
    claimed = store.dequeue_next()
    assert claimed is not None and claimed.id == plain["task"]["id"]
    assert store.dequeue_next() is None


@pytest.mark.parametrize("versions", [(), (2,), (True,), None, 1, "1"])
def test_execution_capability_is_explicit_and_callable_bound(
    monkeypatch: pytest.MonkeyPatch, versions: Any,
) -> None:
    def legacy(**kwargs: Any) -> None:
        pytest.fail("a capability read must not invoke indexing")

    monkeypatch.setattr(engine, "run_reindex", legacy)
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    setattr(legacy, "change_hints_versions", versions)
    assert execution_versions(legacy) == []
    assert intake_contract() == {
        "id": CONTRACT_ID, "intake_versions": [1, 2], "execution_versions": [],
    }


def test_health_uses_exact_current_callable_and_never_inherits_module_capability(
    service: Service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, _ = service

    def capable(**kwargs: Any) -> None:
        pytest.fail("health must not execute indexing")

    setattr(capable, "change_hints_versions", (1,))
    monkeypatch.setattr(engine, "run_reindex", capable)
    assert client.get("/health").json()["indexing_contract"]["execution_versions"] == [1]
    monkeypatch.setattr(engine, "CHANGE_HINTS_VERSIONS", (1,), raising=False)
    monkeypatch.setattr(engine, "run_reindex", lambda **kwargs: None)
    health = client.get("/health").json()
    assert health["indexing_contract"]["intake_versions"] == [1, 2]
    assert health["indexing_contract"]["execution_versions"] == []


@pytest.mark.parametrize("change", [
    {"source": "git:other"}, {"source": "all"}, {"full": True},
    {"extra": True}, {"change_hints": {}},
    {"change_hints": {**_hints().to_dict(), "version": 2}},
    {"change_hints": {**_hints().to_dict(), "item_ids": "a"}},
    {"change_hints": {**_hints().to_dict(), "snapshot_complete": True}},
])
def test_bad_scope_unknown_fields_and_versions_do_not_enqueue(
    service: Service, change: dict[str, Any],
) -> None:
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
def test_duplicate_or_corrupt_json_does_not_enqueue(service: Service, payload: str) -> None:
    client, store, _ = service
    assert client.post("/reindex/hints", content=payload).status_code == 422
    assert store.get_pending_count() == 0


def test_legacy_endpoint_refuses_required_hints_instead_of_silently_dropping(service: Service) -> None:
    client, store, _ = service
    assert client.post("/reindex", json={
        "source": "git:sample", "change_hints": _hints().to_dict(),
    }).status_code == 422
    assert store.get_queue() == []


def test_body_limit_and_drain_refuse_without_queue_effects(
    service: Service, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, store, _ = service
    from agent_index import reindex_intake

    monkeypatch.setattr(reindex_intake, "MAX_HINT_REQUEST_BYTES", 10)
    assert client.post("/reindex/hints", json={
        "source": "git:sample", "change_hints": _hints().to_dict(),
    }).status_code == 413
    cast(FastAPI, client.app).state.drain_gate.set_draining(True)
    assert client.post("/reindex/hints", json={}).status_code == 503
    assert store.get_queue() == []


@pytest.mark.parametrize("health", [
    {}, {"indexing_contract": None},
    {"indexing_contract": {"id": CONTRACT_ID, "intake_versions": [2]}},
    {"indexing_contract": {"id": CONTRACT_ID, "intake_versions": [True]}},
    {"indexing_contract": {"id": "other", "intake_versions": [1]}},
])
def test_new_caller_old_or_unadvertised_provider_refuses_before_post(health: dict[str, Any]) -> None:
    api = AgentIndexClient("http://fixture")
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        assert method == "GET"
        return health

    setattr(api, "_request", request)
    with pytest.raises(RuntimeError, match="does not advertise"):
        api.reindex(source="git:sample", change_hints=_hints())
    assert calls == [("GET", "/health")]


def test_provider_rollback_after_negotiation_never_falls_back_to_legacy_post() -> None:
    legacy = FastAPI()
    legacy_posts: list[dict[str, Any]] = []

    @legacy.get("/health")
    def health() -> dict[str, Any]:
        # Discovery succeeded immediately before the provider rolled back.
        return {"indexing_contract": intake_contract()}

    @legacy.post("/reindex")
    def reindex(body: dict[str, Any]) -> dict[str, Any]:
        legacy_posts.append(body)
        return {"accepted": True}

    api, calls = _connected_api(TestClient(legacy))
    with pytest.raises(Exception, match="404") as refused:
        api.reindex(source="git:sample", change_hints=_hints())
    response = getattr(refused.value, "response", None)
    assert response is not None and response.status_code == 404
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
def test_missing_acceptance_receipt_is_unknown_not_replayed(receipt: dict[str, Any]) -> None:
    api = AgentIndexClient("http://fixture")
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        if method == "GET":
            return {"indexing_contract": intake_contract()}
        return receipt

    setattr(api, "_request", request)
    with pytest.raises(RuntimeError, match="outcome unknown"):
        api.reindex(source="git:sample", change_hints=_hints())
    assert calls == [("GET", "/health"), ("POST", "/reindex/hints")]


def test_ordinary_client_request_does_not_require_negotiation(service: Service) -> None:
    client, _, _ = service
    api, calls = _connected_api(client)
    assert api.reindex(source="git:sample", full=True)["accepted"]
    assert calls == [("POST", "/reindex")]


def test_point_provider_rollback_refuses_feature_without_losing_retained_or_legacy_work(
    service: Service,
) -> None:
    from agent_index.indexing.point_hints import (
        PointBinding, PointChangeHints, WorkItemRevisionTarget,
    )
    from agent_index.indexing.point_service_contract import require_point_service

    client, store, _ = service
    hints = PointChangeHints(
        source="ado:sample:points", canonical_family="ado:points", account_id="account-a",
        binding=PointBinding("https://example.test/project", "query", "context"),
        targets=(WorkItemRevisionTarget("42", 7, ("original-event",)),),
    )
    accepted = client.post("/reindex/hints", json={
        "source": hints.source, "change_hints": hints.to_dict(),
    }).json()["task"]
    retained = store.pin_point_claim(accepted["id"], expected_hints=hints)
    prior = FastAPI()
    effects: list[str] = []

    @prior.get("/health")
    def health() -> dict[str, Any]:
        return {"indexing_contract": intake_contract(), "instanceToken": "prior-provider"}

    @prior.post("/reindex")
    def reindex() -> dict[str, bool]:
        effects.append("legacy-post")
        return {"accepted": True}

    prior_client = TestClient(prior)
    for observed in (client.get("/health").json(), prior_client.get("/health").json()):
        with pytest.raises(RuntimeError, match="point-service v1"):
            require_point_service(observed, hints.scope, operation="execute")
        assert store.get_task(retained.id) == retained
        assert store.dequeue_next() is None
    assert effects == []
    api, calls = _connected_api(client)
    legacy = api.reindex(source="git:unrelated")["task"]
    assert calls == [("POST", "/reindex")]
    dequeued = store.dequeue_next()
    assert dequeued is not None and dequeued.id == legacy["id"]
    assert store.get_task(retained.id) == retained
    assert retained.change_hints == hints and retained.point_claim_id


def test_required_point_schema_and_prior_receiver_refuse_before_effects(service: Service) -> None:
    from fastapi import Body
    from pydantic import BaseModel, ConfigDict

    from agent_index.indexing.point_hints import (
        PointBinding, PointChangeHints, WorkItemRevisionTarget,
    )
    from agent_index.reindex_intake import HintedReindexRequest

    client, store, notifications = service
    hints = PointChangeHints(
        source="ado:sample:points", canonical_family="ado:points", account_id="account-a",
        binding=PointBinding("https://example.test/project", "query", "context"),
        targets=(WorkItemRevisionTarget("42", 7, ("event",)),),
    )
    body = {"source": hints.source, "change_hints": hints.to_dict()}
    invalid: tuple[object, ...] = (None, True, False, "1", 1.0, 0, 2, [], {})
    for requirement in invalid:
        response = client.post("/reindex/hints", json={
            **body, "required_point_service_version": requirement,
        })
        assert response.status_code == 422
    assert client.post("/reindex/hints", json={
        "source": "git:sample", "change_hints": _hints().to_dict(),
        "required_point_service_version": 1,
    }).status_code == 422
    assert not store.get_queue() and not notifications
    assert HintedReindexRequest.model_validate(body).change_hints == hints

    class PriorRequest(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        source: str
        full: bool = False
        change_hints: dict[str, Any]

    prior = FastAPI()

    @prior.post("/reindex/hints")
    def retain(payload: dict[str, Any] = Body()) -> dict[str, Any]:
        # Exercise the prior strict DTO before its durable v2 retention path.
        from pydantic import ValidationError
        from fastapi import HTTPException

        try:
            accepted = PriorRequest.model_validate(payload)
        except ValidationError:
            raise HTTPException(status_code=422, detail="Invalid prior request") from None
        task = store.enqueue(
            source=accepted.source, change_hints=PointChangeHints.from_dict(accepted.change_hints),
        )
        notifications.append("prior")
        return {"accepted": True, "task": task.to_dict()}

    prior_client = TestClient(prior)
    assert prior_client.post("/reindex/hints", json={
        **body, "required_point_service_version": 1,
    }).status_code == 422
    assert not store.get_queue() and not notifications
    accepted = prior_client.post("/reindex/hints", json=body).json()
    assert accepted["accepted"] and notifications == ["prior"]
    reopened = TaskStore(store._db_path)
    retained = reopened.get_task(accepted["task"]["id"])
    assert retained is not None and retained.change_hints == hints
    assert reopened.dequeue_next() is None
