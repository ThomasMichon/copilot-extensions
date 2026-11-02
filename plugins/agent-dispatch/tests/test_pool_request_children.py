"""A completed sender does not retire its unfinished source operations."""

from __future__ import annotations

from concurrent.futures import TimeoutError
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_source_snapshot import DeclarationRead
from tests.test_pool_metadata_sender import _sender
from tests.test_pool_publication_custody import owner as _owner_fixture
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
