import asyncio
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import pytest

import fleet_manager.reverse_listener_reservations as reservations
from fleet_manager.planning import PlanError
from fleet_manager.reverse_listener_reservations import (
    Admission, Candidate, Listener, Pending, Publication, Rejected,
    ReservationRequest, reserve_reverse_listener as coordinate,
)


def reserve_reverse_listener(selected, *, owner):
    operation = coordinate(selected, owner=owner)
    try:
        operation.send(None)
    except StopIteration as completed:
        return completed.value
    else:
        raise AssertionError("pure owner fixture unexpectedly suspended")
    finally:
        operation.close()


def request(**changes):
    value = {
        "correlation": "operation-a", "participant": "participant-a", "session": "session-a",
        "owner_epoch": "epoch-a", "policy_digest": "a" * 64, "target_identity": "b" * 64,
        "expected_generation": 0, "permitted": (Listener("127.0.0.1", 24001),),
    }
    value.update(changes)
    return ReservationRequest(**value)


class JournalOwner:
    """Synthetic authoritative journal, not a production SSH or policy adapter."""

    def __init__(self, permitted=None):
        self.records = {}
        self.calls = []
        self.timeouts = []
        self.generation = 0
        self.epoch = "epoch-a"
        self.policy = "a" * 64
        self.target = "b" * 64
        self.participant = "participant-a"
        self.session = "session-a"
        self.permitted = permitted or (Listener("127.0.0.1", 24001),)
        self.permitlisten = True
        self.permitopen = True
        self.held = {}
        self.intercept = lambda stage, value: None

    def called(self, stage, value, timeout):
        assert 0 < timeout <= 30
        self.calls.append(stage)
        self.timeouts.append(timeout)
        self.intercept(stage, value)

    async def admit(self, requested, *, timeout):
        self.called("admit", requested, timeout)
        prior = self.records.get(requested.correlation)
        if prior is not None:
            if prior["request"] != requested:
                return Rejected(requested, "conflict", "request-conflict")
            return prior.get("terminal", Pending(requested))
        refused = self.current_rejection(requested)
        if refused is not None:
            return refused
        admitted = Admission(requested, 200, b"synthetic-journal-custody")
        self.records[requested.correlation] = {"request": requested, "admission": admitted}
        self.intercept("admitted", admitted)
        return admitted

    def current_rejection(self, requested):
        if requested.owner_epoch != self.epoch or requested.expected_generation != self.generation:
            return Rejected(requested, "conflict", "stale-generation")
        if (requested.policy_digest != self.policy or requested.target_identity != self.target
                or requested.participant != self.participant or requested.session != self.session
                or not self.permitlisten or not self.permitopen
                or any(item not in self.permitted for item in requested.permitted)):
            return Rejected(requested, "refused", "policy-refused")
        return None

    async def reserve(self, admitted, *, timeout):
        self.called("reserve", admitted, timeout)
        row = self.records[admitted.request.correlation]
        assert row["admission"] == admitted and "candidate" not in row
        refused = self.current_rejection(admitted.request)
        if refused is not None:
            row["terminal"] = refused
            return refused
        candidate = Candidate(admitted, admitted.request.permitted[0], "resource-a")
        row["candidate"] = candidate
        self.held[candidate.resource] = candidate
        self.intercept("bound", candidate)
        return candidate

    async def publish(self, candidate, *, timeout):
        self.called("publish", candidate, timeout)
        requested = candidate.admission.request
        row = self.records[requested.correlation]
        assert row["candidate"] == candidate and candidate.resource in self.held
        refused = self.current_rejection(requested)
        if refused is not None:
            row["rejected"] = refused
            return row["rejected"]
        self.generation += 1
        row["terminal"] = Publication(candidate, self.generation)
        self.intercept("published", row["terminal"])
        return row["terminal"]

    async def discard(self, candidate, *, timeout):
        self.called("discard", candidate, timeout)
        row = self.records[candidate.admission.request.correlation]
        assert "rejected" in row and "terminal" not in row and row["candidate"] == candidate
        assert self.held[candidate.resource] == candidate
        del self.held[candidate.resource]
        row["terminal"] = row["rejected"]
        self.intercept("discarded", candidate)
        return "discarded"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(reservations, "time", SimpleNamespace(time=lambda: 100, monotonic=lambda: 10))


def test_success_is_idempotent_without_rebinding_and_serialization_omits_custody():
    owner, selected = JournalOwner(), request()
    result = reserve_reverse_listener(selected, owner=owner)
    assert (result.state, result.reason, result.reobserve_required) == (
        "published", "owner-committed", False,
    )
    assert result.publication.generation == 1
    assert result.to_dict() == {
        "schema": "fleet-manager.reverse-listener-result", "version": 1,
        "correlation": selected.correlation, "state": "published",
        "reason": "owner-committed", "reobserve_required": False,
        "publication": {"generation": 1, "listener": {"host": "127.0.0.1", "port": 24001}},
    }
    assert reserve_reverse_listener(selected, owner=owner) == result
    assert owner.calls == ["admit", "reserve", "publish", "admit"]
    assert len(owner.held) == 1
    assert "synthetic-journal-custody" not in repr(result)


@pytest.mark.parametrize("host", ["*", "0.0.0.0", "example.test", "LOCALHOST", "127.0.0.2", ""])
def test_only_explicit_loopback_host_spellings_are_accepted(host):
    with pytest.raises(PlanError):
        Listener(host, 24001)


@pytest.mark.parametrize("port", [0, -1, 65536, True, 1.5, "24001", None])
def test_port_zero_wildcards_and_invalid_ports_are_not_grants(port):
    with pytest.raises(PlanError):
        Listener("127.0.0.1", port)


@pytest.mark.parametrize("changes", [
    {"permitted": []}, {"permitted": ()}, {"permitted": ("localhost:24001",)},
    {"permitted": (Listener("localhost", 24001),) * 2},
    {"permitted": tuple(Listener("localhost", 24000 + number) for number in range(17))},
    {"expected_generation": True}, {"expected_generation": -1},
    {"correlation": ""}, {"session": "../other"}, {"owner_epoch": ""},
    {"policy_digest": "A" * 64}, {"target_identity": "not-a-digest"},
])
def test_request_rejects_unbounded_mutable_or_invalid_scope(changes):
    with pytest.raises(PlanError):
        request(**changes)


def test_request_is_immutable_and_binds_literal_hosts_order_identity_and_generation():
    selected = request(permitted=(Listener("localhost", 24001), Listener("127.0.0.1", 24002)))
    with pytest.raises(FrozenInstanceError):
        selected.correlation = "other"
    assert selected.request_digest == replace(selected).request_digest
    for changes in (
        {"permitted": tuple(reversed(selected.permitted))},
        {"session": "session-b"}, {"participant": "participant-b"},
        {"owner_epoch": "epoch-b"}, {"expected_generation": 1},
        {"policy_digest": "c" * 64}, {"target_identity": "d" * 64},
    ):
        assert replace(selected, **changes).request_digest != selected.request_digest


@pytest.mark.parametrize("changes", [
    {"owner_epoch": "old-epoch"}, {"expected_generation": 1},
    {"policy_digest": "c" * 64}, {"target_identity": "d" * 64},
    {"participant": "participant-b"}, {"session": "session-b"},
    {"permitted": (Listener("localhost", 24001),)},
])
def test_current_owner_refuses_stale_or_unauthorized_request_before_binding(changes):
    owner = JournalOwner()
    result = reserve_reverse_listener(request(**changes), owner=owner)
    assert result.state in {"conflict", "refused"}
    assert result.reobserve_required is False and owner.held == {}
    assert owner.calls == ["admit"]


@pytest.mark.parametrize("field", ["permitlisten", "permitopen"])
@pytest.mark.parametrize("stage", ["admitted", "bound"])
def test_withdrawn_direction_grant_is_readmitted_before_bind_and_publication(field, stage):
    owner, selected = JournalOwner(), request()

    def withdraw(current, value):
        if current == stage:
            setattr(owner, field, False)

    owner.intercept = withdraw
    result = reserve_reverse_listener(selected, owner=owner)
    assert (result.state, result.reason) == ("refused", "policy-refused")
    assert owner.held == {}
    assert owner.calls.count("publish") == (0 if stage == "admitted" else 1)
    assert owner.calls.count("discard") == (0 if stage == "admitted" else 1)


def test_changed_request_cannot_reuse_a_committed_correlation_or_close_its_resource():
    owner, selected = JournalOwner(), request()
    first = reserve_reverse_listener(selected, owner=owner)
    result = reserve_reverse_listener(replace(selected, session="session-b"), owner=owner)
    assert (result.state, result.reason) == ("conflict", "request-conflict")
    assert len(owner.held) == 1 and owner.calls.count("reserve") == 1
    assert first.publication.candidate.resource in owner.held


@pytest.mark.parametrize("stage", ["admitted", "bound", "published"])
@pytest.mark.parametrize("error", [TimeoutError, OSError, PermissionError])
def test_unknown_effects_retain_original_correlation_without_retry_or_speculative_discard(stage, error):
    owner, selected = JournalOwner(), request()

    def fail(current, value):
        if current == stage:
            raise error("private credential and path must not escape")

    owner.intercept = fail
    result = reserve_reverse_listener(selected, owner=owner)
    assert result.state == "unknown" and result.reobserve_required
    assert result.correlation == selected.correlation
    assert "private" not in str(result.to_dict())
    assert "discard" not in owner.calls
    owner.intercept = lambda current, value: None
    again = reserve_reverse_listener(selected, owner=owner)
    assert again.state == ("published" if stage == "published" else "unknown")
    assert owner.calls.count("reserve") == (0 if stage == "admitted" else 1)
    assert len(owner.held) == (0 if stage == "admitted" else 1)


@pytest.mark.parametrize("stage", ["bound", "published"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt, asyncio.CancelledError])
def test_programming_errors_and_interruption_propagate_without_losing_journal_custody(stage, error):
    owner, selected = JournalOwner(), request()

    def fail(current, value):
        if current == stage:
            raise error("unexpected programming error")

    owner.intercept = fail
    with pytest.raises(error):
        reserve_reverse_listener(selected, owner=owner)
    owner.intercept = lambda current, value: None
    again = reserve_reverse_listener(selected, owner=owner)
    assert again.state == ("published" if stage == "published" else "unknown")
    assert owner.calls.count("reserve") == 1 and "discard" not in owner.calls
    assert len(owner.held) == 1


@pytest.mark.parametrize("field,value,state", [
    ("generation", 1, "conflict"), ("epoch", "epoch-b", "conflict"),
    ("policy", "c" * 64, "refused"), ("target", "c" * 64, "refused"),
])
def test_publish_readmits_current_owner_scope_and_discards_only_confirmed_unpublished(field, value, state):
    owner, selected = JournalOwner(), request()

    def change(stage, candidate):
        if stage == "bound":
            setattr(owner, field, value)

    owner.intercept = change
    result = reserve_reverse_listener(selected, owner=owner)
    assert result.state == state and owner.held == {}
    assert owner.calls == ["admit", "reserve", "publish", "discard"]
    again = reserve_reverse_listener(selected, owner=owner)
    assert again == result and owner.calls.count("reserve") == 1


@pytest.mark.parametrize("stage", ["discard", "discarded"])
def test_lost_discard_acknowledgement_is_unknown_not_a_new_allocation(stage):
    owner, selected = JournalOwner(), request()

    def change(current, value):
        if current == "bound":
            owner.generation += 1
        if current == stage:
            raise TimeoutError("lost cleanup acknowledgement")

    owner.intercept = change
    first = reserve_reverse_listener(selected, owner=owner)
    assert first.state == "unknown"
    owner.intercept = lambda current, value: None
    again = reserve_reverse_listener(selected, owner=owner)
    assert again.state == ("conflict" if stage == "discarded" else "unknown")
    assert owner.calls.count("reserve") == 1 and owner.calls.count("discard") == 1


@pytest.mark.parametrize("method", ["admit", "reserve", "publish"])
def test_malformed_owner_receipt_is_explicit_unknown_and_never_closes_a_candidate(method):
    owner = JournalOwner()

    async def malformed(*args, **kwargs):
        return object()

    setattr(owner, method, malformed)
    result = reserve_reverse_listener(request(), owner=owner)
    assert result.state == "unknown" and result.reobserve_required
    assert "discard" not in owner.calls


def test_receipt_for_another_operation_cannot_publish_or_discard_this_resource():
    owner, selected = JournalOwner(), request()

    async def foreign(candidate, *, timeout):
        admission = replace(candidate.admission, request=replace(selected, correlation="operation-b"))
        return Publication(replace(candidate, admission=admission), 1)

    owner.publish = foreign
    result = reserve_reverse_listener(selected, owner=owner)
    assert (result.state, result.reason) == ("unknown", "owner-receipt-mismatch")
    assert len(owner.held) == 1 and "discard" not in owner.calls


def test_expiry_before_bind_is_inert_and_after_bind_retains_unknown_custody(monkeypatch):
    for when in ("admitted", "bound"):
        owner, selected = JournalOwner(), request()
        now = [100]
        monkeypatch.setattr(reservations, "time", SimpleNamespace(
            time=lambda: now[0], monotonic=lambda: 10,
        ))

        def expire(stage, value):
            if stage == when:
                now[0] = 200

        owner.intercept = expire
        result = reserve_reverse_listener(selected, owner=owner)
        assert (result.state, result.reason) == ("unknown", "owner-admission-expired")
        assert "publish" not in owner.calls and "discard" not in owner.calls
        assert len(owner.held) == (0 if when == "admitted" else 1)


def test_one_absolute_budget_never_restarts_for_a_later_stage(monkeypatch):
    owner, selected = JournalOwner(), request()
    clock = [0.0]
    monkeypatch.setattr(reservations, "time", SimpleNamespace(
        time=lambda: 100, monotonic=lambda: clock[0],
    ))

    def spend(stage, value):
        if stage in {"admitted", "bound", "published"}:
            clock[0] += 11

    owner.intercept = spend
    first = reserve_reverse_listener(selected, owner=owner)
    assert (first.state, first.reason) == ("unknown", "publication-timeout")
    assert owner.timeouts == [30, 19, 8]
    assert len(owner.held) == 1 and "discard" not in owner.calls
    owner.intercept = lambda stage, value: None
    assert reserve_reverse_listener(selected, owner=owner).state == "published"
    assert owner.calls.count("reserve") == 1


def test_float_rounding_never_increases_the_owner_timeout(monkeypatch):
    import math

    instant = math.nextafter(256.0, 0.0)
    assert (instant + 30) - instant > 30
    monkeypatch.setattr(reservations, "time", SimpleNamespace(
        time=lambda: 100, monotonic=lambda: instant,
    ))
    owner = JournalOwner()
    assert reserve_reverse_listener(request(), owner=owner).state == "published"
    assert all(timeout <= 30 for timeout in owner.timeouts)
