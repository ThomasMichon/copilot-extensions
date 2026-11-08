"""Pure receiver contracts; no Git, provider effect or runtime rollout."""

import json
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agent_worktrees.lease_history import validate_transition
from agent_worktrees.lease_protocol import (
    ProtocolError, canonical_json, format_timestamp, parse_record, resource, serialize_record,
)
from agent_worktrees.lease_store import parse_coordination_record
from agent_worktrees.service_custody_protocol import (
    SENTINEL, parse_custody, serialize_custody,
)
from service_custody_support import BASE, PARENT, custody_record, task_record

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.contract("agent_worktrees.service_custody_receiver"),
]


def test_distinct_envelope_preserves_task_semantics_and_resource_slot():
    task = task_record()
    service = custody_record()
    item = resource(task.resource["kind"], task.resource["key"])
    assert parse_coordination_record(serialize_record(task), item) == task
    assert parse_coordination_record(serialize_custody(service), item) == service
    assert service.resource == task.resource
    with pytest.raises(ProtocolError, match="invalid envelope"):
        parse_record(serialize_custody(service), item)
    assert not ({"holder", "lease_id", "ttl_seconds", "expires_at"} & asdict(service).keys())


@pytest.mark.parametrize(("name", "value"), [
    ("schema_version", True), ("schema_version", 2), ("state", "released"),
    ("event", "retire"), ("custody_id", "not-a-uuid"),
    ("fleet_id", ""), ("controller_id", "\n"), ("provider_id", 123),
    ("provider_resource_id", "x" * 257), ("allocation_id", None),
    ("adoption_id", "invalid\x00"), ("predecessor", ""), ("predecessor", None),
    ("approved_digest", "receipt"), ("provider_receipt", "g" * 64),
    ("controller_receipt", []), ("accepted_at", "2026-08-06"),
    ("acceptance_margin_seconds", True), ("acceptance_margin_seconds", -1),
    ("acceptance_margin_seconds", 604801), ("acceptance_margin_seconds", 1.5),
    ("acceptance_margin_seconds", None),
])
def test_custody_malformed_fields_fail_closed(name, value):
    message = serialize_custody(custody_record(**{name: value}))
    with pytest.raises(ProtocolError):
        parse_custody(message)


@pytest.mark.parametrize("mutation", ["unknown", "missing", "duplicate", "format", "resource"])
def test_custody_requires_exact_canonical_fields(mutation):
    raw = asdict(custody_record())
    if mutation == "unknown":
        raw["ttl_seconds"] = 999999
    elif mutation == "missing":
        del raw["provider_receipt"]
    elif mutation == "resource":
        raw["resource"]["identity"] = "machine:wrong"
    body = canonical_json(raw)
    if mutation == "duplicate":
        body = body.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
    elif mutation == "format":
        body = " " + body
    with pytest.raises(ProtocolError):
        parse_custody(SENTINEL + "\n" + body)


def test_wrong_resource_and_unknown_envelopes_are_not_absent():
    message = serialize_custody(custody_record())
    with pytest.raises(ProtocolError, match="does not match its ref"):
        parse_custody(message, resource("machine", "other"))
    for unknown in (message.replace("-v1", "-v2", 1), "unknown\n{}"):
        with pytest.raises(ProtocolError, match="invalid envelope"):
            parse_coordination_record(unknown, resource("machine", "service-host"))


def transition(previous, current, parent=PARENT):
    validate_transition(previous, current, parent_oid=parent, clock_skew_seconds=10)


def test_custody_requires_exact_live_parent_and_safe_acceptance_time():
    transition(task_record(), custody_record())
    transition(task_record(), custody_record(accepted_at=format_timestamp(BASE + timedelta(seconds=50))))
    for seconds in (-1, 51):
        with pytest.raises(ProtocolError):
            transition(
                task_record(),
                custody_record(accepted_at=format_timestamp(BASE + timedelta(seconds=seconds))),
            )
    with pytest.raises(ProtocolError, match="Git parent"):
        transition(task_record(), custody_record(), parent="b" * 40)
    with pytest.raises(ProtocolError, match="live task lease"):
        transition(None, custody_record())
    with pytest.raises(ProtocolError, match="live task lease"):
        transition(replace(task_record(), state="released", event="release"), custody_record())


def test_historical_adoption_uses_recorded_margin_not_inspecting_client():
    service = custody_record(accepted_at=format_timestamp(BASE + timedelta(seconds=50)))
    for client_margin in (0, 10, 30, 120):
        validate_transition(
            task_record(), service, parent_oid=PARENT, clock_skew_seconds=client_margin,
        )
    with pytest.raises(ProtocolError, match="safe deadline"):
        validate_transition(
            task_record(), replace(service, acceptance_margin_seconds=11),
            parent_oid=PARENT, clock_skew_seconds=0,
        )


def test_extracted_task_history_retains_every_existing_event():
    task = task_record()
    transition(None, task, parent="")
    stamp = format_timestamp(BASE + timedelta(seconds=10))
    renewed = replace(
        task, event="renew", renewed_at=stamp,
        expires_at=format_timestamp(BASE + timedelta(seconds=70)),
    )
    transferred = replace(
        renewed, event="transfer", holder="next-owner", lease_id="c" * 32,
        issued_at=stamp,
    )
    released = replace(
        renewed, state="released", event="release", expires_at=stamp, ttl_seconds=0,
    )
    reacquired = replace(
        task, lease_id="d" * 32, issued_at=stamp, renewed_at=stamp,
        expires_at=format_timestamp(BASE + timedelta(seconds=70)),
    )
    late = format_timestamp(BASE + timedelta(seconds=71))
    takeover = replace(
        task, event="takeover", lease_id="e" * 32, issued_at=late, renewed_at=late,
        expires_at=format_timestamp(BASE + timedelta(seconds=131)),
    )
    for previous, current in (
        (task, renewed), (renewed, transferred), (renewed, released),
        (released, reacquired), (task, takeover),
    ):
        assert parse_record(serialize_record(current)) == current
        transition(previous, current)


@pytest.mark.parametrize("event", ["acquire", "renew", "takeover", "transfer", "release", "adopt"])
def test_service_custody_cannot_be_replaced_or_retired_by_task_protocol(event):
    current = custody_record() if event == "adopt" else replace(task_record(), event=event)
    with pytest.raises(ProtocolError, match="cannot be changed"):
        transition(custody_record(), current)


@pytest.mark.parametrize("verb", ["acquire", "borrow", "renew", "release"])
def test_cli_custody_conflict_has_protected_exit_and_no_success_output(verb, monkeypatch, capsys):
    from agent_worktrees import lease_cli
    from agent_worktrees.lease_store import ServiceCustodyConflict, ServiceCustodySnapshot

    snapshot = ServiceCustodySnapshot(ref="synthetic-ref", oid="b" * 40, record=custody_record())

    def refuse(*args, **kwargs):
        raise ServiceCustodyConflict(snapshot)

    monkeypatch.setattr(lease_cli, "load_acquisition_lease_settings", lambda **kwargs: None)
    monkeypatch.setattr(lease_cli, "load_lease_settings", lambda **kwargs: None)
    monkeypatch.setattr(
        lease_cli, "GitLeaseStore",
        lambda _settings: SimpleNamespace(acquire=refuse, renew=refuse, release=refuse),
    )
    options = ["--holder", "task-owner"] if verb in {"acquire", "borrow"} else ["--token", PARENT]
    assert lease_cli.run_lease([verb, "machine", "service-host", *options]) == 3
    result = capsys.readouterr()
    assert not result.out
    data = json.loads(result.err)
    assert data["code"] == "service_custody_conflict"
    assert data["resource"]["custody_type"] == "service"
