"""Queue-bound continuation through actual metadata clients and SQLite fences."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Any

import httpx
import pytest

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator_pool_catalog import _catalog_payload
from agent_dispatch.pool_publication_custody import PoolCatalogRead, PoolPublicationError
from agent_dispatch.queue import TaskQueue
from tests.test_pool_catalog_transport import BEGIN, _client, _headers, _publication
from tests.test_pool_request_targets import wire_owner as _wire_owner_fixture
from tests.test_pool_publication_custody import owner as _owner_fixture

wire_owner = _wire_owner_fixture
owner = _owner_fixture


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_retained_unaccepted_request_resumes_same_queue_on_new_endpoint(
    wire_owner: Any, operation: str,
) -> None:
    daemon, queue, original, replacement, paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()
    body = request.to_dict()
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect() and not original._http.is_closed
    paths.clear()
    result = daemon.send_pool_metadata_request()
    assert result == queue.pool_catalog_snapshot().scopes[0]
    assert daemon._pool_publisher_custody.settled == (request, result)
    assert request.to_dict() == body
    assert all(host == "replacement.test" for host, _method, _path in paths)
    assert [path for _host, method, path in paths if method == "POST"] == [
        f"/pool-catalog/scopes/{operation}",
    ]
    assert original._http.is_closed and not replacement._http.is_closed
    assert not daemon._pool_metadata_binding.retained_clients


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("stage", ["pending", "settled"])
def test_matching_positions_at_another_queue_never_rebase_publisher(
    wire_owner: Any, tmp_path: Any, operation: str, stage: str,
) -> None:
    daemon, queue, original, replacement, paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()
    if stage == "settled":
        daemon.send_pool_metadata_request()
    body = request.to_dict()
    foreign = TaskQueue(tmp_path / "other.db")
    # Copy only the lookalike metadata; the durable queue identity is distinct.
    with queue._connect() as source, foreign._connect() as target:
        for table in (
            "pool_catalog_clock", "pool_publication_scopes", "pool_publication_generations",
            "pool_publication_history",
        ):
            target.execute(f"DELETE FROM {table}")
            for row in source.execute(f"SELECT * FROM {table}"):
                target.execute(
                    f"INSERT INTO {table} VALUES ({','.join('?' for _ in row)})", tuple(row),
                )
    before = foreign.pool_catalog_snapshot()
    assert before.scopes == queue.pool_catalog_snapshot().scopes
    assert before.queue_identity != request.expected_queue_identity
    read = replacement.pool_catalog_metadata
    replacement.pool_catalog_metadata = lambda: _catalog_payload(foreign.pool_catalog_snapshot())
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    paths.clear()
    with pytest.raises(PoolPublicationError, match="identity|target changed"):
        if stage == "pending":
            daemon.send_pool_metadata_request()
        else:
            daemon.prepare_pool_metadata_request()
    assert foreign.pool_catalog_snapshot() == before
    assert not any(method == "POST" for _host, method, _path in paths)
    assert request.to_dict() == body
    if stage == "pending":
        assert daemon._pool_publisher_custody.pending is request
        assert daemon._pool_metadata_binding.client is original
        assert not original._http.is_closed
        assert replacement in daemon._pool_metadata_binding.retained_clients
        replacement.pool_catalog_metadata = read
        result = daemon.send_pool_metadata_request()
        assert daemon._pool_publisher_custody.settled == (request, result)
        assert original._http.is_closed


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("accepted", [False, True])
def test_provider_identity_fence_precedes_write_and_idempotent_recovery(
    tmp_path: Any, operation: str, accepted: bool,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    with _client(queue) as api:
        identity = queue.pool_catalog_snapshot().queue_identity
        body = {**BEGIN, "expected_queue_identity": identity}
        if operation == "publish":
            response = api.post("/pool-catalog/scopes/begin", headers=_headers(), json=body)
            assert response.status_code == 200
            body = _publication(
                queue, response.json()["scope"], expected_queue_identity=identity,
            )
        path = f"/pool-catalog/scopes/{operation}"
        if accepted:
            assert api.post(path, headers=_headers(), json=body).status_code == 200
        with queue._connect() as conn:
            conn.execute("UPDATE workspace_queue_identity SET identity = 'replacement'")
        before = queue.pool_catalog_snapshot()
        response = api.post(path, headers=_headers(), json=body)
        assert response.status_code == 409
        assert "queue identity changed" in response.json()["detail"]["reason"]
        assert queue.pool_catalog_snapshot() == before


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_actual_post_rerouted_after_reads_cannot_mutate_another_queue(
    wire_owner: Any, tmp_path: Any, operation: str,
) -> None:
    daemon, queue, _original, _replacement, _paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()
    foreign = TaskQueue(tmp_path / "other.db")
    identity = foreign.pool_catalog_snapshot().queue_identity

    def reroute(wire: httpx.Request) -> None:
        if wire.method == "POST":
            # Model a same-URL route replacement after the final GET.
            with queue._connect() as conn:
                conn.execute("UPDATE workspace_queue_identity SET identity = ?", (identity,))

    gate["before"] = reroute
    with pytest.raises(DispatchError) as caught:
        daemon.send_pool_metadata_request()
    assert caught.value.is_pool_catalog_rejection(operation)
    assert queue.pool_catalog_snapshot().scopes == request.inputs.catalog.scopes
    assert daemon._pool_publisher_custody.pending is request


@pytest.mark.parametrize("advertised", [None, [], [True], ["1"], [2]])
def test_identity_pinned_client_refuses_rollback_before_post(
    wire_owner: Any, advertised: Any,
) -> None:
    daemon, _queue, original, _replacement, paths, _gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    health = original.health

    def rolled_back() -> dict:
        value = health()
        contract = value["pool_catalog_transport"]
        if advertised is None:
            contract.pop("queue_identity_fencing_versions")
        else:
            contract["queue_identity_fencing_versions"] = advertised
        return value

    original.health = rolled_back
    with pytest.raises(DispatchUpgradeRequired, match="queue identity"):
        daemon.send_pool_metadata_request()
    assert not any(method == "POST" for _host, method, _path in paths)
    assert daemon._pool_publisher_custody.pending is request
    assert not daemon._pool_metadata_binding.post_admitted


def test_unfinished_source_child_stays_on_original_transport(wire_owner: Any) -> None:
    daemon, _queue, original, replacement, paths, _gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    child: Future[None] = Future()
    daemon._pool_metadata_binding.children.add(child)
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    paths.clear()
    try:
        with pytest.raises(PoolPublicationError, match="client changed before POST"):
            daemon.send_pool_metadata_request()
        assert daemon._pool_metadata_binding.client is original
        assert not original._http.is_closed
        assert not any(host == "replacement.test" for host, _method, _path in paths)
        assert daemon._pool_publisher_custody.pending is request
    finally:
        child.set_result(None)
    daemon.send_pool_metadata_request()
    assert original._http.is_closed


@pytest.mark.parametrize("identity", [None, "", " ", " padded ", 1, True])
def test_present_invalid_queue_identity_is_not_legacy_absence(identity: Any) -> None:
    with pytest.raises(PoolPublicationError, match="identity"):
        PoolCatalogRead.from_dict({
            "protocol": "pool_catalog_transport/v1", "version": 1,
            "registration_revision": 0, "scopes": [], "queue_identity": identity,
        })


def test_legacy_absence_keeps_old_request_body_and_refuses_endpoint_change(
    wire_owner: Any,
) -> None:
    daemon, _queue, original, replacement, paths, gate = wire_owner
    read = original.pool_catalog_metadata

    def legacy() -> dict:
        value = read()
        value.pop("queue_identity")
        return value

    original.pool_catalog_metadata = legacy
    request = daemon.prepare_pool_metadata_request()
    assert "expected_queue_identity" not in request.to_dict()
    daemon.send_pool_metadata_request()
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    paths.clear()
    with pytest.raises(PoolPublicationError, match="target changed after settlement"):
        daemon.prepare_pool_metadata_request()
    assert not any(method == "POST" for _host, method, _path in paths)


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_same_endpoint_replacement_cannot_settle_lookalike_result(
    wire_owner: Any, operation: str,
) -> None:
    daemon, queue, original, _replacement, _paths, gate = wire_owner
    if operation == "publish":
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
    request = daemon.prepare_pool_metadata_request()

    def lose(wire: httpx.Request, response: Any) -> None:
        if wire.method == "POST":
            assert response.status_code == 200
            raise httpx.ReadError("synthetic original accepted reply lost")

    gate["after"] = lose
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    gate.clear()
    with queue._connect() as conn:
        conn.execute("UPDATE workspace_queue_identity SET identity = 'other-queue'")
    before = queue.pool_catalog_snapshot()
    with pytest.raises(PoolPublicationError, match="queue identity changed"):
        daemon.send_pool_metadata_request()
    assert queue.pool_catalog_snapshot() == before
    assert daemon._pool_publisher_custody.pending is request
    assert not original._http.is_closed


def test_reconnect_during_replacement_read_retains_both_transports_until_recovery(
    wire_owner: Any,
) -> None:
    daemon, _queue, original, replacement, paths, gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    entered, finish = Event(), Event()
    read = replacement.pool_catalog_metadata

    def blocked() -> dict:
        entered.set()
        assert finish.wait(3)
        return read()

    replacement.pool_catalog_metadata = blocked
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    with DispatchClient(
        "http://latest.test", token="data", control_token="control",
        transport=httpx.MockTransport(replacement._http._transport.handler),
    ) as latest, ThreadPoolExecutor(max_workers=1) as executor:
        sending = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert entered.wait(1)
            daemon._client_factory = lambda: latest
            assert daemon._reconnect()
            assert not original._http.is_closed and not replacement._http.is_closed
            finish.set()
            with pytest.raises(PoolPublicationError, match="changed during continuation"):
                sending.result(timeout=1)
            assert daemon._pool_publisher_custody.pending is request
            daemon.send_pool_metadata_request()
            assert original._http.is_closed and replacement._http.is_closed
            assert not latest._http.is_closed
            assert not daemon._pool_metadata_binding.retained_clients
            assert [host for host, method, _path in paths if method == "POST"] == ["latest.test"]
        finally:
            finish.set()


def test_existing_queue_identity_survives_reopen_without_another_registry(tmp_path: Any) -> None:
    path = tmp_path / "queue.db"
    first = TaskQueue(path).pool_catalog_snapshot().queue_identity
    assert TaskQueue(path).pool_catalog_snapshot().queue_identity == first
    assert TaskQueue(tmp_path / "different.db").pool_catalog_snapshot().queue_identity != first


def test_settled_foreign_queue_is_rejected_before_declaration_collection(
    wire_owner: Any, tmp_path: Any,
) -> None:
    daemon, _queue, _original, replacement, paths, _gate = wire_owner
    daemon.prepare_pool_metadata_request()
    daemon.send_pool_metadata_request()
    foreign = TaskQueue(tmp_path / "foreign.db")
    replacement.pool_catalog_metadata = lambda: _catalog_payload(foreign.pool_catalog_snapshot())
    collected = []

    def declaration() -> Any:
        collected.append(True)
        pytest.fail("foreign queue must be rejected before declaration collection")

    daemon.declared_source = declaration
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    paths.clear()
    with pytest.raises(PoolPublicationError, match="identity"):
        daemon.prepare_pool_metadata_request()
    assert not paths and not collected
    assert foreign.list_registrations() == []
    assert daemon._pool_publisher_custody.pending is None


def test_same_queue_continuation_retains_failed_transport_cleanup_for_next_preparation(
    wire_owner: Any,
) -> None:
    daemon, _queue, original, replacement, _paths, _gate = wire_owner
    request = daemon.prepare_pool_metadata_request()
    close = original.close
    attempts = []

    def failing() -> None:
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("synthetic original transport close failed")
        close()

    original.close = failing
    daemon._client_factory = lambda: replacement
    assert daemon._reconnect()
    result = daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.settled == (request, result)
    assert not original._http.is_closed
    assert daemon._pool_metadata_binding.retained_clients == [original]
    daemon.prepare_pool_metadata_request()
    assert original._http.is_closed and len(attempts) == 2
    assert not daemon._pool_metadata_binding.retained_clients


def test_repeated_rejected_candidates_do_not_accumulate_idle_transports(
    wire_owner: Any, tmp_path: Any,
) -> None:
    daemon, _queue, original, replacement, _paths, _gate = wire_owner
    daemon.prepare_pool_metadata_request()
    foreign = TaskQueue(tmp_path / "foreign.db")
    clients = []
    try:
        for index in range(4):
            candidate = DispatchClient(
                f"http://candidate-{index}.test", token="data", control_token="control",
                transport=httpx.MockTransport(replacement._http._transport.handler),
            )
            clients.append(candidate)
            candidate.pool_catalog_metadata = lambda: _catalog_payload(foreign.pool_catalog_snapshot())
            daemon._client_factory = lambda: candidate
            assert daemon._reconnect()
            with pytest.raises(PoolPublicationError, match="identity"):
                daemon.send_pool_metadata_request()
            assert daemon._pool_metadata_binding.client is original
            assert daemon._pool_metadata_binding.retained_clients == [candidate]
            assert all(prior._http.is_closed for prior in clients[:-1])
            assert not original._http.is_closed and not candidate._http.is_closed
        daemon._client_factory = lambda: replacement
        assert daemon._reconnect()
        daemon.send_pool_metadata_request()
        assert original._http.is_closed
        assert all(candidate._http.is_closed for candidate in clients)
        assert not daemon._pool_metadata_binding.retained_clients
    finally:
        for candidate in clients:
            candidate.close()


def test_timed_out_before_read_cannot_publish_declarations_after_foreign_reconnect(
    wire_owner: Any, tmp_path: Any,
) -> None:
    daemon, _queue, original, _replacement, _paths, _gate = wire_owner
    daemon.prepare_pool_metadata_request()
    daemon.send_pool_metadata_request()
    entered, finish = Event(), Event()
    read = original.pool_catalog_metadata

    def blocked() -> dict:
        observed = read()
        entered.set()
        assert finish.wait(3)
        return observed

    original.pool_catalog_metadata = blocked
    daemon._reconcile_call_budget = 0.05
    unavailable = daemon.capture_pool_publication_inputs()
    assert entered.is_set() and not unavailable.coherent
    future = daemon._discovery_futures["pool-catalog-before"]
    assert not future.done()
    foreign = TaskQueue(tmp_path / "foreign.db")
    requests = []
    with _client(foreign) as api:
        def handle(wire: httpx.Request) -> httpx.Response:
            requests.append((wire.method, wire.url.path))
            response = api.request(
                wire.method, str(wire.url), headers=wire.headers, content=wire.content,
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://foreign.test", token="data", control_token="control",
            transport=httpx.MockTransport(handle),
        ) as replacement:
            try:
                daemon._client_factory = lambda: replacement
                assert daemon._reconnect()
                finish.set()
                assert future.result(timeout=1)["queue_identity"] != foreign.pool_catalog_snapshot().queue_identity
                collected = []

                def declarations() -> Any:
                    collected.append(True)
                    pytest.fail("stale queue identity must not permit declaration collection")

                daemon.declared_source = declarations
                daemon._reconcile_call_budget = 1
                with pytest.raises(PoolPublicationError):
                    daemon.prepare_pool_metadata_request()
                assert daemon._source_observations["pool-catalog-before"].state == "delayed"
                assert not collected and not requests and foreign.list_registrations() == []
                assert daemon._pool_publisher_custody.pending is None
                # Once the delayed read is consumed, the actual foreign read also refuses.
                with pytest.raises(PoolPublicationError):
                    daemon.prepare_pool_metadata_request()
                assert not collected and foreign.list_registrations() == []
                assert not any(method == "POST" for method, _path in requests)
            finally:
                finish.set()
