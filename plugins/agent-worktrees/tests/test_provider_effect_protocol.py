"""Pure shared-slot effect contracts; no assertion of provider deployment."""

import json
from dataclasses import asdict, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from agent_worktrees.lease_config import LeaseSettings
from agent_worktrees.lease_history import ResourceHistory, validate_history
from agent_worktrees.lease_protocol import ProtocolError, canonical_json, format_timestamp, resource
from agent_worktrees.lease_store import (
    GitLeaseStore, LeaseLost, ProviderEffectConflict, ProviderEffectSnapshot,
    parse_coordination_record,
)
from agent_worktrees.provider_effect_protocol import (
    SENTINEL, ProviderEffectRecord, parse_effect, serialize_effect,
)
from service_custody_support import BASE, PARENT, custody_record, task_record

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.contract("agent_worktrees.provider_effect_custody"),
]

CLAIM = "b" * 40
SETTLED = "c" * 40


def begin_record(**changes):
    return replace(ProviderEffectRecord(
        schema_version=1, resource=task_record().resource,
        state="effect-unsettled", event="begin", effect_id="c" * 32,
        owner_id="native-owner", provider_id="example-provider",
        provider_resource_id="immutable-instance", action="stop",
        request_digest="1" * 64, task_token=PARENT, predecessor=PARENT,
        started_at=format_timestamp(BASE + timedelta(seconds=10)),
        admission_margin_seconds=10,
    ), **changes)


def settle_record(**changes):
    return replace(begin_record(), state="effect-settled", event="settle",
                   predecessor=CLAIM, settled_at=format_timestamp(BASE + timedelta(seconds=20)),
                   outcome="succeeded", settlement_digest="2" * 64, **changes)


def history(*entries):
    return validate_history([(PARENT, task_record()), *entries], clock_skew_seconds=10)


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", 2), ("effect_id", "x"),
    ("owner_id", ""), ("provider_id", "\n"), ("provider_resource_id", "x" * 257),
    ("action", "anything"), ("action", []), ("request_digest", "x"),
    ("task_token", None), ("predecessor", ""), ("started_at", "invalid"),
    ("admission_margin_seconds", True), ("admission_margin_seconds", -1),
    ("admission_margin_seconds", 604801), ("state", "leased"),
    ("event", "settle"), ("settled_at", format_timestamp(BASE)),
    ("outcome", "succeeded"), ("settlement_digest", "2" * 64),
])
def test_invalid_effect_fields_are_not_absence(field, value):
    with pytest.raises(ProtocolError):
        parse_effect(serialize_effect(begin_record(**{field: value})))


@pytest.mark.parametrize("mutation", ["unknown", "missing", "duplicate", "format", "resource", "version"])
def test_effect_envelope_is_strict_and_selected_by_canonical_resource(mutation):
    raw = asdict(begin_record())
    if mutation == "unknown":
        raw["expires_at"] = format_timestamp(BASE)
    elif mutation == "missing":
        del raw["outcome"]
    elif mutation == "resource":
        raw["resource"]["identity"] = "machine:wrong"
    body = canonical_json(raw)
    if mutation == "duplicate":
        body = body.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
    elif mutation == "format":
        body = " " + body
    sentinel = SENTINEL.replace("v1", "v2") if mutation == "version" else SENTINEL
    with pytest.raises(ProtocolError):
        parse_coordination_record(sentinel + "\n" + body, resource("machine", "service-host"))


def test_unsettled_effect_blocks_every_custody_successor_without_a_timer():
    pending = begin_record()
    assert history((CLAIM, pending)).current == pending
    for next_record in (
        task_record(), custody_record(predecessor=CLAIM),
        begin_record(effect_id="d" * 32, predecessor=CLAIM, task_token=CLAIM),
    ):
        with pytest.raises(ProtocolError, match="unsettled effect"):
            history((CLAIM, pending), ("d" * 40, next_record))


def test_settlement_preserves_task_generation_and_accepts_exact_adoption_parent():
    pending, settled = begin_record(), settle_record()
    assert parse_effect(serialize_effect(settled)) == settled
    projected = history((CLAIM, pending), (SETTLED, settled))
    assert projected.current == task_record()
    assert projected.effects == ((CLAIM, pending), (SETTLED, settled))
    adopted = custody_record(predecessor=SETTLED)
    assert history((CLAIM, pending), (SETTLED, settled), ("d" * 40, adopted)).current == adopted
    with pytest.raises(ProtocolError, match="live task lease"):
        validate_history([
            (PARENT, task_record()), ("d" * 40, custody_record()),
            (CLAIM, begin_record(task_token="d" * 40, predecessor="d" * 40)),
        ], clock_skew_seconds=10)


@pytest.mark.parametrize(("field", "value"), [
    ("effect_id", "d" * 32), ("owner_id", "other-owner"),
    ("provider_resource_id", "other-instance"), ("action", "delete"),
    ("request_digest", "3" * 64), ("task_token", "e" * 40),
    ("predecessor", "e" * 40),
])
def test_settlement_cannot_change_attempt_or_owner(field, value):
    settled = replace(settle_record(), **{field: value})
    with pytest.raises(ProtocolError, match="settlement"):
        history((CLAIM, begin_record()), (SETTLED, settled))


def test_unpaired_settlement_reused_attempt_and_unsafe_admission_are_refused():
    with pytest.raises(ProtocolError, match="requires an unsettled effect"):
        history((SETTLED, settle_record()))
    with pytest.raises(ProtocolError, match="cannot be reused"):
        history((CLAIM, begin_record()), (SETTLED, settle_record()),
                ("d" * 40, begin_record(task_token=SETTLED, predecessor=SETTLED)))
    for second in (-1, 51):
        with pytest.raises(ProtocolError, match="safe interval"):
            history((CLAIM, begin_record(started_at=format_timestamp(BASE + timedelta(seconds=second)))))
    with pytest.raises(ProtocolError, match="Git parent"):
        history((CLAIM, begin_record(task_token="e" * 40)))


@pytest.mark.parametrize("condition", ["absent", "stale-token", "unsafe-time", "reused-id"])
def test_begin_rejects_unadmitted_attempt_before_any_push(condition, monkeypatch):
    task = task_record()
    effects = () if condition != "reused-id" else ((SETTLED, settle_record()),)
    oid = SETTLED if condition == "reused-id" else PARENT
    selected = None if condition == "absent" else ("synthetic-ref", oid, ResourceHistory(task, effects))
    client = GitLeaseStore(
        LeaseSettings(origin="https://example.invalid/state.git", clock_skew_seconds=10),
        now=lambda: BASE + timedelta(seconds=51 if condition == "unsafe-time" else 10),
    )
    monkeypatch.setattr(client, "_inspect_history", lambda _item: selected)
    monkeypatch.setattr(client, "_transition", lambda *args: pytest.fail("unadmitted effect was pushed"))
    with pytest.raises((LeaseLost, ProtocolError)):
        client.begin_effect(
            "machine", "service-host", "e" * 40 if condition == "stale-token" else oid,
            effect_id="c" * 32, owner_id="native-owner", provider_id="example-provider",
            provider_resource_id="immutable-instance", action="stop", request_digest="1" * 64,
        )


@pytest.mark.parametrize("margin", [0, 10, 30, 120])
def test_each_effect_freezes_only_its_preceding_task_prefix(margin):
    task = task_record()
    first_transfer = replace(
        task, event="transfer", lease_id="d" * 32, holder="second-owner",
        issued_at=format_timestamp(BASE + timedelta(seconds=50)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=50)),
        expires_at=format_timestamp(BASE + timedelta(seconds=110)),
    )
    pending = begin_record(task_token="d" * 40, predecessor="d" * 40,
                           started_at=format_timestamp(BASE + timedelta(seconds=60)))
    settled = replace(
        pending, state="effect-settled", event="settle", predecessor=CLAIM,
        settled_at=format_timestamp(BASE + timedelta(seconds=70)),
        outcome="succeeded", settlement_digest="2" * 64,
    )
    prefix = [(PARENT, task), ("d" * 40, first_transfer), (CLAIM, pending), (SETTLED, settled)]
    assert validate_history(prefix, clock_skew_seconds=margin).current == first_transfer
    second_transfer = replace(
        first_transfer, lease_id="e" * 32, holder="third-owner",
        issued_at=format_timestamp(BASE + timedelta(seconds=80)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=80)),
        expires_at=format_timestamp(BASE + timedelta(seconds=140)),
    )
    second = replace(
        pending, effect_id="f" * 32, task_token="e" * 40, predecessor="e" * 40,
        started_at=format_timestamp(BASE + timedelta(seconds=90)),
        admission_margin_seconds=30,
    )
    assert validate_history(
        [*prefix, ("e" * 40, second_transfer), ("f" * 40, second)],
        clock_skew_seconds=margin,
    ).current == second
    # A later task transition without a following effect retains reader policy.
    unsafe = replace(
        second_transfer, issued_at=format_timestamp(BASE + timedelta(seconds=90)),
        renewed_at=format_timestamp(BASE + timedelta(seconds=90)),
    )
    with pytest.raises(ProtocolError, match="safe deadline"):
        validate_history([*prefix, ("e" * 40, unsafe)], clock_skew_seconds=30)


@pytest.mark.parametrize("margin", [0, 10])
@pytest.mark.parametrize("offset_ms", [-500, 0, 500])
def test_effect_admission_checks_unrounded_safe_deadline(margin, offset_ms, monkeypatch):
    now = BASE + timedelta(seconds=60 - margin, milliseconds=offset_ms)
    client = GitLeaseStore(
        LeaseSettings(origin="https://example.invalid/state.git", clock_skew_seconds=margin),
        now=lambda: now,
    )
    monkeypatch.setattr(
        client, "_inspect_history",
        lambda _item: ("synthetic-ref", PARENT, ResourceHistory(task_record(), ())),
    )
    written = []
    monkeypatch.setattr(client, "_transition", lambda *args: written.append(args) or CLAIM)

    def admit():
        return client.begin_effect(
            "machine", "service-host", PARENT, effect_id="c" * 32, owner_id="native-owner",
            provider_id="example-provider", provider_resource_id="immutable-instance",
            action="stop", request_digest="1" * 64,
        )

    if offset_ms > 0:
        with pytest.raises(LeaseLost, match="safe deadline"):
            admit()
        assert not written
    else:
        assert admit().record.started_at == format_timestamp(now)
        assert len(written) == 1


@pytest.mark.parametrize("verb", ["acquire", "borrow", "renew", "release"])
def test_cli_unsettled_effect_conflict_is_structured_without_success(verb, monkeypatch, capsys):
    from agent_worktrees import lease_cli

    snapshot = ProviderEffectSnapshot("synthetic-ref", CLAIM, begin_record())

    def refuse(*args, **kwargs):
        raise ProviderEffectConflict(snapshot)

    monkeypatch.setattr(lease_cli, "load_acquisition_lease_settings", lambda **kwargs: None)
    monkeypatch.setattr(lease_cli, "load_lease_settings", lambda **kwargs: None)
    monkeypatch.setattr(lease_cli, "GitLeaseStore",
                        lambda _settings: SimpleNamespace(acquire=refuse, renew=refuse, release=refuse))
    options = ["--holder", "task-owner"] if verb in {"acquire", "borrow"} else ["--token", PARENT]
    assert lease_cli.run_lease([verb, "machine", "service-host", *options]) == 3
    captured = capsys.readouterr()
    assert not captured.out
    assert json.loads(captured.err)["code"] == "provider_effect_conflict"
