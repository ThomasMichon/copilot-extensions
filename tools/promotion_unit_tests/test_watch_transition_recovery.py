"""Actor-free prepared adaptation; native custody remains separately qualified."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from promotion_unit_tests.test_watch_sealing import sealing as sealing


@pytest.fixture
def recovery(sealing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    modeled, state, root, binding, lease = sealing
    package = modeled.__package__
    installation = ModuleType(f"{package}.watch_installation")
    setattr(installation, "WatchInstallation", object)
    monkeypatch.setitem(sys.modules, installation.__name__, installation)
    zdd = ModuleType("zdd")
    zdd.__path__ = []
    cutover = ModuleType("zdd.cutover_lock")
    setattr(cutover, "CutoverLock", object)
    monkeypatch.setitem(sys.modules, "zdd", zdd)
    monkeypatch.setitem(sys.modules, cutover.__name__, cutover)
    source = Path(modeled.__file__).with_name("watch_transition_recovery.py")
    spec = importlib.util.spec_from_file_location(f"{package}.watch_transition_recovery", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    spec.loader.exec_module(module)
    paths = module.watch_state_paths(root)
    monkeypatch.setattr(module, "require_private", lambda _path: None)
    monkeypatch.setattr(module, "_regular_bytes", lambda _path: json.dumps(state["document"]).encode())
    monkeypatch.setattr(
        module, "_historical_identity", lambda _root: modeled._identity(paths.historical_lock),
    )
    monkeypatch.setattr(module, "_pin_identity", lambda _root: state["pin"])
    monkeypatch.setattr(module, "_confirm_pin", lambda _root, _identity: None)
    monkeypatch.setattr(module, "_read_subscriptions_state", lambda _path, **_kw: [])
    monkeypatch.setattr(module, "write_private", modeled.write_private)
    state["confirmations"] = []
    monkeypatch.setattr(module, "confirm_private", lambda path: state["confirmations"].append(path))
    lease.held = False
    return module, state, root, binding, lease


def _recover(fixture: Any) -> Any:
    module, _state, root, _binding, lease = fixture
    return module.recover_prepared(
        root, lease, installation=None, guard=None,
        validate=lambda: None, check_deadline=lambda: None,
    )


def test_prepared_adapter_preserves_complete_origin(recovery: Any) -> None:
    _module, state, _root, binding, lease = recovery
    original = json.loads(json.dumps(state["document"]))
    result = _recover(recovery)
    assert result["version"] == 2 and result["phase"] == "prepared"
    assert result["origin"] == original
    assert result["binding"] == original["binding"] and result["lease"] == original["lease"]
    assert result["controller"] == binding
    assert lease.held and len(state["acquisitions"]) == 1
    assert state["writes"] == ["prepared"]


def test_prepared_adapter_final_guard_preserves_concurrent_journal(
    recovery: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module, state, _root, _binding, lease = recovery
    publish = module.write_private

    def raced(*args: Any, **kwargs: Any) -> None:
        state["document"]["unexpected"] = True
        publish(*args, **kwargs)

    monkeypatch.setattr(module, "write_private", raced)
    with pytest.raises(PermissionError):
        _recover(recovery)
    assert state["document"]["version"] == 1 and state["document"]["unexpected"] is True
    assert state["writes"] == [] and lease.held


def test_prepared_adapter_current_retry_confirms_without_rewriting(recovery: Any) -> None:
    _module, state, root, _binding, lease = recovery
    result = _recover(recovery)
    serialized = json.dumps(state["document"])
    lease.held = False
    assert _recover(recovery) == result
    assert json.dumps(state["document"]) == serialized
    assert state["writes"] == ["prepared"]
    assert state["confirmations"] == [root / "watch-state-transition.json"]
    assert len(state["acquisitions"]) == 2
