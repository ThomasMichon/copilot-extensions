from contextlib import contextmanager
import hashlib
import sys
from types import ModuleType, SimpleNamespace

import pytest

from agent_chat_service import construction
from agent_chat_service.client import ClientError


def test_construction_floor_refuses_before_resource_loading(monkeypatch):
    monkeypatch.setattr(construction.sys, "version_info", (3, 10))

    def refuse():
        pytest.fail("unsupported bootstrap must not load or inspect construction resources")

    monkeypatch.setattr(construction, "installed_catalog", refuse)
    with pytest.raises(ClientError) as caught:
        construction._engine()
    assert caught.value.code == "construction_python_unavailable"


@pytest.mark.parametrize("name", [
    "versioned_runtime", "versioned_runtime_source",
    "installer_engine_process", "installer_dependency_policy",
    "installer_wheel_inputs", "installer_wheel_snapshot",
])
def test_occupied_import_scope_is_not_replaced(monkeypatch, name):
    monkeypatch.setattr(construction.sys, "version_info", (3, 11))
    monkeypatch.setattr(construction, "installed_catalog", lambda: {})
    monkeypatch.setattr(construction, "_ENGINE", None)
    foreign = ModuleType(name)
    monkeypatch.setitem(sys.modules, name, foreign)
    before = dict(sys.modules)
    with pytest.raises(ClientError) as caught:
        construction._engine()
    assert caught.value.code == "construction_import_conflict"
    assert dict(sys.modules) == before


def test_client_policy_delegates_one_canonical_candidate(tmp_path, monkeypatch):
    calls = []
    candidate = object()

    @contextmanager
    def numbered_candidate(*args, **kwargs):
        calls.append((args, kwargs))
        yield candidate
        calls.append("settled")

    monkeypatch.setattr(
        construction, "_engine", lambda: SimpleNamespace(numbered_candidate=numbered_candidate),
    )
    arguments = {
        "project": tmp_path / "project", "uv": tmp_path / "uv", "python": tmp_path / "python",
        "wait_timeout": 12, "child_timeout": 34,
    }
    root = tmp_path / "runtime"
    with construction.client_candidate(root, "1.2.3", **arguments) as actual:
        assert actual is candidate
        assert calls == [((root, "1.2.3"), {
            "plugin": arguments["project"], "uv": arguments["uv"],
            "base_python": arguments["python"],
            "extras": ("client",),
            "local_packages": ("agent-endpoint-rendezvous", "agent-procutil"),
            "health_module": "agent_chat_service.construction",
            "health_callable": "validate_client",
            "construction_callable": "agent_chat_service.construction:_prepare_client",
            "construction_data": {"slot": str(root / "versions" / "1.2.3")},
            "wait_timeout": 12, "child_timeout": 34,
        })]
    assert calls[-1] == "settled" and not root.exists()


def installed_slot(tmp_path, monkeypatch):
    slot = tmp_path / "versions" / "1.2.3"
    package = slot / "site" / "agent_chat_service"
    package.mkdir(parents=True)
    monkeypatch.setattr(construction.sys, "prefix", str(slot))
    monkeypatch.setattr(construction, "__file__", str(package / "construction.py"))
    value = b"synthetic canonical bytes"
    catalog = {
        "resource_sha256s": {
            name: hashlib.sha256(value).hexdigest() for name in construction._BOOTSTRAP
        },
    }
    monkeypatch.setattr(construction, "installed_catalog", lambda: catalog)
    return slot, value


def test_completed_slot_cannot_prepare_bootstrap(tmp_path, monkeypatch):
    slot, _ = installed_slot(tmp_path, monkeypatch)
    marker = slot / "completion"
    marker.touch()
    runtime = ModuleType("agent_chat_service._versioned_runtime")
    runtime.marker_path = lambda root, version: marker
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    with pytest.raises(ClientError) as caught:
        construction._prepare_client({"slot": str(slot), "frozen_plugin": "absent"})
    assert caught.value.code == "construction_slot_complete"
    assert not (slot / ".runtime-bootstrap").exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "unexpected"])
def test_installed_health_refuses_bootstrap_drift(tmp_path, monkeypatch, damage):
    slot, value = installed_slot(tmp_path, monkeypatch)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in construction._BOOTSTRAP.values():
        (bootstrap / name).write_bytes(value)
    construction.validate_client()
    launcher = bootstrap / "runtime_launcher.py"
    if damage == "missing":
        launcher.unlink()
    elif damage == "changed":
        launcher.write_bytes(b"changed")
    else:
        (bootstrap / "foreign.py").touch()
    before = {path.name: path.read_bytes() for path in bootstrap.iterdir()}
    with pytest.raises(ClientError) as caught:
        construction.validate_client()
    assert caught.value.code == "construction_bootstrap_invalid"
    assert {path.name: path.read_bytes() for path in bootstrap.iterdir()} == before
