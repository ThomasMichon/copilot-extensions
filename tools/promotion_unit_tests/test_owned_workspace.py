"""Pure Bridge allocation decisions; command execution and reference parsing are boundaries."""

from __future__ import annotations

import copy
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from types import ModuleType

import pytest


@pytest.fixture
def api(monkeypatch):
    name = "promotion_bridge_owned_workspace"
    package = ModuleType(name)
    package.__path__ = []
    monkeypatch.setitem(sys.modules, name, package)

    def stub(child, **fields):
        module = ModuleType(f"{name}.{child}")
        module.__dict__.update(fields)
        monkeypatch.setitem(sys.modules, module.__name__, module)

    class Refused(ValueError):
        def __init__(self, code, message, **kwargs):
            super().__init__(message)

    @dataclass
    class Target:
        type: str = "ssh"
        project: str = "destination"
        caller_owner_ref: str = "origin/control/parent#session"
        execution_space_key: str = "receiver"
        caller_worktree: str = "parent"
        worktree_id: str | None = None
        explicit_cwd: bool = False
        native_resume_id: str | None = None
        spawn_command: list | None = None
        codespace: dict | None = None
        container: dict | None = None
        venue: dict | None = None

    stub("acquisition_authority", worktree_command=lambda *a, **k: pytest.fail("unit executed a command"))
    stub("acquisition_contract", AcquisitionRefused=Refused)
    stub("relationship_authority", canonical_owner=lambda value: value.partition("#")[0])
    stub("spawn_target", REQUEST_OVERRIDES_KEY="request", SpawnTarget=Target)
    path = Path(__file__).resolve().parents[2] / "plugins/agent-bridge/src/agent_bridge/owned_workspace.py"
    spec = importlib.util.spec_from_file_location(f"{name}.owned_workspace", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, Target


def capability():
    return {
        "schema": "copilot-extensions.worktree-allocation", "version": 1, "role": "origin",
        "creator": "origin/control/parent",
        "ordinary_create": {
            "version": 1, "destination_selectors": ["machine", "repo"],
            "creation_key": "opaque-v1", "result": "ordinary-worktree+allocation-v1",
            "kinds": ["session", "bridge"], "caller_worktree": True, "creator_identity": True,
            "claim_linkage": "non-blocking", "agent_launch": False,
        },
    }


def test_bridge_intent_preserves_original_owner_and_refuses_legacy_or_changed_target(api):
    module, Target = api
    target = module.arm(Target(), "original-session")
    assert target.venue["request"]["owned_workspace"]["creation_key"] == "bridge-session:original-session"
    assert module._intent(target)["creator"] == "origin/control/parent"
    with pytest.raises(ValueError):
        module._intent(Target())
    target.execution_space_key = "different"
    with pytest.raises(ValueError):
        module._intent(target)
    with pytest.raises(ValueError):
        module.arm(Target(execution_space_key="origin"), "original-session")
    for selected in (Target(type="local"), Target(worktree_id="existing"), Target(explicit_cwd=True)):
        assert module.arm(selected, "original-session") is selected
    session = SimpleNamespace(
        session_id="original-session", target=module.arm(Target(), "original-session"),
        acp_session_id=None, client=None,
    )
    assert module.can_resume_allocation(session) is True
    session.target.worktree_id = "original-child"
    assert module.retains_allocation(session) is True
    assert module.can_resume_allocation(session) is False
    for selected in (Target(), Target(worktree_id="existing")):
        session.target = selected
        assert module.can_resume_allocation(session) is False
    session.target = module.arm(Target(), "other-session")
    with pytest.raises(ValueError):
        module.can_resume_allocation(session)

    class Lock:
        acquired = 0

        async def acquire(self) -> None:
            self.acquired += 1

    session._lifecycle_lock = lock = Lock()
    for selected, expected in ((Target(), lock), (Target(type="local"), None)):
        session.target = selected
        pending = module.acquire_start(session)
        with pytest.raises(StopIteration) as finished:
            pending.send(None)
        assert finished.value.value is expected
    assert lock.acquired == 1


def test_bridge_capability_requires_actual_origin_and_nonblocking_creation_mode(api):
    module, Target = api
    intent = module._intent(module.arm(Target(), "original-session"))
    assert module.require_capability(capability(), intent) == "origin/control/parent"
    for field, value in (("version", True), ("creator", "receiver/control/parent"), ("role", "receiver")):
        changed = capability()
        changed[field] = value
        with pytest.raises(ValueError):
            module.require_capability(changed, intent)
    for field, value in (("version", True), ("creator_identity", False), ("kinds", ["session"]),
                         ("claim_linkage", "blocking"), ("agent_launch", True)):
        changed = capability()
        changed["ordinary_create"][field] = value
        with pytest.raises(ValueError):
            module.require_capability(changed, intent)


def test_bridge_receiving_result_is_usable_while_linkage_is_pending_but_not_unknown_creation(api):
    module, Target = api
    intent = module._intent(module.arm(Target(), "original-session"))
    saved = {
        "id": "child", "path": "/worktrees/child", "creation_nonce": "original-nonce",
        "repo": "destination", "execution_machine": "receiver",
        "owner_ref": "origin/control/parent", "kind": "bridge", "caller_worktree": "parent",
    }
    value = {
        "creation": {"version": 1, "status": "complete"}, "worktree": saved,
        "allocation": {
            "version": 1, "creator": intent["creator"], "creation_key": intent["creation_key"],
            "request_id": "original-request", "linkage": "pending", "receiving_status": "allocated",
            "target": {"machine": "receiver", "project": "destination",
                       "options": {"kind": "bridge", "interface": "acp", "origin": "delegate",
                                   "caller_worktree": "parent"}},
        },
    }
    assert module._result(value, intent) == saved
    unknown_link = copy.deepcopy(value)
    unknown_link["allocation"]["linkage"] = "unknown"
    assert module._result(unknown_link, intent) == saved
    for section, field, changed_value in (
        ("creation", "status", "pending"), ("allocation", "receiving_status", "unknown"),
        ("allocation", "creation_key", "different"), ("worktree", "kind", "session"),
        ("worktree", "owner_ref", "other/control/parent"), ("worktree", "path", "relative/path"),
    ):
        changed = copy.deepcopy(value)
        changed[section][field] = changed_value
        with pytest.raises(ValueError):
            module._result(changed, intent)
