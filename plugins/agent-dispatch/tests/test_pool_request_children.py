"""A completed sender does not retire its unfinished source operations."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
import httpx

from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_source_snapshot import DeclarationRead
from tests.test_pool_metadata_sender import _sender
from tests.test_pool_publication_custody import _apply, owner as _owner_fixture
from tests.test_pool_source_collection import _declaration

owner = _owner_fixture


@pytest.mark.parametrize("operation", ["read", "declaration"])
def test_timed_out_capture_children_retain_original_client_and_block_discard(
    owner: Any, operation: str,
) -> None:
    daemon, queue, _calls = owner
    original = daemon.client
    original.base_url = "http://original.test"
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    entered, finish = Event(), Event()
    closed = []
    original.close = lambda: closed.append("original")
    replacement = SimpleNamespace(
        base_url="http://replacement.test", close=lambda: closed.append("replacement"),
    )
    daemon._client_factory = lambda: replacement
    if operation == "read":
        read = original.list_registrations

        def blocked_read(**kwargs: Any) -> Any:
            entered.set()
            assert finish.wait(3)
            return read(**kwargs)

        original.list_registrations = blocked_read
    else:
        publish = original.register_registration

        def blocked_publish(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            assert finish.wait(3)
            return publish(*args, **kwargs)

        original.register_registration = blocked_publish
        daemon._publication_executor = None
        daemon.declared_source = lambda: DeclarationRead((_declaration("new-worker"),), True)
    daemon._reconcile_call_budget = 0.1
    try:
        with pytest.raises((TimeoutError, PoolPublicationError)):
            daemon.send_pool_metadata_request()
        assert entered.wait(1)
        flight = daemon._pool_metadata_flight
        if flight is not None:
            with pytest.raises(PoolPublicationError):
                flight.future.result(timeout=1)
        binding = daemon._pool_metadata_binding
        children = {future for future in binding.children if not future.done()}
        assert children and children <= daemon._probe_inflight
        assert not binding.post_admitted and not sent
        daemon._reconcile_call_budget = 1
        assert daemon._reconnect() and not closed
        with pytest.raises(PoolPublicationError, match="source operations remain in progress"):
            daemon.discard_unaccepted_pool_metadata_request(request)
        assert daemon._pool_publisher_custody.pending is request and not closed
        finish.set()
        for future in children:
            future.result(timeout=1)
        daemon.discard_unaccepted_pool_metadata_request(request)
        assert closed == ["original"]
        assert daemon._pool_publisher_custody.pending is None
    finally:
        finish.set()


def test_queued_registry_child_uses_original_client_across_two_reconnects(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, _queue, _calls = owner
    original = daemon.client
    original.base_url = "http://original.test"
    daemon.prepare_pool_metadata_request()
    binding = daemon._pool_metadata_binding
    queued, dequeue, entered, finish = (Event() for _ in range(4))
    reads, closed = [], []
    read = original.list_registrations

    def blocked(target: str, **kwargs: Any) -> Any:
        reads.append(target)
        entered.set()
        assert finish.wait(3)
        return read(**kwargs)

    original.list_registrations = lambda **kwargs: blocked("original", **kwargs)
    original.close = lambda: closed.append("original")
    replacement = SimpleNamespace(
        base_url="http://replacement.test",
        list_registrations=lambda **kwargs: blocked("replacement", **kwargs),
        close=lambda: closed.append("replacement"),
    )
    latest = SimpleNamespace(base_url="http://latest.test", close=lambda: closed.append("latest"))

    def occupy() -> None:
        queued.set()
        assert dequeue.wait(3)

    with ThreadPoolExecutor(max_workers=1) as executor:
        blocker = executor.submit(occupy)
        try:
            assert queued.wait(1)
            monkeypatch.setattr(daemon, "_read_pool", lambda: executor)
            token = daemon._pool_send_binding.set(binding)
            deadline = daemon._pool_capture_deadline.set(time.monotonic() + 0.05)
            try:
                daemon._store_registrations()
            finally:
                daemon._pool_capture_deadline.reset(deadline)
                daemon._pool_send_binding.reset(token)
            assert not reads
            children = {future for future in binding.children if not future.done()}
            assert len(children) == 1 and children <= daemon._probe_inflight
            daemon._client_factory = lambda: replacement
            assert daemon._reconnect() and not closed
            dequeue.set()
            assert entered.wait(1)
            daemon._client_factory = lambda: latest
            assert daemon._reconnect()
            assert reads == ["original"] and closed == ["replacement"]
            finish.set()
            blocker.result(timeout=1)
            for future in children:
                future.result(timeout=1)
            daemon.discard_unaccepted_pool_metadata_request(binding.request)
            assert closed == ["replacement", "original"]
        finally:
            dequeue.set()
            finish.set()


def test_accepted_recovery_returns_result_while_prior_source_child_remains_active(
    owner: Any,
) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = daemon.client
    entered, finish = Event(), Event()
    closed = []
    original.close = lambda: closed.append("original")

    def unknown(_request: Any, *, boundary: Any) -> Any:
        with boundary():
            raise httpx.ReadError("synthetic original POST still pending at provider")

    original.send_pool_metadata = unknown
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    read = original.list_registrations

    def blocked(**kwargs: Any) -> Any:
        entered.set()
        assert finish.wait(3)
        return read(**kwargs)

    original.list_registrations = blocked
    daemon._reconcile_call_budget = 0.1
    try:
        with pytest.raises((TimeoutError, PoolPublicationError)):
            daemon.send_pool_metadata_request()
        assert entered.wait(1)
        flight = daemon._pool_metadata_flight
        if flight is not None:
            with pytest.raises(PoolPublicationError):
                flight.future.result(timeout=1)
        binding = daemon._pool_metadata_binding
        children = {future for future in binding.children if not future.done()}
        assert children and not closed
        # The original unknown provider operation commits after the caller times out.
        expected = _apply(queue, request)
        replacement = SimpleNamespace(close=lambda: closed.append("replacement"))
        daemon._client_factory = lambda: replacement
        daemon._reconcile_call_budget = 1
        assert daemon._reconnect()
        assert daemon.send_pool_metadata_request() == expected
        assert daemon._pool_publisher_custody.pending is None
        assert daemon._pool_publisher_custody.settled == (request, expected)
        assert daemon._pool_metadata_flight is None and daemon._pool_metadata_binding is binding
        assert not closed and any(not future.done() for future in children)
        with pytest.raises(PoolPublicationError, match="source operations remain in progress"):
            daemon._close_retained_pool_metadata_client()
        finish.set()
        for future in children:
            future.result(timeout=1)
        daemon._close_retained_pool_metadata_client()
        assert closed == ["original"]
    finally:
        finish.set()


def test_accepted_result_survives_logged_retained_client_close_failure(
    owner: Any, caplog: Any,
) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = daemon.client
    attempts = []

    def lost(_request: Any, *, boundary: Any) -> Any:
        with boundary():
            _apply(queue, _request)
            raise httpx.ReadError("synthetic original accepted reply lost")

    def close() -> None:
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("synthetic original transport close failed")

    original.send_pool_metadata = lost
    original.close = close
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    expected = queue.pool_catalog_snapshot().scopes[0]
    daemon._client_factory = lambda: SimpleNamespace(close=lambda: None)
    assert daemon._reconnect()
    assert daemon.send_pool_metadata_request() == expected
    assert daemon._pool_publisher_custody.settled == (request, expected)
    assert not daemon._pool_metadata_binding.closed and len(attempts) == 1
    assert "accepted pool metadata transport retirement deferred" in caplog.text
    daemon._close_retained_pool_metadata_client()
    assert daemon._pool_metadata_binding.closed and len(attempts) == 2
