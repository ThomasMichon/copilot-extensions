import argparse
import asyncio
import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from agent_procutil.startup import PROGRESS_PREFIX, StartupClock, StartupTimeouts
from agent_bridge import __main__ as cli
from agent_bridge import startup_observation as observation
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.send_outcome import run_send
from agent_bridge.session_status import SessionStatus


class Stream:
    def __init__(self, events):
        self.events = events
        self.closed = threading.Event()

    def __iter__(self):
        yield from self.events()

    def close(self):
        self.closed.set()


class Client:
    def __init__(self, events):
        self.stream = Stream(events)
        self.stream_args = None

    def get_cursor_info(self, session_id, **kwargs):
        return {"head_id": 7, "continuity_id": "same-lineage"}

    def stream_events(self, session_id, **kwargs):
        self.stream_args = (session_id, kwargs)
        return self.stream


def test_actual_observer_accepts_unknown_events_and_target_keepalives(capsys):
    mutation_calls = []

    def events():
        while True:
            time.sleep(.03)
            yield {"event": "future-startup-v100", "data": {"secret": "do-not-forward"}}
            yield {"event": "_heartbeat", "data": {}}

    client = Client(events)
    item = observation.StartupObservation(StartupTimeouts(.5, 4, .5))

    def request():
        mutation_calls.append("original")
        time.sleep(1.1)
        return {"session_id": "original", "status": "idle"}

    assert item.request(client, "original", request)["session_id"] == "original"
    assert mutation_calls == ["original"]
    assert client.stream_args == ("original", {
        "after": 7, "controlled": True, "transient": True, "request_timeout": .5,
        "caller_id": item.caller_id, "continuity_id": "same-lineage",
    })
    text = capsys.readouterr().err
    assert "future-startup-v100" in text
    assert "_heartbeat" in text
    assert "do-not-forward" not in text


def test_silence_ends_observation_without_cancelling_or_replaying_request():
    release = threading.Event()
    finished = threading.Event()
    mutations = []

    def events():
        release.wait(2)
        yield {"event": "_heartbeat", "data": {}}

    item = observation.StartupObservation(StartupTimeouts(.2, 2, .2))
    client = Client(events)

    def request():
        mutations.append("original")
        release.wait(2)
        finished.set()
        return {"status": "idle"}

    try:
        with pytest.raises(observation.StartupObservationError) as raised:
            item.request(client, "original", request)
        assert raised.value.clock == "communication"
        assert mutations == ["original"]
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)
        assert client.stream.closed.wait(2)


def test_independent_process_ceiling_expires_despite_target_updates():
    release = threading.Event()
    finished = threading.Event()

    def events():
        while not release.wait(.01):
            yield {"event": "startup_progress", "data": {"phase": "Git"}}

    client = Client(events)
    item = observation.StartupObservation(StartupTimeouts(1, .5, .2))

    def request():
        release.wait(2)
        finished.set()
        return {"status": "idle"}

    try:
        with pytest.raises(observation.StartupObservationError) as raised:
            item.request(client, "original", request)
        assert raised.value.clock == "process"
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)
        assert client.stream.closed.wait(2)


def test_initial_silence_and_ready_phase_do_not_manufacture_target_contact(capsys):
    now = [0.0]
    item = observation.StartupObservation(StartupTimeouts())
    item.clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
    now[0] = 29
    item.local_phase("waiting-for-lock")
    now[0] = 30
    with pytest.raises(observation.StartupObservationError) as raised:
        item.check()
    assert raised.value.clock == "communication"
    assert "communication_timeout" in capsys.readouterr().err


def test_late_preflight_response_cannot_renew_an_expired_contact_clock(monkeypatch):
    now = [0.0]
    item = observation.StartupObservation(StartupTimeouts())
    item.clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            now[0] = 31
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b'{"protocol_version":46}'

    def open_response(request, **kwargs):
        requests.append((request.method, kwargs["timeout"]))
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", open_response)
    token = observation._active.set(item)
    try:
        with pytest.raises(observation.StartupObservationError) as raised:
            BridgeClient("http://fixture", "fixture")._request("GET", "/health")
        assert raised.value.clock == "communication"
        assert requests == [("GET", 30)]
        assert item.clock.expired().silence == 31
    finally:
        observation._active.reset(token)


@pytest.mark.parametrize("result", [
    None, {}, [], {"session_id": "original"}, {"status": "idle"},
    {"session_id": "original", "status": "starting"},
    {"session_id": "original", "status": []},
    {"session_id": "other", "status": "idle"},
])
def test_incomplete_resume_acknowledgement_never_marks_ready(result):
    release = threading.Event()

    def events():
        while not release.wait(.01):
            yield {"event": "_heartbeat", "data": {}}

    item = observation.StartupObservation(StartupTimeouts())
    try:
        with pytest.raises(observation.StartupObservationError) as raised:
            item.request(Client(events), "original", lambda: result)
        assert raised.value.reason == "resume_unknown"
        assert not item.clock.ready
    finally:
        release.set()


def test_ready_ends_startup_budget_not_a_later_turn(capsys):
    now = [0.0]
    item = observation.StartupObservation(StartupTimeouts())
    item.clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
    item.received("ready-target")
    now[0] = 1
    item.ready()
    now[0] = 600
    item.check()
    frames = [
        json.loads(line.removeprefix(PROGRESS_PREFIX))
        for line in capsys.readouterr().err.splitlines() if line.startswith(PROGRESS_PREFIX)
    ]
    assert frames[-1]["event"] == "ready"
    assert frames[-1]["expected_s"] == 60


@pytest.mark.parametrize("option", [
    "--startup-communication-timeout", "--startup-process-timeout",
])
@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_parser_refuses_invalid_startup_budgets_before_execution(option, value):
    with pytest.raises(SystemExit) as raised:
        cli.build_parser().parse_args(["send", "original", "prompt", option, value])
    assert raised.value.code == 2


def test_unknown_outcome_is_not_a_retryable_refusal_or_false_success(capsys):
    args = argparse.Namespace(json=True, target="original")

    def failed(args):
        raise observation.StartupObservationError("communication", "Git", 30)

    with pytest.raises(SystemExit) as raised:
        run_send(args, failed)
    assert raised.value.code == 70
    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "unknown"
    assert payload["retryable"] is False
    assert payload["target"] == "original"


def test_acknowledged_refusal_is_not_relabelled_unknown():
    release = threading.Event()

    def events():
        release.wait(2)
        yield {"event": "_heartbeat", "data": {}}

    item = observation.StartupObservation(StartupTimeouts(1, 2, 1))
    client = Client(events)
    error = BridgeClientError(409, "retained owner is busy")
    try:
        with pytest.raises(BridgeClientError) as raised:
            item.request(client, "original", lambda: (_ for _ in ()).throw(error))
        assert raised.value is error
    finally:
        release.set()
        assert client.stream.closed.wait(2)


def test_selected_policy_does_not_import_legacy_lifecycle_to_compute_a_deadline(monkeypatch):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not name.endswith("session_manager"), "startup must not import the legacy budget graph"
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    args = argparse.Namespace(startup_communication_timeout=30, startup_process_timeout=300)
    with observation.observing_send(args):
        assert 0 < cli._startup_request_timeout(resume=True, fresh_fallback=True) <= 300
        observation.current().received()
        observation.ready()
        assert cli._startup_request_timeout(resume=True, fresh_fallback=True) == 30


def test_cached_protocol_metadata_does_not_renew_communication():
    now = [0.0]
    args = argparse.Namespace(
        startup_communication_timeout=30, startup_process_timeout=300, target="original",
    )
    with observation.observing_send(args):
        item = observation.current()
        item.clock = StartupClock(StartupTimeouts(), monotonic=lambda: now[0])
        now[0] = 30
        client = argparse.Namespace(daemon_supports=lambda version: True)
        with pytest.raises(observation.StartupObservationError) as raised:
            observation.configure_client(client, args)
        assert raised.value.clock == "communication"


def test_resume_acknowledgement_ends_startup_at_receipt_not_queue_consumption(monkeypatch):
    requested = threading.Event()
    caller = threading.get_ident()
    delayed = False

    def events():
        while True:
            time.sleep(.03)
            yield {"event": "_heartbeat", "data": {}}

    item = observation.StartupObservation(StartupTimeouts(.3, 1, .2))
    check = item.check

    def delayed_check():
        nonlocal delayed
        if threading.get_ident() == caller and requested.is_set() and not delayed:
            delayed = True
            time.sleep(1.2)
        check()

    monkeypatch.setattr(item, "check", delayed_check)

    def request():
        requested.set()
        return {"session_id": "original", "status": "idle"}

    assert item.request(Client(events), "original", request)["status"] == "idle"
    assert delayed
    assert item.clock.ready
    assert item.clock.elapsed > item.clock.policy.process


@pytest.mark.parametrize("empty", [False, True])
def test_actual_service_accepts_the_observers_initial_subscription(empty):
    from agent_bridge.events import EventLog
    from agent_bridge.routes.sessions import get_cursor, get_events

    log = EventLog()
    if not empty:
        log.append("startup_progress", {"phase": "previous"})
    session = SimpleNamespace(session_id="original", event_log=log)
    ensured = []

    def state(*args):
        return {
            "last_acked_id": 0, "head_id": log.latest_id,
            "continuity_id": log.continuity_id, "registered": False, "invalidation": None,
        }

    manager = SimpleNamespace(
        get_session=lambda key: session,
        db=SimpleNamespace(
            get_controlled_cursor_state=state,
            ensure_cursor=lambda *args: ensured.append(args),
        ),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)))
    subscribed = []

    def events():
        while True:
            time.sleep(.01)
            yield {"event": "_heartbeat", "data": {}}

    class RouteClient:
        def get_cursor_info(self, session_id, **kwargs):
            return asyncio.run(get_cursor(session_id, request, **kwargs)).model_dump()

        def stream_events(self, session_id, **kwargs):
            kwargs.pop("request_timeout")
            kwargs.setdefault("continuity_id", None)  # The HTTP layer resolves Query defaults.
            response = asyncio.run(get_events(
                session_id, request, before=None, limit=256, **kwargs,
            ))
            assert response.status_code == 200
            asyncio.run(response.body_iterator.aclose())
            subscribed.append(kwargs)
            return Stream(events)

    item = observation.StartupObservation(StartupTimeouts(1, 3, 1))
    assert item.request(
        RouteClient(), "original", lambda: {"session_id": "original", "status": "idle"},
    )["status"] == "idle"
    assert len(subscribed) == 1
    assert subscribed[0]["caller_id"] == item.caller_id
    assert subscribed[0]["transient"] is (not empty)
    assert ensured[0][:2] == (item.caller_id, "original")


@pytest.mark.parametrize("error", [
    http.client.IncompleteRead(b"{", 100),
    http.client.RemoteDisconnected("response lost"),
    http.client.BadStatusLine("invalid response"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    json.JSONDecodeError("truncated", "{", 1),
])
def test_admission_read_or_decode_error_is_unknown_without_retry(monkeypatch, error):
    calls = []
    client = BridgeClient("http://fixture.invalid", "fixture")

    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        raise error

    monkeypatch.setattr(client, "_request", failed)
    args = argparse.Namespace(startup_communication_timeout=30, startup_process_timeout=300)
    with observation.observing_send(args):
        observation.current().received()
        observation.ready()
        with pytest.raises(observation.StartupObservationError) as raised:
            client.submit_prompt("original", "one exact prompt")
    assert raised.value.reason == "admission_unknown"
    assert raised.value.__cause__ is error
    assert len(calls) == 1
    assert calls[0][1]["allow_retry"] is False
    assert calls[0][0][2]["no_resume"] is True


@pytest.mark.parametrize("result", [
    None, {}, [], {"status": "running"},
    {"session_id": "original", "turn_index": 0, "status": ""},
    {"session_id": "original", "turn_index": 0, "status": "not-a-lifecycle-status"},
])
def test_incomplete_admission_shape_cannot_become_delivered(monkeypatch, result):
    client = BridgeClient("http://fixture.invalid", "fixture")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: result)
    args = argparse.Namespace(startup_communication_timeout=30, startup_process_timeout=300)
    with observation.observing_send(args):
        observation.current().received()
        observation.ready()
        with pytest.raises(observation.StartupObservationError, match="acknowledgement"):
            client.submit_prompt("original", "one exact prompt")


@pytest.mark.parametrize("status", list(SessionStatus))
def test_observed_admission_keeps_the_real_first_turn_index_zero(monkeypatch, status):
    client = BridgeClient("http://fixture.invalid", "fixture")
    result = {"status": status.value, "session_id": "original", "turn_index": 0}
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: result)
    args = argparse.Namespace(startup_communication_timeout=30, startup_process_timeout=300)
    with observation.observing_send(args):
        observation.current().received()
        observation.ready()
        assert client.submit_prompt("original", "one exact prompt") is result
