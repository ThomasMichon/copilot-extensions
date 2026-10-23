"""Conditional recovery depends on the authorized view, not global activity."""

from contextlib import contextmanager
from dataclasses import FrozenInstanceError, asdict, replace
import json
import sqlite3

import pytest

from fleet_manager import observation_reader
from fleet_manager.observation_reader import ScopedObservationReader, ScopedObservationRefresh
from fleet_manager.observation_store import MAX_POINT_READS, MAX_REPLAY_EVENTS
from fleet_manager.planning import PlanError
from fleet_manager.runtime_admission import AdmissionAuthority, ObservationRead
from fleet_manager.runtime_identity import IdentityVerifier
from test_observation_reader import update
from test_observation_snapshot import point_keys, snapshot as snapshot
from test_observation_store import seed_updates
from test_runtime_admission import A, B, actor, policy, refuses
from test_runtime_identity import NOW, TARGET, pair
from test_runtime_identity import keys as keys


def refresh(harness, selection=None, *, subject="principal-1", identity=None,
            validator=None, role="client", installation="client-a"):
    return harness.reader.refresh(
        identity or harness.identity(subject),
        selection if selection is not None else ObservationRead("fleet-a", (A, B)),
        role=role, installation=installation, validator=validator,
    )


def test_complete_and_unchanged_results_are_exact_immutable_and_not_cursors(snapshot):
    first = refresh(snapshot)
    assert type(first) is ScopedObservationRefresh and first.status == "snapshot"
    assert len(first.validator) == 64
    assert tuple(point.scope for point in first.observations) == (A, B)
    assert first.observations[0].state == (("status", "active"),)
    assert first.observations[1].state == (("title", "private-origin-b"),)
    unchanged = refresh(snapshot, validator=first.validator)
    assert unchanged == ScopedObservationRefresh(first.validator, "unchanged", ())
    baseline = first.observations if unchanged.status == "unchanged" else unchanged.observations
    assert baseline == first.observations
    assert refresh(snapshot).observations == baseline
    assert snapshot.batches == [point_keys((A, B))] * 3
    assert not snapshot.owner._issued
    with pytest.raises(FrozenInstanceError):
        first.status = "unchanged"
    with pytest.raises(TypeError):
        first.observations[0].state[0] = ("status", "changed")
    encoded = json.dumps(asdict(first))
    for forbidden in ("cursor", "position", "sequence", "store", "producer",
                      "installation", "account", "enrollment", "permit", "private-origin-a"):
        assert forbidden not in encoded


@pytest.mark.parametrize("validator", ["", "0" * 63, "0" * 65, "F" * 64,
                                    "g" * 64, "0" * 64 + "\n", b"0" * 64, 0, [], {}])
def test_malformed_validator_never_acquires_fence_or_reads(snapshot, validator):
    refuses("unsupported-read", refresh, snapshot, validator=validator)
    assert not snapshot.batches and not snapshot.fence.timeouts


@pytest.mark.parametrize("selection", [
    None, object(), ObservationRead("fleet-a"), ObservationRead("fleet-a", ()),
    ObservationRead("fleet-a", (A, replace(A, fields=("title",)))),
    ObservationRead("fleet-a", tuple(
        replace(A, entity=f"entity-{i}") for i in range(MAX_POINT_READS + 1)
    )),
])
def test_unsupported_requests_never_acquire_fence_or_read(snapshot, selection):
    refuses("unsupported-read", snapshot.reader.refresh, snapshot.identity(), selection,
            role="client", installation="client-a", validator="0" * 64)
    assert not snapshot.batches and not snapshot.fence.timeouts


def test_foreign_and_random_validators_are_not_validity_or_authorization_oracles(snapshot):
    first = refresh(snapshot)
    for validator in ("0" * 64, "1" * 64):
        assert refresh(snapshot, validator=validator) == first
    second = refresh(snapshot, ObservationRead("fleet-a", (B,)), subject="principal-2",
                     validator=first.validator)
    assert second.status == "snapshot" and len(second.observations) == 1
    assert second.validator != first.validator
    snapshot.batches.clear()
    refuses("forbidden-scope", refresh, snapshot, subject="principal-2",
            validator=first.validator)
    assert not snapshot.batches and not snapshot.owner._issued


def test_fresh_equivalent_proofs_and_unrelated_policy_changes_preserve_validator(snapshot):
    first = refresh(snapshot)
    snapshot.clock.value += 1
    unrelated = actor("unrelated", principal="other", account="other", reads=(B,))
    snapshot.current = policy(revision=2, enrollments=(actor(), unrelated))
    result = refresh(snapshot, validator=first.validator)
    assert result.status == "unchanged" and result.validator == first.validator
    assert snapshot.calls == 4 and not snapshot.owner._issued


@pytest.mark.parametrize("change", [
    {"generation": 2}, {"principal": "new-principal"}, {"account": "new-account"},
    {"role": "new-role"}, {"installation": "new-installation"}, {"kind": "automation"},
    {"subject": "principal-2"},
])
def test_each_independently_admitted_enrollment_binding_invalidates_baseline(snapshot, change):
    first = refresh(snapshot)
    snapshot.current = policy(revision=2, enrollments=(actor(**change),))
    snapshot.reader._authority = snapshot.owner = AdmissionAuthority(
        snapshot=snapshot.snapshot, clock=snapshot.clock, capacity=1, fence=snapshot.fence,
    )
    result = refresh(
        snapshot, validator=first.validator, subject=change.get("subject", "principal-1"),
        role=change.get("role", "client"), installation=change.get("installation", "client-a"),
    )
    assert result.status == "snapshot" and result.observations == first.observations
    assert result.validator != first.validator
    assert not snapshot.owner._issued


@pytest.mark.parametrize("identity_field", ["issuer", "audience"])
def test_equivalent_authorized_identity_domains_do_not_share_validators(snapshot, identity_field):
    first = refresh(snapshot)
    current = snapshot.current
    domain = "https://other.example" if identity_field == "issuer" else "other-audience"
    snapshot.current = replace(current, **{identity_field: domain})
    snapshot.owner = AdmissionAuthority(
        snapshot=snapshot.snapshot, clock=snapshot.clock, capacity=1, fence=snapshot.fence,
    )
    # Keep the reader's namespace identical to isolate domain binding from key rotation.
    snapshot.reader._authority = snapshot.owner
    verifier = IdentityVerifier(
        issuer=snapshot.current.issuer, audience=snapshot.current.audience,
        jwks={"keys": [{**snapshot.keys[0].as_dict(private=False), "kid": "issuer-1"}]},
        trust_valid_until=NOW + 3600, clock=snapshot.clock,
    )
    identity = verifier.verify(*pair(
        snapshot.keys, verifier,
        access={"iss": snapshot.current.issuer, "aud": snapshot.current.audience},
    ), method="GET", target_uri=TARGET)
    result = refresh(snapshot, identity=identity, validator=first.validator)
    assert result.status == "snapshot" and result.validator != first.validator
    assert result.observations == first.observations


def test_selection_and_reader_instance_are_distinct_baseline_domains(snapshot):
    first = refresh(snapshot)
    narrowed = refresh(snapshot, ObservationRead("fleet-a", (A,)), validator=first.validator)
    assert narrowed.status == "snapshot" and narrowed.validator != first.validator
    assert narrowed.observations == first.observations[:1]
    replacement = ScopedObservationReader(
        authority=snapshot.owner, store=snapshot.store, fence=snapshot.fence,
    )
    prior_key = snapshot.reader._refresh_key
    assert len(prior_key) == len(replacement._refresh_key) == 32
    assert replacement._refresh_key != prior_key
    snapshot.reader = replacement
    restarted = refresh(snapshot, validator=first.validator)
    assert restarted.status == "snapshot" and restarted.validator != first.validator
    assert restarted.observations == first.observations
    assert set(vars(replacement)) == {"_authority", "_store", "_fence", "_refresh_key"}


def test_fleet_binding_is_not_reused_with_an_independently_authorized_request(snapshot):
    first = refresh(snapshot)
    snapshot.current = replace(snapshot.current, fleet="other-fleet")
    snapshot.reader._authority = snapshot.owner = AdmissionAuthority(
        snapshot=snapshot.snapshot, clock=snapshot.clock, capacity=1, fence=snapshot.fence,
    )
    result = refresh(snapshot, ObservationRead("other-fleet", (A, B)), validator=first.validator)
    assert result.status == "snapshot" and result.validator != first.validator
    assert result.observations == first.observations


def test_refresh_baseline_distinguishes_missing_null_removal_and_origin_resync(snapshot):
    wide = replace(A, fields=("status", "checkout_present"))
    missing = replace(A, entity="missing")
    snapshot.current = policy(revision=2, enrollments=(actor(reads=(wide, missing)),))
    selection = ObservationRead("fleet-a", (wide, missing))
    first = refresh(snapshot, selection)
    assert first.observations[0].state == (("status", "active"),)
    assert first.observations[1].reason == "unobserved"
    binding = snapshot.bindings[A.origin]
    for sequence, state, reason in (
        (2, {"status": "active", "checkout_present": None}, None),
        (3, {"status": "active", "checkout_present": True}, None),
        (4, None, "projection-removed"),
    ):
        snapshot.store.apply(update(binding, A, sequence=sequence, state=state, reason=reason))
        result = refresh(snapshot, selection, validator=first.validator)
        assert result.status == "snapshot" and result.validator != first.validator
        assert result.observations[1].reason == "unobserved"
        point = result.observations[0]
        if state is None:
            assert point.state is None and point.reason == "projection-removed"
        else:
            assert point.state == (("checkout_present", state["checkout_present"]),
                                   ("status", "active"))
        first = result
    snapshot.store.bind_origin(replace(binding, generation=2, producer="next"), expected=binding)
    resync = refresh(snapshot, selection, validator=first.validator)
    assert resync.status == "snapshot" and resync.validator != first.validator
    assert resync.observations[0].generation == 1
    assert resync.observations[0].coverage == "origin-resync-required"
    identity = snapshot.identity()
    assert refresh(snapshot, selection, identity=identity).status == "snapshot"
    refuses("identity-reused", refresh, snapshot, selection, identity=identity,
            validator=resync.validator)
    assert not snapshot.owner._issued


def test_unrelated_retention_overflow_does_not_disclose_a_recovery_gap(snapshot):
    selection = ObservationRead("fleet-a", (A,))
    first = refresh(snapshot, selection)
    old = snapshot.original_read(origin=A.origin, project=A.project, entity=A.entity)["cursor"]
    binding = snapshot.bindings[B.origin]
    seed_updates(snapshot.store, (
        update(binding, B, sequence=sequence)
        for sequence in range(2, MAX_REPLAY_EVENTS + 3)
    ))
    with sqlite3.connect(snapshot.store.path) as connection:
        head, floor = connection.execute("SELECT position,floor FROM identity").fetchone()
    assert floor > old.position and head - floor == MAX_REPLAY_EVENTS == 1024
    with pytest.raises(PlanError, match="expired.*fresh snapshot"):
        snapshot.store.replay(old)
    result = refresh(snapshot, selection, validator=first.validator)
    assert result == ScopedObservationRefresh(first.validator, "unchanged", ())


@pytest.mark.parametrize("field,value", [
    ("generation", 2), ("source_revision", 2), ("observed_at", NOW + 1),
    ("availability", "stale"), ("reason", "source-delayed"),
    ("coverage", "origin-resync-required"),
    ("state", (("status", "paused"),)), ("state", None),
])
def test_every_visible_metadata_field_changes_the_validator(snapshot, monkeypatch, field, value):
    first = refresh(snapshot)
    original = snapshot.reader._snapshot

    def changed(*args):
        points = original(*args)
        return (replace(points[0], **{field: value}), *points[1:])

    monkeypatch.setattr(snapshot.reader, "_snapshot", changed)
    result = refresh(snapshot, validator=first.validator)
    assert result.status == "snapshot" and result.validator != first.validator
    assert getattr(result.observations[0], field) == value


def test_unselected_payload_is_excluded_without_skipping_complete_row_validation(snapshot):
    selection = ObservationRead("fleet-a", (A,))
    first = refresh(snapshot, selection)
    binding = snapshot.bindings[A.origin]
    candidate = update(binding, A, state={"status": "active", "title": "changed"})
    # An isolated valid row holds visible metadata constant, without claiming
    # conflicting same-revision updates are accepted by ingestion.
    with sqlite3.connect(snapshot.store.path) as connection:
        connection.execute(
            "UPDATE current SET payload=? WHERE origin=?", (candidate.encoded, A.origin),
        )
    result = refresh(snapshot, selection, validator=first.validator)
    assert result.status == "unchanged"
    with sqlite3.connect(snapshot.store.path) as connection:
        value = candidate.to_dict()
        value["state"]["title"] = {"invalid": "unselected"}
        connection.execute(
            "UPDATE current SET payload=? WHERE origin=?",
            (json.dumps(value).encode(), A.origin),
        )
    with pytest.raises(PlanError):
        refresh(snapshot, selection, validator=first.validator)
    assert not snapshot.owner._issued


@pytest.mark.parametrize("baseline", [False, True])
@pytest.mark.parametrize("change,code", [
    ("revoked", "revoked-enrollment"), ("generation", "enrollment-changed"),
    ("scope", "forbidden-scope"), ("policy", "stale-policy"),
    ("trust", "stale-identity-trust"), ("expiry", "expired-permit"),
    ("clock", "clock-rollback"), ("unavailable", "authority-unavailable"),
])
def test_terminal_checkpoint_withholds_both_prepared_result_statuses(
    snapshot, monkeypatch, baseline, change, code,
):
    first = refresh(snapshot)
    original = snapshot.original_points

    def changed(points):
        result = original(points)
        if change == "expiry":
            snapshot.clock.value += 30
        elif change == "clock":
            snapshot.clock.value -= 1
        elif change == "unavailable":
            snapshot.current = None
        else:
            changes = {"revision": 2}
            if change == "revoked":
                changes["enrollments"] = (actor(status="revoked"),)
            elif change == "generation":
                changes["enrollments"] = (actor(generation=2),)
            elif change == "scope":
                changes["enrollments"] = (actor(reads=(B,)),)
            elif change == "policy":
                changes["valid_until"] = NOW
            elif change == "trust":
                changes["identity_trust_until"] = NOW
            snapshot.current = policy(**changes)
        return result

    monkeypatch.setattr(snapshot.store, "read_points", changed)
    refuses(code, refresh, snapshot, validator=first.validator if baseline else None)
    assert not snapshot.owner._issued
    assert snapshot.fence.releases == len(snapshot.fence.timeouts)


@pytest.mark.parametrize("stage", ["storage", "serialization", "owner"])
def test_errors_propagate_and_permit_capacity_recovers(snapshot, monkeypatch, stage):
    def failed(*_):
        raise RuntimeError(f"{stage}-failed")

    with monkeypatch.context() as patch:
        if stage == "storage":
            patch.setattr(snapshot.store, "read_points", failed)
        elif stage == "serialization":
            patch.setattr(observation_reader, "canonical", failed)
        else:
            original = snapshot.owner._snapshot
            calls = 0

            def owner():
                nonlocal calls
                calls += 1
                return original() if calls == 1 else failed()

            patch.setattr(snapshot.owner, "_snapshot", owner)
        with pytest.raises(RuntimeError, match=f"{stage}-failed"):
            refresh(snapshot)
    assert not snapshot.owner._issued
    assert refresh(snapshot).status == "snapshot"


def test_refresh_at_maximum_points_uses_one_transaction_and_no_history(snapshot, monkeypatch):
    scopes = tuple(replace(A, entity=f"entity-{i:02}") for i in range(MAX_POINT_READS))
    seed_updates(snapshot.store, [
        update(snapshot.bindings[A.origin], scope, sequence=i + 2,
               state={field: "x" * 4096 for field in (
                   "status", "summary", "title", "branch", "codename",
               )})
        for i, scope in enumerate(scopes)
    ])
    scopes = tuple(replace(scope, fields=(
        "status", "summary", "title", "branch", "codename",
    )) for scope in scopes)
    snapshot.current = policy(revision=2, enrollments=(actor(reads=scopes),))
    transactions, queries = [], []
    original = snapshot.store._transaction

    @contextmanager
    def transaction(**options):
        transactions.append(options)
        with original(**options) as state:
            assert state[0].execute("PRAGMA query_only").fetchone()[0] == 1
            state[0].set_trace_callback(queries.append)
            yield state

    for method in ("read", "page", "replay"):
        monkeypatch.setattr(snapshot.store, method, lambda *_, **__: pytest.fail("unscoped API"))
    monkeypatch.setattr(snapshot.store, "_transaction", transaction)
    result = refresh(snapshot, ObservationRead("fleet-a", scopes))
    assert len(result.observations) == MAX_POINT_READS
    assert sum(len(value) for point in result.observations
               for _, value in point.state) > 1024 * 1024
    assert transactions == [{"write": False}]
    assert len([query for query in queries if " FROM current " in query]) == MAX_POINT_READS
    assert not any(" FROM changes " in query or " FROM streams " in query for query in queries)
    assert not snapshot.owner._issued
