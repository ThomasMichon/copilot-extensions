"""Tests for best-effort embodiment tracking (CLI-session status -> tracking)."""

from __future__ import annotations

import json
import types

from agent_dispatch import tracking


def test_worktree_from_owner_parses_machine_slash_worktree():
    assert tracking.worktree_from_owner("lambda-core/wt-abc") == "wt-abc"


def test_worktree_from_owner_handles_missing_and_malformed():
    assert tracking.worktree_from_owner(None) is None
    assert tracking.worktree_from_owner("") is None
    assert tracking.worktree_from_owner("no-slash") is None


def test_embodiment_overlay_keeps_only_present_keys():
    session = {
        "session_id": "s1",
        "worktree_id": "wt-abc",
        "driven_by": "agent-dispatch",
        "status": "live",
        "updated_at": 123.0,
        "cwd": "/x",  # dropped -- not an overlay key
    }
    overlay = tracking.embodiment_overlay(session)
    assert overlay == {
        "session_id": "s1",
        "worktree_id": "wt-abc",
        "driven_by": "agent-dispatch",
        "status": "live",
        "updated_at": 123.0,
    }


def test_embodiment_overlay_none_for_empty():
    assert tracking.embodiment_overlay(None) is None
    assert tracking.embodiment_overlay({}) is None


def test_resolve_live_session_shells_bridge_json_resolve(monkeypatch):
    captured = {}

    def fake_which(_name):
        return "/usr/bin/agent-bridge"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"session_id": "s9", "worktree_id": "wt-x"}),
            stderr="",
        )

    monkeypatch.setattr(tracking.shutil, "which", fake_which)
    monkeypatch.setattr(tracking.subprocess, "run", fake_run)

    got = tracking.resolve_live_session("wt-x")
    assert got == {"session_id": "s9", "worktree_id": "wt-x"}
    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/agent-bridge"
    assert "--json" in cmd
    assert cmd[cmd.index("--handle") + 1] == "wt-x"
    assert "live-sessions" in cmd and "resolve" in cmd


def test_resolve_live_session_none_without_cli(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: None)
    assert tracking.resolve_live_session("wt-x") is None


def test_resolve_live_session_degrades_on_failures(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/agent-bridge")

    # non-zero exit
    monkeypatch.setattr(
        tracking.subprocess, "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="x"),
    )
    assert tracking.resolve_live_session("wt-x") is None

    # invalid JSON
    monkeypatch.setattr(
        tracking.subprocess, "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )
    assert tracking.resolve_live_session("wt-x") is None

    # empty object
    monkeypatch.setattr(
        tracking.subprocess, "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    assert tracking.resolve_live_session("wt-x") is None


def test_enrich_task_adds_overlay_for_leased_task(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    monkeypatch.setattr(
        tracking, "resolve_live_session",
        lambda wt, **kw: {"session_id": "s1", "worktree_id": wt, "driven_by": "agent-dispatch"},
    )
    task = {"id": "t1", "status": "started", "owner": "lambda-core/wt-abc"}
    out = tracking.enrich_task(task)
    assert out["embodiment"] == {
        "session_id": "s1", "worktree_id": "wt-abc", "driven_by": "agent-dispatch",
    }
    # original is not mutated
    assert "embodiment" not in task


def test_enrich_task_skips_unleased_and_ownerless(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    monkeypatch.setattr(tracking, "resolve_live_session", lambda wt, **kw: {"session_id": "s"})

    queued = {"id": "t", "status": "queued", "owner": "lambda-core/wt-abc"}
    assert tracking.enrich_task(queued) is queued

    ownerless = {"id": "t", "status": "started", "owner": None}
    assert tracking.enrich_task(ownerless) is ownerless


def test_enrich_task_degrades_when_bridge_absent(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: False)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    task = {"id": "t1", "status": "started", "owner": "lambda-core/wt-abc"}
    assert tracking.enrich_task(task) is task


def test_enrich_task_no_overlay_when_no_live_session(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    monkeypatch.setattr(tracking, "resolve_live_session", lambda wt, **kw: None)
    task = {"id": "t1", "status": "started", "owner": "lambda-core/wt-abc"}
    assert tracking.enrich_task(task) is task


# -- Cross-machine embodiment tracking (Phase 8 Slice 8b) ---------------------


def test_machine_from_owner_parses_and_handles_malformed():
    assert tracking.machine_from_owner("borealis/wt-1") == "borealis"
    assert tracking.machine_from_owner(None) is None
    assert tracking.machine_from_owner("") is None
    assert tracking.machine_from_owner("no-slash") is None


def test_remote_resolve_argv_shells_ssh_to_the_owner_machine(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/ssh")
    argv = tracking._bridge_resolve_argv("wt-x", machine="borealis")
    assert argv is not None
    assert argv[0] == "/usr/bin/ssh"
    assert "borealis" in argv
    assert "BatchMode=yes" in argv
    # The remote command carries the same agent-bridge resolve, quoted.
    remote_cmd = argv[-1]
    assert remote_cmd.startswith("agent-bridge --json live-sessions resolve --handle")
    assert "wt-x" in remote_cmd


def test_remote_resolve_argv_none_without_ssh(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: None)
    assert tracking._bridge_resolve_argv("wt-x", machine="borealis") is None


def test_resolve_live_session_runs_over_ssh_for_remote_owner(monkeypatch):
    captured = {}

    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/ssh")

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"session_id": "s-remote", "worktree_id": "wt-x"}),
            stderr="",
        )

    monkeypatch.setattr(tracking.subprocess, "run", fake_run)

    got = tracking.resolve_live_session("wt-x", machine="borealis")
    assert got == {"session_id": "s-remote", "worktree_id": "wt-x"}
    assert captured["cmd"][0] == "/usr/bin/ssh"
    assert "borealis" in captured["cmd"]


def test_enrich_task_resolves_remote_owner_over_mesh(monkeypatch):
    # Owner is on borealis; the local machine is lambda-core -> remote path.
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", lambda: True)
    # The local bridge must NOT be consulted for a remote owner.
    monkeypatch.setattr(
        tracking, "bridge_available",
        lambda: (_ for _ in ()).throw(AssertionError("local bridge used for remote owner")),
    )

    seen = {}

    def fake_resolve(wt, *, machine=None, **kw):
        seen["machine"] = machine
        return {"session_id": "s9", "worktree_id": wt, "turn_state": "running"}

    monkeypatch.setattr(tracking, "resolve_live_session", fake_resolve)

    task = {"id": "t1", "status": "started", "owner": "borealis/wt-x"}
    out = tracking.enrich_task(task)
    assert seen["machine"] == "borealis"
    assert out["embodiment"]["turn_state"] == "running"


def test_enrich_task_remote_degrades_without_ssh(monkeypatch):
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "lambda-core")
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", lambda: False)
    task = {"id": "t1", "status": "started", "owner": "borealis/wt-x"}
    assert tracking.enrich_task(task) is task


def test_enrich_task_unresolvable_local_treats_owner_as_local(monkeypatch):
    # When the local machine can't be resolved, an owner can't be proven remote,
    # so fall back to the local bridge path (unchanged pre-8b behavior).
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: None)
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(
        tracking, "resolve_live_session",
        lambda wt, *, machine=None, **kw: {"session_id": "s", "worktree_id": wt}
        if machine is None else None,
    )
    task = {"id": "t1", "status": "started", "owner": "borealis/wt-x"}
    out = tracking.enrich_task(task)
    assert out["embodiment"]["session_id"] == "s"


def test_enrich_tasks_skips_bridge_probe_when_none_leased(monkeypatch):
    called = {"which": 0}

    def fake_available():
        called["which"] += 1
        return True

    monkeypatch.setattr(tracking, "bridge_available", fake_available)
    tasks = [
        {"id": "a", "status": "queued", "owner": None},
        {"id": "b", "status": "completed", "owner": "m/wt"},
    ]
    out = tracking.enrich_tasks(tasks)
    assert out is tasks
    # No leased tasks -> never probes for the bridge.
    assert called["which"] == 0


def test_enrich_tasks_probes_once_for_a_batch(monkeypatch):
    called = {"which": 0}

    def fake_available():
        called["which"] += 1
        return True

    monkeypatch.setattr(tracking, "bridge_available", fake_available)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "m")
    monkeypatch.setattr(
        tracking, "resolve_live_session",
        lambda wt, **kw: {"session_id": f"s-{wt}", "worktree_id": wt},
    )
    tasks = [
        {"id": "a", "status": "started", "owner": "m/wt-a"},
        {"id": "b", "status": "claimed", "owner": "m/wt-b"},
        {"id": "c", "status": "queued", "owner": None},
    ]
    out = tracking.enrich_tasks(tasks)
    assert called["which"] == 1  # single probe hoisted for the batch
    assert out[0]["embodiment"]["session_id"] == "s-wt-a"
    assert out[1]["embodiment"]["session_id"] == "s-wt-b"
    assert "embodiment" not in out[2]


def test_enrich_tasks_hoists_probes_and_mixes_local_and_remote(monkeypatch):
    # A batch with a local-owner and a remote-owner task: each probe runs once,
    # and each task resolves against the correct machine (Phase 8 Slice 8b).
    probes = {"bridge": 0, "ssh": 0, "local": 0}

    def fake_bridge():
        probes["bridge"] += 1
        return True

    def fake_ssh():
        probes["ssh"] += 1
        return True

    def fake_local():
        probes["local"] += 1
        return "lambda-core"

    monkeypatch.setattr(tracking, "bridge_available", fake_bridge)
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", fake_ssh)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", fake_local)

    resolved = []

    def fake_resolve(wt, *, machine=None, **kw):
        resolved.append((wt, machine))
        return {"session_id": f"s-{wt}", "worktree_id": wt}

    monkeypatch.setattr(tracking, "resolve_live_session", fake_resolve)

    tasks = [
        {"id": "a", "status": "started", "owner": "lambda-core/wt-local"},
        {"id": "b", "status": "claimed", "owner": "borealis/wt-remote"},
    ]
    out = tracking.enrich_tasks(tasks)

    assert probes == {"bridge": 1, "ssh": 1, "local": 1}
    assert ("wt-local", None) in resolved  # local owner -> local bridge
    assert ("wt-remote", "borealis") in resolved  # remote owner -> mesh
    assert out[0]["embodiment"]["session_id"] == "s-wt-local"
    assert out[1]["embodiment"]["session_id"] == "s-wt-remote"
