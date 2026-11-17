"""Actor-free immutable current-operation controls over the actual adapter."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from .test_ordinary_control_adoption import control as control
from .test_ordinary_writer_admission import admission as admission


@pytest.fixture
def current(control: Any, monkeypatch: Any) -> Any:
    adoption, previous, incoming, row = control
    root = Path(__file__).resolve().parents[2] / "plugins/agent-dispatch/src/agent_dispatch"
    monkeypatch.setitem(sys.modules, "promotion_ordinary_writer.ordinary_control_adoption", adoption)
    modules: dict[str, Any] = {}
    for leaf in ("ordinary_invocation", "ordinary_control_invocation"):
        name = f"promotion_ordinary_writer.{leaf}"
        spec = importlib.util.spec_from_file_location(name, root / f"{leaf}.py")
        assert spec and spec.loader
        loaded = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, loaded)
        spec.loader.exec_module(loaded)
        modules[leaf] = loaded
    original = {
        "version": 1, "invocation_key": "original-key",
        "expected_native_session_id": "native", "prompt": "Retained assignment and coordinator routing",
        "selection": {"agent": "worker", "charter": None},
        "binding": {"admission_generation": 3, "expected_turn_index": 7, "origin_kind": "created"},
    }
    previous["startup_invocation"] = copy.deepcopy(original)
    incoming["startup_invocation"] = original
    incoming["turn_count"] = 0
    adoption.retain_control(previous, incoming, row, resumed=True)
    reservation = {**row, "key": "reservation", "startup_revision": 1}
    module = modules["ordinary_control_invocation"]
    operation = module.prepare({"generation": 3}, reservation, incoming)
    incoming[module.CONTROL] = operation
    return module, previous, incoming, reservation


def test_current_control_has_distinct_bytes_and_retains_birth_and_unknown_history(current: Any) -> None:
    module, previous, incoming, reservation = current
    before = copy.deepcopy(previous)
    module.retain(previous, incoming, reservation, 3)
    invocation = incoming[module.CONTROL]["invocation"]
    assert invocation["invocation_key"] != previous["startup_invocation"]["invocation_key"]
    assert invocation["binding"]["expected_turn_index"] == 0
    assert incoming["startup_invocation"] == previous["startup_invocation"]
    assert incoming["retained_control_history"]["predecessor"] == previous == before
    refreshed = {key: value for key, value in incoming.items() if key != module.CONTROL}
    module.retain(incoming, refreshed, reservation, 3)
    assert refreshed[module.CONTROL] == incoming[module.CONTROL]
    assert refreshed[module.CONTROL] is not incoming[module.CONTROL]


@pytest.mark.parametrize("change", ["key", "prompt", "turn", "revision", "generation", "bridge", "history", "null"])
def test_current_control_refuses_changed_operation_without_rewriting_history(current: Any, change: str) -> None:
    module, previous, incoming, reservation = current
    before = copy.deepcopy(previous)
    if change == "key":
        incoming[module.CONTROL]["invocation"]["invocation_key"] = "original-key"
    elif change == "prompt":
        incoming[module.CONTROL]["invocation"]["prompt"] += " changed"
    elif change == "turn":
        incoming[module.CONTROL]["invocation"]["binding"]["expected_turn_index"] = 1
    elif change == "revision":
        reservation["startup_revision"] += 1
    elif change == "generation":
        incoming[module.CONTROL]["task_generation"] += 1
    elif change == "bridge":
        incoming[module.CONTROL]["bridge_session_id"] = "other"
    elif change == "null":
        incoming[module.CONTROL] = None
    else:
        incoming.pop("retained_control_history")
    with pytest.raises(RuntimeError):
        module.retain(previous, incoming, reservation, 3)
    assert previous == before
