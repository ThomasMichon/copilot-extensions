"""Lossless direct metadata through the actual registration and source owners."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.pool_instances import PoolDefinitionError, PoolInstanceDefinition
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_reconcile import declaration_to_registration
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from agent_dispatch.supervisor_registration import _spec_fingerprint, build_command
from tests.test_managed_companion import Executor
from tests.test_supervisor_daemon import FakeCompanionController, FakeLauncher


def _decl() -> Any:
    return load_declaration({
        "name": "worker", "owner": "repo:example", "repos": "example",
        "labels": ["review"], "concurrency": 2, "priority": 17,
        "filters": {
            "permit": {"role": ["review"], "capabilities": ["git", "python"]},
            "reject": {"worktree": ["excluded"], "machine": ["host-b"]},
        },
    })


def _body() -> dict:
    registration = declaration_to_registration(_decl(), machine="host-a", env="isolated")
    definition = registration.pop("pool_definition")
    definition["registration_id"] = "direct-worker"
    registration["id"] = "direct-worker"
    registration["spec"]["pool_definition"] = definition
    return {key: registration[key] for key in ("id", "kind", "machine", "env", "spec")}


@pytest.fixture
def queue(tmp_path: Any) -> TaskQueue:
    return TaskQueue(tmp_path / "tasks.db")


def _api(queue: TaskQueue) -> TestClient:
    return TestClient(create_app(
        queue, token=None, control_token=None, enable_mcp=False, verification_interval=0,
    ))


def _client(api: TestClient, calls: list[tuple[str, str]]) -> DispatchClient:
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        response = api.request(
            request.method, str(request.url), content=request.content, headers=request.headers,
        )
        return httpx.Response(response.status_code, json=response.json())

    return DispatchClient("http://testserver", transport=httpx.MockTransport(handle))


def _register(client: DispatchClient, body: dict) -> dict:
    return client.register_registration(
        body["kind"], body["spec"], reg_id=body["id"], machine=body["machine"], env=body["env"],
    )


def _daemon(client: DispatchClient, **kwargs: Any) -> SupervisorDaemon:
    daemon = SupervisorDaemon(
        client, "host-a", env="isolated", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(), overrides_source=lambda: {}, **kwargs,
    )
    daemon._publication_executor = Executor()
    return daemon


def test_real_api_sqlite_collection_pause_and_resume_are_lossless(queue: TaskQueue) -> None:
    body = _body()
    calls: list[tuple[str, str]] = []
    with _api(queue) as api, _client(api, calls) as client:
        record = _register(client, body)
        assert calls == [("GET", "/health"), ("POST", "/registrations/pool-definition")]
        assert record["spec"] == body["spec"]
        assert asdict(queue.get_registration(body["id"])) == record
        assert asdict(TaskQueue(queue.db_path).get_registration(body["id"])) == record
        daemon = _daemon(client)
        try:
            assert daemon._desired() == {body["id"]: record}
            snapshot = daemon.pool_source_snapshot()
            assert snapshot.complete
            definition, = snapshot.definitions
            assert definition.to_dict() == body["spec"]["pool_definition"]
            assert definition.filters.permit["repo"] == frozenset({"example"})
            assert definition.filters.permit["task-type"] == frozenset({"worker", "review"})
            assert definition.filters.reject["machine"] == frozenset({"host-b"})
            assert definition.max_concurrent == 2 and definition.priority == 17
            assert definition.identity == ("direct-worker", "host-a", "isolated")
            client.set_registration_status(body["id"], "paused")
            assert daemon._desired() == {}
            assert daemon.pool_source_snapshot().complete
            assert daemon.pool_source_snapshot().definitions == ()
            client.set_registration_status(body["id"], "active")
            daemon._desired()
            assert daemon.pool_source_snapshot().definitions == snapshot.definitions
            assert queue.get_registration(body["id"]).spec == body["spec"]
        finally:
            daemon.shutdown()


@pytest.mark.parametrize("endpoint", ["/registrations", "/registrations/pool-definition"])
@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("id", None), ("machine", None), ("machine", "host-b"),
    ("env", "other"), ("env", ""),
])
def test_wrong_or_missing_scope_refuses_before_upsert(
    queue: TaskQueue, endpoint: str, field: str, value: object,
) -> None:
    body = _body()
    original = queue.register_registration(
        body["kind"], body["spec"], reg_id=body["id"], machine=body["machine"], env=body["env"],
    )
    revision = queue.pool_registration_revision()
    body[field] = value
    with _api(queue) as api:
        response = api.post(endpoint, json=body)
    assert response.status_code == 400, response.text
    assert queue.list_registrations() == [original]
    assert queue.pool_registration_revision() == revision


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("priority", None), ("priority", True),
    ("max_concurrent", 0), ("max_concurrent", 3),
    ("filters", {"permit": {"repo": ["example"]}}),
    ("filters", {"permit": {"unknown": ["value"]}, "reject": {}}),
])
def test_incomplete_or_conflicting_definition_never_creates_a_row(
    queue: TaskQueue, field: str, value: object,
) -> None:
    body = _body()
    body["spec"]["pool_definition"][field] = value
    with _api(queue) as api:
        response = api.post("/registrations/pool-definition", json=body)
    assert response.status_code == 400, response.text
    assert queue.list_registrations() == []
    assert queue.pool_registration_revision() == 0


@pytest.mark.parametrize("kind", ["emitter", "schedule", "evaluator"])
def test_nonworkers_cannot_carry_pool_definitions(queue: TaskQueue, kind: str) -> None:
    with pytest.raises(TaskError, match="only supervised worker lanes"):
        queue.register_registration(kind, _body()["spec"])
    assert queue.list_registrations() == []


def test_legacy_registration_stays_usable_but_cannot_qualify_collection(queue: TaskQueue) -> None:
    body = _body()
    body["spec"].pop("pool_definition")
    with _api(queue) as api, _client(api, []) as client:
        record = _register(client, body)
        daemon = _daemon(client)
        try:
            assert daemon._desired() == {body["id"]: record}
            snapshot = daemon.pool_source_snapshot()
            assert not snapshot.complete and not snapshot.definitions
            assert "effective-worker-definition-invalid" in snapshot.reasons
        finally:
            daemon.shutdown()
        assert api.post("/registrations/pool-definition", json=body).status_code == 400


@pytest.mark.parametrize("veto", ["direct-worker", "logical:repo:example:worker"])
def test_equivalent_definition_deduplicates_and_preserves_override_veto(
    queue: TaskQueue, veto: str,
) -> None:
    with _api(queue) as api, _client(api, []) as client:
        _register(client, _body())
        daemon = _daemon(client, declared_source=lambda: DeclarationRead((_decl(),), True))
        try:
            desired = daemon._desired()
            assert set(desired) == {"declared:repo:example:worker"}
            assert daemon._deduplicated == ["direct-worker"]
            assert daemon.pool_source_snapshot().complete
            daemon.overrides_source = lambda: {veto: {"disabled": True}}
            assert daemon._desired() == {}
            assert daemon.pool_source_snapshot().complete
            daemon.overrides_source = lambda: {}
            daemon.declared_source = lambda: DeclarationRead((), True)
            assert set(daemon._desired()) == {"direct-worker"}
            assert not daemon.pool_source_snapshot().complete
            assert "declaration-publication-unconfirmed" in daemon.pool_source_snapshot().reasons
            assert set(daemon._desired()) == {"direct-worker"}
            assert daemon.pool_source_snapshot().complete
            assert queue.get_registration("direct-worker").spec == _body()["spec"]
        finally:
            daemon.shutdown()


@pytest.mark.parametrize("difference", ["priority", "permit", "reject"])
def test_distinct_explicit_policy_is_not_erased_by_same_launch_flags(
    queue: TaskQueue, difference: str,
) -> None:
    body = _body()
    if difference == "priority":
        body["spec"]["pool_definition"]["priority"] += 1
    else:
        body["spec"]["pool_definition"]["filters"][difference]["role"] = ["other"]
    with _api(queue) as api, _client(api, []) as client:
        _register(client, body)
        daemon = _daemon(client, declared_source=lambda: DeclarationRead((_decl(),), True))
        try:
            assert set(daemon._desired()) == {"direct-worker", "declared:repo:example:worker"}
            assert not daemon._deduplicated
            assert daemon.pool_source_snapshot().complete
            assert len(daemon.pool_source_snapshot().definitions) == 2
        finally:
            daemon.shutdown()


def test_definition_only_updates_invalidate_catalog_without_changing_legacy_child(
    queue: TaskQueue,
) -> None:
    body = _body()
    first = asdict(queue.register_registration(
        body["kind"], body["spec"], reg_id=body["id"], machine=body["machine"], env=body["env"],
    ))
    revision = queue.pool_registration_revision()
    body["spec"]["pool_definition"]["priority"] += 1
    second = asdict(queue.register_registration(
        body["kind"], body["spec"], reg_id=body["id"], machine=body["machine"], env=body["env"],
    ))
    assert queue.pool_registration_revision() == revision + 1
    assert first["created_at"] == second["created_at"]
    assert _spec_fingerprint(first) == _spec_fingerprint(second)
    assert build_command(first, python="python") == build_command(second, python="python")
    legacy = copy.deepcopy(first)
    legacy["spec"].pop("pool_definition")
    assert _spec_fingerprint(first) == _spec_fingerprint(legacy)
    assert build_command(first, python="python") == build_command(legacy, python="python")


def test_conflicting_projected_and_stored_definitions_refuse() -> None:
    body = _body()
    body["pool_definition"] = copy.deepcopy(body["spec"]["pool_definition"])
    body["pool_definition"]["priority"] += 1
    with pytest.raises(PoolDefinitionError, match="stored and projected"):
        PoolInstanceDefinition.from_registration(body)


@pytest.mark.parametrize("versions", [None, [], [True], [2], "1"])
def test_unsupported_running_peer_refuses_only_the_new_write(versions: object) -> None:
    from fastapi import FastAPI

    legacy = FastAPI()
    writes: list[dict] = []

    @legacy.get("/health")
    def health() -> dict:
        return {"pool_catalog_transport": {
            "protocol": "pool_catalog_transport/v1", "registration_definition_versions": versions,
        }}

    @legacy.post("/registrations")
    def store(body: dict) -> dict:
        writes.append(body)
        return body

    with TestClient(legacy) as api, _client(api, []) as client:
        with pytest.raises(DispatchUpgradeRequired, match="lossless direct"):
            _register(client, _body())
        assert writes == []
        body = _body()
        body["spec"].pop("pool_definition")
        assert _register(client, body) == body
        assert writes == [body]


def test_provider_rollback_after_health_has_no_legacy_write_or_fallback() -> None:
    from fastapi import FastAPI

    legacy = FastAPI()
    writes: list[dict] = []
    calls: list[str] = []

    @legacy.post("/registrations")
    def store(body: dict) -> dict:
        writes.append(body)
        return body

    with TestClient(legacy) as api:
        def rollback(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/health":
                return httpx.Response(200, json={"pool_catalog_transport": {
                    "protocol": "pool_catalog_transport/v1", "registration_definition_versions": [1],
                }})
            response = api.request(request.method, str(request.url), content=request.content)
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient("http://testserver", transport=httpx.MockTransport(rollback)) as client:
            with pytest.raises(DispatchError):
                _register(client, _body())
    assert calls == ["/health", "/registrations/pool-definition"]
    assert writes == []


def test_old_persisted_partial_definition_blocks_the_entire_set(queue: TaskQueue) -> None:
    body = _body()
    queue.register_registration(
        body["kind"], body["spec"], reg_id=body["id"], machine=body["machine"], env=body["env"],
    )
    legacy = copy.deepcopy(body["spec"])
    legacy["pool_definition"]["filters"].pop("reject")
    # A previous permissive registration writer could retain arbitrary spec JSON.
    with queue._connect() as conn:
        conn.execute("UPDATE registrations SET spec = ? WHERE id = ?", (json.dumps(legacy), body["id"]))
    with _api(queue) as api, _client(api, []) as client:
        daemon = _daemon(client, declared_source=lambda: DeclarationRead((_decl(),), True))
        try:
            assert set(daemon._desired()) == {"direct-worker", "declared:repo:example:worker"}
            assert not daemon.pool_source_snapshot().complete
            assert daemon.pool_source_snapshot().definitions == ()
        finally:
            daemon.shutdown()


def test_additive_definition_data_survives_store_and_normalizes_for_collection(queue: TaskQueue) -> None:
    body = _body()
    body["spec"]["pool_definition"]["future"] = {"retained": [1, 2]}
    with _api(queue) as api, _client(api, []) as client:
        record = _register(client, body)
        assert record["spec"] == body["spec"]
        definition = PoolInstanceDefinition.from_registration(record)
        assert definition == PoolInstanceDefinition.from_registration(_body())


def test_upsert_preserves_pause_and_does_not_duplicate_capacity(queue: TaskQueue) -> None:
    with _api(queue) as api, _client(api, []) as client:
        first = _register(client, _body())
        client.set_registration_status(first["id"], "paused")
        revision = queue.pool_registration_revision()
        second = _register(client, _body())
        assert second["created_at"] == first["created_at"]
        assert second["status"] == "paused"
        assert len(queue.list_registrations()) == 1
        assert queue.pool_registration_revision() == revision


def test_new_registration_route_keeps_existing_api_and_task_authority_boundaries(
    queue: TaskQueue,
) -> None:
    from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
    from tests.test_pool_catalog_transport import _client as authenticated_api, _headers

    with authenticated_api(queue) as api:
        assert api.post("/registrations/pool-definition", json=_body()).status_code == 401
        scoped = mint_task_scope_token(
            "control", task_id="task-a", worker_id="worker-a", owner_session_id="session-a",
            epoch=0, generation=1,
        )
        response = api.post(
            "/registrations/pool-definition", json=_body(),
            headers={**_headers("data"), TASK_SCOPE_HEADER: scoped},
        )
        assert response.status_code == 403
        assert queue.list_registrations() == []
        assert api.post(
            "/registrations/pool-definition", json=_body(), headers=_headers("data"),
        ).status_code == 200


def test_existing_cli_spec_path_reaches_negotiated_registration(
    queue: TaskQueue, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervise_cli
    from agent_dispatch.__main__ import build_parser

    body = _body()
    args = build_parser().parse_args([
        "supervise", "register", "--id", body["id"], "--machine", body["machine"],
        "--env", body["env"], "--spec", json.dumps(body["spec"]),
    ])
    emitted: list[dict] = []
    with _api(queue) as api:
        calls: list[tuple[str, str]] = []
        monkeypatch.setattr(supervise_cli, "_client", lambda _args: _client(api, calls))
        monkeypatch.setattr(supervise_cli, "_emit", lambda record: emitted.append(record) or 0)
        assert supervise_cli._cmd_supervise_register(args) == 0
    assert calls == [("GET", "/health"), ("POST", "/registrations/pool-definition")]
    assert emitted[0]["spec"] == body["spec"]
    assert queue.get_registration(body["id"]).spec == body["spec"]
