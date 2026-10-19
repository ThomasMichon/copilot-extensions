"""Caller deadlines include preflight, capability lookup and confirmation."""

from types import SimpleNamespace
import threading
from uuid import uuid4

import pytest

from agent_pull_requests import watch_control
from agent_pull_requests.watch_control import WatchControlClient
from test_watch_writer import owners as owners  # noqa: F401 -- pytest fixture


def _client(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(watch_control.time, "monotonic", lambda: clock[0])
    instance = SimpleNamespace(
        pid=1, start_time="1",
        evidence=lambda: {"test-owned": True},
        runtime=SimpleNamespace(require_current=lambda *_args, **_kwargs: None),
        require_live=lambda *_args, **kwargs: kwargs["read_health"](),
    )
    monkeypatch.setattr(watch_control.diagnostics, "process_start_time", lambda _pid: "1")
    client = WatchControlClient(
        endpoint=("127.0.0.1", 12345, "test-owned-token"), instance=instance,
        installation=lambda: SimpleNamespace(), transaction=lambda: ("transaction", "capability"),
        request_timeout=60,
    )
    return client, clock


@pytest.mark.parametrize("operation", ["adopt", "retire", "cleanup-stop"])
def test_preflight_consumes_control_budget_instead_of_request_timeout(monkeypatch, operation):
    client, clock = _client(monkeypatch)
    seen = []

    def health(*, timeout):
        seen.append(("health", timeout))
        clock[0] += 3
        return {}

    client.health = client.cleanup_health = health
    client.instance.require_live = lambda *_args, **kwargs: (
        health(timeout=5), kwargs["read_health"](),
    )
    client._control_request = lambda operation, timeout: seen.append((operation, timeout)) or {}
    if operation != "cleanup-stop":
        client.control(operation, timeout=5)
    else:
        client.cleanup_stop(timeout=5)
    assert seen == [("health", 5), (operation, 2)]


def test_drain_confirmation_shares_one_deadline(monkeypatch):
    client, clock = _client(monkeypatch)
    seen = []

    def control(operation, *, timeout):
        seen.append((operation, timeout))
        clock[0] += 4
        return {}

    def health(*, timeout):
        seen.append(("health", timeout))
        return {"writerHeld": False, "readReady": False, "effectsEnabled": False}

    client.control, client.health = control, health
    assert client.drain(timeout=5, poll=0.1, force=False)["clean"] is True
    assert seen == [("drain", 5), ("health", 1)]


def test_capability_lookup_is_charged_before_wire_control(monkeypatch):
    client, clock = _client(monkeypatch)
    seen = []

    def transaction():
        clock[0] += 2
        return "transaction", "capability"

    def request(kind, payload, timeout):
        seen.append((timeout, payload["timeout"]))
        return {**payload, "accepted": True}

    client._transaction, client._request = transaction, request
    assert client._control_request("adopt", 5)["accepted"] is True
    assert seen == [(3, 3)]


@pytest.mark.parametrize("overrun", ["capability", "acknowledgement", "preflight"])
def test_expired_deadline_never_sends_or_reports_a_successful_control(monkeypatch, overrun):
    client, clock = _client(monkeypatch)
    sent = []

    def transaction():
        if overrun == "capability":
            clock[0] += 6
        return "transaction", "capability"

    def request(kind, payload, timeout):
        sent.append(True)
        clock[0] += 6
        return {**payload, "accepted": True}

    client._transaction, client._request = transaction, request
    if overrun == "preflight":
        def health(*, timeout):
            clock[0] += 6
            return {}
        client.health = health
        client.instance.require_live = lambda *_args, **kwargs: (
            health(timeout=5), kwargs["read_health"](),
        )
        action = lambda: client.control("adopt", timeout=5)
    else:
        action = lambda: client._control_request("adopt", 5)
    with pytest.raises(TimeoutError, match="deadline expired"):
        action()
    assert bool(sent) is (overrun == "acknowledgement")


def test_health_rpc_gets_only_budget_left_after_governance(monkeypatch):
    client, clock = _client(monkeypatch)
    seen = []

    def installation():
        clock[0] += 2
        return SimpleNamespace()

    client._installation = installation
    client._request = lambda kind, payload, timeout: seen.append(timeout) or {}
    client.health(timeout=5)
    assert seen == [3]


def test_owner_rpc_passes_remaining_budget_as_absolute_transport_completion(monkeypatch):
    client, _ = _client(monkeypatch)
    seen = []

    def request(*_args, **kwargs):
        seen.append(kwargs)
        return {}

    monkeypatch.setattr(watch_control, "request", request)
    client._request("health", {}, 2)
    assert seen[0]["request_deadline_s"] == 2
    assert seen[0]["completion_timeout_s"] == 2


@pytest.mark.parametrize("expired", [False, True])
def test_journal_health_transport_does_not_claim_authority_or_extend_deadline(monkeypatch, expired):
    client, clock = _client(monkeypatch)
    seen = []
    client.instance.require_live = lambda *_args, **_kwargs: pytest.fail(
        "journal owns the identity bracket around this transport",
    )

    def wire(kind, payload, timeout):
        seen.append((kind, timeout))
        clock[0] += 6 if expired else 1
        return {"instance": client.instance.evidence()}

    client._request = wire
    if expired:
        with pytest.raises(TimeoutError, match="deadline expired"):
            client.read_health_response(timeout=5)
    else:
        assert client.read_health_response(timeout=5)["instance"] == client.instance.evidence()
    assert seen == [("health", 5)]

def test_health_attestation_brackets_wire_and_rechecks_native_birth(monkeypatch):
    client, clock = _client(monkeypatch)
    seen = []

    def validate(_installation, *, read_health, environment):
        seen.append("before")
        clock[0] += 2
        read_health()
        seen.append("after")
        clock[0] += 1

    def wire(kind, payload, timeout):
        seen.append(("wire", timeout))
        return {"instance": client.instance.evidence()}

    def birth(_pid):
        seen.append("birth")
        return "1"

    client.instance.require_live = validate
    client._request = wire
    monkeypatch.setattr(watch_control.diagnostics, "process_start_time", birth)
    assert client.health(timeout=5)["instance"] == client.instance.evidence()
    assert seen == ["before", ("wire", 3), "after", "birth"]


def test_original_process_change_during_health_never_reports_ready(monkeypatch):
    client, _ = _client(monkeypatch)
    client._request = lambda *_args: {"instance": client.instance.evidence()}
    monkeypatch.setattr(watch_control.diagnostics, "process_start_time", lambda _pid: "2")
    with pytest.raises(ProcessLookupError, match="changed during"):
        client.health(timeout=5)


def test_native_capture_closes_reference_if_attestation_exhausts_deadline(monkeypatch):
    client, clock = _client(monkeypatch)
    closed = []

    def capture(_installation, *, read_health, environment):
        read_health()
        clock[0] += 6
        return SimpleNamespace(close=lambda: closed.append(True))

    client.instance.capture_exit = capture
    client._request = lambda *_args: {"instance": client.instance.evidence()}
    with pytest.raises(TimeoutError, match="deadline expired"):
        client.capture_exit(timeout=5)
    assert closed == [True]


@pytest.mark.parametrize("after", ["live", "expired", "changed"])
def test_nonterminal_control_attests_the_actual_acknowledgement(monkeypatch, after):
    client, clock = _client(monkeypatch)
    seen = []
    client.health = lambda **_kwargs: pytest.fail("control must attest its own wire exchange")

    def live(_installation, *, read_health, environment):
        seen.append("before")
        read_health()
        seen.append("after")
        if after == "expired":
            clock[0] += 6
        elif after == "changed":
            raise ProcessLookupError("fixture native instance changed after control")

    def wire(operation, timeout):
        seen.append((operation, timeout))
        return {"instance": client.instance.evidence()}

    client.instance.require_live = live
    client._control_request = wire
    if after == "live":
        assert client.control("adopt", timeout=5)["instance"] == client.instance.evidence()
    else:
        with pytest.raises(TimeoutError if after == "expired" else ProcessLookupError):
            client.control("adopt", timeout=5)
    assert seen == ["before", ("adopt", 5), "after"]


def test_drain_confirms_the_authenticated_transition_snapshot_without_another_rpc(monkeypatch):
    client, _ = _client(monkeypatch)
    client.control = lambda *_args, **_kwargs: {
        "writerHeld": False, "readReady": False, "effectsEnabled": False,
    }
    client.health = lambda **_kwargs: pytest.fail("drain already has its exact control snapshot")
    assert client.drain(timeout=5, poll=0.1, force=False)["clean"] is True


@pytest.mark.parametrize("refused", [None, "adopt", "release"])
def test_control_authorizes_writer_transition_under_its_lifecycle_lock(owners, refused):
    owner = object.__new__(watch_control.WatchControlOwner)
    owner.instance = SimpleNamespace(evidence=lambda: {"test-owned": True})
    owner._control_lock = threading.Lock()
    owner._request_context = threading.local()
    owner._terminal = threading.Event()
    seen = []

    def authorize(operation):
        seen.append((operation, owner.writer._lifecycle.locked()))
        if operation == refused:
            raise PermissionError("fixture transition refused")

    owner._authorize = authorize
    owner.writer = owners(authorize=authorize)

    def control(operation):
        return owner._control({
            "operation": operation, "instance": owner.instance.evidence(),
            "transactionId": "test-owned", "authorization": "test-owned",
            "nonce": str(uuid4()), "timeout": 5,
        })

    if refused == "adopt":
        with pytest.raises(PermissionError, match="transition refused"):
            control("adopt")
        assert not owner.writer.writer_held
        assert seen == [("adopt", True)]
        return
    control("adopt")
    control("activate")
    assert owner.writer.writer_held and owner.writer.daemon.effects_enabled
    if refused == "release":
        with pytest.raises(PermissionError, match="transition refused"):
            control("drain")
        assert owner.writer.writer_held and owner.writer.daemon.quiescent
    else:
        control("drain")
        assert not owner.writer.writer_held and owner.writer.daemon.quiescent
    assert seen == [
        ("adopt", True), ("activate", True), ("drain", True), ("release", True),
    ]
