"""Pure work counts at the actual keyed-admission scheduling boundary."""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest


@pytest.mark.parametrize("origin,read_only,cached,expected", [
    ("created", False, False, ["submit"]),
    ("resumed", False, False, ["writer", "submit"]),
    ("resumed", True, False, []),
    ("resumed", True, True, []),
    ("resumed", False, True, []),
], ids=["created-fresh", "resumed-fresh", "receipt-miss", "receipt-present", "cached-admission"])
def test_keyed_admission_qualifies_only_fresh_resumed_work(
    monkeypatch: Any, origin: str, read_only: bool, cached: bool, expected: list[str],
) -> None:
    path = (Path(__file__).resolve().parents[2] / "plugins/agent-bridge/src/agent_bridge"
            / "native_keyed_admission.py")
    function = next(
        node for node in ast.parse(path.read_bytes()).body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "submit"
    )
    calls: list[str] = []
    package = ModuleType("promotion_retained_work")
    package.__dict__["__path__"] = []
    owner = ModuleType("promotion_retained_work.creation_worktree_writer")

    async def qualified(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("submit")
        return {"accepted": True}

    async def continuation(
        manager: Any, session: Any, method: Any, session_id: Any, args: Any, kwargs: Any,
    ) -> Any:
        calls.append("writer")
        return True, await method(manager, session_id, *args, **kwargs)

    setattr(owner, "continue_session", continuation)
    monkeypatch.setitem(sys.modules, package.__name__, package)
    monkeypatch.setitem(sys.modules, owner.__name__, owner)

    class Lock:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: Any) -> None:
            return None

    session = SimpleNamespace(
        session_id="control", acp_session_id="native", _turn_start_lock=Lock(),
        target=SimpleNamespace(worktree_id="workspace", project="project", cwd="/workspace"),
    )
    manager = SimpleNamespace(get_session=lambda session_id: session, db=object(), is_draining=False)
    request = SimpleNamespace(
        expected_native_session_id="native",
        binding=SimpleNamespace(model_dump=lambda **kwargs: {
            "origin_kind": origin, "worktree_id": "workspace", "project": "project", "path": "/workspace",
        }),
    )
    namespace: dict[str, Any] = {
        "__package__": package.__name__, "Any": Any, "KeyedNativePrompt": Any,
        "stored_receipt": lambda *args: {"receipt": "accepted"} if cached else None,
        "_submit_qualified": qualified,
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    coroutine = namespace["submit"](manager, "control", request, read_only=read_only)
    # Every awaited seam completes in memory; no event loop, socket or child is needed.
    with pytest.raises(StopIteration) as complete:
        coroutine.send(None)
    expected_result: Any = (
        {"receipt": "accepted"} if cached else None if read_only else {"accepted": True}
    )
    assert complete.value.value == expected_result
    assert calls == expected
