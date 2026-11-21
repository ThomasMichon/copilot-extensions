"""Bound ordinary startup keeps its original route when discovery is unavailable."""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from agent_dispatch import bridge, ordinary_bridge
from agent_dispatch.queue import TaskQueue
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_ordinary_bridge_startup import startup as startup

pytestmark = pytest.mark.contract("agent_dispatch.ordinary_bridge")

Startup = tuple[TaskQueue, dict[str, Any], object, list[tuple[Any, ...]]]


def unavailable(*_args: Any, **_kwargs: Any) -> str:
    raise bridge.BridgeUnavailable("agent registry unavailable")


def test_unbound_startup_still_requires_agent_discovery(
    startup: Startup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task, client, calls = startup
    monkeypatch.setattr(bridge, "registered_agent_project", unavailable)
    with pytest.raises(bridge.BridgeUnavailable, match="agent registry unavailable"):
        ordinary_bridge.start(
            task, client=client, agent="local", project="example",
            charter="reviewer", prompt="assigned task",
        )
    assert calls == []
    reservation = queue.get_reservation(task["spawn_reservation_key"])
    assert reservation is not None and reservation.allocation_project is None


@pytest.mark.parametrize("lost_create_reply", [False, True])
def test_bound_retry_does_not_require_fresh_agent_discovery(
    startup: Startup, monkeypatch: pytest.MonkeyPatch, lost_create_reply: bool,
) -> None:
    queue, task, client, calls = startup
    original = ordinary_bridge.subprocess.run

    def lose_reply(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        result: subprocess.CompletedProcess[str] = original(argv, **kwargs)
        if argv[2] == "create":
            raise subprocess.TimeoutExpired(argv, 1)
        return result

    if lost_create_reply:
        monkeypatch.setattr(ordinary_bridge.subprocess, "run", lose_reply)
        with pytest.raises(ordinary_bridge.CreatedAgentUnrecorded):
            ordinary_bridge.start(
                task, client=client, agent="local", project="example",
                charter="reviewer", prompt="assigned task",
            )
        monkeypatch.setattr(ordinary_bridge.subprocess, "run", original)
    else:
        ordinary_bridge.start(
            task, client=client, agent="local", project="example",
            charter="reviewer", prompt="assigned task",
        )
    first = queue.get_reservation(task["spawn_reservation_key"])
    assert first is not None and first.allocation_project == "example"
    queue.defer_spawn(first.key, detail="original startup continuation")
    restarted = TaskQueue(queue.db_path)
    retry, admitted = restarted.reserve_spawn(task["id"], driver="ordinary-bridge")
    assert admitted and retry.creation_key == first.creation_key
    monkeypatch.setattr(bridge, "registered_agent_project", unavailable)
    result = ordinary_bridge.start(
        {**task, "spawn_reservation_key": retry.key}, client=client,
        agent="local", project="changed-routing", charter="reviewer", prompt="assigned task",
    )
    assert result["session"] == "local-body:bridge-a"
    assert result["native_session_id"] == "native-a"
    creates = [call[1] for call in calls if call[0] == "create"]
    assert len(creates) == (2 if lost_create_reply else 1)
    assert all(argv == creates[0] for argv in creates)
    stored = queue.get_reservation(retry.key)
    assert stored is not None
    assert stored.allocation_project == "example"
    assert stored.worktree == "worktree-a"
    assert stored.session_handle == "local-body:bridge-a"
