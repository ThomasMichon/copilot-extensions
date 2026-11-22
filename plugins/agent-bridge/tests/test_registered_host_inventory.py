"""Standalone Hosts are observations, never new controllers or native identities."""

from __future__ import annotations

import json
import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.session_host import registered_inventory as inventory

pytestmark = pytest.mark.contract("agent_bridge.session_host.registered_inventory")


@pytest.fixture(params=("agent_bridge", "agent_remote_driver"), autouse=True)
def inventory_module(request: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        globals(), "inventory",
        importlib.import_module(request.param + ".session_host.registered_inventory"),
    )


@pytest.fixture
def host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, inventory_module: None,
) -> Any:
    record = {
        "state": "running", "birth": "native-birth", "artifact_count": "0",
        "purpose": "production", "process_identity_state": "matching",
        "pid": "123", "runtime_root": str(tmp_path), "instance_id": "original-instance",
    }
    state = {
        "state": "running", "host_pid": 123, "host_instance_id": "original-instance",
        "host_process_start_time": "original-birth", "cwd": str(tmp_path),
        "session_id": None, "control_nonce": "must-not-export", "port": 1234,
    }
    path = tmp_path / "host-original.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    calls = []

    def inspect(root: Path | None, **kwargs: Any) -> list[dict[str, str]]:
        calls.append(kwargs)
        return [record]

    monkeypatch.setattr(inventory, "observe_instances", inspect)
    monkeypatch.setattr(inventory, "process_start_time", lambda pid: "original-birth")
    return record, state, path, calls


def test_unbound_host_has_original_instance_handle_without_native_identity_or_secrets(host: Any) -> None:
    _, _, _, calls = host
    targets, source = inventory.registered_hosts()
    assert source["availability"] == "available"
    assert len(targets) == 1
    assert targets[0]["handle"] == "host-instance:original-instance"
    registration = targets[0]["registration"]
    assert registration["session_id"] is None
    assert registration["controller"] == "original_owner"
    assert "must-not-export" not in json.dumps(targets)
    assert calls == [{"service": "agent-bridge-session-host"}]


@pytest.mark.parametrize("identity", ["dead", "reused", "unknown"])
def test_dead_reused_or_unknown_process_cannot_be_a_live_host(host: Any, identity: str) -> None:
    record, _, _, _ = host
    record["process_identity_state"] = identity
    targets, source = inventory.registered_hosts()
    assert targets == []
    assert source["availability"] == ("unavailable" if identity == "unknown" else "available")


@pytest.mark.parametrize("birth", ["original-birth", "reused-birth", None])
def test_legacy_host_requires_matching_native_birth(host: Any, birth: str | None) -> None:
    _, state, path, _ = host
    state.pop("host_instance_id")
    state["host_process_start_time"] = birth
    state["session_id"] = "actual-native"
    path.write_text(json.dumps(state), encoding="utf-8")
    targets, source = inventory.registered_hosts()
    if birth == "original-birth":
        assert targets[0]["handle"] == "actual-native"
        assert source["availability"] == "available"
    else:
        assert targets[0]["registration"]["session_id"] is None
        assert source["availability"] == "unavailable"


def test_disposable_or_corrupt_state_never_becomes_a_production_host(host: Any) -> None:
    record, _, path, _ = host
    record["purpose"] = "unattended-test"
    assert inventory.registered_hosts()[0] == []
    record["purpose"] = "production"
    path.write_text("{", encoding="utf-8")
    targets, source = inventory.registered_hosts()
    assert targets[0]["registration"]["session_id"] is None
    assert source["availability"] == "unavailable"


def test_instance_bound_state_without_optional_birth_retains_observed_birth(host: Any) -> None:
    _, state, path, _ = host
    state.pop("host_process_start_time")
    path.write_text(json.dumps(state), encoding="utf-8")
    targets, source = inventory.registered_hosts()
    assert source["availability"] == "available"
    assert targets[0]["registration"]["host_process_start_time"] == "original-birth"


def test_direct_state_locator_outside_runtime_root_uses_attributed_file_without_cleanup_claim(
    host: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_procutil import _daemon_instance_store as store
    from agent_procutil._daemon_instance_records import ProcessIdentity
    from agent_procutil.daemon_instances import DaemonInstanceReceipt
    from agent_procutil.daemon_observation import publish_file_observation

    record, state, old_path, _ = host
    runtime = tmp_path / "source-code"
    runtime.mkdir()
    private = tmp_path / "private-state"
    private.mkdir()
    state["session_id"] = "actual-native"
    state_path = private / "caller-selected.data"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    old_path.unlink()
    record["runtime_root"] = str(runtime)

    @contextmanager
    def locked(receipt: Any, **kwargs: Any) -> Iterator[Path]:
        yield tmp_path / "fixture.instance"

    def write(path: Path, value: dict[str, str]) -> None:
        record.clear()
        record.update(value)

    monkeypatch.setattr(store, "_locked", locked)
    monkeypatch.setattr(store, "_read", lambda path: dict(record))
    monkeypatch.setattr(store, "_write", write)
    monkeypatch.setattr(
        ProcessIdentity, "current", classmethod(lambda cls: ProcessIdentity(123, "native-birth")),
    )
    publish_file_observation(
        DaemonInstanceReceipt("a" * 32, tmp_path / "registry"), "session_host_state", state_path,
    )
    assert record["runtime_root"] == str(runtime) and record["artifact_count"] == "0"
    assert record["observation_session_host_state_path"] == str(state_path.resolve())
    targets, source = inventory.registered_hosts()
    assert source["availability"] == "available"
    assert targets[0]["handle"] == "actual-native"


def test_running_host_without_metadata_remains_unbound_and_finished_process_is_not_listed(host: Any) -> None:
    record, _, path, _ = host
    path.unlink()
    targets, source = inventory.registered_hosts()
    assert targets[0]["handle"] == "host-instance:original-instance"
    assert targets[0]["registration"]["session_id"] is None
    assert source["availability"] == "unavailable"
    record["state"] = "finished"
    assert inventory.registered_hosts()[0] == []


def test_wrong_state_instance_cannot_be_adopted_through_matching_process_birth(host: Any) -> None:
    _, state, path, _ = host
    state["host_instance_id"] = "other-incarnation"
    state["session_id"] = "wrong-native"
    path.write_text(json.dumps(state), encoding="utf-8")
    targets, source = inventory.registered_hosts()
    assert targets[0]["registration"]["session_id"] is None
    assert targets[0]["handle"] == "host-instance:original-instance"
    assert source["availability"] == "unavailable"


@pytest.mark.parametrize("damage", ["none", "nonce", "port", "instance"])
def test_core_private_endpoint_requires_same_live_identity_and_valid_capability(host: Any, damage: str) -> None:
    if not hasattr(inventory, "registered_host_endpoints"):
        pytest.skip("Private endpoint selection belongs to Core, not the compatibility wrapper")
    _, state, path, _ = host
    state.update(session_id="actual-native", nonce="private-capability-" + "x" * 32)
    if damage == "nonce":
        state["nonce"] = "\u00e9" * 32
    elif damage == "port":
        state["port"] = True
    elif damage == "instance":
        state["host_instance_id"] = "another-owner"
    path.write_text(json.dumps(state), encoding="utf-8")
    endpoints, source = inventory.registered_host_endpoints()
    if damage == "none":
        assert len(endpoints) == 1
        assert (endpoints[0].session_id, endpoints[0].port) == ("actual-native", 1234)
        assert state["nonce"] not in repr(endpoints)
        assert source["availability"] == "available"
    else:
        assert endpoints == [] and source["availability"] == "unavailable"
