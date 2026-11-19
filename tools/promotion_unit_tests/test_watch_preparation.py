"""Actor-free preparation admission and final-publication guards."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-pull-requests/src/agent_pull_requests"


@pytest.fixture
def preparation(monkeypatch: pytest.MonkeyPatch) -> Any:
    package = ModuleType("promotion_watch_preparation")
    package.__path__ = [str(SOURCE)]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    for name in ("watch_private", "watch_registry", "watch_storage"):
        module = ModuleType(f"{package.__name__}.{name}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        if name == "watch_storage":
            spec = importlib.util.spec_from_file_location(module.__name__, SOURCE / f"{name}.py")
            assert spec and spec.loader
            spec.loader.exec_module(module)
        elif name == "watch_private":
            for operation in ("confirm_private", "require_private", "write_private"):
                setattr(module, operation, lambda *_a, **_kw: pytest.fail("unselected storage effect"))
        else:
            setattr(module, "WatchRegistry", lambda: SimpleNamespace(restore_state=lambda *_a, **_kw: None))
    lease = ModuleType("single_instance_lease")
    setattr(lease, "SingleInstance", object)
    monkeypatch.setitem(sys.modules, lease.__name__, lease)
    spec = importlib.util.spec_from_file_location(
        f"{package.__name__}.watch_transition", SOURCE / "watch_transition.py",
    )
    assert spec and spec.loader
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    state: dict[str, Any] = {"document": None, "writes": 0, "confirmed": 0}
    monkeypatch.setattr(result, "_historical_identity", lambda _root: (2, 3))
    monkeypatch.setattr(result, "read_transition_phase", lambda _root: (
        "absent" if state["document"] is None else state["document"]["phase"]
    ))
    monkeypatch.setattr(result, "_regular_bytes", lambda _path: json.dumps(state["document"]).encode())
    monkeypatch.setattr(result, "require_private", lambda _path: None)
    monkeypatch.setattr(result.os.path, "lexists", lambda _path: False)
    monkeypatch.setattr(result, "_read_subscriptions_state", lambda _path: [])

    def publish(
        _path: Path, document: dict[str, Any], *, replace: bool, before_publish: Any,
    ) -> None:
        assert replace is False
        before_publish()
        state["document"] = json.loads(json.dumps(document))
        state["writes"] += 1

    def confirm(_path: Path) -> None:
        state["confirmed"] += 1

    monkeypatch.setattr(result, "write_private", publish)
    monkeypatch.setattr(result, "confirm_private", confirm)
    return result, state


def fixture_owner(module: Any) -> tuple[Path, dict[str, Any], Any]:
    root = Path("state")
    binding = {"kind": "legacy", "stateRoot": str(root)}
    lease = SimpleNamespace(
        lock_path=module.watch_state_paths(root).historical_lock,
        held_file_identity=lambda: (2, 3),
    )
    return root, binding, lease


def test_preparation_commit_and_recovery_never_select_isolated_state(preparation: Any) -> None:
    module, state = preparation
    root, binding, lease = fixture_owner(module)
    document = module.prepare(root, binding, lease, validate=lambda: None, check_deadline=lambda: None)
    assert document["phase"] == "prepared"
    assert state["writes"] == 1
    assert module.prepare(
        root, binding, lease, validate=lambda: None, check_deadline=lambda: None,
    ) == document
    assert state["writes"] == 1 and state["confirmed"] == 1


@pytest.mark.parametrize("loss", ["scope", "lease", "journal"])
def test_preparation_rechecks_every_custody_boundary(
    preparation: Any, monkeypatch: pytest.MonkeyPatch, loss: str,
) -> None:
    module, state = preparation
    root, binding, lease = fixture_owner(module)
    publish = module.write_private
    refused: list[bool] = []

    def validate() -> None:
        if refused:
            raise PermissionError("scope changed")

    def changed(*args: Any, **kwargs: Any) -> None:
        if loss == "scope":
            refused.append(True)
        elif loss == "lease":
            lease.held_file_identity = lambda: (5, 6)
        else:
            state["document"] = {"phase": "active"}
        publish(*args, **kwargs)

    monkeypatch.setattr(module, "write_private", changed)
    with pytest.raises((ValueError, PermissionError)):
        module.prepare(root, binding, lease, validate=validate, check_deadline=lambda: None)
    assert state["writes"] == 0


@pytest.mark.parametrize("corruption", ["binding", "boolean-inode", "extra", "orphan"])
def test_prepared_record_is_not_custody_proof(
    preparation: Any, monkeypatch: pytest.MonkeyPatch, corruption: str,
) -> None:
    module, state = preparation
    root, binding, lease = fixture_owner(module)
    module.prepare(root, binding, lease, validate=lambda: None, check_deadline=lambda: None)
    if corruption == "binding":
        state["document"]["binding"]["stateRoot"] = "foreign"
    elif corruption == "boolean-inode":
        state["document"]["lease"]["inode"] = True
    elif corruption == "extra":
        state["document"]["paths"] = {"legacy": "foreign"}
    else:
        monkeypatch.setattr(module.os.path, "lexists", lambda _path: True)
    with pytest.raises(PermissionError):
        module.read_preparation(root, binding)
    assert state["writes"] == 1 and state["confirmed"] == 0


def test_preparation_does_not_equate_boolean_and_integer_installation_identity(
    preparation: Any,
) -> None:
    module, state = preparation
    root, binding, lease = fixture_owner(module)
    binding.update(
        kind="installation", installation={"stateRoot": str(root), "generation": 1},
    )
    module.prepare(root, binding, lease, validate=lambda: None, check_deadline=lambda: None)
    state["document"]["binding"]["installation"]["generation"] = True
    with pytest.raises(PermissionError):
        module.read_preparation(root, binding)


@pytest.mark.parametrize("condition", ["held", "unheld", "child", "replaced", "linked"])
def test_lease_identity_is_current_descriptor_custody_not_a_pid_record(
    monkeypatch: pytest.MonkeyPatch, condition: str,
) -> None:
    path = ROOT / "libs/single-instance-lease/src/single_instance_lease/lease.py"
    spec = importlib.util.spec_from_file_location("promotion_lease_identity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.SingleInstance(Path("root"))
    owner._owner_pid = os.getpid() + (condition == "child")
    owner._fh = None if condition == "unheld" else SimpleNamespace(fileno=lambda: 7)
    opened = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_ino=3, st_dev=2, st_file_attributes=0)
    current = SimpleNamespace(
        st_mode=stat.S_IFLNK if condition == "linked" else stat.S_IFREG | 0o600,
        st_ino=4 if condition == "replaced" else 3, st_dev=2, st_file_attributes=0,
    )
    monkeypatch.setattr(module.os, "fstat", lambda _descriptor: opened)
    monkeypatch.setattr(Path, "lstat", lambda _path: current)
    if condition == "held":
        assert owner.held_file_identity() == (2, 3)
    else:
        with pytest.raises(PermissionError):
            owner.held_file_identity()


@pytest.mark.parametrize("inherited", [False, True])
def test_inherited_lease_release_closes_without_unlocking_parent(
    monkeypatch: pytest.MonkeyPatch, inherited: bool,
) -> None:
    path = ROOT / "libs/single-instance-lease/src/single_instance_lease/lease.py"
    spec = importlib.util.spec_from_file_location("promotion_lease_release", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    owner = module.SingleInstance(Path("root"))
    owner._owner_pid = os.getpid() + int(inherited)
    calls: list[str] = []
    owner._fh = SimpleNamespace(close=lambda: calls.append("close"))
    monkeypatch.setattr(module, "_release_os_lock", lambda _handle: calls.append("unlock"))
    owner.release()
    assert calls == (["close"] if inherited else ["unlock", "close"])
    assert owner._fh is None and owner._owner_pid is None


def test_preparation_checks_deadline_after_the_last_journal_read(
    preparation: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state = preparation
    root, binding, lease = fixture_owner(module)
    read = module.read_preparation
    publish = module.write_private
    publishing = False
    expired = False

    def late_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal expired
        result = read(*args, **kwargs)
        if publishing:
            expired = True
        return result

    def about_to_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal publishing
        publishing = True
        publish(*args, **kwargs)

    def deadline() -> None:
        if expired:
            raise TimeoutError("final reads exceeded deadline")

    monkeypatch.setattr(module, "read_preparation", late_read)
    monkeypatch.setattr(module, "write_private", about_to_publish)
    with pytest.raises(TimeoutError, match="final reads"):
        module.prepare(root, binding, lease, validate=lambda: None, check_deadline=deadline)
    assert state["writes"] == 0
