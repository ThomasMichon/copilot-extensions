"""Standalone lifecycle planning stays with the existing reconciliation owner."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as main
from agent_worktrees import local_cache_refresh, reconcile, session_context
from agent_worktrees import session_inspection_cli, terminal_identity


@pytest.fixture
def standalone(monkeypatch, tmp_path):
    events = []
    plans = []
    children = []
    runtime = tmp_path / "runtime"
    original_spawn = main.subprocess.Popen
    original_detached = main.detached_kwargs
    environment = {}
    payload = {
        "sessionId": "synthetic-session",
        "workingDirectory": str(tmp_path),
        "_agentWorktrees": {"environment": environment},
    }
    monkeypatch.setattr(session_inspection_cli, "_read_hook_stdin", lambda: payload)
    monkeypatch.setattr(main.cfg, "active_project", lambda: "synthetic-project")
    monkeypatch.setattr(main.cfg, "set_active_project", lambda value: None)
    monkeypatch.setattr(main, "_start_project_session_hook", lambda *args: None)
    monkeypatch.setattr(main, "_finish_project_session_hook", lambda *args: ({}, ""))
    monkeypatch.setattr(terminal_identity, "record_session_terminal", lambda *args: None)
    monkeypatch.setattr(session_context, "publish_machine_context", lambda *args: "")
    monkeypatch.setattr(main, "_registration_nudge_context", lambda *args: "")
    monkeypatch.setattr(main, "_migrate_legacy_marketplace_overrides", lambda *args: None)
    monkeypatch.setattr(main, "_reconcile_knowledge_plugin_overlay", lambda *args: None)
    monkeypatch.setattr(local_cache_refresh, "sessionstart_diagnostic", lambda *a, **k: "")
    monkeypatch.setattr(main, "_anchor_hygiene_diagnostic", lambda *args: "")
    monkeypatch.setattr(main, "_aw_runtime_home", lambda: runtime)
    python = str(runtime / "versions" / "fixture" / "Scripts" / "pythonw.exe")
    monkeypatch.setattr(main, "_windowless_python", lambda: python)
    monkeypatch.setattr(main, "windowless_python_env", lambda executable: {"PYTHONUTF8": "1"})
    detach = {"creationflags": 123}

    def detached(**kwargs):
        assert kwargs == {"breakaway": True}
        return detach

    monkeypatch.setattr(main, "detached_kwargs", detached)
    monkeypatch.setattr(
        main, "_write_session_lifecycle_receipt",
        lambda payload, state: events.append(state),
    )
    monkeypatch.setattr(
        main, "_write_session_lifecycle_snapshot",
        lambda stage, payload, output: events.append(stage),
    )

    def register(args):
        events.append("registered")
        args.result_holder.append("{}")
        return 0

    def plan(*args, **kwargs):
        plans.append((args, kwargs))
        return {"action": "continue"}

    def spawn(argv, **kwargs):
        events.append("spawned")
        children.append((argv, kwargs))
        assert not kwargs["stdout"].closed
        assert not kwargs["stderr"].closed
        return SimpleNamespace(wait=lambda: pytest.fail("standalone must not join maintenance"))

    monkeypatch.setattr(main, "cmd_register_session", register)
    monkeypatch.setattr(reconcile, "build_plan", plan)
    monkeypatch.setattr(main.subprocess, "Popen", spawn)

    def run():
        result = session_inspection_cli.cmd_session_lifecycle(
            argparse.Namespace(stdin=True, timeout_seconds=2.0)
        )
        events.append("returned")
        return result

    return SimpleNamespace(
        run=run, events=events, plans=plans, children=children,
        runtime=runtime, python=python, repo=tmp_path, environment=environment, spawn=spawn,
        payload=payload,
        original_spawn=original_spawn, original_detached=original_detached,
    )


def test_standalone_provisioning_launches_one_noop_candidate_without_foreground_plan(
    standalone, capsys,
):
    assert standalone.run() == 0
    assert standalone.plans == []
    assert len(standalone.children) == 1
    argv, kwargs = standalone.children[0]
    assert argv[:2] == [standalone.python, "-B"]
    assert not (standalone.runtime / "cache" / "python").exists()
    assert argv[2:] == [
        "-m", "agent_worktrees", "reconcile-plugins",
        "--repo", str(standalone.repo),
        "--status", str(standalone.runtime / "logs" / "provision-status.json"),
        "--apply",
    ]
    assert kwargs["stdin"] == main.subprocess.DEVNULL
    assert kwargs["cwd"] == os.path.expanduser("~")
    assert kwargs["creationflags"] == 123
    assert kwargs["env"]["PYTHONUTF8"] == "1"
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    assert standalone.events.index("registered") < standalone.events.index("spawned")
    assert standalone.events.index("register-session") < standalone.events.index("spawned")
    assert standalone.events[-2:] == ["completed", "returned"]
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {}
    assert captured.err == "[agent-worktrees] Background reconciliation launched.\n"


@pytest.mark.parametrize("flag", ["WORKTREE_NO_RECONCILE", "WORKTREE_NO_PROVISION"])
def test_standalone_provisioning_opt_out_launches_nothing(standalone, capsys, flag):
    standalone.environment[flag] = "1"
    assert standalone.run() == 0
    assert standalone.plans == [] and standalone.children == []
    assert standalone.events[-2:] == ["completed", "returned"]
    assert capsys.readouterr().err == ""


def test_standalone_provisioning_preserves_previous_failure_status(standalone, capsys):
    status = standalone.runtime / "logs" / "provision-status.json"
    status.parent.mkdir(parents=True)
    previous = json.dumps({
        "ok": False, "repo": str(standalone.repo),
        "failed": [{"service": "synthetic-runtime"}],
    })
    status.write_text(previous, encoding="utf-8")
    assert standalone.run() == 0
    assert status.read_text(encoding="utf-8") == previous
    captured = capsys.readouterr()
    assert "Previous background provisioning failed: synthetic-runtime." in captured.err
    assert "Background reconciliation launched." in captured.err
    assert len(standalone.children) == 1 and standalone.plans == []


def test_standalone_provisioning_spawn_failure_closes_streams(
    standalone, monkeypatch, capsys,
):
    def failed(argv, **kwargs):
        standalone.spawn(argv, **kwargs)
        raise OSError("synthetic spawn refusal")

    monkeypatch.setattr(main.subprocess, "Popen", failed)
    assert standalone.run() == 0
    assert standalone.plans == [] and len(standalone.children) == 1
    _, kwargs = standalone.children[0]
    assert kwargs["stdout"].closed and kwargs["stderr"].closed
    assert standalone.events[-2:] == ["completed", "returned"]
    captured = capsys.readouterr()
    assert captured.err == "[agent-worktrees] Could not start background provisioning.\n"


def test_standalone_provisioning_default_preview_still_skips_noop(standalone):
    assert main._start_provisioning_if_needed(str(standalone.repo), {}) == ""
    assert len(standalone.plans) == 1 and standalone.children == []


def test_default_provisioning_preview_preserves_requested_services(
    standalone, monkeypatch,
):
    monkeypatch.setattr(reconcile, "build_plan", lambda *args, **kwargs: {
        "action": "reconcile",
        "updates": [
            {"service": "first"}, {"service": "first"}, None,
            {"service": "second"}, {},
        ],
    })
    assert main._start_provisioning_if_needed(str(standalone.repo), {}) == (
        "[agent-worktrees] Provisioning runtime(s) in background: first, second\n"
    )
    assert len(standalone.children) == 1


def test_default_provisioning_preview_failure_does_not_launch(
    standalone, monkeypatch,
):
    def failed(*args, **kwargs):
        raise ValueError("synthetic preview failure")

    monkeypatch.setattr(reconcile, "build_plan", failed)
    assert main._start_provisioning_if_needed(str(standalone.repo), {}) == (
        "[agent-worktrees] Runtime provisioning preview failed: synthetic preview failure\n"
    )
    assert standalone.children == []


def test_standalone_provisioning_expired_deadline_skips_optional_child(standalone):
    assert main._run_session_lifecycle(standalone.payload, deadline=0.0) == {}
    assert standalone.plans == [] and standalone.children == []
    assert "registered" in standalone.events
    assert standalone.events[-1] == "completed"


def test_standalone_optional_planning_cannot_consume_the_command_deadline(
    standalone, monkeypatch, capsys,
):
    clock = SimpleNamespace(now=100.0)
    timer = SimpleNamespace(time=lambda: clock.now)
    monkeypatch.setattr(session_inspection_cli, "time", timer)
    monkeypatch.setattr(main, "time", timer)

    def slow_optional_plan(*args, **kwargs):
        standalone.plans.append((args, kwargs))
        clock.now += 3.0
        return {"action": "continue"}

    monkeypatch.setattr(reconcile, "build_plan", slow_optional_plan)
    assert standalone.run() == 0
    assert clock.now - 100.0 < 2.0
    assert standalone.plans == []
    assert len(standalone.children) == 1
    assert standalone.events[-2:] == ["completed", "returned"]
    assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.portfolio_tier("T2")
def test_standalone_returns_while_the_existing_child_owns_optional_work(
    standalone, monkeypatch, tmp_path, capsys,
):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    monkeypatch.delenv("PYTHONPYCACHEPREFIX", raising=False)
    worker_root = tmp_path / "worker"
    package = worker_root / "agent_worktrees"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import pathlib, sys, time\n"
        "assert sys.argv[1:] == ['reconcile-plugins', '--repo', sys.argv[3], "
        "'--status', sys.argv[5], '--apply']\n"
        "root = pathlib.Path(sys.argv[5]).parent\n"
        "(root / 'worker-cache').write_text(__cached__, encoding='utf-8')\n"
        "(root / 'worker-started').write_text('planning', encoding='utf-8')\n"
        "deadline = time.monotonic() + 10\n"
        "while not (root / 'release-worker').exists():\n"
        "    if time.monotonic() >= deadline: raise SystemExit(9)\n"
        "    time.sleep(0.01)\n"
        "(root / 'worker-finished').write_text('complete', encoding='utf-8')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(worker_root))
    monkeypatch.setattr(main, "_windowless_python", lambda: sys.executable)
    monkeypatch.setattr(main, "detached_kwargs", standalone.original_detached)
    children = []

    def spawn(argv, **kwargs):
        child = standalone.original_spawn(argv, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(main.subprocess, "Popen", spawn)
    logs = standalone.runtime / "logs"
    try:
        assert standalone.run() == 0
        assert standalone.plans == []
        assert len(children) == 1
        deadline = time.monotonic() + 10
        while not (logs / "worker-started").exists():
            assert children[0].poll() is None
            assert time.monotonic() < deadline
            time.sleep(0.01)
        cached = Path((logs / "worker-cache").read_text(encoding="utf-8"))
        assert not cached.exists()
        assert not list(worker_root.rglob("*.pyc"))
        assert not (standalone.runtime / "cache" / "python").exists()
        assert not (logs / "worker-finished").exists()
        assert standalone.events[-2:] == ["completed", "returned"]
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {}
        assert captured.err == "[agent-worktrees] Background reconciliation launched.\n"
    finally:
        if children:
            (logs / "release-worker").write_text("release", encoding="utf-8")
            for child in children:
                child.wait(timeout=10)
    assert children[0].returncode == 0
    assert (logs / "worker-finished").read_text(encoding="utf-8") == "complete"


def test_resident_provisioning_keeps_start_gate_and_one_preview_owner(
    standalone, monkeypatch,
):
    workers = []
    events = []
    monkeypatch.setattr(
        main, "threading",
        SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(
            start=lambda: workers.append(kwargs["target"]),
        )),
    )
    gate = SimpleNamespace(wait=lambda: events.append("released"))
    assert main._schedule_provisioning_if_needed(
        str(standalone.repo), {}, start_event=gate,
    ) == ""
    assert main._schedule_provisioning_if_needed(str(standalone.repo), {}) == ""
    assert len(workers) == 1 and standalone.plans == []
    workers[0]()
    assert events == ["released"]
    assert len(standalone.plans) == 1 and standalone.children == []
    assert str(standalone.repo) not in main._PROVISIONING_WORKERS
