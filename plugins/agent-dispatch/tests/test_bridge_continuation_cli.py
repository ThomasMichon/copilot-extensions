"""Fleet continuation retains the published startup-bound CLI contract."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from typing import Any

import pytest

from agent_dispatch import bridge, bridge_reclaim, ordinary_bridge, session_acquisition


@pytest.mark.parametrize("wait,timeout", [(False, 20.0), (True, 20.0), (False, None)])
def test_fleet_continuation_uses_one_startup_bound_command_and_original_input(
    monkeypatch: pytest.MonkeyPatch, wait: bool, timeout: float | None,
) -> None:
    calls: list[tuple[list[str], str | None, float | None]] = []
    prompt = "continue\nliteral 'quote' & value"
    monkeypatch.setattr(shutil, "which", lambda name: "ssh-fixture" if name == "ssh" else None)

    def run(
        argv: list[str], *, input: str | None = None, timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((argv, input, timeout))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", run)
    assert bridge.resume_session("session-a", prompt, host=" Pool-A ", wait=wait, timeout=timeout)
    assert len(calls) == 1
    argv, original, wall_timeout = calls[0]
    assert argv[:6] == [
        "ssh-fixture", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "pool-a",
    ]
    command = shlex.split(argv[6])
    assert command[:6] == [
        "agent-bridge", "send", "session-a", "--require-agent-hosting", "--prompt-file", "-",
    ]
    assert ("--no-wait" in command) is not wait
    assert command[-4:] == [
        "--startup-communication-timeout", str(30.0 if timeout is None else timeout),
        "--startup-process-timeout", "300.0",
    ]
    assert "--legacy-selector" not in command
    assert prompt not in argv[6] and original == prompt and wall_timeout == timeout


@pytest.mark.parametrize("producer", ["worktree", "reclaim"])
@pytest.mark.parametrize("wait", [False, True])
def test_fleet_continuation_startup_producers_keep_existing_owner_contract(
    monkeypatch: pytest.MonkeyPatch, producer: str, wait: bool,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        output = json.dumps({"session_id": "session-a"}) if "resume" in argv else "accepted"
        return subprocess.CompletedProcess(argv, 0, output, "")

    monkeypatch.setattr(subprocess, "run", run)
    if producer == "worktree":
        result = bridge._spawn_worker_via_worktree(
            exe=["bridge"], task_id="task-a", agent="worker", worker_id="controller-a",
            prompt="original\nprompt", route="", project="worker-project", target_dir=None,
            worktree_id="worker-a", reclaim=False, wait=wait, json_output=False, timeout=20.0,
        )
        assert calls[0][0] == [
            "bridge", "--json", "resume", "worker-a", "--require-agent-hosting",
        ]
        assert len(calls) == 2
    else:
        result = bridge_reclaim._send(
            "worker-a", "original\nprompt", exe=["bridge"], caller="agent-dispatch:controller-a",
            wait=wait, json_output=False, timeout=20.0,
        )
        assert len(calls) == 1
    assert result.returncode == 0
    argv, options = calls[-1]
    assert argv[:8] == [
        "bridge", "send", "session-a" if producer == "worktree" else "worker-a",
        "--require-agent-hosting", "--prompt-file", "-",
        "--caller", "agent-dispatch:controller-a",
    ]
    assert ("--no-wait" in argv) is not wait
    assert argv[-4:] == [
        "--startup-communication-timeout", "20.0", "--startup-process-timeout", "300.0",
    ]
    assert "--legacy-selector" not in argv
    assert options["input"] == "original\nprompt" and options["timeout"] == 20.0


@pytest.mark.parametrize("response", [
    "legacy success", "", "[]", "null", "{}", '{"session_id":7}', '{"session_id":"  "}',
])
def test_fleet_continuation_worktree_startup_without_resumed_identity_never_sends(
    monkeypatch: pytest.MonkeyPatch, response: str,
) -> None:
    calls: list[list[str]] = []

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, response, "")

    monkeypatch.setattr(subprocess, "run", run)
    result = bridge._spawn_worker_via_worktree(
        exe=["bridge"], task_id="task-a", agent="worker", worker_id="controller-a",
        prompt="original prompt", route="", project="worker-project", target_dir=None,
        worktree_id="workspace-a", reclaim=False, wait=False, json_output=True, timeout=20.0,
    )
    assert calls == [["bridge", "--json", "resume", "workspace-a", "--require-agent-hosting"]]
    assert result.returncode == 1 and "no session_id" in result.stderr


@pytest.mark.parametrize("producer", ["ordinary", "acquisition"])
def test_fleet_continuation_observation_keeps_legacy_wait_and_parent_environment(
    monkeypatch: pytest.MonkeyPatch, producer: str,
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_LEGACY_OBSERVATION", "parent-value")
    before = dict(os.environ)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        response: dict[str, Any] = {}
        if "acquire" in argv:
            request = json.loads(kwargs["input"])
            response = {
                **{key: request[key] for key in (
                    "mode", "task_id", "task_generation", "worktree_id", "delivery_id",
                )},
                "session_id": "worker-a", "native_session_id": "native-worker",
            }
        return subprocess.CompletedProcess(argv, 0, json.dumps(response), "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ordinary_bridge, "agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])
    if producer == "ordinary":
        result = ordinary_bridge.observe({"session": "local-body:worker-a"}, timeout=20.0)
        assert len(calls) == 1
    else:
        from agent_dispatch import session_origin

        monkeypatch.setattr(session_origin, "acquisition", lambda _task, _mode: (None, None))
        result = session_acquisition.acquire(
            {
                "id": "task-a", "generation": 2, "spawn_reservation_key": "reservation-a",
                "spawn_worktree": "workspace-a", "spawn_worktree_ownership": "reused",
                "spawn_allocation_project": "worker-project", "spawn_worktree_path": "worker-path",
                "spawn_session_handle": "local-body:worker-a", "spawn_conversation_retired": False,
            },
            mode="acp", prompt="original prompt", wait=True, timeout=20.0,
        )
        assert len(calls) == 2 and calls[0][0] == ["bridge", "acquire", "--request-file", "-"]
    assert result.returncode == 0
    argv, options = calls[-1]
    assert argv == ["bridge", "wait", "worker-a", "--json"]
    assert options["env"] == {**before, "AGENT_BRIDGE_LEGACY_OBSERVATION": "1"}
    assert dict(os.environ) == before
    assert options["timeout"] == 20.0


def test_fleet_continuation_reclaim_without_proven_identity_never_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refusal = subprocess.CompletedProcess(
        [], 1, json.dumps({"reason": "live_cli_holds_worktree", "session_id": "holder-a"}), "",
    )
    monkeypatch.setattr(bridge_reclaim, "_resume", lambda *_args, **_kwargs: (refusal, None))
    monkeypatch.setattr(bridge_reclaim, "_take_over_live_holder", lambda *_args, **_kwargs: (None, None))
    monkeypatch.setattr(
        bridge_reclaim, "_send", lambda *_args, **_kwargs: pytest.fail("unproven identity reached send"),
    )
    result = bridge_reclaim.resume_worktree_and_send(
        "workspace-a", "original prompt", exe=["bridge"], agent="worker",
        caller="agent-dispatch:controller-a", wait=False, json_output=False, timeout=20.0,
    )
    assert result.returncode == 1 and "no session_id" in result.stderr
