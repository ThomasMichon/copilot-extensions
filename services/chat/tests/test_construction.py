from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from typing import Any, Iterator, NoReturn, TypedDict

import pytest

from agent_chat_service import construction
from agent_chat_service.client import ClientError


class CandidateArguments(TypedDict):
    project: Path
    uv: Path
    python: Path
    wait_timeout: float
    child_timeout: float


def test_construction_floor_refuses_before_resource_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(construction.sys, "version_info", (3, 10))

    def refuse() -> NoReturn:
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
def test_occupied_import_scope_is_not_replaced(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
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


def test_client_policy_delegates_one_canonical_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    candidate = object()

    @contextmanager
    def numbered_candidate(*args: object, **kwargs: object) -> Iterator[object]:
        calls.append((args, kwargs))
        yield candidate
        calls.append("settled")

    monkeypatch.setattr(
        construction, "_engine", lambda: SimpleNamespace(numbered_candidate=numbered_candidate),
    )
    arguments: CandidateArguments = {
        "project": tmp_path / "project", "uv": tmp_path / "uv", "python": tmp_path / "python",
        "wait_timeout": 12, "child_timeout": 34,
    }
    for role, packages in (
        ("client", ("agent-endpoint-rendezvous", "agent-procutil")),
        ("service", ("agent-endpoint-rendezvous", "agent-procutil", "agent-single-instance-lease")),
    ):
        calls.clear()
        root = tmp_path / role
        with getattr(construction, role + "_candidate")(root, "1.2.3", **arguments) as actual:
            assert actual is candidate
            assert calls == [((root, "1.2.3"), {
                "plugin": arguments["project"], "uv": arguments["uv"],
                "base_python": arguments["python"],
                "extras": (role,),
                "local_packages": packages,
                "health_module": "agent_chat_service.construction",
                "health_callable": "validate_" + role,
                "construction_callable": "agent_chat_service.construction:_prepare_client",
                "construction_data": {"slot": str(root / "versions" / "1.2.3")},
                "wait_timeout": 12, "child_timeout": 34,
            })]
        assert calls[-1] == "settled" and not root.exists()


def installed_slot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes]:
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


def test_completed_slot_cannot_prepare_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slot, _ = installed_slot(tmp_path, monkeypatch)
    marker = slot / "completion"
    marker.touch()
    claimed_slot = slot / ".." / slot.name
    if os.name == "nt":
        native_slot = Path("\\\\?\\" + str(slot))
        monkeypatch.setattr(construction.sys, "prefix", str(native_slot))
        monkeypatch.setattr(
            construction, "__file__", str(native_slot / "site" / "agent_chat_service" / "construction.py"),
        )
        claimed_slot = slot
    runtime: Any = ModuleType("agent_chat_service._versioned_runtime")
    runtime.marker_path = lambda root, version: marker
    monkeypatch.setitem(sys.modules, runtime.__name__, runtime)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    for rejected_slot in (str(foreign), "relative", None):
        with pytest.raises(ClientError) as caught:
            construction._prepare_client({"slot": rejected_slot, "frozen_plugin": "absent"})
        assert caught.value.code == "construction_slot_invalid"
    with pytest.raises(ClientError) as caught:
        construction._prepare_client({"slot": str(claimed_slot), "frozen_plugin": "absent"})
    assert caught.value.code == "construction_slot_complete"
    assert not (slot / ".runtime-bootstrap").exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "unexpected"])
def test_installed_health_refuses_bootstrap_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    slot, value = installed_slot(tmp_path, monkeypatch)
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in construction._BOOTSTRAP.values():
        (bootstrap / name).write_bytes(value)

    def refuse_effect(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("installed service validation must not enter hosting")

    modules = {}
    for name in (
        "agent_chat_service.hosting", "agent_procutil.daemon_instances",
        "single_instance_lease", "endpoint_rendezvous",
    ):
        module = ModuleType(name)
        path = slot / (name.replace(".", "-") + ".py")
        path.write_bytes(b"synthetic installed module")
        module.__file__ = str(path)
        for attribute in (
            "HostedChat", "ChatStore", "SingleInstance", "admit_daemon_instance",
            "write_endpoint", "initialize_storage", "probe_health",
        ):
            setattr(module, attribute, refuse_effect)
        monkeypatch.setitem(sys.modules, name, module)
        modules[name] = module
    construction.validate_client()
    construction.validate_service()
    runtime = construction.importlib.import_module("agent_chat_service._versioned_runtime")
    runtime.mark_complete(slot.parent.parent, slot.name, payload_hash="synthetic-inputs")
    construction.validate_client()
    construction.validate_service()
    generation = bootstrap / "runtime-generation"
    original_generation = generation.read_bytes()
    generation.write_bytes(b"invalid-generation\n")
    with pytest.raises(ClientError) as caught:
        construction.validate_client()
    assert caught.value.code == "construction_bootstrap_invalid"
    generation.write_bytes(original_generation)
    if damage == "changed":
        foreign = tmp_path / "foreign.py"
        foreign.write_bytes(b"foreign module")
        for name, module in modules.items():
            for unavailable in (True, False):
                with monkeypatch.context() as patch:
                    if unavailable:
                        patch.setitem(sys.modules, name, None)
                    else:
                        patch.setattr(module, "__file__", str(foreign))
                    with pytest.raises(ClientError) as caught:
                        construction.validate_service()
                    assert caught.value.code == "construction_service_unavailable"
    launcher = bootstrap / "runtime_launcher.py"
    if damage == "missing":
        launcher.unlink()
    elif damage == "changed":
        launcher.write_bytes(b"changed")
    else:
        (bootstrap / "foreign.py").touch()
    before = {path.name: path.read_bytes() for path in bootstrap.iterdir()}
    for validate in (construction.validate_client, construction.validate_service):
        with pytest.raises(ClientError) as caught:
            validate()
        assert caught.value.code == "construction_bootstrap_invalid"
        assert {path.name: path.read_bytes() for path in bootstrap.iterdir()} == before
