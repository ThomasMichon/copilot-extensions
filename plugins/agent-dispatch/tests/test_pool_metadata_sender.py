"""Original-request metadata transport with synthetic sources and real fences."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from threading import Event, Lock, get_ident
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch.client import DispatchClient, DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_pool_catalog import _scope_payload, pool_catalog_contract
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_publication_response import read_pool_metadata_result
from agent_dispatch.pool_source_snapshot import DeclarationRead
from agent_dispatch.registrations import RegistrationKind
from agent_dispatch.single_instance import SingletonAdmissionError
from tests.test_pool_publication_custody import _apply, _settle, owner as _owner_fixture
from tests.test_pool_source_collection import _declaration

owner = _owner_fixture


def _sender(daemon: Any, queue: Any) -> list[dict]:
    sent: list[dict] = []

    def send(request: Any, *, boundary: Any) -> Any:
        with boundary():
            sent.append(request.to_dict())
            return _apply(queue, request)

    daemon.client.send_pool_metadata = send
    return sent


def test_explicit_send_begin_publish_and_unavailable_preserve_all_attribution(owner: Any) -> None:
    daemon, queue, _calls = owner
    sent = _sender(daemon, queue)
    begin = daemon.prepare_pool_metadata_request()
    assert not sent and not queue.pool_catalog_snapshot().scopes
    enrolled = daemon.send_pool_metadata_request()
    assert enrolled.publisher_id == begin.inputs.publisher_id
    assert enrolled.state == "pending" and enrolled.revision == 0
    publish = daemon.prepare_pool_metadata_request()
    published = daemon.send_pool_metadata_request()
    assert published.definitions == publish.inputs.snapshot.definitions
    daemon.declared_source = lambda: DeclarationRead((), False, "source-scan-unavailable")
    unavailable = daemon.prepare_pool_metadata_request()
    result = daemon.send_pool_metadata_request()
    assert result.state == "unavailable" and unavailable.reason
    assert sent == [begin.to_dict(), publish.to_dict(), unavailable.to_dict()]
    assert sent[-1]["definitions"] == []
    assert result.definitions == published.definitions
    assert result.definitions_revision == published.definitions_revision
    assert result.definitions_generation == published.definitions_generation
    assert not queue.pool_catalog_snapshot().ready
    assert daemon._pool_publisher_custody.settled == (unavailable, result)


def test_send_does_not_prepare_or_replace_a_request(owner: Any) -> None:
    daemon, _queue, calls = owner
    with pytest.raises(PoolPublicationError, match="original prepared"):
        daemon.send_pool_metadata_request()
    assert not calls and daemon._pool_metadata_flight is None


@pytest.mark.parametrize("operation", ["begin", "publish"])
def test_lost_accepted_reply_recovers_original_even_after_inputs_and_lock_change(
    owner: Any, operation: str,
) -> None:
    daemon, queue, calls = owner
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    writes = []

    def lost(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            _apply(queue, request)
            raise httpx.ReadError("synthetic reply lost")

    daemon.client.send_pool_metadata = lost
    with pytest.raises(httpx.ReadError):
        daemon.send_pool_metadata_request()
    first = queue.pool_catalog_snapshot().scopes[0]
    daemon.client.set_regs([])
    daemon.release_singleton()
    assert daemon.acquire_singleton()
    calls_before = len(calls)
    recovered = daemon.send_pool_metadata_request()
    assert recovered == first and len(calls) == calls_before + 1
    assert writes == [original] and request.to_dict() == original
    assert daemon._pool_publisher_custody.settled == (request, recovered)
    assert recovered.valid_until == first.valid_until
    with pytest.raises(PoolPublicationError, match="unsettled"):
        daemon.prepare_pool_metadata_request()


def test_unaccepted_reply_retries_same_request_only_under_original_fences(owner: Any) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    tries = 0
    writes = []

    def retry(request: Any, *, boundary: Any) -> Any:
        nonlocal tries
        tries += 1
        with boundary():
            writes.append(request.to_dict())
            if tries == 1:
                raise httpx.ConnectError("synthetic transport unavailable")
            return _apply(queue, request)

    daemon.client.send_pool_metadata = retry
    with pytest.raises(httpx.ConnectError):
        daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is request
    result = daemon.send_pool_metadata_request()
    assert writes == [original, original] and result.generation == 1


def test_wrong_completed_ack_retains_request_then_recovers_actual_original(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    writes = []

    def wrong(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            return replace(_apply(queue, request), input_revision="wrong-reply")

    daemon.client.send_pool_metadata = wrong
    with pytest.raises(PoolPublicationError, match="original request fences"):
        daemon.send_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is request
    assert daemon._pool_metadata_flight is None
    result = daemon.send_pool_metadata_request()
    assert result.input_revision == request.input_revision
    assert writes == [request.to_dict()]


@pytest.mark.parametrize("change", ["declaration", "override", "registration", "publisher", "release"])
def test_original_fences_rechecked_after_transport_preflight_before_post(owner: Any, change: str) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    original = request.to_dict()
    writes = []

    def send(request: Any, *, boundary: Any) -> Any:
        if change == "declaration":
            daemon.declared_source = lambda: DeclarationRead((_declaration(priority=4),), True)
        elif change == "override":
            daemon.overrides_source = lambda: {"logical:repo:example:worker": {"disabled": True}}
        elif change == "registration":
            queue.register_registration(
                RegistrationKind.SUPERVISED_LANE, {"all_repos": True}, reg_id="changed",
            )
        elif change == "publisher":
            queue.begin_pool_publication("host-a", "default", "successor", expected_generation=0)
        else:
            daemon.release_singleton()
        with boundary():
            writes.append(request.to_dict())
            return _apply(queue, request)

    daemon.client.send_pool_metadata = send
    with pytest.raises((PoolPublicationError, SingletonAdmissionError)):
        daemon.send_pool_metadata_request()
    assert writes == [] and daemon._pool_publisher_custody.pending is request
    assert request.to_dict() == original


def test_timeout_before_effect_keeps_future_and_expired_worker_cannot_send(owner: Any) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish = Event(), Event()
    read = daemon.client.pool_catalog_metadata

    def slow() -> dict:
        started.set()
        assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 0.03
    try:
        with pytest.raises(TimeoutError):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        assert flight.future in daemon._probe_inflight and not flight.future.done()
        daemon.release_singleton()
        finish.set()
        with pytest.raises(SingletonAdmissionError):
            flight.future.result(timeout=1)
        assert not sent and daemon._pool_publisher_custody.pending is request
        assert not queue.pool_catalog_snapshot().scopes
    finally:
        finish.set()


def test_pending_post_survives_shutdown_and_late_result_only_settles_custody(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon

    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    started, finish = Event(), Event()
    writes = []

    def slow(request: Any, *, boundary: Any) -> Any:
        with boundary():
            writes.append(request.to_dict())
            result = _apply(queue, request)
            started.set()
            assert finish.wait(2)
            return result

    daemon.client.send_pool_metadata = slow
    daemon._reconcile_call_budget = 0.05
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    try:
        with pytest.raises((TimeoutError, PoolPublicationError)):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        assert not daemon.shutdown()
        assert flight.future in daemon._probe_inflight
        assert daemon._retired_bounded_executors
        assert daemon._pool_publisher_custody.pending is request
        finish.set()
        result = flight.future.result(timeout=1)
        daemon.release_singleton()
        calls_before = len(calls)
        assert daemon.send_pool_metadata_request() == result
        assert len(calls) == calls_before and len(writes) == 1
        assert daemon._pool_publisher_custody.pending is None
        assert daemon._lock.acquisition is None
        with pytest.raises(SingletonAdmissionError):
            daemon.prepare_pool_metadata_request()
    finally:
        finish.set()


def test_shutdown_fences_delayed_preflight_even_with_original_lock_still_held(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon

    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish = Event(), Event()
    read = daemon.client.pool_catalog_metadata

    def slow() -> dict:
        started.set()
        assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 1
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert started.wait(1)
            assert not daemon.shutdown()
            assert daemon._pool_metadata_closed.is_set()
            finish.set()
            with pytest.raises(PoolPublicationError, match="admission is closed"):
                future.result(timeout=1)
            assert not sent and daemon._pool_publisher_custody.pending is request
            assert daemon._lock.acquisition == request.inputs.publisher_id
        finally:
            finish.set()


def test_two_callers_share_one_original_future_without_duplicate_post(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, queue, _calls = owner
    daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    started, finish, second = Event(), Event(), Event()
    read = daemon.client.pool_catalog_metadata
    reads = 0
    waits = 0
    joined = Lock()
    wait = daemon._wait_pool_metadata_result

    def joining(flight: Any, deadline: float) -> Any:
        nonlocal waits
        with joined:
            waits += 1
            if waits == 2:
                second.set()
        return wait(flight, deadline)

    monkeypatch.setattr(daemon, "_wait_pool_metadata_result", joining)

    def slow() -> dict:
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            assert finish.wait(2)
        return read()

    daemon.client.pool_catalog_metadata = slow
    daemon._reconcile_call_budget = 1

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(daemon.send_pool_metadata_request)
        assert started.wait(1)
        later = executor.submit(daemon.send_pool_metadata_request)
        assert second.wait(1)
        finish.set()
        assert first.result(timeout=1) == later.result(timeout=1)
    assert len(sent) == 1 and daemon._pool_publisher_custody.pending is None


def test_unavailable_observation_revision_detects_changed_valid_overrides(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _settle(daemon, queue)
    daemon.declared_source = lambda: DeclarationRead((), False, "source-scan-unavailable")
    daemon.overrides_source = lambda: {"worker-a": {"disabled": False, "reason": "first"}}
    request = daemon.prepare_pool_metadata_request()
    assert request.reason and request.inputs.snapshot.observation_revision
    assert request.inputs.snapshot.input_revision is None and not request.inputs.snapshot.definitions
    daemon.overrides_source = lambda: {"worker-a": {"disabled": False, "reason": "changed"}}
    current = daemon.capture_pool_publication_inputs()
    assert current.reasons == request.inputs.reasons
    assert current.snapshot.observation_revision != request.inputs.snapshot.observation_revision
    sent = _sender(daemon, queue)
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    assert not sent and daemon._pool_publisher_custody.pending is request


def test_uncomparable_unavailable_sources_never_authorize_an_empty_write(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _settle(daemon, queue)
    daemon.overrides_source = lambda: {"invalid": 1}
    request = daemon.prepare_pool_metadata_request()
    assert request.reason and request.inputs.snapshot.observation_revision is None
    before = queue.pool_catalog_snapshot()
    sent = _sender(daemon, queue)
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    assert not sent and queue.pool_catalog_snapshot() == before
    assert daemon._pool_publisher_custody.pending is request


def test_delayed_waiter_returns_its_ack_after_newer_request_settles(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, queue, _calls = owner
    original = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    start, finish, joined, release = Event(), Event(), Event(), Event()
    read = daemon.client.pool_catalog_metadata
    wait = daemon._wait_pool_metadata_result
    reads, waits = 0, 0
    counter_lock = Lock()

    def slow_read() -> dict:
        nonlocal reads
        reads += 1
        if reads == 1:
            start.set()
            assert finish.wait(2)
        return read()

    def slow_wait(flight: Any, deadline: float) -> Any:
        nonlocal waits
        with counter_lock:
            waits += 1
            delayed = waits == 2
        if delayed:
            joined.set()
            assert release.wait(2)
        return wait(flight, deadline)

    daemon.client.pool_catalog_metadata = slow_read
    monkeypatch.setattr(daemon, "_wait_pool_metadata_result", slow_wait)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(daemon.send_pool_metadata_request)
        assert start.wait(1)
        delayed = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert joined.wait(1)
            finish.set()
            result = first.result(timeout=1)
            newer = daemon.prepare_pool_metadata_request()
            newer_result = daemon.send_pool_metadata_request()
            assert daemon._pool_publisher_custody.settled == (newer, newer_result)
            release.set()
            assert delayed.result(timeout=1) == result
            assert daemon._pool_publisher_custody.settled == (newer, newer_result)
            assert sent == [original.to_dict(), newer.to_dict()]
        finally:
            finish.set()
            release.set()


def test_submission_registration_and_admission_close_share_one_short_guard(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon

    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    submitted, register, reading, finish, closing = (Event() for _ in range(5))
    read = daemon.client.pool_catalog_metadata

    class PausingInventory(set):
        def add(self, future: Any) -> None:
            submitted.set()
            assert register.wait(2)
            super().add(future)

    def slow() -> dict:
        reading.set()
        assert finish.wait(2)
        return read()

    def shutdown() -> bool:
        closing.set()
        return daemon.shutdown()

    daemon._probe_inflight = PausingInventory(daemon._probe_inflight)
    daemon.client.pool_catalog_metadata = slow
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    with ThreadPoolExecutor(max_workers=2) as executor:
        sender = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert submitted.wait(1) and reading.wait(1)
            drain = executor.submit(shutdown)
            assert closing.wait(1) and not drain.done()
            register.set()
            assert not drain.result(timeout=1)
            assert daemon._pool_metadata_flight.future in daemon._probe_inflight
            finish.set()
            with pytest.raises(PoolPublicationError, match="admission is closed"):
                sender.result(timeout=1)
            assert not sent and daemon._pool_publisher_custody.pending is request
            with pytest.raises(PoolPublicationError, match="admission is closed"):
                daemon.send_pool_metadata_request()
        finally:
            register.set()
            finish.set()


@pytest.mark.parametrize("failure", ["wait", "ack-lock"])
def test_successful_reply_survives_wait_or_ack_lock_timeout_then_shutdown(
    owner: Any, monkeypatch: Any, failure: str,
) -> None:
    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    wait = daemon._wait_pool_metadata_result
    state = daemon._pool_request_state
    entries = 0

    def timed_out_wait(flight: Any, deadline: float) -> Any:
        wait(flight, deadline)
        raise TimeoutError("synthetic wait timeout racing successful completion")

    @contextmanager
    def timed_out_state(deadline: float) -> Any:
        nonlocal entries
        entries += 1
        if entries == 2:
            raise PoolPublicationError("synthetic acknowledgment lock timeout")
        with state(deadline):
            yield

    if failure == "wait":
        monkeypatch.setattr(daemon, "_wait_pool_metadata_result", timed_out_wait)
    else:
        monkeypatch.setattr(daemon, "_pool_request_state", timed_out_state)
    with pytest.raises((TimeoutError, PoolPublicationError)):
        daemon.send_pool_metadata_request()
    flight = daemon._pool_metadata_flight
    assert flight is not None and flight.request is request
    assert flight.future.done() and flight.future.exception() is None
    assert flight.acknowledged is None and daemon._pool_publisher_custody.pending is request
    expected = flight.future.result()
    assert daemon.shutdown()
    assert daemon._pool_metadata_closed.is_set()
    calls_before = len(calls)
    monkeypatch.setattr(daemon, "_wait_pool_metadata_result", wait)
    monkeypatch.setattr(daemon, "_pool_request_state", state)
    assert daemon.send_pool_metadata_request() == expected
    assert len(calls) == calls_before and sent == [request.to_dict()]
    assert daemon._pool_publisher_custody.settled == (request, expected)
    assert queue.pool_catalog_snapshot().scopes[0] == expected


def test_final_source_recheck_crossing_deadline_never_admits_post(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_pool_sender

    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    sent = _sender(daemon, queue)
    capture = daemon.capture_pool_publication_inputs
    deadline = time.monotonic() + 1

    def final_capture() -> Any:
        current = capture()
        assert current.coherent
        monkeypatch.setattr(
            supervisor_pool_sender, "time", SimpleNamespace(monotonic=lambda: deadline + 1),
        )
        return current

    monkeypatch.setattr(daemon, "capture_pool_publication_inputs", final_capture)
    with pytest.raises(PoolPublicationError, match="deadline exhausted"):
        daemon._run_pool_metadata_send(request, deadline)
    assert not sent and not queue.pool_catalog_snapshot().scopes
    assert daemon._pool_publisher_custody.pending is request


def test_changed_final_unavailable_recheck_is_refused_before_request_custody(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _settle(daemon, queue)
    reads = 0

    def changes() -> DeclarationRead:
        nonlocal reads
        reads += 1
        return DeclarationRead(
            (_declaration(priority=reads if reads % 2 == 0 else 0),),
            False, "source-scan-unavailable",
        )

    daemon.declared_source = changes
    observed = daemon.capture_pool_publication_inputs()
    assert observed.snapshot.observation_revision
    assert "local-inputs-changed" in observed.reasons
    before = queue.pool_catalog_snapshot()
    with pytest.raises(PoolPublicationError, match="unchanged final local inputs"):
        daemon.prepare_pool_metadata_request()
    assert daemon._pool_publisher_custody.pending is None
    assert queue.pool_catalog_snapshot() == before


def test_unavailable_send_refuses_changes_only_in_final_recheck(owner: Any) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _settle(daemon, queue)
    reads = 0

    def changes() -> DeclarationRead:
        nonlocal reads
        reads += 1
        return DeclarationRead(
            (_declaration(priority=9 if reads == 4 else 0),),
            False, "source-scan-unavailable",
        )

    daemon.declared_source = changes
    request = daemon.prepare_pool_metadata_request()
    assert request.reason and "local-inputs-changed" not in request.inputs.reasons
    original = request.inputs.snapshot.observation_revision
    sent = _sender(daemon, queue)
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    current = daemon.pool_source_snapshot()
    assert current.observation_revision == original
    assert reads == 4 and not sent
    assert daemon._pool_publisher_custody.pending is request


def test_final_admission_decision_serializes_close_without_holding_post(
    owner: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import supervisor_daemon, supervisor_pool_sender

    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    deciding, decide, posting, finish, closing = (Event() for _ in range(5))
    writer_id = None
    clock = time.monotonic
    sent = []

    def decision_clock() -> float:
        if get_ident() == writer_id and daemon._pool_metadata_admission_lock.locked():
            deciding.set()
            assert decide.wait(2)
        return clock()

    def send(request: Any, *, boundary: Any) -> Any:
        nonlocal writer_id
        writer_id = get_ident()
        with boundary():
            sent.append(request.to_dict())
            posting.set()
            assert finish.wait(2)
            return _apply(queue, request)

    def shutdown() -> bool:
        closing.set()
        return daemon.shutdown()

    daemon.client.send_pool_metadata = send
    monkeypatch.setattr(supervisor_pool_sender, "time", SimpleNamespace(monotonic=decision_clock))
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.01)
    with ThreadPoolExecutor(max_workers=2) as executor:
        sender = executor.submit(daemon.send_pool_metadata_request)
        try:
            assert deciding.wait(1)
            drain = executor.submit(shutdown)
            assert closing.wait(1) and not drain.done()
            assert not daemon._pool_metadata_closed.is_set()
            decide.set()
            assert posting.wait(1)
            assert not drain.result(timeout=1)
            assert daemon._pool_metadata_closed.is_set()
            assert daemon._pool_metadata_flight.future in daemon._probe_inflight
            finish.set()
            result = sender.result(timeout=1)
            assert sent == [request.to_dict()]
            assert daemon._pool_publisher_custody.settled == (request, result)
        finally:
            decide.set()
            finish.set()


def test_repeated_failed_final_read_cannot_qualify_unavailable_source_comparison(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    _settle(daemon, queue)

    def failed_recheck() -> Any:
        raise PermissionError("synthetic final input unavailable")

    monkeypatch.setattr(daemon, "_read_pool_local_inputs", failed_recheck)
    request = daemon.prepare_pool_metadata_request()
    assert request.reason and "local-input-capture-unavailable" in request.inputs.reasons
    assert request.inputs.snapshot.observation_revision
    assert request.inputs.local_recheck_revision is None
    current = daemon.capture_pool_publication_inputs()
    assert current.reasons == request.inputs.reasons
    assert current.snapshot.observation_revision == request.inputs.snapshot.observation_revision
    sent = _sender(daemon, queue)
    before = queue.pool_catalog_snapshot()
    with pytest.raises(PoolPublicationError, match="fences changed"):
        daemon.send_pool_metadata_request()
    assert not sent and queue.pool_catalog_snapshot() == before
    assert daemon._pool_publisher_custody.pending is request


def test_failed_flight_after_timeout_recovers_original_on_next_invocation(owner: Any) -> None:
    daemon, queue, calls = owner
    request = daemon.prepare_pool_metadata_request()
    started, finish = Event(), Event()
    sent = []

    def lost(request: Any, *, boundary: Any) -> Any:
        with boundary():
            sent.append(request.to_dict())
            _apply(queue, request)
            started.set()
            assert finish.wait(2)
            raise httpx.ReadError("synthetic late lost acknowledgment")

    daemon.client.send_pool_metadata = lost
    daemon._reconcile_call_budget = 0.05
    try:
        with pytest.raises(TimeoutError):
            daemon.send_pool_metadata_request()
        assert started.is_set()
        flight = daemon._pool_metadata_flight
        finish.set()
        with pytest.raises(httpx.ReadError):
            flight.future.result(timeout=1)
        assert daemon._pool_metadata_flight is flight
        expected = queue.pool_catalog_snapshot().scopes[0]
        calls_before = len(calls)
        daemon._reconcile_call_budget = 1
        assert daemon.send_pool_metadata_request() == expected
        assert len(calls) == calls_before + 1 and sent == [request.to_dict()]
        assert daemon._pool_publisher_custody.settled == (request, expected)
    finally:
        finish.set()


@pytest.mark.parametrize("replacement", [False, True])
def test_pending_reply_cannot_change_original_definition_baseline(
    owner: Any, replacement: bool,
) -> None:
    daemon, queue, _calls = owner
    if replacement:
        definitions = daemon.capture_pool_publication_inputs().snapshot.definitions
        prior = queue.begin_pool_publication("host-a", "default", "earlier", expected_generation=0)
        queue.publish_pool_catalog_scope(
            "host-a", "default", "earlier", generation=prior.generation,
            expected_revision=0, registration_revision=queue.pool_registration_revision(),
            input_revision="earlier-input", definitions=definitions,
        )
    request = daemon.prepare_pool_metadata_request()

    def wrong(request: Any, *, boundary: Any) -> Any:
        with boundary():
            result = _apply(queue, request)
            return replace(result, definitions_generation=result.generation, definitions_revision=99)

    daemon.client.send_pool_metadata = wrong
    with pytest.raises(PoolPublicationError, match="original retained baseline"):
        daemon.send_pool_metadata_request()
    custody = daemon._pool_publisher_custody
    assert custody.pending is request and custody.position is None and custody.settled is None
    sent = _sender(daemon, queue)
    enrolled = daemon.send_pool_metadata_request()
    assert not sent and custody.settled == (request, enrolled)
    published = daemon.prepare_pool_metadata_request()
    result = daemon.send_pool_metadata_request()
    assert result.definitions == published.inputs.snapshot.definitions
    assert result.definitions_generation == result.generation
    assert result.definitions_revision == result.revision


@pytest.mark.parametrize("changes", [
    {"definitions_generation": 1, "definitions_revision": 1},
    {"definitions_generation": None, "definitions_revision": 1},
    {"definitions_generation": 1, "definitions_revision": None},
])
def test_pending_wire_reply_refuses_impossible_or_partial_attribution(owner: Any, changes: dict) -> None:
    daemon, queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    body = {
        "protocol": "pool_catalog_transport/v1", "version": 1,
        "scope": {**_scope_payload(result), **changes},
    }
    with pytest.raises(PoolPublicationError, match="definition attribution"):
        read_pool_metadata_result(body)
    assert daemon._pool_publisher_custody.pending is request


@pytest.mark.parametrize("field", [
    "version", "generation", "definitions_revision", "valid_until",
    "missing_freshness", "oversized_freshness", "definitions", "scope",
])
def test_malformed_response_never_becomes_acknowledgment(owner: Any, field: str) -> None:
    daemon, queue, _calls = owner
    _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    result = _apply(queue, request)
    body = {"protocol": "pool_catalog_transport/v1", "version": 1, "scope": _scope_payload(result)}
    if field == "version":
        body["version"] = True
    elif field == "scope":
        body["scope"].pop("reason")
    else:
        body["scope"][field] = {
            "generation": True, "definitions_revision": True,
            "valid_until": float("nan"), "definitions": [{}, result.definitions[0].to_dict()],
            "missing_freshness": None, "oversized_freshness": 10 ** 1000,
        }[field]
        if field in ("missing_freshness", "oversized_freshness"):
            body["scope"]["valid_until"] = body["scope"].pop(field)
    with pytest.raises(PoolPublicationError):
        read_pool_metadata_result(body)
    assert daemon._pool_publisher_custody.pending is request


@pytest.mark.parametrize("operation", ["begin", "publish"])
@pytest.mark.parametrize("supported", [False, True])
def test_real_client_negotiates_before_boundary_and_uses_original_control_body(
    owner: Any, operation: str, supported: bool,
) -> None:
    daemon, queue, _calls = owner
    if operation == "publish":
        _settle(daemon, queue)
    request = daemon.prepare_pool_metadata_request()
    paths, admitted = [], []

    def boundary() -> Any:
        admitted.append(True)
        return nullcontext()

    def handler(wire: httpx.Request) -> httpx.Response:
        paths.append(wire.url.path)
        if wire.url.path == "/health":
            return httpx.Response(200, json={
                "pool_catalog_transport": pool_catalog_contract("control") if supported else None,
            })
        scheme = "Be" + "arer"
        assert wire.headers["Authorization"] == " ".join((scheme, "control"))
        assert json.loads(wire.content) == request.to_dict()
        result = _apply(queue, request)
        return httpx.Response(200, json={
            "protocol": "pool_catalog_transport/v1", "version": 1,
            "scope": {**_scope_payload(result), "additive": "accepted"},
        })

    with DispatchClient("http://example.test", control_token="control", transport=httpx.MockTransport(handler)) as client:
        if supported:
            result = client.send_pool_metadata(request, boundary=boundary)
            assert result.publisher_id == request.inputs.publisher_id
            assert paths == ["/health", f"/pool-catalog/scopes/{operation}"] and admitted == [True]
        else:
            with pytest.raises(DispatchUpgradeRequired):
                client.send_pool_metadata(request, boundary=boundary)
            assert paths == ["/health"] and not admitted


@pytest.mark.parametrize("status", [401, 403, 409, 422, 503])
def test_client_write_refusal_is_explicit_and_never_legacy_replayed(owner: Any, status: int) -> None:
    daemon, _queue, _calls = owner
    request = daemon.prepare_pool_metadata_request()
    paths = []

    def handler(wire: httpx.Request) -> httpx.Response:
        paths.append(wire.url.path)
        if wire.url.path == "/health":
            return httpx.Response(200, json={"pool_catalog_transport": pool_catalog_contract("control")})
        return httpx.Response(status, json={"detail": {"code": "synthetic-refusal"}})

    with DispatchClient("http://example.test", control_token="control", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchError) as caught:
            client.send_pool_metadata(request, boundary=nullcontext)
    assert caught.value.status_code == status
    assert paths == ["/health", "/pool-catalog/scopes/begin"]
    assert daemon._pool_publisher_custody.pending is request


def test_sender_round_trip_through_actual_provider_retains_unknown_publish(owner: Any) -> None:
    daemon, queue, _calls = owner
    server = TestClient(create_app(
        queue, token="data", control_token="control", enable_mcp=False, verification_interval=0,
    ))
    lost = False
    writes = []

    def handler(wire: httpx.Request) -> httpx.Response:
        nonlocal lost
        response = server.request(
            wire.method, str(wire.url), headers=dict(wire.headers), content=wire.content,
        )
        if wire.method == "POST":
            writes.append(json.loads(wire.content))
            if wire.url.path.endswith("/publish") and not lost:
                lost = True
                assert response.status_code == 200
                raise httpx.ReadError("synthetic accepted reply lost")
        return httpx.Response(response.status_code, json=response.json())

    with server, DispatchClient(
        "http://testserver", token="data", control_token="control", transport=httpx.MockTransport(handler),
    ) as client:
        daemon.client.pool_catalog_metadata = client.pool_catalog_metadata
        daemon.client.send_pool_metadata = client.send_pool_metadata
        daemon.prepare_pool_metadata_request()
        daemon.send_pool_metadata_request()
        original = daemon.prepare_pool_metadata_request()
        with pytest.raises(httpx.ReadError):
            daemon.send_pool_metadata_request()
        before = queue.pool_catalog_snapshot()
        daemon.client.set_regs([])
        assert daemon.send_pool_metadata_request() == before.scopes[0]
        assert queue.pool_catalog_snapshot() == before
        assert writes[-1] == original.to_dict() and len(writes) == 2
        with queue._connect() as conn:
            assert conn.execute("SELECT count(*) FROM pool_publication_history").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM pool_publication_generations").fetchone()[0] == 1
