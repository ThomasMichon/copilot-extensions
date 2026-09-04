"""Tests for agent-bridge integration (spawn worker) and claim-by-id."""

from __future__ import annotations

import os
import subprocess

import pytest

from agent_dispatch import bridge, procutil
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


def test_stop_worker_preserves_session(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.stop_worker("session-1") is True
    assert calls["cmd"] == ["/usr/bin/agent-bridge", "stop", "session-1"]


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

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bridge, "run_ssh_command", fake_run)

    assert bridge.resume_steered_owner(
        "Worker-Host/worktree-1",
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
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: None)
    assert bridge.registered_agent_names() is None


def test_registered_agent_names_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
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
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )
    monkeypatch.setattr(bridge.subprocess, "run", fake_run)
    assert bridge.registered_agent_names() == {
        "general-loop-worker", "sweep-worker", "document-intake-processor"
    }
    # --json is a global flag before the `agents` subcommand.
    assert seen["cmd"] == ["/usr/bin/agent-bridge", "--json", "agents"]


def test_registered_agents_skips_human_preamble(monkeypatch):
    monkeypatch.setattr(
        bridge, "_agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
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
        bridge,
        "registered_agents",
        lambda **_kw: [
            {"name": "reviewer", "project": "review-harness"},
            {"name": "other", "project": "other-harness"},
        ],
    )

    assert bridge.registered_agent_project("reviewer") == "review-harness"
    assert bridge.registered_agent_project("missing") is None


def test_preflight_local_warns_when_agent_absent(monkeypatch):
    monkeypatch.setattr(
        bridge, "registered_agent_names", lambda **_kw: {"general-loop-worker"}
    )
    warnings = bridge.preflight_headless_agent("task-worker")
    assert len(warnings) == 1
    w = warnings[0]
    assert "task-worker" in w and "not registered" in w and "this host" in w


def test_preflight_local_silent_when_agent_present(monkeypatch):
    monkeypatch.setattr(
        bridge, "registered_agent_names", lambda **_kw: {"sweep-worker"}
    )
    assert bridge.preflight_headless_agent("sweep-worker") == []


def test_preflight_local_silent_when_indeterminate(monkeypatch):
    # None registry (couldn't check) must never produce a false warning.
    monkeypatch.setattr(bridge, "registered_agent_names", lambda **_kw: None)
    assert bridge.preflight_headless_agent("task-worker") == []


def test_preflight_fleet_probes_each_pool_host(monkeypatch):
    from agent_dispatch import embody

    probed = []

    def fake_remote(host, **_kw):
        probed.append(host)
        # present on the first host, absent on the second, indeterminate on third
        return {
            "pool-a": {"sweep-worker"},
            "pool-b": {"other"},
            "pool-c": None,
        }[host]

    monkeypatch.setattr(embody, "remote_registered_agent_names", fake_remote)
    warnings = bridge.preflight_headless_agent(
        "sweep-worker", pool=["pool-a", "pool-b", "pool-c"]
    )
    assert probed == ["pool-a", "pool-b", "pool-c"]
    # Only pool-b (present-but-absent-agent) warns; pool-a present, pool-c unknown.
    assert len(warnings) == 1
    assert "pool-b" in warnings[0]
