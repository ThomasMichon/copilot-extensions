"""Promotion custody survives route churn and unavailable negative evidence."""

from __future__ import annotations

import json
import time
from copy import deepcopy

import pytest

from zdd import breadcrumb, publication_history, routing


def _publish(root, pid, port):
    return routing.publish_active(
        root, bind="127.0.0.1", port=port, pid=pid, version="fixture",
        demote_existing=True,
    )


@pytest.mark.parametrize("row", ["active", "previous"])
@pytest.mark.parametrize("value", [{}, False])
@pytest.mark.parametrize("operation", ["witness", "publish", "guarded", "atomic"])
def test_mutation_refuses_unparseable_endpoint_custody(
    tmp_path, row, value, operation,
):
    endpoint = _publish(tmp_path, 101, 61001)
    table = routing.read_table(tmp_path)
    table["previous"] = table["active"].copy()
    table[row] = value
    original = json.dumps(table).encode()
    path = routing.routing_table_path(tmp_path)
    path.write_bytes(original)
    with pytest.raises(routing.ActivePublicationRefused, match="endpoint unverifiable"):
        if operation == "witness":
            routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
        elif operation == "publish":
            _publish(tmp_path, 202, 61002)
        elif operation == "guarded":
            routing.publish_active_with_previous_guarded(
                tmp_path, bind="127.0.0.1", port=61002, pid=202,
                expected_active=endpoint,
            )
        else:
            routing._atomic_write(path, {"active": endpoint.to_dict()})
    assert path.read_bytes() == original


@pytest.mark.parametrize("row", ["active", "previous"])
@pytest.mark.parametrize("value", [{}, []])
def test_atomic_write_refuses_invalid_proposed_endpoint(tmp_path, row, value):
    _publish(tmp_path, 101, 61001)
    path = routing.routing_table_path(tmp_path)
    original = path.read_bytes()
    following = routing.read_table(tmp_path)
    following[row] = value
    with pytest.raises(routing.ActivePublicationRefused, match="endpoint unverifiable"):
        routing._atomic_write(path, following)
    assert path.read_bytes() == original


@pytest.mark.parametrize("fields", [
    {"port": 1}, {"port": 65535}, {"pid": None}, {"pid": 2**32 - 1},
    {"generation": 0}, {"generation": 2**63 - 1}, {"bind": ""},
    {"bind": "x" * 255}, {"version": None}, {"version": ""},
    {"process_start_time": "0"}, {"process_start_time": "1" * 64},
])
def test_mutation_preserves_supported_endpoint_boundaries(tmp_path, fields):
    row = {"bind": "127.0.0.1", "port": 61001, "pid": 101, **fields}
    routing._atomic_write(routing.routing_table_path(tmp_path), {"active": row})
    assert routing.read_table(tmp_path)["active"] == row


@pytest.mark.parametrize("caller_history", [False, True])
def test_atomic_write_preserves_input_and_uses_persisted_custody(tmp_path, caller_history):
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    data = {"active": routing.Endpoint(
        bind="127.0.0.1", port=61002, pid=202, generation=2, process_start_time="2020",
    ).to_dict()}
    if caller_history:
        data[publication_history.KEY] = {
            "version": 1, "epoch": "caller-memory", "saturated": False, "entries": [],
        }
    original = deepcopy(data)
    routing._atomic_write(routing.routing_table_path(tmp_path), data)
    assert data == original
    persisted = routing.read_table(tmp_path)
    assert persisted["active"] == data["active"]
    assert publication_history.read(persisted)["epoch"] == witness
    assert not publication_history.never_published(persisted, witness, pid=202, port=61002)
    assert routing.publication_witness(
        tmp_path, deadline=time.monotonic() + 1,
    ) == witness


def test_displaced_publication_survives_restore_clear_and_later_flips(tmp_path):
    _publish(tmp_path, 101, 61001)
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    _publish(tmp_path, 202, 61002)
    _publish(tmp_path, 303, 61003)
    last = _publish(tmp_path, 404, 61004)
    assert all(
        routing.read_table(tmp_path)[slot]["pid"] != 202 for slot in ("active", "previous")
    )
    assert routing.restore_previous_if_owner(tmp_path, pid=404, generation=last.generation)
    assert routing.clear_if_owner(tmp_path, pid=303)
    _publish(tmp_path, 505, 61005)
    table = routing.read_table(tmp_path)
    assert not publication_history.never_published(table, witness, pid=202, port=61002)
    assert publication_history.never_published(table, witness, pid=606, port=61006)


@pytest.mark.parametrize("damage", ["missing", "malformed", "reinitialized"])
def test_unavailable_continuity_cannot_authorize_disposal(tmp_path, damage):
    _publish(tmp_path, 101, 61001)
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    table = routing.read_table(tmp_path)
    if damage == "malformed":
        table[publication_history.KEY]["entries"] = ["invalid"]
    else:
        table.pop(publication_history.KEY)
    routing.routing_table_path(tmp_path).write_text(json.dumps(table))
    if damage == "reinitialized":
        replacement = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
        assert replacement != witness
    assert not publication_history.never_published(
        routing.read_table(tmp_path), witness, pid=202, port=61002,
    )


def test_capacity_exhaustion_retains_evidence_and_disables_negative_proofs(tmp_path, monkeypatch):
    monkeypatch.setattr(publication_history, "MAX_ENTRIES", 2)
    _publish(tmp_path, 101, 61001)
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    _publish(tmp_path, 202, 61002)
    _publish(tmp_path, 303, 61003)
    table = routing.read_table(tmp_path)
    history = publication_history.read(table)
    assert history["saturated"]
    assert [entry["pid"] for entry in history["entries"]] == [101, 202]
    assert not publication_history.never_published(table, witness, pid=404, port=61004)


def test_identityless_publication_protects_matching_port(tmp_path):
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    _publish(tmp_path, None, 61002)
    _publish(tmp_path, 303, 61003)
    _publish(tmp_path, 404, 61004)
    assert not publication_history.never_published(
        routing.read_table(tmp_path), witness, pid=202, port=61002,
    )


def test_generic_reap_refuses_historically_promoted_candidate(tmp_path):
    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    _publish(tmp_path, 202, 61002)
    _publish(tmp_path, 303, 61003)
    _publish(tmp_path, 404, 61004)
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="started", new_pid=202, new_port=61002,
        publication_witness=witness,
        new_process_start_time="2020",
    )
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, record=record, grace_seconds=0,
        pid_alive=lambda _: True, terminate=lambda pid: terminated.append(pid) or True,
    )
    assert result["refused"] and not result["reaped"]
    assert terminated == []


def test_legacy_candidate_without_prebirth_witness_is_retained(tmp_path):
    _publish(tmp_path, 101, 61001)
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="started", new_pid=202, new_port=61002,
    )
    terminated = []
    result = breadcrumb.reap_abandoned_passive(
        tmp_path, record=record, grace_seconds=0,
        pid_alive=lambda _: True, terminate=lambda pid: terminated.append(pid) or True,
    )
    assert result["refused"] and terminated == []


def test_custody_is_durable_before_new_route_is_visible(tmp_path, monkeypatch):
    _publish(tmp_path, 101, 61001)
    from zdd import routing_persistence

    windows = routing_persistence.sys.platform == "win32"
    original = (
        routing_persistence.windows_replace if windows else routing_persistence.os.replace
    )
    observed = []
    synchronized = []
    real_fsync = routing_persistence.os.fsync

    def replace(source, target):
        data = json.loads(source.read_text())
        observed.append(data)
        if data["active"]["pid"] == 202:
            barriers = 3 if routing_persistence.sys.platform == "linux" else 2
            assert len(synchronized) >= barriers
            assert routing.read_table(tmp_path)["active"]["pid"] == 101
            assert any(
                entry["pid"] == 202
                for entry in routing.read_table(tmp_path)[publication_history.KEY]["entries"]
            )
        original(source, target)

    def fsync(descriptor):
        synchronized.append(descriptor)
        real_fsync(descriptor)

    if windows:
        monkeypatch.setattr(routing_persistence, "windows_replace", replace)
    else:
        monkeypatch.setattr(routing_persistence.os, "replace", replace)
    monkeypatch.setattr(routing_persistence.os, "fsync", fsync)
    _publish(tmp_path, 202, 61002)
    assert [data["active"]["pid"] for data in observed] == [101, 202]


def test_failed_route_flip_retains_precommitted_custody(tmp_path, monkeypatch):
    _publish(tmp_path, 101, 61001)
    from zdd import routing_persistence

    witness = routing.publication_witness(tmp_path, deadline=time.monotonic() + 1)
    original = routing_persistence.write
    calls = []

    def write(path, data):
        calls.append(data)
        if len(calls) == 2:
            raise OSError("fixture publication failure")
        original(path, data)

    monkeypatch.setattr(routing_persistence, "write", write)
    with pytest.raises(OSError):
        _publish(tmp_path, 202, 61002)
    table = routing.read_table(tmp_path)
    assert table["active"]["pid"] == 101
    assert not publication_history.never_published(table, witness, pid=202, port=61002)


def test_windows_custody_replace_uses_native_write_through(tmp_path):
    from types import SimpleNamespace
    from zdd import routing_persistence

    calls = []

    class Move:
        def __call__(self, source, target, flags):
            calls.append((source, target, flags))
            return 1

    routing_persistence.windows_replace(
        tmp_path / "source", tmp_path / "target", SimpleNamespace(MoveFileExW=Move()),
    )
    assert calls == [(str(tmp_path / "source"), str(tmp_path / "target"), 0x9)]


def test_corrupt_route_is_a_typed_refusal_not_an_empty_namespace(tmp_path):
    path = routing.routing_table_path(tmp_path)
    path.write_text("{corrupt")
    with pytest.raises(routing.ActivePublicationRefused, match="owner-led recovery"):
        _publish(tmp_path, 202, 61002)
    assert path.read_text() == "{corrupt"
