"""Actor-free inventory decisions; native identity and owner processes are boundaries."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = "plugins/agent-bridge/src/agent_bridge/"


def load(monkeypatch: pytest.MonkeyPatch, name: str, relative: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def package(monkeypatch: pytest.MonkeyPatch, name: str) -> ModuleType:
    module = ModuleType(name)
    module.__path__ = []
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_source_window_and_degraded_render_do_not_renew_or_invent_native_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package(monkeypatch, "_inventory_bridge")
    package(monkeypatch, "_inventory_bridge.routes")
    refresh = load(
        monkeypatch, "_inventory_bridge.routes.worktree_refresh", BRIDGE + "routes/worktree_refresh.py",
    )
    catalog = load(monkeypatch, "_inventory_bridge.local_session_catalog", BRIDGE + "local_session_catalog.py")
    value: dict[str, Any] = {
        "version": 1, "contract_version": 1,
        "projection": {
            "quality": "current", "observed_at": time.time() - 40,
            "freshness": {"bound_s": 90, "refreshed_age_s": 40},
        },
        "worktrees": [{"id": "workspace", "path": "workspace-path"}],
    }
    observed, expiry = refresh.inventory_window(json.dumps(value))
    assert 39 <= time.monotonic() - observed <= 42
    assert 48 <= expiry - time.monotonic() <= 51
    evidence, retained_expiry = catalog._inventory_observation(json.dumps(value))
    assert evidence["observation_valid"] is True
    assert retained_expiry is not None and abs(retained_expiry - expiry) < 1
    value["availability"] = {"status": "stale", "reason": "cleanup pending"}
    evidence, retained_expiry = catalog._inventory_observation(json.dumps(value))
    assert evidence == {
        "availability": "unavailable", "reason": "source-degraded", "observation_valid": False,
    }
    assert retained_expiry is None
    assert "session_id" not in evidence
    value["worktrees"] = [{"path": "missing-id"}]
    with pytest.raises(ValueError):
        catalog._inventory_observation(json.dumps(value))


def test_host_observation_filters_service_and_exact_birth_without_controller_adoption(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    package(monkeypatch, "_inventory_procutil")
    records = [
        {"service": "other", "pid": "999", "birth": "other"},
        {
            "service": "agent-bridge-session-host", "pid": "123", "birth": "native-birth",
            "state": "running",
            "purpose": "production", "runtime_root": str(tmp_path), "instance_id": "original",
        },
    ]
    monkeypatch.setitem(sys.modules, "_inventory_procutil._daemon_instance_store", SimpleNamespace(
        inspect=lambda root: records,
    ))
    probes = []

    def identity(pid: int, birth: str) -> str:
        probes.append((pid, birth))
        return "matching"

    monkeypatch.setitem(sys.modules, "_inventory_procutil._daemon_instance_identity", SimpleNamespace(
        identity_state=identity,
    ))
    procutil = load(
        monkeypatch, "_inventory_procutil.daemon_instances",
        "libs/agent-procutil/src/agent_procutil/daemon_instances.py",
    )
    assert procutil.inspect_instances(tmp_path) is records
    assert probes == []
    observation = load(
        monkeypatch, "_inventory_procutil.daemon_observation",
        "libs/agent-procutil/src/agent_procutil/daemon_observation.py",
    )
    package(monkeypatch, "agent_procutil")
    monkeypatch.setitem(sys.modules, "agent_procutil.daemon_instances", procutil)
    monkeypatch.setitem(sys.modules, "agent_procutil.daemon_observation", observation)
    package(monkeypatch, "_inventory_host")
    package(monkeypatch, "agent_remote_driver")
    package(monkeypatch, "agent_remote_driver.session_host")
    monkeypatch.setitem(sys.modules, "agent_remote_driver.session_host.osutil", SimpleNamespace(
        process_start_time=lambda pid: "native-birth",
    ))
    load(
        monkeypatch, "agent_remote_driver.session_host.registered_inventory",
        "libs/copilot-driver-core/src/agent_remote_driver/session_host/registered_inventory.py",
    )
    hosts = load(
        monkeypatch, "_inventory_host.registered_inventory",
        BRIDGE + "session_host/registered_inventory.py",
    )
    state = {
        "state": "running", "host_pid": 123, "host_instance_id": "original",
        "host_process_start_time": "native-birth", "cwd": "workspace",
        "session_id": None, "control_nonce": "private-control",
    }
    (tmp_path / "host-original.json").write_text(json.dumps(state), encoding="utf-8")
    rows, evidence = hosts.registered_hosts()
    assert probes == [(123, "native-birth")]
    assert evidence["availability"] == "available"
    assert rows[0]["handle"] == "host-instance:original"
    assert rows[0]["registration"]["session_id"] is None
    assert rows[0]["registration"]["controller"] == "original_owner"
    assert "private-control" not in json.dumps(rows)


def test_managed_listing_reader_preserves_arrays_and_isolates_qualified_catalog_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = load(
        monkeypatch, "_inventory_dispatch_listing",
        "plugins/agent-dispatch/src/agent_dispatch/bridge_listing.py",
    )
    managed = {"session_id": "owned-control", "status": "running"}
    assert reader.managed_rows([managed]) == [managed]
    catalog: dict[str, Any] = {
        "version": 1,
        "sources": {
            "managed": {"availability": "available"},
            "worktrees": {"availability": "unavailable"},
        },
        "targets": [
            {"kind": "managed", "session": managed},
            {"kind": "worktree", "handle": "cold-workspace"},
            {"kind": "session_host", "handle": "unbound-host"},
            {"kind": "live", "handle": "interactive-native"},
        ],
    }
    assert reader.managed_rows(catalog) == [managed]
    for changed in (
        {**catalog, "version": True}, {**catalog, "version": 2},
        {**catalog, "sources": {"managed": {"availability": "unavailable"}}},
        {**catalog, "targets": [{"kind": "managed", "session": None}]},
    ):
        with pytest.raises(ValueError):
            reader.managed_rows(changed)


@pytest.mark.parametrize("owns_record", [False, True])
def test_file_observation_publication_is_owner_bound_and_never_grants_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, owns_record: bool,
) -> None:
    package(monkeypatch, "_inventory_file_publisher")
    api = load(
        monkeypatch, "_inventory_file_publisher.daemon_instances",
        "libs/agent-procutil/src/agent_procutil/daemon_instances.py",
    )
    record = {
        "state": "running", "pid": "123", "birth": "native-birth",
        "runtime_root": "unchanged-source-root", "artifact_count": "0",
    }
    writes = []

    @contextmanager
    def locked(receipt: Any, **kwargs: Any) -> Iterator[Path]:
        yield tmp_path / "fixture.instance"

    class Identity:
        @classmethod
        def current(cls) -> Identity:
            return cls()

        def matches(self, value: dict[str, str]) -> bool:
            assert value == record
            return owns_record

    monkeypatch.setitem(sys.modules, "_inventory_file_publisher._daemon_instance_store", SimpleNamespace(
        _locked=locked, _read=lambda path: dict(record),
        _write=lambda path, value: writes.append(value),
    ))
    monkeypatch.setitem(sys.modules, "_inventory_file_publisher._daemon_instance_records", SimpleNamespace(
        ProcessIdentity=Identity,
    ))
    observation = load(
        monkeypatch, "_inventory_file_publisher.daemon_observation",
        "libs/agent-procutil/src/agent_procutil/daemon_observation.py",
    )
    state = tmp_path / "caller-selected.data"
    state.write_text("{}", encoding="utf-8")
    receipt = api.DaemonInstanceReceipt("a" * 32, tmp_path / "registry")
    if not owns_record:
        with pytest.raises(ValueError):
            observation.publish_file_observation(receipt, "session_host_state", state)
        assert writes == []
    else:
        observation.publish_file_observation(receipt, "session_host_state", state)
        assert len(writes) == 1
        assert writes[0]["observation_session_host_state_path"] == str(state.resolve())
        assert writes[0]["runtime_root"] == record["runtime_root"]
        assert writes[0]["artifact_count"] == "0"
