"""Real HTTP metadata boundaries, with no production publisher or placement."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_pool_catalog import POOL_CATALOG_PROTOCOL
from agent_dispatch.queue import TaskQueue
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, mint_task_scope_token
from tests._helpers import TEST_REPO

BEGIN = {
    "version": 1, "machine": "host-a", "env": "default",
    "publisher_id": "incarnation-a", "expected_generation": 0,
}
DEFINITION = {
    "version": 1, "registration_id": "lane-a", "machine": "host-a", "env": "default",
    "priority": 8, "max_concurrent": 2,
    "filters": {"permit": {"repo": ["example"], "role": ["review"]},
                "reject": {"worktree": ["excluded"]}},
}


def _headers(credential: str = "control") -> dict[str, str]:
    scheme = "Bear" + "er "
    return {"Authorization": scheme + credential}


@pytest.fixture
def queue(tmp_path, monkeypatch) -> TaskQueue:
    queue = TaskQueue(tmp_path / "tasks.db")
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    return queue


def _client(queue: TaskQueue, *, token: str | None = "data",
            control: str | None = "control") -> TestClient:
    return TestClient(create_app(
        queue, token=token, control_token=control, enable_mcp=False, verification_interval=0,
    ))


def _begin(client: TestClient, **changes) -> dict:
    response = client.post("/pool-catalog/scopes/begin", headers=_headers(),
                           json={**BEGIN, **changes})
    assert response.status_code == 200, response.text
    return response.json()["scope"]


def _publication(queue: TaskQueue, scope: dict, **changes) -> dict:
    return {
        "version": 1, "machine": scope["machine"], "env": scope["env"],
        "publisher_id": scope["publisher_id"], "generation": scope["generation"],
        "expected_revision": scope["revision"],
        "registration_revision": queue.pool_registration_revision(),
        "input_revision": "inputs-1", "definitions": [copy.deepcopy(DEFINITION)], **changes,
    }


def _publish(client: TestClient, body: dict) -> dict:
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(), json=body)
    assert response.status_code == 200, response.text
    return response.json()["scope"]


@pytest.mark.parametrize("endpoint,method", [
    ("/pool-catalog", "GET"),
    ("/pool-catalog/scopes/begin", "POST"),
    ("/pool-catalog/scopes/publish", "POST"),
])
@pytest.mark.parametrize("credential", [None, "data", "wrong"])
def test_all_routes_refuse_ordinary_authority_before_metadata_mutation(
    queue, endpoint, method, credential,
) -> None:
    before = queue.pool_catalog_snapshot()
    response = _client(queue, token=None).request(
        method, endpoint, headers={} if credential is None else _headers(credential),
        json=BEGIN if method == "POST" else None,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "pool_catalog_control_forbidden"
    assert queue.pool_catalog_snapshot() == before


@pytest.mark.parametrize("control", [None, ""])
@pytest.mark.parametrize("endpoint,method", [
    ("/pool-catalog", "GET"),
    ("/pool-catalog/scopes/begin", "POST"),
    ("/pool-catalog/scopes/publish", "POST"),
])
def test_missing_control_authority_is_explicit_even_on_an_open_server(
    queue, control, endpoint, method,
) -> None:
    before = queue.pool_catalog_snapshot()
    client = _client(queue, token=None, control=control)
    response = client.request(method, endpoint, headers=_headers(),
                              json=BEGIN if method == "POST" else None)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "pool_catalog_control_unavailable"
    assert not client.get("/health").json()["pool_catalog_transport"]["control_authority_configured"]
    assert queue.pool_catalog_snapshot() == before


def test_task_scoped_authority_cannot_reach_catalog_even_with_control_bearer(queue) -> None:
    scoped = mint_task_scope_token(
        "control", task_id="task-a", worker_id="worker-a", owner_session_id="session-a",
        epoch=0, generation=1,
    )
    response = _client(queue).post(
        "/pool-catalog/scopes/begin",
        headers={**_headers(), TASK_SCOPE_HEADER: scoped}, json=BEGIN,
    )
    assert response.status_code == 403
    assert queue.pool_catalog_snapshot().scopes == ()


def test_running_health_advertises_only_the_registered_metadata_contract(queue) -> None:
    client = _client(queue)
    before = queue.pool_catalog_snapshot()
    contract = client.get("/health", headers=_headers("data")).json()["pool_catalog_transport"]
    assert contract == {
        "protocol": POOL_CATALOG_PROTOCOL, "supported_versions": [1], "definition_versions": [1],
        "control_authority_configured": True, "metadata_only": True,
        "lock_holder_verification": False, "configured_scope_membership": False,
        "atomic_placement": False,
    }
    assert queue.pool_catalog_snapshot() == before
    paths = {route.path for route in client.app.routes}
    assert {"/pool-catalog", "/pool-catalog/scopes/begin",
            "/pool-catalog/scopes/publish"} <= paths


def test_http_round_trip_preserves_all_filters_order_and_revision_attribution(queue) -> None:
    client = _client(queue)
    initial = client.get("/pool-catalog", headers=_headers()).json()
    assert initial["protocol"] == POOL_CATALOG_PROTOCOL
    assert not initial["metadata_ready"] and initial["registration_revision"] == 0
    scope = _begin(client)
    low = {**DEFINITION, "registration_id": "lane-low", "priority": -2}
    published = _publish(client, _publication(queue, scope, definitions=[low, DEFINITION]))
    catalog = client.get("/pool-catalog", headers=_headers()).json()
    assert catalog["metadata_ready"] and catalog["revision"] == 2
    assert catalog["scopes"] == [published]
    assert catalog["definitions"] == [DEFINITION, low]
    assert published["definitions_generation"] == 1 and published["definitions_revision"] == 1
    assert published["valid_until"] == 130.0


def test_lost_responses_retry_original_requests_without_renewal_or_new_history(
    queue, monkeypatch,
) -> None:
    client = _client(queue)
    scope = _begin(client)
    body = _publication(queue, scope)
    published = _publish(client, body)
    before = queue.pool_catalog_snapshot()
    monkeypatch.setattr(queue, "_now", lambda now=None: 129.0)
    assert _begin(client) == published
    assert _publish(client, body) == published
    assert queue.pool_catalog_snapshot() == before
    with queue._connect() as conn:
        assert conn.execute("SELECT count(*) FROM pool_publication_history").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM pool_publication_generations").fetchone()[0] == 1


def test_http_unavailability_retains_evidence_and_confirmed_empty_withdraws(queue) -> None:
    client = _client(queue)
    published = _publish(client, _publication(queue, _begin(client)))
    unavailable = _publish(client, _publication(
        queue, published, definitions=[], reason="override read unavailable", input_revision="inputs-2",
    ))
    assert unavailable["definitions"] == published["definitions"]
    assert unavailable["definitions_revision"] == published["revision"]
    assert not client.get("/pool-catalog", headers=_headers()).json()["metadata_ready"]
    empty = _publish(client, _publication(
        queue, unavailable, definitions=[], input_revision="inputs-3",
    ))
    assert empty["definitions"] == [] and empty["reason"] is None
    assert client.get("/pool-catalog", headers=_headers()).json()["metadata_ready"]


@pytest.mark.parametrize("changes", [
    {"generation": 2}, {"publisher_id": "foreign"}, {"expected_revision": 8},
    {"registration_revision": 1},
])
def test_http_publication_fences_refuse_changed_authority_and_inputs(queue, changes) -> None:
    client = _client(queue)
    body = _publication(queue, _begin(client), **changes)
    before = queue.pool_catalog_snapshot()
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(), json=body)
    assert response.status_code == 409
    assert response.json()["detail"]["operation"] == "publish"
    assert queue.pool_catalog_snapshot() == before


def test_http_successor_fences_old_publisher_and_preserves_original_definition_custody(
    queue,
) -> None:
    client = _client(queue)
    old = _publish(client, _publication(queue, _begin(client)))
    new = _begin(client, publisher_id="incarnation-b", expected_generation=1)
    assert new["generation"] == 2 and new["definitions"] == old["definitions"]
    assert new["definitions_generation"] == 1
    before = queue.pool_catalog_snapshot()
    late = client.post("/pool-catalog/scopes/publish", headers=_headers(),
                       json=_publication(queue, old, definitions=[], reason="late unavailable"))
    assert late.status_code == 409
    stale = client.post("/pool-catalog/scopes/begin", headers=_headers(), json=BEGIN)
    assert stale.status_code == 409
    assert queue.pool_catalog_snapshot() == before


def test_unavailable_or_expired_other_scope_blocks_the_unfiltered_global_view(
    queue, monkeypatch,
) -> None:
    client = _client(queue)
    _publish(client, _publication(queue, _begin(client)))
    _begin(client, machine="host-b", publisher_id="incarnation-b")
    catalog = client.get("/pool-catalog", headers=_headers()).json()
    assert not catalog["metadata_ready"] and len(catalog["scopes"]) == 2
    assert catalog["blockers"] == ["host-b/default: pending"]
    before = queue.pool_catalog_snapshot()
    assert client.get("/pool-catalog?machine=host-a", headers=_headers()).status_code == 400
    assert queue.pool_catalog_snapshot() == before
    monkeypatch.setattr(queue, "_now", lambda now=None: 130.0)
    expired = client.get("/pool-catalog", headers=_headers()).json()
    assert expired["blockers"] == ["host-a/default: publication expired", "host-b/default: pending"]
    assert expired["scopes"] == catalog["scopes"]


@pytest.mark.parametrize("value", [True, "1", 1.0, 0, 2])
def test_write_contract_version_is_required_and_never_coerced(queue, value) -> None:
    client = _client(queue)
    response = client.post("/pool-catalog/scopes/begin", headers=_headers(),
                           json={**BEGIN, "version": value})
    assert response.status_code == 422
    assert queue.pool_catalog_snapshot().scopes == ()
    scope = _begin(client)
    before = queue.pool_catalog_snapshot()
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(),
                           json=_publication(queue, scope, version=value))
    assert response.status_code == 422
    assert queue.pool_catalog_snapshot() == before


@pytest.mark.parametrize("changes", [
    {"expected_generation": True}, {"expected_generation": -1}, {"expected_generation": "0"},
    {"now": 200.0}, {"ttl": 99999}, {"available_slots": 2}, {"complete": True},
])
def test_write_requests_cannot_bypass_fences_clock_or_completeness(queue, changes) -> None:
    response = _client(queue).post("/pool-catalog/scopes/begin", headers=_headers(),
                                  json={**BEGIN, **changes})
    assert response.status_code == 422
    assert queue.pool_catalog_snapshot().scopes == ()


@pytest.mark.parametrize("changes", [
    {"generation": True}, {"expected_revision": "0"}, {"registration_revision": 0.0},
    {"input_revision": None}, {"now": 200.0}, {"ttl": 99999},
    {"preferred_pool": "lane-a"}, {"available_slots": 2}, {"complete": True},
])
def test_publication_envelope_rejects_coercion_and_extra_authority(queue, changes) -> None:
    client = _client(queue)
    scope = _begin(client)
    before = queue.pool_catalog_snapshot()
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(),
                           json=_publication(queue, scope, **changes))
    assert response.status_code == 422
    assert queue.pool_catalog_snapshot() == before


@pytest.mark.parametrize("field", [
    "version", "generation", "expected_revision", "registration_revision",
    "input_revision", "definitions",
])
def test_required_publication_semantics_cannot_be_omitted(queue, field) -> None:
    client = _client(queue)
    body = _publication(queue, _begin(client))
    del body[field]
    before = queue.pool_catalog_snapshot()
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(), json=body)
    assert response.status_code == 422
    assert queue.pool_catalog_snapshot() == before


@pytest.mark.parametrize("definitions,reason,status", [
    ([DEFINITION, {**DEFINITION, "registration_id": "lane-b", "version": 2}], None, 400),
    ([{**DEFINITION, "filters": {"permit": {}}}], None, 400),
    ([{**DEFINITION, "max_concurrent": True}], None, 400),
    ([{**DEFINITION, "machine": "host-b"}], None, 409),
    ([DEFINITION, DEFINITION], None, 400),
    ([DEFINITION], "unavailable", 409),
])
def test_bad_or_partial_definitions_never_publish_a_valid_subset(
    queue, definitions, reason, status,
) -> None:
    client = _client(queue)
    scope = _begin(client)
    before = queue.pool_catalog_snapshot()
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(),
                           json=_publication(queue, scope, definitions=definitions, reason=reason))
    assert response.status_code == status
    assert queue.pool_catalog_snapshot() == before


def test_additive_definition_reader_preserves_documented_semantics(queue) -> None:
    client = _client(queue)
    published = _publish(client, _publication(
        queue, _begin(client), definitions=[{**DEFINITION, "future_note": "ignored"}],
    ))
    assert published["definitions"] == [DEFINITION]


def test_legacy_registration_contract_and_task_capacity_are_not_migrated(queue) -> None:
    client = _client(queue)
    body = {"kind": "supervised-lane", "id": "legacy", "machine": "host-a",
            "spec": {"repo": TEST_REPO, "max_concurrent": 1, "command": ["echo", "hello"]}}
    response = client.post("/registrations", headers=_headers("data"), json=body)
    assert response.status_code == 200, response.text
    assert response.json()["spec"] == body["spec"]
    assert "pool_definition" not in response.json()
    assert client.get("/registrations", headers=_headers("data")).status_code == 200
    assert queue.pool_catalog_snapshot().scopes == ()
    with queue._connect() as conn:
        for table in ("tasks", "spawn_reservations"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_intervening_legacy_registry_write_invalidates_http_collection(queue) -> None:
    client = _client(queue)
    body = _publication(queue, _begin(client))
    response = client.post(
        "/registrations", headers=_headers("data"),
        json={"kind": "supervised-lane", "id": "legacy", "machine": "host-a",
              "spec": {"repo": TEST_REPO, "max_concurrent": 1}},
    )
    assert response.status_code == 200
    before = queue.pool_catalog_snapshot()
    assert before.registration_revision == body["registration_revision"] + 1
    response = client.post("/pool-catalog/scopes/publish", headers=_headers(), json=body)
    assert response.status_code == 409
    assert "registration inputs changed" in response.json()["detail"]["reason"]
    assert queue.pool_catalog_snapshot() == before


def test_transport_rejections_use_the_existing_event_bus_without_credentials(
    queue, monkeypatch,
) -> None:
    client = _client(queue, token=None)
    observed: list[dict] = []
    monkeypatch.setattr(client.app.state.bus, "publish", observed.append)
    response = client.post("/pool-catalog/scopes/begin", headers=_headers("wrong"), json=BEGIN)
    assert response.status_code == 403
    assert observed == [{
        "type": "pool_catalog.rejected", "code": "pool_catalog_control_forbidden",
        "operation": "pool-catalog", "reason": "invalid_control_authority", "retryable": False,
    }]
    _begin(client)
    response = client.post("/pool-catalog/scopes/begin", headers=_headers(),
                           json={**BEGIN, "publisher_id": "successor"})
    assert response.status_code == 409
    assert observed[-1]["operation"] == "begin"
    assert "control" not in str(observed[-1])


def test_header_construction_is_not_a_masked_placeholder() -> None:
    assert _headers("synthetic-credential")["Authorization"].split() == [
        "Bear" + "er", "synthetic-credential",
    ]
