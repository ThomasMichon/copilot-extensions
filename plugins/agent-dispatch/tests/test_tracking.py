"""Tests for best-effort embodiment tracking (CLI-session status -> tracking)."""

from __future__ import annotations

import json
import types

import pytest

from agent_dispatch import tracking


def test_worktree_from_owner_parses_machine_slash_worktree():
    assert tracking.worktree_from_owner("anomalous-potato/wt-abc") == "wt-abc"


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


def test_enrich_local_body_tasks_joins_spawned_reservation(monkeypatch):
    monkeypatch.setattr(
        tracking,
        "list_local_body_sessions",
        lambda: [
            {
                "session_id": "brg-1",
                "worktree_id": "wt-headless",
                "status": "running",
                "liveness": "active",
            }
        ],
    )
    tasks = [
        {"id": "t1", "status": "started"},
        {"id": "t2", "status": "queued"},
    ]
    reservations = [
        {
            "task_id": "t1",
            "state": "spawned",
            "session_handle": "local-body:brg-1",
        }
    ]
    out = tracking.enrich_local_body_tasks(tasks, reservations)
    assert out[0]["embodiment"]["session_id"] == "brg-1"
    assert out[0]["embodiment"]["liveness"] == "active"
    assert "embodiment" not in out[1]


def test_list_local_body_sessions_degrades_when_passive_probe_detaches(monkeypatch):
    monkeypatch.setattr(
        tracking, "agent_bridge_launch_prefix", lambda: ["python", "-m", "agent_bridge"]
    )
    monkeypatch.setattr(tracking, "_run_capture", lambda *_a, **_k: None)
    assert tracking.list_local_body_sessions() == []


def test_enrich_local_body_tasks_ignores_nonlocal_handles(monkeypatch):
    monkeypatch.setattr(
        tracking,
        "list_local_body_sessions",
        lambda: pytest.fail("bridge sessions should not be listed"),
    )
    tasks = [{"id": "t1", "status": "started"}]
    reservations = [{
        "task_id": "t1",
        "state": "spawned",
        "session_handle": "fleet-body:peer:brg-1",
    }]
    assert tracking.enrich_local_body_tasks(tasks, reservations) is tasks


def test_resolve_live_session_shells_bridge_json_resolve(monkeypatch):
    captured = {}

    def fake_run(cmd, *, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"session_id": "s9", "worktree_id": "wt-x"}),
            stderr="",
        )

    monkeypatch.setattr(
        tracking, "agent_bridge_launch_prefix",
        lambda: [r"C:\runtime\python.exe", "-m", "agent_bridge"],
    )
    monkeypatch.setattr(tracking, "run_background_capture", fake_run)

    got = tracking.resolve_live_session("wt-x")
    assert got == {"session_id": "s9", "worktree_id": "wt-x"}
    cmd = captured["cmd"]
    assert cmd[:3] == [r"C:\runtime\python.exe", "-m", "agent_bridge"]
    assert not any(arg.lower().endswith((".cmd", ".bat")) for arg in cmd)
    assert "--json" in cmd
    assert cmd[cmd.index("--handle") + 1] == "wt-x"
    assert "live-sessions" in cmd and "resolve" in cmd
    assert captured["timeout"] == 3.0


def test_resolve_live_session_none_without_cli(monkeypatch):
    monkeypatch.setattr(tracking, "agent_bridge_launch_prefix", lambda: None)
    assert tracking.resolve_live_session("wt-x") is None


def test_resolve_live_session_degrades_on_failures(monkeypatch):
    monkeypatch.setattr(
        tracking, "agent_bridge_launch_prefix", lambda: ["/usr/bin/agent-bridge"]
    )

    # non-zero exit
    monkeypatch.setattr(
        tracking, "run_background_capture",
        lambda cmd, **kw: types.SimpleNamespace(returncode=1, stdout="", stderr="x"),
    )
    assert tracking.resolve_live_session("wt-x") is None

    # invalid JSON
    monkeypatch.setattr(
        tracking, "run_background_capture",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="not json", stderr=""),
    )
    assert tracking.resolve_live_session("wt-x") is None

    # empty object
    monkeypatch.setattr(
        tracking, "run_background_capture",
        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="{}", stderr=""),
    )
    assert tracking.resolve_live_session("wt-x") is None


def test_enrich_task_adds_overlay_for_leased_task(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    monkeypatch.setattr(
        tracking, "resolve_live_session",
        lambda wt, **kw: {"session_id": "s1", "worktree_id": wt, "driven_by": "agent-dispatch"},
    )
    task = {"id": "t1", "status": "started", "owner": "anomalous-potato/wt-abc"}
    out = tracking.enrich_task(task)
    assert out["embodiment"] == {
        "session_id": "s1", "worktree_id": "wt-abc", "driven_by": "agent-dispatch",
    }
    # original is not mutated
    assert "embodiment" not in task


def test_enrich_task_skips_unleased_and_ownerless(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    monkeypatch.setattr(tracking, "resolve_live_session", lambda wt, **kw: {"session_id": "s"})

    queued = {"id": "t", "status": "queued", "owner": "anomalous-potato/wt-abc"}
    assert tracking.enrich_task(queued) is queued

    ownerless = {"id": "t", "status": "started", "owner": None}
    assert tracking.enrich_task(ownerless) is ownerless


def test_enrich_task_degrades_when_bridge_absent(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: False)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    task = {"id": "t1", "status": "started", "owner": "anomalous-potato/wt-abc"}
    assert tracking.enrich_task(task) is task


def test_enrich_task_no_overlay_when_no_live_session(monkeypatch):
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    monkeypatch.setattr(tracking, "resolve_live_session", lambda wt, **kw: None)
    task = {"id": "t1", "status": "started", "owner": "anomalous-potato/wt-abc"}
    assert tracking.enrich_task(task) is task


# -- Cross-machine embodiment tracking (Phase 8 Slice 8b) ---------------------


def test_machine_from_owner_parses_and_handles_malformed():
    assert tracking.machine_from_owner("emancipation-cube/wt-1") == "emancipation-cube"
    assert tracking.machine_from_owner(None) is None
    assert tracking.machine_from_owner("") is None
    assert tracking.machine_from_owner("no-slash") is None


def test_remote_resolve_argv_shells_ssh_to_the_owner_machine(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/ssh")
    argv = tracking._bridge_resolve_argv("wt-x", machine="emancipation-cube")
    assert argv is not None
    assert argv[0] == "/usr/bin/ssh"
    assert "emancipation-cube" in argv
    assert "BatchMode=yes" in argv
    # The remote command carries the same agent-bridge resolve, quoted.
    remote_cmd = argv[-1]
    assert remote_cmd.startswith("agent-bridge --json live-sessions resolve --handle")
    assert "wt-x" in remote_cmd


def test_remote_resolve_argv_none_without_ssh(monkeypatch):
    monkeypatch.setattr(tracking.shutil, "which", lambda _n: None)
    assert tracking._bridge_resolve_argv("wt-x", machine="emancipation-cube") is None


def test_resolve_live_session_runs_over_ssh_for_remote_owner(monkeypatch):
    captured = {}

    monkeypatch.setattr(tracking.shutil, "which", lambda _n: "/usr/bin/ssh")
    monkeypatch.setattr(
        tracking.bridge_remote.LocalBridgeRemoteClient,
        "resolve_live_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            tracking.bridge_remote.RemoteBridgeUnavailable("not installed")
        ),
    )

    def fake_run(cmd, *, timeout):
        captured["cmd"] = cmd
        captured["timeout"] = timeout
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"session_id": "s-remote", "worktree_id": "wt-x"}),
            stderr="",
        )

    monkeypatch.setattr(tracking, "run_ssh_capture", fake_run)

    got = tracking.resolve_live_session("wt-x", machine="emancipation-cube")
    assert got == {"session_id": "s-remote", "worktree_id": "wt-x"}
    assert captured["cmd"][0] == "/usr/bin/ssh"
    assert "emancipation-cube" in captured["cmd"]
    assert captured["timeout"] == 6.0


def test_resolve_live_session_uses_carrier_without_ssh(monkeypatch):
    calls = {}

    def resolve(_self, host, target, **kwargs):
        calls.update(host=host, target=target, **kwargs)
        return {"session_id": "s-remote", "worktree_id": target}

    monkeypatch.setattr(
        tracking.bridge_remote.LocalBridgeRemoteClient,
        "resolve_live_session",
        resolve,
    )
    monkeypatch.setattr(
        tracking.shutil,
        "which",
        lambda _name: pytest.fail("carrier-backed resolve must not use ssh"),
    )

    got = tracking.resolve_live_session(
        "wt-x", machine="emancipation-cube"
    )
    assert got == {"session_id": "s-remote", "worktree_id": "wt-x"}
    assert calls == {
        "host": "emancipation-cube",
        "target": "wt-x",
        "timeout": 6.0,
    }


def test_enrich_task_resolves_remote_owner_over_mesh(monkeypatch):
    # Owner is on emancipation-cube; the local machine is anomalous-potato -> remote path.
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
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

    task = {"id": "t1", "status": "started", "owner": "emancipation-cube/wt-x"}
    out = tracking.enrich_task(task)
    assert seen["machine"] == "emancipation-cube"
    assert out["embodiment"]["turn_state"] == "running"


def test_enrich_task_remote_uses_carrier_without_ssh(monkeypatch):
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", lambda: False)
    monkeypatch.setattr(
        tracking.bridge_remote.LocalBridgeRemoteClient,
        "resolve_live_session",
        lambda _self, _host, target, **_kwargs: {
            "session_id": "s-remote",
            "worktree_id": target,
        },
    )
    task = {"id": "t1", "status": "started", "owner": "emancipation-cube/wt-x"}
    assert tracking.enrich_task(task)["embodiment"]["session_id"] == "s-remote"


def test_enrich_task_remote_degrades_when_carrier_and_ssh_absent(monkeypatch):
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "anomalous-potato")
    monkeypatch.setattr(tracking.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        tracking.bridge_remote.LocalBridgeRemoteClient,
        "resolve_live_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            tracking.bridge_remote.RemoteBridgeUnavailable("not installed")
        ),
    )
    task = {"id": "t1", "status": "started", "owner": "emancipation-cube/wt-x"}
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
    task = {"id": "t1", "status": "started", "owner": "emancipation-cube/wt-x"}
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
        return "anomalous-potato"

    monkeypatch.setattr(tracking, "bridge_available", fake_bridge)
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", fake_ssh)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", fake_local)

    resolved = []

    def fake_resolve(wt, *, machine=None, **kw):
        resolved.append((wt, machine))
        return {"session_id": f"s-{wt}", "worktree_id": wt}

    monkeypatch.setattr(tracking, "resolve_live_session", fake_resolve)

    tasks = [
        {"id": "a", "status": "started", "owner": "anomalous-potato/wt-local"},
        {"id": "b", "status": "claimed", "owner": "emancipation-cube/wt-remote"},
    ]
    out = tracking.enrich_tasks(tasks)

    assert probes == {"bridge": 1, "ssh": 1, "local": 1}
    assert ("wt-local", None) in resolved  # local owner -> local bridge
    assert ("wt-remote", "emancipation-cube") in resolved  # remote owner -> mesh
    assert out[0]["embodiment"]["session_id"] == "s-wt-local"
    assert out[1]["embodiment"]["session_id"] == "s-wt-remote"


def test_enrich_budget_env_override_and_fallback(monkeypatch):
    """`_enrich_budget` honors a positive env override, else the default."""
    monkeypatch.delenv("AGENT_DISPATCH_ENRICH_BUDGET_S", raising=False)
    assert tracking._enrich_budget() == tracking._ENRICH_BUDGET_S
    monkeypatch.setenv("AGENT_DISPATCH_ENRICH_BUDGET_S", "1.5")
    assert tracking._enrich_budget() == 1.5
    # non-numeric / non-positive -> fall back to the default (never 0/negative)
    monkeypatch.setenv("AGENT_DISPATCH_ENRICH_BUDGET_S", "not-a-number")
    assert tracking._enrich_budget() == tracking._ENRICH_BUDGET_S
    monkeypatch.setenv("AGENT_DISPATCH_ENRICH_BUDGET_S", "-3")
    assert tracking._enrich_budget() == tracking._ENRICH_BUDGET_S


def test_enrich_tasks_stops_probing_over_budget(monkeypatch):
    """Once the total budget is spent, remaining leased tasks are returned
    UNENRICHED instead of probed -- so one slow/hanging resolve cannot wedge the
    whole `list` (the #1704 list-hang fix)."""
    monkeypatch.setenv("AGENT_DISPATCH_ENRICH_BUDGET_S", "5")
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "m")

    calls = {"n": 0}

    def counting_resolve(wt, **kw):
        calls["n"] += 1
        return {"session_id": f"s-{wt}"}

    monkeypatch.setattr(tracking, "resolve_live_session", counting_resolve)

    # Deterministic fake clock: each monotonic() call advances 3s, so the deadline
    # (set at ~3s + 5s budget = 8s) is crossed after the first task's probe.
    clock = {"t": 0.0}

    def fake_monotonic():
        clock["t"] += 3.0
        return clock["t"]

    monkeypatch.setattr(tracking.time, "monotonic", fake_monotonic)

    tasks = [
        {"id": f"t{i}", "status": "started", "owner": f"m/wt-{i}"} for i in range(8)
    ]
    out = tracking.enrich_tasks(tasks)

    # Bounded: not every leased task was probed.
    assert calls["n"] < 8
    # All tasks are still returned, and the over-budget ones are unenriched.
    assert len(out) == 8
    assert any("embodiment" not in t for t in out)


def test_enrich_tasks_within_budget_enriches_all(monkeypatch):
    """A generous budget with fast resolves still enriches every leased task, and
    non-leased tasks pass through untouched."""
    monkeypatch.setenv("AGENT_DISPATCH_ENRICH_BUDGET_S", "60")
    monkeypatch.setattr(tracking, "bridge_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "ssh_available", lambda: True)
    monkeypatch.setattr(tracking.remote_dispatch, "local_machine", lambda: "m")
    monkeypatch.setattr(
        tracking, "resolve_live_session", lambda wt, **kw: {"session_id": f"s-{wt}"}
    )
    tasks = [
        {"id": f"t{i}", "status": "started", "owner": f"m/wt-{i}"} for i in range(4)
    ]
    out = tracking.enrich_tasks(tasks)
    assert all("embodiment" in t for t in out)
    passthrough = tracking.enrich_tasks([{"id": "q", "status": "queued"}])
    assert "embodiment" not in passthrough[0]


def test_session_activity_reports_explicit_idle():
    assert tracking.session_activity(
        {"status": "running", "liveness": "idle", "turn_state": "idle"}
    ) == "IDLE"


def test_session_activity_reports_idle_status_without_liveness_fields():
    assert tracking.session_activity({"status": "idle"}) == "IDLE"
