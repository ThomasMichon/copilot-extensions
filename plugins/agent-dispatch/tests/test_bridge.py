"""Tests for agent-bridge integration (spawn worker) and claim-by-id."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent_dispatch import bridge, bridge_agent_registry, procutil
from agent_dispatch.queue import Status
from tests._helpers import RepoDefaultingQueue as TaskQueue

# -- claim by id -------------------------------------------------------------


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def test_claim_specific_task_by_id(q):
    a = q.create("a")
    b = q.create("b")
    # claim the *second*, older-ordering notwithstanding
    got = q.claim_one("w1", task_id=b.id)
    assert got is not None and got.id == b.id
    # a is still queued
    assert q.get(a.id).status == Status.QUEUED


def test_claim_by_id_respects_eligibility(q):
    t = q.create("needs-cap", requires=["review"])
    assert q.claim_one("w1", task_id=t.id) is None  # lacks capability
    assert q.claim_one("w1", ["review"], task_id=t.id).id == t.id


def test_claim_by_id_missing_returns_none(q):
    assert q.claim_one("w1", task_id="does-not-exist") is None


def test_claim_by_id_already_claimed_returns_none(q):
    t = q.create("x")
    q.claim_one("w1", task_id=t.id)
    assert q.claim_one("w2", task_id=t.id) is None  # no longer queued


# -- bridge spawn ------------------------------------------------------------


def test_worker_prompt_mentions_task_and_verbs():
    prompt = bridge.worker_prompt("abc123", worker_id="w9")
    assert "abc123" in prompt
    assert "w9" in prompt
    assert "without `--url`" in prompt
    assert "http://" not in prompt
    assert "agent-dispatch claim abc123 --worker w9" in prompt
    assert "agent-dispatch steer take abc123 w9 --all" in prompt


def test_worker_prompt_threads_shared_moniker_route():
    prompt = bridge.worker_prompt("abc123", worker_id="w9", route=" --shared")
    assert "agent-dispatch --shared show abc123" in prompt
    assert "agent-dispatch --shared claim abc123 --worker w9" in prompt
    assert "http://" not in prompt


def test_spawn_worker_unavailable_when_no_bridge(monkeypatch):
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    assert bridge.bridge_available() is False
    with pytest.raises(bridge.BridgeUnavailable):
        bridge.spawn_worker("t1", worker_id="w1")


def test_launch_prefix_prefers_versioned_runtime_over_cmd_shim(monkeypatch, tmp_path):
    """The autopilot seed carries cmd.exe metacharacters (``&``, ``()``, ``<>``,
    backtick). Launching the Windows ``agent-bridge.cmd`` shim makes cmd.exe
    re-parse ``%*`` and corrupt the seed (WinError 2, BatBadBut -- #4395). So when
    the agent-bridge versioned runtime is installed, its slot interpreter +
    ``-m agent_bridge`` is preferred (resolved the canonical way via the
    ``current-version`` marker), bypassing any ``.cmd`` shim entirely."""
    slot_py = tmp_path / ".agent-bridge" / "versions" / "0.1.0-dev9" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    slot_py.parent.mkdir(parents=True)
    slot_py.write_text("")  # only needs to exist as a file
    (tmp_path / ".agent-bridge" / "current-version").write_text("0.1.0-dev9")
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    # Even with a .cmd binstub on PATH, the versioned interpreter wins.
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-bridge.cmd"
    )
    prefix = bridge._agent_bridge_launch_prefix()
    assert prefix == [str(slot_py), "-m", "agent_bridge"]
    # The launcher is a real interpreter, never a shell shim that re-parses args.
    assert not prefix[0].lower().endswith((".cmd", ".bat"))


def test_launch_prefix_falls_back_to_binstub_on_posix(monkeypatch, tmp_path):
    """Without an installed versioned runtime, fall back to the ``agent-bridge``
    binstub on PATH **on POSIX only** (its shims are plain exec scripts -- no
    cmd.exe re-parse)."""
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(procutil.shutil, "which", lambda _n: "/usr/bin/agent-bridge")
    assert bridge._agent_bridge_launch_prefix() == ["/usr/bin/agent-bridge"]


def test_launch_prefix_no_ps1_fallback_on_windows(monkeypatch, tmp_path):
    """On Windows, with no versioned runtime, do NOT fall back to the ``.ps1``
    binstub (``subprocess`` cannot exec it -> WinError 2). Return ``None`` so the
    caller degrades deliberately (the #974 fix)."""
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-bridge.ps1"
    )
    assert bridge._agent_bridge_launch_prefix() is None


def test_launch_prefix_none_when_unresolvable(monkeypatch, tmp_path):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(procutil.shutil, "which", lambda _n: None)
    assert bridge._agent_bridge_launch_prefix() is None


def test_spawn_worker_invokes_agent_bridge_create(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge.spawn_worker(
        "task42", agent="task-worker", worker_id="w1", wait=False
    )
    assert result.returncode == 0
    cmd = calls["cmd"]
    assert cmd[:3] == ["/usr/bin/agent-bridge", "create", "task-worker"]
    assert "task42" in cmd[3]  # the prompt carries the task id
    assert cmd[-1] == "--no-wait"  # wait=False -> --no-wait


def test_spawn_worker_uses_worktree_resume_send_for_unaddressable_profile(monkeypatch):
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if cmd[1:3] == ["--json", "live-sessions"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout='{"session_id":"sid-123","worktree_id":"wt-1"}',
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge,
        "_resolve_agent_record",
        lambda name, **_kw: {
            "name": name,
            "managed": False,
            "spawnable_as_target": False,
        },
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge.spawn_worker(
        "task42",
        agent="task-worker",
        worker_id="w1",
        worktree_id="wt-1",
        wait=False,
        json_output=True,
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["session_id"] == "sid-123"
    assert calls[0][0] == ["/usr/bin/agent-bridge", "resume", "wt-1"]
    assert calls[1][0][:4] == ["/usr/bin/agent-bridge", "send", "wt-1", "--prompt-file"]
    assert calls[1][1]["input"]
    assert calls[2][0] == [
        "/usr/bin/agent-bridge",
        "--json",
        "live-sessions",
        "resolve",
        "--handle",
        "wt-1",
    ]


def test_spawn_worker_passes_caller_for_picker_origin(monkeypatch):
    """copilot-extensions#2202: without --caller, the spawned worktree has no
    caller_worktree stamped, so agent-worktrees' resolved_origin falls through
    to "user" (Picker-visible) instead of "delegate" (Picker-hidden) -- every
    autopilot worker looked exactly like an operator-created worktree. --caller
    must ride every `create` invocation, keyed to this specific spawn attempt."""
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    bridge.spawn_worker("task42", agent="task-worker", worker_id="worker-abc123")

    cmd = calls["cmd"]
    assert "--caller" in cmd
    caller_value = cmd[cmd.index("--caller") + 1]
    assert caller_value == "agent-dispatch:worker-abc123"


def test_spawn_worker_passes_no_window_kwargs(monkeypatch):
    """The console launcher runs windowless (CREATE_NO_WINDOW on Windows)."""
    calls = {}

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge, "no_window_kwargs", lambda: {"creationflags": 0x08000000}
    )

    def fake_run(cmd, **kwargs):
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    bridge.spawn_worker("t", worker_id="w")
    assert calls["kwargs"].get("creationflags") == 0x08000000


def test_spawn_worker_wait_omits_no_wait(monkeypatch):
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    result = bridge.spawn_worker("t", worker_id="w", wait=True)
    assert result.returncode == 0


def test_spawn_worker_reclaim_resumes_then_sends(monkeypatch):
    """``reclaim=True`` (#6744 Phase 3, since ``create --reclaim`` was removed
    from agent-bridge) resolves via ``resume <worktree_id>`` (no ``--force`` --
    see ``bridge_reclaim``'s own docstring on why this never bypasses the
    live-CLI-holder guard) followed by ``send`` -- not a ``create`` invocation
    at all -- so a coordinator-judged-stale worktree occupant (e.g. an
    unclaimed handoff past its reconciliation window) is replaced in place
    instead of refused 409."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:4] == ["--json", "resume", "wt-1"]:
            return subprocess.CompletedProcess(
                cmd, 0, '{"session_id": "resumed-9"}', "",
            )
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    result = bridge.spawn_worker(
        "task42", agent="task-worker", worker_id="w1",
        worktree_id="wt-1", reclaim=True, wait=False,
    )
    assert result.returncode == 0
    assert len(calls) == 3
    assert calls[0][1:4] == ["--json", "agent-show", "task-worker"]
    assert calls[1] == ["/usr/bin/agent-bridge", "--json", "resume", "wt-1"]
    assert calls[2][:4] == ["/usr/bin/agent-bridge", "send", "resumed-9", "--prompt-file"]
    assert "--no-wait" in calls[2]


def test_spawn_worker_reclaim_requires_worktree_id(monkeypatch):
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    with pytest.raises(ValueError):
        bridge.spawn_worker("task42", worker_id="w1", reclaim=True)


def test_spawn_worker_omits_reclaim_by_default(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    bridge.spawn_worker(
        "task42", agent="task-worker", worker_id="w1", worktree_id="wt-1",
    )
    assert "--reclaim" not in calls["cmd"]
    assert "create" in calls["cmd"]


def test_stop_worker_reaps_owned_session_host(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.stop_worker("session-1") is True
    assert calls["cmd"] == [
        "/usr/bin/agent-bridge",
        "stop",
        "session-1",
        "--reap-host",
    ]


def test_stop_worker_falls_back_for_unsupported_reap_host(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--reap-host" in cmd:
            return subprocess.CompletedProcess(
                cmd,
                2,
                "",
                "agent-bridge: error: unrecognized arguments: --reap-host",
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge.stop_worker("session-1") is True
    assert calls == [
        ["/usr/bin/agent-bridge", "stop", "session-1", "--reap-host"],
        ["/usr/bin/agent-bridge", "stop", "session-1"],
    ]


def test_stop_worker_does_not_fallback_for_general_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            1,
            "",
            "session teardown failed",
        )

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge.stop_worker("session-1") is False
    assert calls == [
        ["/usr/bin/agent-bridge", "stop", "session-1", "--reap-host"],
    ]


def test_end_worker_requires_atomic_idle_state(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.end_worker("session-1") is True
    assert calls["cmd"] == [
        "/usr/bin/agent-bridge",
        "end",
        "session-1",
        "--if-idle",
    ]


def test_force_end_session_runs_unconditional_end(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.force_end_session("session-1") is True
    assert calls["cmd"] == ["/usr/bin/agent-bridge", "end", "session-1", "--force"]


def test_force_end_session_returns_false_without_bridge(monkeypatch):
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    assert bridge.force_end_session("session-1") is False


@pytest.mark.parametrize(
    "raised", [OSError("no such file"), subprocess.TimeoutExpired(cmd="agent-bridge", timeout=20)]
)
def test_force_end_session_degrades_to_false_on_subprocess_exception(monkeypatch, raised):
    """PR #2913 review: the docstring promises this never raises -- a missing
    binary (OSError) or a stuck process (TimeoutExpired) must degrade to
    `False` like every other failure mode, since `force_stop` calls this
    before its own fenced `suspend` and must not itself blow up mid-call."""
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        raise raised

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.force_end_session("session-1") is False


def test_resume_worker_sends_to_existing_session(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.resume_worker("session-1", "continue") is True
    assert calls["cmd"] == [
        "/usr/bin/agent-bridge",
        "send",
        "session-1",
        "--prompt-file",
        "-",
        "--no-wait",
    ]
    assert calls["input"] == "continue"


def test_resume_session_local_delegates_to_resume_worker(monkeypatch):
    """``host=None`` delegates straight to :func:`resume_worker`."""
    seen = {}

    def fake_resume_worker(session_id, prompt, *, wait=False, timeout=20.0):
        seen.update(session_id=session_id, prompt=prompt, wait=wait, timeout=timeout)
        return True

    monkeypatch.setattr(bridge, "resume_worker", fake_resume_worker)
    assert bridge.resume_session("session-1", "continue") is True
    assert seen == {
        "session_id": "session-1", "prompt": "continue", "wait": False, "timeout": 20.0,
    }


def test_resume_session_fleet_normalizes_host_and_runs_over_ssh(monkeypatch):
    """A fleet ``host`` is normalized (case/whitespace) before it reaches the
    SSH argv -- an un-normalized alias could pass the fleet liveness gate
    (which normalizes) yet fail delivery here if it didn't (#2889 review)."""
    calls = {}

    def fake_which(name):
        return "/usr/bin/ssh" if name == "ssh" else None

    def fake_run_ssh_command(argv, *, input=None, timeout=None):
        calls["argv"] = argv
        calls["input"] = input
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge.shutil, "which", fake_which)
    monkeypatch.setattr(bridge, "run_ssh_command", fake_run_ssh_command)

    assert bridge.resume_session("session-1", "continue", host=" Pool-A ") is True
    assert calls["argv"][:5] == [
        "/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
    ]
    assert calls["argv"][5] == "pool-a"  # normalized: stripped + lowercased
    assert "agent-bridge send session-1 --prompt-file - --no-wait" in calls["argv"][6]
    assert calls["input"] == "continue"


def test_resume_session_fleet_no_ssh_binary_fails_closed(monkeypatch):
    monkeypatch.setattr(bridge.shutil, "which", lambda name: None)
    assert bridge.resume_session("session-1", "continue", host="pool-a") is False


# -- steer-owner resume ------------------------------------------------------


def test_resume_steered_owner_queues_work_prompt(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert bridge.resume_steered_owner(
        "host/worktree-1", "task-42", owner_session_id="session-42"
    ) is True
    cmd = calls["cmd"]
    assert cmd[:8] == [
        "/usr/bin/agent-bridge",
        "send",
        "--no-wait",
        "--queue",
        "--kind",
        "prompt",
        "--sender",
        "agent-dispatch-steer",
    ]
    assert cmd[8:10] == ["--expected-session-id", "session-42"]
    assert cmd[10:] == ["worktree-1", "--prompt-file", "-"]
    assert "agent-dispatch steer take task-42 --all" in calls["kwargs"]["input"]


def test_resume_steered_headless_owner_uses_exact_session(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge,
        "resume_worker",
        lambda session_id, prompt, **kwargs: (
            calls.update(session_id=session_id, prompt=prompt) or True
        ),
    )
    assert bridge.resume_steered_owner(
        "headless-owner",
        "task-42",
        owner_session_id="bridge-session",
    )
    assert calls["session_id"] == "bridge-session"
    assert "task-42" in calls["prompt"]


def test_resume_steered_owner_degrades_when_bridge_unavailable(monkeypatch):
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host")
    assert bridge.resume_steered_owner("host/worktree-1", "task-42") is False


def test_resume_steered_owner_forwards_idempotency_key(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["agent-bridge"]
    )
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host")

    def fake_run(cmd, **_kwargs):
        calls["cmd"] = cmd
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.resume_steered_owner(
        "host/worktree-1",
        "task-42",
        owner_session_id="session-42",
        idempotency_key="wake:task-42:1:1",
    )
    index = calls["cmd"].index("--idempotency-key")
    assert calls["cmd"][index + 1] == "wake:task-42:1:1"


def test_resume_steered_owner_preserves_custom_message(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 1, "", "not live")

    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.remote_dispatch, "local_machine", lambda: "host")
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)

    assert (
        bridge.resume_steered_owner(
            "host/worktree-1",
            "task-42",
            "Continue with the operator's choice.",
            owner_session_id="session-42",
        )
        is False
    )
    assert calls["cmd"][-3:] == ["worktree-1", "--prompt-file", "-"]
    assert calls["kwargs"]["input"] == "Continue with the operator's choice."


def test_resume_steered_owner_requires_captured_session(monkeypatch):
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    assert bridge.resume_steered_owner("host/worktree-1", "task-42") is False


def test_resume_steered_owner_routes_remote_machine_over_ssh(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge.remote_dispatch, "local_machine", lambda: "coordinator"
    )
    monkeypatch.setattr(
        bridge.shutil,
        "which",
        lambda command: "/usr/bin/ssh" if command == "ssh" else None,
    )
    monkeypatch.setattr(
        bridge.bridge_remote.LocalBridgeRemoteClient,
        "send_live_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bridge.bridge_remote.RemoteBridgeUnavailable("not installed")
        ),
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", fake_run)

    assert bridge.resume_steered_owner(
        "  Worker-Host  /worktree-1",
        "task-42",
        "resume now",
        owner_session_id="session-42",
        idempotency_key="wake:task-42:1:2",
    )

    assert calls["cmd"] == [
        "/usr/bin/ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=3",
        "worker-host",
        (
            "agent-bridge send --no-wait --queue --kind prompt --sender "
            "agent-dispatch-steer --idempotency-key wake:task-42:1:2 "
            "--expected-session-id session-42 worktree-1 --prompt-file -"
        ),
    ]
    assert calls["kwargs"]["input"] == "resume now"


def test_resume_steered_owner_uses_carrier_without_ssh(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge.remote_dispatch, "local_machine", lambda: "coordinator"
    )

    def send(_self, host, target, **kwargs):
        calls.update(host=host, target=target, **kwargs)
        return {"message_id": "message-1"}

    monkeypatch.setattr(
        bridge.bridge_remote.LocalBridgeRemoteClient,
        "send_live_message",
        send,
    )
    monkeypatch.setattr(
        bridge.shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed send must not resolve ssh"),
    )

    assert bridge.resume_steered_owner(
        "  Worker-Host  /worktree-1",
        "task-42",
        "resume now",
        owner_session_id="session-42",
        idempotency_key="wake:task-42:1:2",
    )
    assert calls == {
        "host": "worker-host",
        "target": "worktree-1",
        "sender": "agent-dispatch-steer",
        "message": "resume now",
        "kind": "prompt",
        "expected_session_id": "session-42",
        "idempotency_key": "wake:task-42:1:2",
        "timeout": 20.0,
    }


def test_resume_steered_owner_does_not_fallback_after_carrier_error(monkeypatch):
    monkeypatch.setattr(
        bridge.remote_dispatch, "local_machine", lambda: "coordinator"
    )
    monkeypatch.setattr(
        bridge.bridge_remote.LocalBridgeRemoteClient,
        "send_live_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bridge.bridge_remote.RemoteBridgeOperationError("remote rejected")
        ),
    )
    monkeypatch.setattr(
        bridge.shutil,
        "which",
        lambda _name: pytest.fail("carrier operation errors must not fall back"),
    )

    assert not bridge.resume_steered_owner(
        "Worker-Host/worktree-1",
        "task-42",
        owner_session_id="session-42",
    )


# -- registered-agent preflight ----------------------------------------------

_AGENTS_JSON = (
    '[{"name": "general-loop-worker"}, {"name": "sweep-worker"}, '
    '{"name": "document-intake-processor"}]'
)


def test_parse_agent_names_extracts_names():
    names = bridge.parse_agent_names(_AGENTS_JSON)
    assert names == {"general-loop-worker", "sweep-worker", "document-intake-processor"}


def test_parse_agent_names_skips_human_preamble():
    out = "Loading agents...\n" + _AGENTS_JSON
    assert bridge.parse_agent_names(out) == {
        "general-loop-worker", "sweep-worker", "document-intake-processor"
    }


def test_parse_agent_names_indeterminate_on_junk():
    # Empty / unparseable / wrong-shape all mean "couldn't tell" (None), never {}.
    assert bridge.parse_agent_names("") is None
    assert bridge.parse_agent_names(None) is None
    assert bridge.parse_agent_names("not json at all") is None
    assert bridge.parse_agent_names('{"name": "x"}') is None  # object, not a list


def test_registered_agent_names_none_when_no_bridge(monkeypatch):
    monkeypatch.setattr(bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: None)
    assert bridge.registered_agent_names() is None


def test_registered_agent_names_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    assert bridge.registered_agent_names() is None


def test_registered_agent_names_parses_list(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, _AGENTS_JSON, "")

    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.registered_agent_names() == {
        "general-loop-worker", "sweep-worker", "document-intake-processor"
    }
    # --json is a global flag before the `agents` subcommand.
    assert seen["cmd"] == ["/usr/bin/agent-bridge", "--json", "agents"]


def test_registered_agents_skips_human_preamble(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, "Loading agents...\n" + _AGENTS_JSON, ""
        ),
    )

    assert bridge.registered_agents() == [
        {"name": "general-loop-worker"},
        {"name": "sweep-worker"},
        {"name": "document-intake-processor"},
    ]


def test_registered_agent_project_reads_explicit_project(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry,
        "registered_agent",
        lambda name, **_kw: (
            {"name": "reviewer", "project": "review-harness"}
            if name == "reviewer"
            else bridge._AGENT_NOT_FOUND
        ),
    )

    assert bridge.registered_agent_project("reviewer") == "review-harness"
    assert bridge.registered_agent_project("missing") is None


def test_registered_agent_project_strictly_rejects_indeterminate_registry(
    monkeypatch,
):
    monkeypatch.setattr(bridge_agent_registry, "registered_agent", lambda name, **_kw: None)

    with pytest.raises(bridge.BridgeUnavailable, match="local agent registry"):
        bridge.registered_agent_project("reviewer", strict=True)


def test_registered_agent_project_strict_does_not_raise_on_confirmed_absence(
    monkeypatch,
):
    # A legitimately-absent agent (registry read fine, agent just isn't
    # registered) must NOT raise even under strict=True -- only a truly
    # indeterminate read (None) does. Conflating "confirmed absent" with
    # "couldn't tell" would be a real regression here.
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agent", lambda name, **_kw: bridge._AGENT_NOT_FOUND
    )
    assert bridge.registered_agent_project("missing", strict=True) is None


def test_registered_agent_uses_fast_single_lookup_command(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"name": "reviewer", "project": "review-harness"}), ""
        )

    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.registered_agent("reviewer") == {
        "name": "reviewer", "project": "review-harness"
    }
    # The whole point: a single named agent never triggers the full listing
    # (which would enumerate namespace/CodeSpace/container resolvers).
    assert seen["cmd"] == ["/usr/bin/agent-bridge", "--json", "agent-show", "reviewer"]


def test_registered_agent_not_found_is_distinct_from_indeterminate(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    # agent-show exits 1 for a confirmed-absent agent (see agent-bridge's
    # _cmd_agent_show) -- distinct from a crash/timeout/absent-bridge (None).
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", ""),
    )
    assert bridge.registered_agent("no-such-agent") is bridge._AGENT_NOT_FOUND

    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "crashed"),
    )
    assert bridge.registered_agent("whatever") is None


def test_registered_agent_unsupported_subcommand_is_distinct(monkeypatch):
    # An older, independently-updated agent-bridge (version skew) rejects
    # 'agent-show' with argparse's usual exit 2 -- must be distinguishable
    # from every other failure so callers can fall back to the full listing
    # instead of treating every local allocation as unreadable.
    monkeypatch.setattr(
        bridge_agent_registry, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 2, "",
            "agent-bridge: error: argument command: invalid choice: 'agent-show'",
        ),
    )
    assert bridge.registered_agent("reviewer") is bridge._AGENT_SHOW_UNSUPPORTED


def test_resolve_agent_record_falls_back_when_agent_show_unsupported(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agent", lambda name, **_kw: bridge._AGENT_SHOW_UNSUPPORTED
    )
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agents",
        lambda **_kw: [{"name": "reviewer", "project": "review-harness"}],
    )
    assert bridge._resolve_agent_record("reviewer", timeout=8.0) == {
        "name": "reviewer", "project": "review-harness"
    }
    assert bridge.registered_agent_project("reviewer") == "review-harness"


def test_resolve_agent_record_never_calls_fast_path_for_namespaced_name(monkeypatch):
    # 'codespace:foo' can never be answered by agent-show (it deliberately
    # never enumerates namespace resolvers) -- must go straight to the full
    # listing, not even attempt the fast path first.
    called = []
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agent",
        lambda name, **_kw: called.append(name) or bridge._AGENT_NOT_FOUND,
    )
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agents",
        lambda **_kw: [{"name": "codespace:foo", "project": "some-repo"}],
    )
    assert bridge._resolve_agent_record("codespace:foo", timeout=8.0) == {
        "name": "codespace:foo", "project": "some-repo"
    }
    assert called == []


def test_agent_is_registered_tri_state(monkeypatch):
    monkeypatch.setattr(bridge_agent_registry, "registered_agent", lambda name, **_kw: {"name": name})
    assert bridge.agent_is_registered("x") is True

    monkeypatch.setattr(
        bridge_agent_registry, "registered_agent", lambda name, **_kw: bridge._AGENT_NOT_FOUND
    )
    assert bridge.agent_is_registered("x") is False

    monkeypatch.setattr(bridge_agent_registry, "registered_agent", lambda name, **_kw: None)
    assert bridge.agent_is_registered("x") is None


def test_preflight_local_warns_when_agent_absent(monkeypatch):
    monkeypatch.setattr(bridge_agent_registry, "_resolve_agent_record", lambda name, **_kw: bridge._AGENT_NOT_FOUND)
    warnings = bridge.preflight_headless_agent("task-worker")
    assert len(warnings) == 1
    w = warnings[0]
    assert "task-worker" in w and "not registered" in w and "this host" in w


def test_preflight_local_silent_when_agent_present(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry, "_resolve_agent_record", lambda name, **_kw: {"name": name, "managed": False}
    )
    assert bridge.preflight_headless_agent("sweep-worker") == []


def test_preflight_local_silent_when_indeterminate(monkeypatch):
    # None registry (couldn't check) must never produce a false warning.
    monkeypatch.setattr(bridge_agent_registry, "_resolve_agent_record", lambda name, **_kw: None)
    assert bridge.preflight_headless_agent("task-worker") == []


def test_preflight_local_warns_when_agent_is_managed(monkeypatch):
    monkeypatch.setattr(
        bridge_agent_registry,
        "_resolve_agent_record",
        lambda name, **_kw: {"name": name, "managed": True},
    )
    warnings = bridge.preflight_headless_agent("task-worker")
    assert len(warnings) == 1
    assert "managed" in warnings[0]


def test_preflight_local_uses_full_listing_fallback_for_namespaced_agent(monkeypatch):
    # A user-configured --headless-agent can legitimately be namespace-
    # prefixed (codespace:, container:, admin:); agent_is_registered's own
    # fallback (see _resolve_agent_record) must make this correctly silent,
    # not a false "not registered" warning from the fast path's inherent
    # inability to resolve a namespace-prefixed name.
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agent",
        lambda name, **_kw: pytest.fail("must not attempt agent-show for a namespaced name"),
    )
    monkeypatch.setattr(
        bridge_agent_registry, "registered_agents",
        lambda **_kw: [{"name": "codespace:my-cs"}],
    )
    assert bridge.preflight_headless_agent("codespace:my-cs") == []


def test_preflight_fleet_probes_each_pool_host(monkeypatch):
    from agent_dispatch import embody

    probed = []

    def fake_remote(host, **_kw):
        probed.append(host)
        # present on the first host, absent on the second, indeterminate on third
        return {
            "pool-a": {"name": "sweep-worker", "managed": False},
            "pool-b": bridge._AGENT_NOT_FOUND,
            "pool-c": None,
        }[host]

    monkeypatch.setattr(embody, "remote_registered_agent_record", fake_remote)
    warnings = bridge.preflight_headless_agent(
        "sweep-worker", pool=["pool-a", "pool-b", "pool-c"]
    )
    assert probed == ["pool-a", "pool-b", "pool-c"]
    # Only pool-b (present-but-absent-agent) warns; pool-a present, pool-c unknown.
    assert len(warnings) == 1
    assert "pool-b" in warnings[0]
