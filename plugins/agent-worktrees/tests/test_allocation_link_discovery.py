"""Discovery contains malformed data and rediscovers later durable reservations."""

from __future__ import annotations

import yaml
import pytest

from agent_worktrees import allocation_link_discovery as discovery
from test_allocation_link_runtime import runtime as runtime
from test_owned_worktree_transactions import reserve
from test_resource_producer import owner as owner


@pytest.mark.parametrize("field,value,error", [
    ("worktree_id", None, KeyError),
    ("branch", None, KeyError),
    ("resume_count", [], TypeError),
    ("resume_count", float("inf"), OverflowError),
])
def test_actual_ledger_parse_failure_stays_visible_and_retries_original_claim(
    owner, runtime, monkeypatch, field, value, error,
):
    path, _ = owner
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    pending = reserve(path)
    original = path.read_bytes()
    document = yaml.safe_load(original)
    if value is None:
        document.pop(field)
    else:
        document[field] = value
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    runtime_value, clock, _ = runtime
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    with pytest.raises(discovery.AllocationDiscoveryError) as failure:
        discovery.current_records("p")
    assert isinstance(failure.value.__cause__, error)
    runtime_value.discover()
    assert runtime_value.health()["allocationLinker"]["errors"] == 1
    assert runtime_value.has_demand() and runtime_value._worker is None
    path.write_bytes(original)
    clock[0] += 30
    runtime_value.discover()
    assert runtime_value.health()["allocationLinker"]["errors"] == 0
    assert next(iter(runtime_value._candidates.values())).token == pending["token"]


@pytest.mark.parametrize("initially_registered", [False, True])
def test_later_reservation_is_discovered_after_an_initially_empty_scan(
    owner, runtime, monkeypatch, initially_registered,
):
    value, clock, _ = runtime
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: owner[0].parent)
    projects = {"p"} if initially_registered else set()
    calls = []

    def registry():
        calls.append(True)
        return set(projects)

    monkeypatch.setattr(discovery, "registered_projects", registry)
    value.discover()
    assert not value.has_demand()
    pending = reserve(owner[0])
    projects.add("p")
    clock[0] += 60
    value.discover()
    assert len(calls) == 2
    assert next(iter(value._candidates.values())).token == pending["token"]
    assert value.has_demand()


def test_successful_periodic_scans_do_not_turn_empty_inventory_into_permanent_demand(runtime, monkeypatch):
    value, clock, _ = runtime
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"a", "b", "c"})
    monkeypatch.setattr(discovery, "current_records", lambda project: [])
    for _ in range(3):
        value.discover()
        assert not value.has_demand()
        assert value.health()["allocationLinker"]["discovering"] is False
        clock[0] += 60
