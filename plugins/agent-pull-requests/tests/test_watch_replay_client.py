"""Finite replay calls: exact identity, strict responses and no hidden retries."""

import asyncio
import json
from dataclasses import FrozenInstanceError

import pytest
from fleet_manager.gateway_service_caller import ServiceResponse
from fleet_manager.gateway_service_channel import TransportError

from agent_pull_requests.watch_replay_client import (
    MAX_BATCH_BYTES, PROTOCOL, ReplayBatch, ReplayClient, ReplayClientError,
    ReplayOutcome, ReplayReceipt, ReplayRequest,
)


REQUEST = ReplayRequest("original-request", 100.5)
RECEIPT = {"status": "active", "registration_id": "registration", "deadline_at": None,
           "event_id": None}
CAPABILITIES = {
    "protocol": PROTOCOL,
    "operations": ["capabilities", "register", "inspect", "pending", "acknowledge", "cancel"],
    "max_batch": 16, "max_batch_bytes": 24576, "receipt_retention_seconds": 86400,
}
EVENT = {
    "repo": "example/project", "number": 1, "subscriber_id": "registration",
    "notification_protocol": "acknowledged_notifications/v1",
    "registration_id": "registration", "event_id": "event",
    "transitions": ["merged"], "timed_out": False, "pr_state": "MERGED", "merged": True,
    "review_decision": "", "mergeable": "", "checks_state": "",
}


class Caller:
    def __init__(self, value=None, *, raw=None, status=200, content_type="application/json"):
        self.calls = []
        self.error = None
        self.response = ServiceResponse(
            status, (("Content-Type", content_type),),
            raw if raw is not None else json.dumps(value).encode(),
        )

    async def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error is not None:
            raise self.error
        return self.response


def client(caller):
    return ReplayClient(caller, route_prefix="/api", target="project", repo="example/project")


def run(awaitable):
    return asyncio.run(awaitable)


def test_capabilities_one_bounded_call_and_exact_register_replay():
    caller = Caller(CAPABILITIES)
    selected = client(caller)
    capabilities = run(selected.capabilities())
    assert capabilities.protocol == PROTOCOL and capabilities.max_batch == 16
    assert caller.calls == [(("GET", "/api/v1/watch/project/capabilities"), {
        "request_headers": (("Content-Type", "application/json"),),
        "body": b"", "response_limit": MAX_BATCH_BYTES,
    })]
    caller.response = Caller(RECEIPT).response
    first = run(selected.register(REQUEST, number=1, until=("merged",)))
    second = run(selected.register(REQUEST, number=1, until=("merged",)))
    assert first == second == ReplayReceipt("active", "registration", None, None)
    assert len(caller.calls) == 3
    assert caller.calls[1] == caller.calls[2]
    assert json.loads(caller.calls[-1][1]["body"]) == {
        "request_id": REQUEST.request_id, "issued_at": REQUEST.issued_at,
        "number": 1, "until": ["merged"], "deadline_at": None,
    }
    assert run(selected.inspect(REQUEST)) == first
    assert json.loads(caller.calls[-1][1]["body"]) == vars(REQUEST)
    with pytest.raises(FrozenInstanceError):
        REQUEST.request_id = "replacement"


@pytest.mark.parametrize("operation,status", [("cancel", "cancelled"), ("acknowledge", "acknowledged")])
def test_exact_completion_receipt(operation, status):
    payload = dict(RECEIPT, status=status, event_id="event" if operation == "acknowledge" else None)
    caller = Caller(payload)
    options = {"registration_id": "registration"}
    if operation == "acknowledge":
        options["event_id"] = "event"
    result = run(getattr(client(caller), operation)(REQUEST, **options))
    assert result == ReplayReceipt(**payload)
    assert json.loads(caller.calls[0][1]["body"]) == dict(vars(REQUEST), **options)
    assert len(caller.calls) == 1


@pytest.mark.parametrize("value", [
    {"status": "uncertain"}, {"status": "uncertain", "reason": "state_commit_uncertain"},
    {"status": "uncertain", "reason": "request_outside_retention"},
    {"status": "unavailable", "reason": "replay_capacity"}, {"status": "conflict"},
])
def test_domain_outcomes_are_not_success_or_retry(value):
    caller = Caller(value)
    result = run(client(caller).register(REQUEST, number=1, until=("merged",)))
    assert result == ReplayOutcome(**value) and len(caller.calls) == 1


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
def test_http_failure_keeps_status_and_never_exposes_response_or_retries(status):
    caller = Caller({"secret": "synthetic-private-detail"}, status=status)
    with pytest.raises(ReplayClientError) as caught:
        run(client(caller).register(REQUEST, number=1, until=("merged",)))
    assert caught.value.code == "watch_http_refused"
    assert caught.value.http_status == status
    assert caught.value.outcome_unknown
    assert "synthetic" not in str(caught.value) and len(caller.calls) == 1


@pytest.mark.parametrize("error", [
    TransportError("outcome-unknown"), TransportError("caller-credentials-unavailable", 401),
    asyncio.CancelledError(),
])
def test_transport_failure_and_cancellation_never_fallback(error):
    caller = Caller()
    caller.error = error
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            run(client(caller).register(REQUEST, number=1, until=("merged",)))
    else:
        with pytest.raises(ReplayClientError) as caught:
            run(client(caller).register(REQUEST, number=1, until=("merged",)))
        assert caught.value.code == error.code
        assert caught.value.outcome_unknown == (error.code == "outcome-unknown")
    assert len(caller.calls) == 1


@pytest.mark.parametrize("raw", [
    b'{"status":"uncertain","status":"active"}', b'{"deadline_at":NaN}',
    b'{"deadline_at":1e999}', b"[]", b"\xff", b"{", b" " * (MAX_BATCH_BYTES + 1),
])
def test_bad_json_is_sanitized_and_mutation_stays_uncertain(raw):
    caller = Caller(raw=raw)
    with pytest.raises(ReplayClientError, match="invalid_watch_response") as caught:
        run(client(caller).register(REQUEST, number=1, until=("merged",)))
    assert caught.value.outcome_unknown and len(caller.calls) == 1


def test_wrong_media_type_and_over_limit_batch_are_refused():
    with pytest.raises(ReplayClientError, match="invalid_watch_response"):
        run(client(Caller(RECEIPT, content_type="text/html")).inspect(REQUEST))
    with pytest.raises(ReplayClientError, match="invalid_watch_response"):
        run(client(Caller({"events": [EVENT, dict(EVENT, registration_id="other")],
                           "more": False})).pending(limit=1))


@pytest.mark.parametrize("value", [
    dict(RECEIPT, status=[]), dict(RECEIPT, event_id="unexpected"),
    dict(RECEIPT, registration_id=""), dict(RECEIPT, deadline_at=True),
    dict(RECEIPT, status="acknowledged"), dict(RECEIPT, extra=1),
    dict(RECEIPT, deadline_at=2**53 + 1),
    {"status": "uncertain", "reason": []}, {"status": "uncertain", "reason": "unknown"},
    {"status": "unavailable"}, {"status": "observed"},
])
def test_invalid_receipts_cannot_claim_success(value):
    with pytest.raises(ReplayClientError, match="invalid_watch_response"):
        run(client(Caller(value)).register(REQUEST, number=1, until=("merged",)))


@pytest.mark.parametrize("value", [
    dict(RECEIPT, status="cancelled", registration_id="replacement"),
    dict(RECEIPT, status="acknowledged", event_id="different"),
    RECEIPT,
])
def test_completion_cannot_ack_a_replacement_or_other_event(value):
    with pytest.raises(ReplayClientError, match="invalid_watch_response") as caught:
        run(client(Caller(value)).acknowledge(
            REQUEST, registration_id="registration", event_id="event",
        ))
    assert caught.value.outcome_unknown


@pytest.mark.parametrize("value", [
    dict(CAPABILITIES, protocol="other"), dict(CAPABILITIES, max_batch=True),
    dict(CAPABILITIES, max_batch_bytes=24577), dict(CAPABILITIES, operations=[]),
    dict(CAPABILITIES, operations=CAPABILITIES["operations"] + ["inspect"]),
    dict(CAPABILITIES, operations=[{}]), dict(CAPABILITIES, extra=1),
])
def test_capabilities_cannot_silently_select_legacy_protocol(value):
    with pytest.raises(ReplayClientError, match="invalid_watch_capabilities"):
        run(client(Caller(value)).capabilities())


def test_pending_is_typed_bounded_and_payload_mutations_are_detached():
    caller = Caller({"events": [EVENT], "more": False})
    batch = run(client(caller).pending(limit=1))
    assert isinstance(batch, ReplayBatch) and batch.more is False
    event, = batch.events
    assert (event.registration_id, event.event_id, event.number) == ("registration", "event", 1)
    payload = event.payload()
    assert payload == EVENT
    payload["transitions"].clear()
    assert event.payload() == EVENT
    assert json.loads(caller.calls[0][1]["body"]) == {"limit": 1}
    assert run(client(Caller({"status": "uncertain"})).pending()) == ReplayOutcome("uncertain")


@pytest.mark.parametrize("value", [
    {"events": [dict(EVENT, repo="foreign/project")], "more": False},
    {"events": [dict(EVENT, subscriber_id="replacement")], "more": False},
    {"events": [dict(EVENT, number=True)], "more": False},
    {"events": [dict(EVENT, event_id="")], "more": False},
    {"events": [dict(EVENT, transitions=["unknown"])], "more": False},
    {"events": [dict(EVENT, snapshot={})], "more": False},
    {"events": [EVENT, EVENT], "more": False},
    {"events": [EVENT], "more": 1},
    {"events": {}, "more": False},
    {"events": [], "more": True},
    {"status": "conflict"}, {"status": "unavailable", "reason": "replay_capacity"},
])
def test_invalid_pending_never_reaches_consumer(value):
    with pytest.raises(ReplayClientError, match="invalid_watch_response"):
        run(client(Caller(value)).pending())


def test_timeout_event_without_snapshot_remains_valid():
    payload = {name: value for name, value in EVENT.items()
               if name not in ("pr_state", "merged", "review_decision", "mergeable", "checks_state")}
    payload.update(transitions=[], timed_out=True)
    batch = run(client(Caller({"events": [payload], "more": False})).pending())
    assert batch.events[0].payload() == payload


def test_input_refusal_does_not_call_transport():
    caller = Caller()
    selected = client(caller)
    for options in (
        {"number": True, "until": ("merged",)}, {"number": 1, "until": ["merged"]},
        {"number": 1, "until": ("merged", "merged")}, {"number": 1, "until": ({},)},
        {"number": 1, "until": ("merged",), "deadline_at": 2**53 + 1},
    ):
        with pytest.raises((ValueError, OverflowError)):
            run(selected.register(REQUEST, **options))
    for limit in (True, 0, 17):
        with pytest.raises(ValueError):
            run(selected.pending(limit=limit))
    for identity, timestamp in (("", 1), ("id\n", 1), ("x" * 129, 1), ("id", True),
                                ("id", float("nan")), ("id", 2**53 + 1)):
        with pytest.raises(ValueError):
            ReplayRequest(identity, timestamp)
    for prefix in ("/api/", "//foreign", "/../api", "/api?query", "https://example.invalid"):
        with pytest.raises(ValueError):
            ReplayClient(caller, route_prefix=prefix, target="project", repo="example/project")
    assert caller.calls == []
