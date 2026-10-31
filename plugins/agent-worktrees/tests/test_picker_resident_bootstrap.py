"""Resident bootstrap preserves policy and never reconstructs a roster in a reader."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from zdd import routing

from agent_worktrees import config as cfg, picker_bootstrap_runtime as bootstrap
from agent_worktrees import picker_fast_entry, status_monitor_cutover as control
from agent_worktrees.worktrees_runtime import WorktreesRuntime


@pytest.fixture
def resident(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    policy = SimpleNamespace(
        headless=False, machine="fixture-host", configured_machine="fixture-host",
        default_repo=SimpleNamespace(anchor=str(anchor)),
    )
    loads = []
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)

    def load(path, **kwargs):
        assert kwargs == {"project": "sample", "include_control_plane_related_pr": False}
        loads.append(path)
        return policy

    monkeypatch.setattr(cfg, "load_config", load)
    monkeypatch.setattr(bootstrap.tracking, "list_records", lambda *_a, **_k: pytest.fail("bootstrap scanned a roster"))
    manifest = SimpleNamespace(resident_picker_command=("qualified-manager", "--resident-picker"))
    monkeypatch.setattr(bootstrap.providers, "_discover_control_plane_provider_manifests", lambda **_kwargs: {"manager": manifest})
    runtime = WorktreesRuntime(tmp_path / "current.sqlite", elected=lambda: True)
    runtime.activate()
    yield runtime, anchor, policy, loads
    runtime.shutdown()


def request(anchor, **changes):
    return {
        "contract_version": 1, "project": "sample", "cwd": str(anchor),
        "default_live": True, "provider": "", **changes,
    }


def test_bootstrap_uses_fresh_policy_without_caller_project_mutation(resident):
    runtime, anchor, policy, loads = resident
    original = cfg.active_project()
    first = runtime.picker_bootstrap(request(anchor))
    assert first["ok"] is True
    assert first["bootstrap"]["project_config"]["machine"] == "fixture-host"
    assert first["bootstrap"]["workspace"] is None
    assert first["manager_command"] == ["qualified-manager", "--resident-picker"]
    policy.headless = True
    second = runtime.picker_bootstrap(request(anchor))
    assert second["bootstrap"]["headless"] is True
    assert second["manager_command"] is None
    assert len(loads) == 2
    assert cfg.active_project() == original
    assert runtime.demand_projects() == {"sample"}


@pytest.mark.parametrize("invalid", ["noncanonical", "platform"])
def test_invalid_execution_space_is_refused_before_provider_handoff(resident, monkeypatch, invalid):
    from agent_worktrees import execution_spaces

    runtime, anchor, policy, _loads = resident
    entry = cfg.MachineEntry(
        key="fixture-space", display_name="Fixture", hostname="fixture-host",
        execution_platform="linux",
    )
    monkeypatch.setattr(execution_spaces, "registry_for_config", lambda _: {entry.key: entry})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    policy.machine = "fixture-host" if invalid == "noncanonical" else entry.key
    policy.platform = "linux" if invalid == "noncanonical" else "wsl"
    monkeypatch.setattr(
        bootstrap.providers, "_discover_control_plane_provider_manifests",
        lambda **_kwargs: pytest.fail("invalid execution space offered a provider"),
    )
    result = runtime.picker_bootstrap(request(anchor))
    assert result["ok"] is False
    assert result["error"]["code"] == "unavailable"
    assert not runtime.demand_projects()


@pytest.mark.parametrize("changes", [
    {"contract_version": True}, {"contract_version": 2}, {"project": "../foreign"},
    {"cwd": "relative"}, {"default_live": 1}, {"extra": "unknown"},
    {"providers_dir": "relative"}, {"provider": None},
])
def test_invalid_bootstrap_does_not_load_configuration(resident, changes):
    runtime, anchor, _policy, loads = resident
    result = runtime.picker_bootstrap(request(anchor, **changes))
    assert result["ok"] is False
    assert not loads


def test_drained_or_superseded_resident_does_not_offer_a_fast_front_door(resident):
    runtime, anchor, _policy, loads = resident
    runtime.close_admission("draining")
    result = runtime.picker_bootstrap(request(anchor))
    assert result["ok"] is False
    assert result["error"]["code"] == "draining"
    assert not loads


def test_election_loss_during_policy_resolution_cannot_issue_a_handoff(resident, monkeypatch):
    runtime, anchor, _policy, _loads = resident
    original = bootstrap.build

    def superseded(payload):
        result = original(payload)
        runtime._elected = lambda: False
        return result

    monkeypatch.setattr(bootstrap, "build", superseded)
    result = runtime.picker_bootstrap(request(anchor))
    assert result["ok"] is False
    assert result["error"]["code"] == "superseded"
    assert not runtime.demand_projects()


def test_fast_entry_reads_the_existing_authenticated_control_contract(resident, tmp_path, monkeypatch):
    runtime, anchor, policy, _loads = resident
    token = tmp_path / "status-monitor-control.token"
    token.write_text("fixture-token", encoding="utf-8")
    monkeypatch.chdir(anchor)
    server = control.ControlServer(
        lambda action, payload: runtime.picker_bootstrap(payload) if action == "picker-bootstrap" else {},
        token="fixture-token",
    )
    server.start()
    try:
        routing.publish_active(
            tmp_path / "status-monitor-routing", bind="127.0.0.1", port=server.port,
        )
        result = picker_fast_entry._request(tmp_path, "sample")
        assert result["bootstrap"]["project"] == "sample"
        assert result["runtime_root"] == str(tmp_path)
        policy.headless = True
        assert picker_fast_entry._request(tmp_path, "sample") is None
        token.write_text("wrong-token", encoding="utf-8")
        assert picker_fast_entry._request(tmp_path, "sample") is None
    finally:
        server.close()


def test_provider_capability_is_optional_and_explicit():
    payload = {
        "schema_version": 1, "provider": "manager", "command": ["qualified-manager"],
        "minimum_version": "1.0.0",
    }
    parse = bootstrap.providers._parse_control_plane_provider_manifest
    assert parse(payload, source_path="fixture.json").resident_picker_command == ()
    payload["resident_picker"] = {"version": 1, "command": ["qualified-manager", "--resident-picker"]}
    assert parse(json.loads(json.dumps(payload)), source_path="fixture.json").resident_picker_command == (
        "qualified-manager", "--resident-picker",
    )


@pytest.mark.parametrize("cause", ["headless", "nonterminal", "context", "override"])
def test_fast_entry_cannot_bypass_the_normal_front_door_gates(tmp_path, monkeypatch, cause):
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: cause != "nonterminal"))
    monkeypatch.setattr(picker_fast_entry.registry_paths, "registry_root", lambda: tmp_path)
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    if cause == "override":
        monkeypatch.setenv("WORKTREE_MANAGER_ENGINE_CMD", "explicit-engine")
    if cause == "context":
        def invalid_context():
            raise ValueError("foreign installation")
        monkeypatch.setattr(picker_fast_entry.registry_paths, "registry_root", invalid_context)
    monkeypatch.setattr(
        picker_fast_entry, "_request",
        lambda *_args: None if cause == "headless" else pytest.fail("ineligible front door probed the resident"),
    )
    assert picker_fast_entry.dispatch(["--project", "sample"]) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows launch-job custody")
def test_fast_handoff_retains_the_selected_engine_and_closes_its_job(tmp_path, monkeypatch):
    import agent_procutil

    result = {
        "manager_command": ["qualified-manager", "--resident-picker"],
        "bootstrap": {"project": "sample"}, "runtime_root": str(tmp_path),
    }
    monkeypatch.delenv("WORKTREE_MANAGER_ENGINE_CMD", raising=False)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(picker_fast_entry.registry_paths, "registry_root", lambda: tmp_path)
    monkeypatch.setattr(picker_fast_entry, "_request", lambda *_args: result)
    events = []

    def spawn(command, *, env):
        assert command == ["qualified-manager", "--resident-picker", "--project", "sample"]
        assert json.loads(env["WORKTREE_MANAGER_ENGINE_ARGV"]) == [sys.executable, "-m", "agent_worktrees"]
        assert json.loads(env[picker_fast_entry.BOOTSTRAP_ENV]) == result
        return (
            SimpleNamespace(wait=lambda: 17, poll=lambda: 17),
            SimpleNamespace(close=lambda: events.append("closed")),
        )

    monkeypatch.setattr(agent_procutil, "spawn_sync_in_kill_on_close_job", spawn)
    assert picker_fast_entry.dispatch(["--project", "sample"]) == 17
    assert events == ["closed"]


def test_native_module_entry_can_handoff_before_importing_the_cli():
    code = """
import json, runpy, sys
from agent_worktrees import picker_fast_entry
picker_fast_entry.dispatch = lambda argv: 17
sys.argv = ["agent_worktrees", "--project", "sample"]
try:
    runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
except SystemExit as result:
    print(json.dumps({"exit": result.code, "cli": "agent_worktrees.__main__" in sys.modules,
                      "config": "agent_worktrees.config" in sys.modules}))
"""
    child = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {"exit": 17, "cli": False, "config": False}


def test_provider_session_is_optional_and_qualified_by_its_declared_root(tmp_path):
    payload = {
        "schema_version": 1, "provider": "sample",
        "command": ["qualified-manager"], "minimum_version": "1.0.0",
        "resident_picker": {
            "version": 1, "command": ["qualified-manager", "--resident-picker"],
            "session": {"version": 1, "root": str(tmp_path)},
        },
    }
    parse = bootstrap.providers._parse_control_plane_provider_manifest
    assert parse(payload, source_path=str(tmp_path / "provider.json")).resident_picker_session_root == str(tmp_path)
    payload["resident_picker"]["session"]["root"] = "relative"
    with pytest.raises(ValueError, match="absolute root"):
        parse(payload, source_path=str(tmp_path / "provider.json"))


@pytest.mark.parametrize("layout", ["ordinary", "converted-bare", "pure-bare"])
def test_resident_workspace_lookup_is_exact_and_ignores_foreign_git_selectors(
    tmp_path, monkeypatch, layout,
):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()

    def git(directory, *args):
        run = subprocess.run(
            ["git", "-C", str(directory), "-c", f"core.hooksPath={hooks}", *args],
            capture_output=True, text=True, env=environment, timeout=15, **no_window_kwargs(),
        )
        assert run.returncode == 0, run.stderr
        return run.stdout.strip()

    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--allow-empty", "-m", "fixture")
    if layout == "pure-bare":
        anchor = tmp_path / "anchor"
        git(tmp_path, "clone", "--bare", str(seed), str(anchor))
    else:
        anchor = seed
        if layout == "converted-bare":
            git(anchor, "config", "core.bare", "true")
    workspace = tmp_path / "workspace"
    git(anchor, "worktree", "add", "-b", "task", str(workspace))
    nested = workspace / "nested"
    nested.mkdir()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "foreign.git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(tmp_path / "foreign-worktree"))
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    reads = []

    def record(worktree_id, **kwargs):
        reads.append(worktree_id)
        return SimpleNamespace(repo="sample", worktree_path=str(workspace))

    monkeypatch.setattr(bootstrap.tracking, "load_record_by_id", record)
    monkeypatch.setattr(
        bootstrap.tracking, "list_records",
        lambda *_args, **_kwargs: pytest.fail("resident bootstrap enumerated tracking records"),
    )
    assert bootstrap._invocation(nested, "sample", anchor) == (True, str(workspace))
    assert reads == ["workspace"]


def test_bootstrap_delivery_participates_in_existing_current_read_drain_accounting(monkeypatch):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def handle(action, payload):
        assert action == "picker-bootstrap"
        entered.set()
        assert release.wait(3)
        return {"ok": True}

    server = control.ControlServer(handle, token="fixture-token")
    finish = server._on_current_read_finished

    def delivered(action):
        assert action == "worktrees-read"
        finish(action)
        finished.set()

    monkeypatch.setattr(server, "_on_current_read_finished", delivered)
    server.start()
    result = {}

    def read():
        client = control.ControlClient(
            f"http://127.0.0.1:{server.port}", token="fixture-token",
        )
        result.update(client._request("picker-bootstrap"))

    worker = threading.Thread(target=read)
    try:
        worker.start()
        assert entered.wait(3)
        assert server.current_read_count() == 1
        assert server.current_read_count("repos-map-read") == 0
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
        assert result == {"ok": True}
        assert finished.wait(3)
        assert server.current_read_count() == 0
    finally:
        release.set()
        worker.join(timeout=3)
        server.close()
