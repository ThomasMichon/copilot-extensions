"""Source-owned projection through the ordinary server composition and ASGI."""

from __future__ import annotations

from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import server
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.config import Config
from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_cohort_preparation import PoolCohortPreparation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.registrar_discovery import RegistrarSources
from agent_dispatch.single_instance import SingleInstance
from tests.test_pool_catalog_transport import _headers
from tests.test_pool_cohort_preparation import _write


@pytest.fixture
def owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    base = tmp_path / "registrar"
    base.mkdir()
    _write(base)
    sources = RegistrarSources(base=base)
    route = {"active": True}
    monkeypatch.setattr(server, "RegistrarSources", lambda: sources)
    monkeypatch.setattr(server, "_owns_active_route", lambda: route["active"])
    monkeypatch.setattr(server.telemetry, "load_sink_from_config", lambda: True)
    app = server.build_app(Config(
        db_path=str(tmp_path / "queue.db"), token="data", control_token="control",
        sweep_interval=0,
    ))
    api = TestClient(app)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = api.request(
            request.method, str(request.url), headers=request.headers, content=request.content,
        )
        return httpx.Response(response.status_code, json=response.json())

    with DispatchClient(
        "http://coordinator", token="data", control_token="control",
        transport=httpx.MockTransport(handle),
    ) as client:
        yield SimpleNamespace(
            base=base, sources=sources, route=route, app=app, api=api, client=client,
            queue=app.state.queue, requests=requests,
            custody=PoolCohortPreparation(sources, client),
        )
    api.close()


def _body(request: Any) -> dict:
    return {"version": 1, **request.to_dict()}


def test_ordinary_composition_binds_actual_owner_without_automatic_projection(owner: Any) -> None:
    source = owner.app.state.pool_cohort_source
    assert source.sources is owner.sources and source.queue is owner.queue
    health = owner.client.health()
    assert health["pool_catalog_transport"]["configured_cohort_projection_owner"] == "coordinator-registrar"
    assert owner.client.pool_configured_cohort().revision == 0
    assert owner.client.pool_catalog_metadata()["configured_cohort"]["revision"] == 0
    assert source.sources.last_report is None
    with owner.queue._connect() as conn:
        for table in (
            "tasks", "spawn_reservations", "pool_publication_scopes", "pool_catalog_cohort_history",
        ):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_existing_client_holding_same_pointer_lock_does_not_deadlock(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = owner.custody.prepare()
    observe = owner.queue.observe_pool_configured_cohort
    checked = []

    def project(observation: Any, *, expected_revision: int) -> Any:
        contender = SingleInstance(owner.base / "pointers.lock")
        try:
            assert not contender.acquire()
            assert owner.app.state.drain_gate.claims == 1
            checked.append(True)
        finally:
            contender.release()
        return observe(observation, expected_revision=expected_revision)

    monkeypatch.setattr(owner.queue, "observe_pool_configured_cohort", project)
    result = owner.custody.project(request)
    assert checked == [True] and result.revision == 1
    assert owner.custody.pending is None
    assert owner.app.state.drain_gate.claims == 0


@pytest.mark.parametrize("already_projected", [False, True])
def test_another_hosts_equivalent_member_list_cannot_replace_coordinator_source(
    owner: Any, tmp_path: Path, already_projected: bool,
) -> None:
    if already_projected:
        owner.custody.project(owner.custody.prepare())
    base = tmp_path / "another-registrar"
    base.mkdir()
    path = _write(base)
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    other = RegistrarSources(base=base)
    assert other.configured_supervisor_cohort().members == owner.sources.configured_supervisor_cohort().members
    custody = PoolCohortPreparation(other, owner.client)
    request = custody.prepare()
    before = owner.queue.pool_catalog_snapshot()
    with pytest.raises(DispatchError, match="coordinator registrar source") as refused:
        custody.project(request)
    assert refused.value.status_code == 409
    assert custody.pending is request and custody.settled is None
    assert owner.queue.pool_catalog_snapshot() == before
    assert owner.app.state.drain_gate.claims == 0


@pytest.mark.parametrize("blocked", ["passive", "draining", "demoted-during-read"])
def test_fresh_projection_requires_current_active_owner_and_existing_drain_gate(
    owner: Any, monkeypatch: pytest.MonkeyPatch, blocked: str,
) -> None:
    request = owner.custody.prepare()
    if blocked == "passive":
        owner.route["active"] = False
    elif blocked == "draining":
        owner.app.state.drain_gate.set_draining(True)
    else:
        read = owner.sources.configured_supervisor_cohort

        def demote() -> Any:
            owner.route["active"] = False
            return read()

        monkeypatch.setattr(owner.sources, "configured_supervisor_cohort", demote)
    response = owner.api.post(
        "/pool-catalog/cohort/project", headers=_headers(), json=_body(request),
    )
    assert response.status_code == 409 and "active coordinator" in response.text
    assert owner.queue.pool_configured_cohort().revision == 0
    assert owner.app.state.drain_gate.claims == 0


@pytest.mark.parametrize("path", ["project", "recover"])
def test_accepted_original_recovers_after_demotion_source_loss_and_drain(
    owner: Any, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    request = owner.custody.prepare()
    accepted = owner.custody.project(request)
    owner.route["active"] = False
    owner.app.state.drain_gate.set_draining(True)
    monkeypatch.setattr(
        owner.sources, "configured_supervisor_cohort",
        lambda: pytest.fail("accepted history must not read current source"),
    )
    response = owner.api.post(
        f"/pool-catalog/cohort/{path}", headers=_headers(), json=_body(request),
    )
    assert response.status_code == 200 and response.json()["cohort"]["revision"] == accepted.revision
    assert owner.queue.pool_configured_cohort() == accepted
    assert owner.app.state.drain_gate.claims == 0


@pytest.mark.parametrize("change", ["source", "unreadable", "registrar-target", "queue-target"])
def test_changed_owner_inputs_refuse_without_new_projection(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, change: str,
) -> None:
    request = owner.custody.prepare()
    if change == "source":
        _write(owner.base, revision=1)
    elif change == "unreadable":
        (owner.base / "pointers.json").write_text("{bad", encoding="utf-8")
    elif change == "registrar-target":
        other = tmp_path / "other"
        other.mkdir()
        _write(other)
        owner.sources.base = other
    else:
        monkeypatch.setattr(owner.queue, "db_path", TaskQueue(tmp_path / "other.db").db_path)
    response = owner.api.post(
        "/pool-catalog/cohort/project", headers=_headers(), json=_body(request),
    )
    assert response.status_code == 409
    assert owner.queue.pool_configured_cohort().revision == 0
    assert owner.app.state.drain_gate.claims == 0


def test_inflight_projection_remains_in_existing_drain_accounting(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = owner.custody.prepare()
    observe = owner.queue.observe_pool_configured_cohort
    entered, finish = Event(), Event()
    gate = owner.app.state.drain_gate

    def held(observation: Any, *, expected_revision: int) -> Any:
        entered.set()
        assert finish.wait(5)
        return observe(observation, expected_revision=expected_revision)

    monkeypatch.setattr(owner.queue, "observe_pool_configured_cohort", held)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            owner.api.post, "/pool-catalog/cohort/project",
            headers=_headers(), json=_body(request),
        )
        try:
            assert entered.wait(5) and gate.claims == 1
            gate.set_draining(True)
            assert not gate.wait_for_claims(timeout=0.01, poll=0.01)
        finally:
            finish.set()
        assert future.result(timeout=5).status_code == 200
    assert gate.claims == 0 and gate.wait_for_claims(timeout=0, poll=0.01)
    _write(owner.base, revision=1)
    fresh = {
        "version": 1, "expected_revision": 1,
        "observation": asdict(owner.sources.configured_supervisor_cohort()),
    }
    response = owner.api.post(
        "/pool-catalog/cohort/project", headers=_headers(), json=fresh,
    )
    assert response.status_code == 409 and owner.queue.pool_configured_cohort().revision == 1


def test_source_change_after_commit_refuses_then_recovers_original_history(
    owner: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = owner.custody.prepare()
    observe = owner.queue.observe_pool_configured_cohort

    def change(observation: Any, *, expected_revision: int) -> Any:
        accepted = observe(observation, expected_revision=expected_revision)
        _write(owner.base, revision=1)
        return accepted

    monkeypatch.setattr(owner.queue, "observe_pool_configured_cohort", change)
    response = owner.api.post(
        "/pool-catalog/cohort/project", headers=_headers(), json=_body(request),
    )
    assert response.status_code == 409 and owner.queue.pool_configured_cohort().revision == 1
    accepted = owner.custody.recover()
    assert accepted.revision == 1 and owner.custody.pending is None
    assert owner.custody.settled == (request, accepted)
    with owner.queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_catalog_cohort_history").fetchone()[0] == 1


def test_explicit_embedding_retains_existing_caller_owned_projection(owner: Any) -> None:
    app = create_app(
        owner.queue, token="data", control_token="control", enable_mcp=False,
        verification_interval=0,
    )
    with TestClient(app) as api:
        assert api.get("/health", headers=_headers()).json()["pool_catalog_transport"][
            "configured_cohort_projection_owner"
        ] == "explicit-caller"
        observation = owner.sources.configured_supervisor_cohort()
        result = owner.client.observe_pool_configured_cohort(
            observation, expected_revision=0, boundary=nullcontext,
        )
        body = {"version": 1, "expected_revision": 0, "observation": {
            **asdict(observation),
            "source_revision": "a" * 64, "revision": 1,
        }}
        response = api.post("/pool-catalog/cohort/project", headers=_headers(), json=body)
        assert response.status_code == 409  # stale CAS is still enforced
        body["expected_revision"] = result.revision
        assert api.post("/pool-catalog/cohort/project", headers=_headers(), json=body).status_code == 200


def test_source_embedding_requires_actual_active_route_callback(owner: Any) -> None:
    with pytest.raises(ValueError, match="active-owner authority"):
        create_app(owner.queue, cohort_sources=owner.sources, enable_mcp=False)
