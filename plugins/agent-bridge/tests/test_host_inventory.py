"""Strict fresh host storage does not establish native host custody."""

from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path

import pytest

from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.session_host.host_inventory import HostInventoryError


def record(sid: str = "session-a") -> HostRecord:
    return HostRecord(session_id=sid, port=9001, host_pid=101, child_pid=102,
                      host_version="1.0.0", owner_generation="generation-a", owner_pid=103)


def write(path: Path, rows=None) -> None:
    path.write_text(json.dumps({
        "version": 1, "hosts": rows if rows is not None else {"session-a": asdict(record())},
    }), encoding="utf-8")


def test_locked_inventory_is_fresh_and_does_not_flush_or_update_cache(tmp_path):
    path = tmp_path / "hosts.json"
    first = HostIndex(path)
    first.register(record())
    second = HostIndex(path)
    second.register(record("session-b"))
    before = path.read_bytes(), path.stat().st_mtime_ns
    observed = first.locked_inventory()
    assert len(first.all()) == 1
    assert [item.session_id for item in observed.records] == ["session-a", "session-b"]
    assert observed.storage_complete and not observed.native_custody_verified
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    first.assert_inventory_current(observed)


def test_inventory_revalidation_detects_another_owner_write(tmp_path):
    path = tmp_path / "hosts.json"
    write(path)
    index = HostIndex(path)
    observed = index.locked_inventory()
    HostIndex(path).register(record("session-b"))
    with pytest.raises(HostInventoryError, match="changed"):
        index.assert_inventory_current(observed)


def test_missing_index_is_unknown_and_is_not_created(tmp_path):
    path = tmp_path / "missing.json"
    index = HostIndex(path, load=False)
    observed = index.locked_inventory()
    assert not observed.present and not observed.storage_complete
    assert not observed.native_custody_verified
    assert observed.records == () and not path.exists()
    with pytest.raises(HostInventoryError, match="missing index"):
        index.assert_inventory_current(observed)


def test_missing_parent_is_not_provisioned_for_inventory(tmp_path):
    path = tmp_path / "missing-parent" / "index.json"
    with pytest.raises(FileNotFoundError):
        HostIndex(path, load=False).locked_inventory()
    assert not path.parent.exists()


@pytest.mark.parametrize("text", [
    "{broken", "[]", '{"version":1,"hosts":{},"extra":true}',
    '{"version":true,"hosts":{}}', '{"version":2,"hosts":{}}',
    '{"version":1,"version":1,"hosts":{}}', '{"version":1,"hosts":[]}',
    '{"version":1,"hosts":{"session-a":null}}',
])
def test_malformed_or_unknown_index_never_reads_as_empty(tmp_path, text):
    path = tmp_path / "hosts.json"
    path.write_text(text, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(HostInventoryError):
        HostIndex(path, load=False).locked_inventory()
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"session_id": "another-session"}, {"port": True}, {"host_pid": 0},
    {"protocol_version": 2}, {"owner_pid": -1}, {"endpoint": []},
    {"created_at": float("nan")}, {"extra": {"unobserved": float("inf")}},
    {"unexpected": "field"},
])
def test_one_bad_record_cannot_disappear_from_complete_inventory(tmp_path, change):
    path = tmp_path / "hosts.json"
    bad = {**asdict(record("session-b")), **change}
    write(path, {"session-a": asdict(record()), "session-b": bad})
    with pytest.raises(HostInventoryError):
        HostIndex(path, load=False).locked_inventory()


def test_legacy_absent_fields_are_not_synthesized(tmp_path):
    path = tmp_path / "hosts.json"
    row = asdict(record())
    del row["owner_generation"]
    write(path, {"session-a": row})
    with pytest.raises(HostInventoryError, match="Incomplete"):
        HostIndex(path, load=False).locked_inventory()


@pytest.mark.parametrize("token", ["1e400", "-1e400"])
@pytest.mark.parametrize("location", ["extra", "endpoint", "extra-array", "endpoint-array"])
def test_exponent_overflow_refuses_at_every_nesting_without_mutation(tmp_path, token, location):
    path = tmp_path / "hosts.json"
    write(path)
    index = HostIndex(path)
    cached = index.all()
    row = asdict(record())
    field = location.split("-")[0]
    row[field] = {"value": ["__number__"] if location.endswith("-array") else "__number__"}
    raw = json.dumps({"version": 1, "hosts": {"session-a": row}}).replace('"__number__"', token)
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(HostInventoryError):
        index.locked_inventory()
    assert path.read_bytes() == before
    assert index.all() == cached


@pytest.mark.parametrize("token", ["1e308", "-1e308", "1e-308", "-0.0"])
def test_finite_nested_float_controls_remain_supported(tmp_path, token):
    path = tmp_path / "hosts.json"
    row = asdict(record())
    row["extra"] = {"value": ["__number__"]}
    raw = json.dumps({"version": 1, "hosts": {"session-a": row}}).replace('"__number__"', token)
    path.write_text(raw, encoding="utf-8")
    observed = HostIndex(path, load=False).locked_inventory()
    value = observed.records[0].extra["value"][0]
    assert type(value) is float and math.isfinite(value)
    assert value == float(token)
    assert observed.storage_complete and not observed.native_custody_verified


def test_linked_or_hardlinked_index_is_not_read(tmp_path):
    source = tmp_path / "original.json"
    write(source)
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(source)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("native file symlink privilege unavailable")
        raise
    with pytest.raises(HostInventoryError):
        HostIndex(alias, load=False).locked_inventory()
    hardlink = tmp_path / "hardlink.json"
    hardlink.hardlink_to(source)
    with pytest.raises(HostInventoryError):
        HostIndex(hardlink, load=False).locked_inventory()


def test_linked_parent_is_refused_before_inventory_mutex_creation(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    write(target / "index.json")
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("native directory symlink privilege unavailable")
        raise
    with pytest.raises(HostInventoryError, match="parent"):
        HostIndex(alias / "index.json", load=False).locked_inventory()
    assert not (target / "index.json.lock").exists()
