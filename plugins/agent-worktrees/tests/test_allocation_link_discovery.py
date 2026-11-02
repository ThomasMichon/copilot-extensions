"""Discovery contains malformed data and rediscovers later durable reservations."""

from __future__ import annotations

import yaml
import pytest

from agent_worktrees import allocation_link_discovery as discovery, config as cfg, tracking
from agent_worktrees import owned_worktree as allocation, owned_worktree_remote as remote
from test_allocation_link_runtime import runtime as runtime
from test_owned_worktree_transactions import call, observation, reserve
from test_resource_producer import owner as owner

_load_config = cfg.load_config
_load_project_config = cfg.load_project_config


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


def test_lenient_roster_cannot_clear_a_strict_inventory_failure(owner, runtime, monkeypatch):
    path, _ = owner
    value, clock, _ = runtime
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    value.discover()
    assert not value.has_demand()
    pending = reserve(path)
    original = path.read_bytes()
    document = yaml.safe_load(original)
    document.pop("branch")
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    clock[0] += 60
    value.discover()
    assert value.health()["allocationLinker"]["errors"] == 1
    rows = tracking.list_records(path.parent)
    assert all(record.worktree_id != "owner" for record in rows)
    value.observe("p", rows)
    assert value.health()["allocationLinker"]["errors"] == 1 and value.has_demand()
    path.write_bytes(original)
    value.observe("p", [tracking.load_record(path)])
    assert value.health()["allocationLinker"]["errors"] == 1
    clock[0] += 30
    value.discover()
    assert value.health()["allocationLinker"]["errors"] == 0
    assert next(iter(value._candidates.values())).token == pending["token"]


@pytest.mark.parametrize("damage", [
    "null-entry", "opaque-collection", "null-producer", "absent-producer",
    "missing-lifetime", "null-lifetime", "wrong-lifetime",
])
def test_opaque_or_allocation_marked_damage_keeps_demand_until_real_ledger_repair(
    owner, runtime, monkeypatch, damage,
):
    path, _ = owner
    value, clock, _ = runtime
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    pending = reserve(path)
    original = path.read_bytes()
    document = yaml.safe_load(original)
    claim = document["resources"][0]
    if damage == "null-entry":
        document["resources"] = [None]
    elif damage == "opaque-collection":
        document["resources"] = {}
    elif damage == "null-producer":
        claim["producer"] = None
    elif damage == "absent-producer":
        claim.pop("producer")
    elif damage == "missing-lifetime":
        claim["producer"].pop("lifetime")
    else:
        claim["producer"]["lifetime"] = None if damage == "null-lifetime" else "other"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    damaged = path.read_bytes()
    value.discover()
    assert value.health()["allocationLinker"]["errors"] == 1
    assert value.has_demand() and value._worker is None
    assert path.read_bytes() == damaged
    path.write_bytes(original)
    clock[0] += 30
    value.discover()
    assert value.health()["allocationLinker"]["errors"] == 0
    assert next(iter(value._candidates.values())).token == pending["token"]
    assert path.read_bytes() == original


def test_late_project_registration_keeps_every_first_scan_pending_beyond_budget(
    owner, runtime, monkeypatch, tmp_path,
):
    path, _ = owner
    value, clock, _ = runtime
    projects = set()
    roots = {"p": path.parent}
    for project in ("a", "b", "c", "d"):
        roots[project] = tmp_path / project
        roots[project].mkdir()
    monkeypatch.setattr(discovery, "registered_projects", lambda: set(projects))
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: roots[project or "p"])
    value.discover()
    assert not value.has_demand()
    pending = reserve(path)
    projects.update(roots)
    clock[0] += 60
    value.discover()
    assert value._startup_pending == {"p"}
    assert value.health()["allocationLinker"]["discovering"] is True
    assert value.health()["allocationLinker"]["pending"] == 0 and value.has_demand()
    value.discover()
    assert not value._startup_pending
    assert value.health()["allocationLinker"]["discovering"] is False
    assert next(iter(value._candidates.values())).token == pending["token"]


@pytest.mark.parametrize("closed", [False, True])
def test_strict_scan_shares_configuration_only_within_that_scan(
    owner, runtime, monkeypatch, closed,
):
    path, config = owner
    value, clock, _ = runtime
    pending = reserve(path)
    if closed:
        monkeypatch.setattr(remote, "request", lambda op, meta, **kw: observation(meta, "cancelled"))
        assert call(path, "cancel", {"token": pending["token"]})["phase"] == "closed"
    document = yaml.safe_load(path.read_text())
    document["worktree_id"] = "consumer"
    metadata = document["resources"][0]["producer"]
    metadata["creator"] = "m/p/consumer"
    if closed:
        metadata["closeout"]["origin"] = metadata["creator"]
    document["resources"][0]["ref"] = allocation.claim_ref(metadata)
    (path.parent / "consumer.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    loads = []

    def uncached(*args, **kwargs):
        loads.append(kwargs["project"])
        return config

    monkeypatch.setattr(cfg, "load_config", _load_config)
    monkeypatch.setattr(cfg, "load_project_config", _load_project_config)
    monkeypatch.setattr(cfg, "_load_config_uncached", uncached)
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    with cfg.ConfigCacheSession().scope():
        value.discover()
        assert loads == ["p"] and len(value._candidates) == (0 if closed else 2)
        clock[0] += 60
        value.discover()
        assert loads == ["p", "p"]
        discovery.locally_owned(tracking.load_record(path))
        assert loads == ["p", "p", "p"]
    discovery.locally_owned(tracking.load_record(path))
    assert loads == ["p", "p", "p", "p"]


def test_opaque_foreign_claim_does_not_authorize_local_reconciliation(owner, runtime, monkeypatch):
    path, _ = owner
    value, _, _ = runtime
    document = yaml.safe_load(path.read_text())
    document["resources"] = [None]
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    monkeypatch.setattr(discovery, "locally_owned", lambda record: False)
    value.discover()
    assert not value.has_demand() and value._worker is None
    assert value.health()["allocationLinker"]["errors"] == 0


def test_other_valid_producer_lifetime_is_not_allocation_linker_demand(owner, runtime, monkeypatch):
    from test_resource_producer import reserve as reserve_disposable

    path, _ = owner
    value, _, _ = runtime
    reserve_disposable(path)
    monkeypatch.setattr(discovery.cfg, "tracking_dir", lambda project=None: path.parent)
    monkeypatch.setattr(discovery, "registered_projects", lambda: {"p"})
    value.discover()
    assert not value.has_demand() and value._worker is None
    assert value.health()["allocationLinker"]["errors"] == 0
