"""Managed caller admission before shared resident policy locks."""

from __future__ import annotations

import argparse
import json
import socket
import time
from _thread import RLock
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from agent_worktrees import __main__ as m
from agent_worktrees import allocation_link_runtime, hook_ipc, locks as worktree_locks, loop_governance
from agent_worktrees import status_monitor_cutover as smc
from test_status_monitor import _elect_monitor_for_one_iteration


@pytest.mark.parametrize("kind,admitted", [
    ("preToolUse", False), ("postToolUse", False),
    ("nativeToolMutation", False), ("nativeToolBatchMutation", False),
    ("shellToolMutation", False), ("mixedToolBatchMutation", False),
    ("sessionStart", False), ("sessionEnd", False),
    ("preToolUse", True), ("nativeToolMutation", True),
])
def test_managed_hook_membership_precedes_busy_policy_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, admitted: bool,
) -> None:
    events: list[str] = []
    locks: list[RLock] = []
    replies: list[dict[str, Any] | bytes] = []
    native_mutation = kind in {
        "nativeToolMutation", "nativeToolBatchMutation",
        "shellToolMutation", "mixedToolBatchMutation",
    }

    class Client:
        def _load_sibling(self, name: str) -> object:
            return object()

        def _managed_session_admitted(self, payload: dict[str, Any]) -> bool:
            events.append("membership")
            return admitted

    monkeypatch.setattr(m, "_monitor_lock_path", lambda: tmp_path / "status-monitor.lock")
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: Client())
    monkeypatch.setattr(worktree_locks, "write_lock", lambda _path, extra=None: True)
    monkeypatch.setattr(smc, "publish_route", lambda *a, **k: None)
    cast(Callable[[pytest.MonkeyPatch], None], _elect_monitor_for_one_iteration)(monkeypatch)
    monkeypatch.setattr(smc, "clear_route_if_owner", lambda pid: False)
    monkeypatch.setattr(m, "_runtime_superseded", lambda **kw: False)
    monkeypatch.setattr(m, "_monitor_sweep", lambda *a, **k: pytest.fail("unexpected sweep"))
    monkeypatch.setattr(time, "sleep", lambda *a: None)
    monkeypatch.setattr(
        m, "_resident_hook_decision",
        lambda *a, **k: pytest.fail("policy must not run while its state lock is held"),
    )

    class Governance:
        def recheck(self, checkpoint: str) -> dict[str, str]:
            return {"status": "backoff" if checkpoint == "iteration-boundary" else "ready"}

    monkeypatch.setattr(loop_governance, "LoopGovernance", Governance)
    real_linker = allocation_link_runtime.AllocationLinkRuntime
    real_timeout = m._resident_hook_lock_timeout

    def linker(*args: Any, **kwargs: Any) -> allocation_link_runtime.AllocationLinkRuntime:
        locks.append(kwargs["context_lock"])
        return real_linker(*args, **kwargs)

    def lock_timeout(request_kind: str, remaining: float) -> float:
        events.append("lock")
        return real_timeout(request_kind, remaining)

    monkeypatch.setattr(allocation_link_runtime, "AllocationLinkRuntime", linker)
    monkeypatch.setattr(m, "_resident_hook_lock_timeout", lock_timeout)

    class Server(hook_ipc.HookIpcServer):
        def start(self) -> None:
            super().start()
            original_lock = self._read_demand_lock

            class AdmissionLock:
                def acquire(self, *, timeout: float) -> bool:
                    events.append("ipc-lock")
                    return original_lock.acquire(timeout=timeout)

                def release(self) -> None:
                    original_lock.release()

            self._read_demand_lock = AdmissionLock()
            payload: dict[str, Any] = {"cwd": str(tmp_path), "toolName": "view"}
            if native_mutation:
                payload.update(toolName="edit", toolArgs={"path": "one", "new_str": "value"})
                if kind == "shellToolMutation":
                    payload.update(toolName="powershell", toolArgs={"command": "git status"})
                elif kind in {"nativeToolBatchMutation", "mixedToolBatchMutation"}:
                    payload = {"cwd": str(tmp_path), "toolCalls": [
                        {"name": "edit", "args": {"path": "one", "new_str": "value"}},
                    ]}
            address = cast(tuple[str, int], self.server.server_address)
            with locks[0], socket.create_connection(address, timeout=2) as conn:
                conn.sendall(json.dumps({
                    "version": 1, "token": self.token, "kind": kind,
                    "hook_generation": self.generation,
                    "payload": payload,
                    "deadline": time.time() + 0.5,
                }).encode() + b"\n")
                if native_mutation:
                    replies.append(conn.recv(1))
                else:
                    with conn.makefile("rb") as stream:
                        replies.append(json.loads(stream.readline(65536)))

    monkeypatch.setattr(hook_ipc, "HookIpcServer", Server)
    assert m.cmd_status_monitor(argparse.Namespace(interval=5)) == 0
    assert len(replies) == 1
    if native_mutation:
        assert events == (["membership", "ipc-lock"] if admitted else ["membership"])
        assert replies == [b""]
    else:
        reply = replies[0]
        assert isinstance(reply, dict)
        if admitted:
            assert events == ["membership", "lock"]
            assert reply["fallback"] is True
        else:
            assert events == ["membership"]
            assert reply["result"] == {}
            assert "fallback" not in reply
