"""Actor-free admission of retained-conversation housekeeping decisions."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-dispatch/src/agent_dispatch"


@pytest.fixture
def cleanup(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    package = ModuleType("agent_dispatch")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "agent_dispatch", package)
    for name in ("queue_records", "retained_conversation_cleanup"):
        qualified = f"agent_dispatch.{name}"
        spec = importlib.util.spec_from_file_location(qualified, SOURCE / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, qualified, module)
        spec.loader.exec_module(module)
    return module


class TaskSnapshot:
    def __init__(self, status: str | None) -> None:
        self.status = status
        self.queries: list[tuple[str, tuple[str]]] = []

    def execute(self, query: str, values: tuple[str]) -> TaskSnapshot:
        self.queries.append((query, values))
        return self

    def fetchone(self) -> dict[str, str] | None:
        return {"status": self.status} if self.status is not None else None


def reservation(**changes: Any) -> dict[str, Any]:
    return {
        "driver": "ordinary-bridge", "conversation_retired": False,
        "session_handle": "local-body:original", "task_id": "original-task", **changes,
    }


@pytest.mark.parametrize(("status", "held"), [
    ("queued", True), ("submitted", True), ("completed", False),
    ("abandoned", False), (None, True),
], ids=["queued", "submitted", "completed", "abandoned", "unknown"])
def test_current_task_need_controls_cleanup(
    cleanup: ModuleType, status: str | None, held: bool,
) -> None:
    snapshot = TaskSnapshot(status)
    original = reservation()
    assert cleanup.pending_conversation(snapshot, original) is held
    if held:
        with pytest.raises(RuntimeError, match="non-terminal conversation"):
            cleanup.require_conversation_released(snapshot, original)
    else:
        cleanup.require_conversation_released(snapshot, original)
    assert [values for _query, values in snapshot.queries] == [("original-task",)] * 2
    assert original == reservation()


@pytest.mark.parametrize("changes", [
    {"conversation_retired": True}, {"driver": "cli"}, {"session_handle": None},
], ids=["retired", "other-driver", "no-local-handle"])
def test_unrelated_cleanup_does_not_acquire_task_evidence(
    cleanup: ModuleType, changes: dict[str, Any],
) -> None:
    snapshot = TaskSnapshot("queued")
    cleanup.require_conversation_released(snapshot, reservation(**changes))
    assert snapshot.queries == []


def test_terminal_observation_cannot_authorize_a_later_reopened_task(cleanup: ModuleType) -> None:
    snapshot = TaskSnapshot("completed")
    original = reservation()
    cleanup.require_conversation_released(snapshot, original)
    snapshot.status = "queued"
    with pytest.raises(RuntimeError, match="non-terminal conversation"):
        cleanup.require_conversation_released(snapshot, original)
    assert len(snapshot.queries) == 2
