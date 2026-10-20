"""Managed and provider workspaces retain their independently packaged owners."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge import session_manager, transport, venue_launch_policy
from agent_bridge._peer_launch import ContextRefused

pytestmark = pytest.mark.contract("agent_bridge.transport.workspace_owner")
PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def installed_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    own = tmp_path / ".agent-bridge"
    scripts = own / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "versioned_runtime_leases.py", "versioned_runtime_construction.py", "versioned_runtime.py",
        "versioned_runtime_source.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copy2(PLUGIN / "scripts" / name, scripts)
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(lifecycle.shutil, "which", Mock(return_value=None))
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    peer = tmp_path / ".agent-worktrees"
    slot = peer / "versions" / "1.0.0"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(slot)],
        check=True, capture_output=True, timeout=20, **no_window_kwargs(),
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    query = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    package = Path(query.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        "import json, os, pathlib, sys\n"
        "assert not any(k in os.environ for k in "
        "('AGENT_RT_ROOT', 'AGENT_RT_PY', 'PYTHONPATH', 'PYTHONHOME', "
        "'COPILOT_PLUGIN_ROOT', 'VIRTUAL_ENV', 'AGENT_HOME'))\n"
        "args = sys.argv[1:]\n"
        "pathlib.Path(os.environ['OWNER_CALL_LOG']).write_text(json.dumps(args))\n"
        "print(json.dumps({'launch': {'cmd': ['fixture-copilot'], "
        "'worktree_id': 'workspace-a', 'work_dir': os.environ['OWNER_WORKSPACE'], "
        "'env': {'FIXTURE_OWNER': 'worktrees'}}}))\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": "1.0.0", "completed_at": "2026-01-01", "pid": 0}),
        encoding="utf-8",
    )
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    call_log = tmp_path / "owner-call.json"
    workspace = tmp_path / "workspace-a"
    workspace.mkdir()
    monkeypatch.setenv("OWNER_CALL_LOG", str(call_log))
    monkeypatch.setenv("OWNER_WORKSPACE", str(workspace))
    return own, call_log, workspace


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True])
async def test_real_managed_launch_uses_owner_not_bridge_interpreter(installed_owner, resume):
    own, call_log, workspace = installed_owner
    assert importlib.util.find_spec("agent_worktrees") is None
    negative = subprocess.run(
        [sys.executable, "-I", "-m", "agent_worktrees", "--help"],
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert negative.returncode != 0
    assert "No module named agent_worktrees" in negative.stderr
    target = transport.SpawnTarget(
        project="project-a", worktree_id="workspace-a" if resume else None,
        caller_worktree="caller-a", caller_owner_ref="host/project/caller-a",
        env={"AGENT_RT_ROOT": str(own), "AGENT_RT_PY": sys.executable,
             "PYTHONPATH": "/not-a-package-root", "COPILOT_PLUGIN_ROOT": "/caller/payload",
             "VIRTUAL_ENV": "/caller/venv", "AGENT_HOME": "/caller/registry"},
    )
    args, cwd, environment = await transport.resolve_local_launch(target)
    assert args == ["fixture-copilot", "--acp", "--stdio", "--no-auto-update"]
    assert cwd == str(workspace)
    assert target.cwd == str(workspace)
    assert target.worktree_id == "workspace-a"
    assert environment["FIXTURE_OWNER"] == "worktrees"
    argv = json.loads(call_log.read_text())
    expected = ["--project", "project-a", "resolve", "--json", "--no-resume"]
    expected += (["--worktree-id", "workspace-a"] if resume else [
        "--new", "--bridge", "--caller-worktree", "caller-a",
        "--owner-ref", "host/project/caller-a",
    ])
    assert argv == expected


@pytest.mark.asyncio
async def test_real_cleanup_uses_owner_and_only_exact_workspace(installed_owner):
    _, call_log, _ = installed_owner
    await session_manager._cleanup_worktree(
        transport.SpawnTarget(project="project-a", worktree_id="workspace-a"), 0,
    )
    assert json.loads(call_log.read_text()) == [
        "--project", "project-a", "cleanup", "--worktree-id", "workspace-a",
        "--automatic", "--json", "--include-unused",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("refused", [False, True])
async def test_managed_workspace_missing_or_refused_owner_never_spawns(monkeypatch, refused):
    prefix = Mock(
        side_effect=ContextRefused("owner provenance refused") if refused else None,
        return_value=None,
    )
    monkeypatch.setattr(transport, "_agent_worktrees_launch_prefix", prefix)
    spawn = Mock(side_effect=AssertionError("must not start a worker without its owner"))
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(RuntimeError, match="owner provenance refused|owner capability is unavailable"):
        await transport.resolve_local_launch(
            transport.SpawnTarget(project="project-a", worktree_id="workspace-a"),
        )
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_real_codespace_singleton_spawn_and_resume_need_no_worktree_owner(tmp_path, monkeypatch):
    assert importlib.util.find_spec("agent_worktrees") is None
    unavailable = Mock(side_effect=AssertionError("singleton must not ask Worktrees"))
    monkeypatch.setattr(transport, "_agent_worktrees_launch_prefix", unavailable)
    policy = tmp_path / "provider.py"
    policy.write_text(
        "import json, sys\n"
        "assert sys.argv[1:3] == ['launch-check', 'codespace-a']\n"
        "print(json.dumps({'refuse': None}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        venue_launch_policy, "_check_command", lambda: [sys.executable, "-I", str(policy)],
    )
    target = transport.SpawnTarget(
        type="command",
        spawn_command=[sys.executable, "-I", "-c",
                       "import json; print(json.dumps({'workspace': '/workspaces/project-a'}))"],
        codespace={"name": "codespace-a", "workspace_folder": "/workspaces/project-a"},
    )
    for selected in (target, transport.SpawnTarget.from_json(target.to_json())):
        process = await transport.spawn(selected)
        stdout, stderr = await asyncio.wait_for(process.proc.communicate(), 10)
        assert process.proc.returncode == 0, stderr
        assert json.loads(stdout) == {"workspace": "/workspaces/project-a"}
        assert selected.worktree_id is None
    unavailable.assert_not_called()


@pytest.mark.asyncio
async def test_codespace_singleton_still_refuses_provider_policy(monkeypatch):
    monkeypatch.setattr(
        venue_launch_policy, "codespace_launch_refusal", lambda name: "provider fence held",
    )
    child = Mock(side_effect=AssertionError("provider refusal must precede spawn"))
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", child)
    with pytest.raises(venue_launch_policy.LaunchRefusedError, match="provider fence held"):
        await transport.spawn(transport.SpawnTarget(
            type="command", spawn_command=["unreachable"],
            codespace={"name": "codespace-a"},
        ))
    child.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "retrying", "unused"])
async def test_zero_turn_cleanup_crosses_actual_owner_harness(tmp_path, monkeypatch, caplog, state):
    owner_python = os.environ.get("COPILOT_EXTENSIONS_WORKTREE_OWNER_TEST_PYTHON")
    if not owner_python:
        pytest.skip("explicit independent Worktrees test interpreter required")
    assert Path(owner_python).is_file()
    assert importlib.util.find_spec("agent_worktrees") is None
    fixture = (
        PLUGIN.parent / "agent-worktrees" / "tests" / "_automatic_cleanup_owner_fixture.py"
    )
    workspace = tmp_path / "owner-fixture"
    monkeypatch.setattr(
        session_manager, "_agent_worktrees_launch_prefix",
        lambda: [owner_python, "-I", str(fixture), str(workspace), state],
    )
    await session_manager._cleanup_worktree(
        transport.SpawnTarget(project="project-a", worktree_id="workspace-a"), 0,
    )
    proof = json.loads((workspace / "proof.json").read_text(encoding="utf-8"))
    assert proof["argv"] == [
        "--project", "project-a", "cleanup", "--worktree-id", "workspace-a",
        "--automatic", "--json", "--include-unused",
    ]
    assert proof["workspace_exists"] and proof["unrelated_exists"] and proof["record_exists"]
    if state == "unused":
        assert proof["payload"]["removed"] is True
        assert proof["reaped"] == [["workspace-a", "unused"]]
    else:
        assert proof["payload"]["removed"] is False
        assert proof["payload"]["bucket"] == "dispatch-pending"
        assert proof["reaped"] == []
        assert proof["payload"]["reason"] in caplog.text


@pytest.mark.asyncio
async def test_cleanup_never_retries_without_automatic_protection(monkeypatch, caplog):
    monkeypatch.setattr(session_manager, "_agent_worktrees_launch_prefix", lambda: ["owner"])
    calls = []

    async def reject(*args, **kwargs):
        calls.append(args)
        return type("Process", (), {
            "returncode": 2,
            "communicate": lambda self: asyncio.sleep(
                0, result=(b"", b"unrecognized arguments: --automatic"),
            ),
        })()

    monkeypatch.setattr(session_manager.asyncio, "create_subprocess_exec", reject)
    await session_manager._cleanup_worktree(
        transport.SpawnTarget(project="project-a", worktree_id="workspace-a"), 0,
    )
    assert len(calls) == 1
    assert "--automatic" in calls[0]
    assert "unrecognized arguments: --automatic" in caplog.text
