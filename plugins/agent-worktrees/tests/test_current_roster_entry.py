"""Canonical early-feed selection, context and compatibility contracts."""

from __future__ import annotations

import argparse
import builtins
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import current_roster_entry as entry
from agent_worktrees import invocation_project as context
from agent_worktrees import list_arguments, list_cli, worktrees_client


@pytest.mark.parametrize("argv", [
    [], ["list", "--follow"], ["--project", "p", "list", "--help"],
    ["-p", "p", "list", "--follow"], ["--project=p", "list", "--follow"],
    ["--project", "p", "list", "--follow", "--follow"],
    ["--project", "p", "list", "--fol"],
    ["--project", "p", "list", "--follow", "--worktree-id"],
    ["--project", "p", "list", "--follow", "--tracking-status", "bad"],
    ["--project", "p", "list", "--follow", "--unknown"],
    ["--project", "p", "list", "--follow", "--project", "other"],
    ["--project", "p", "worktree", "list", "--follow"],
    ["--project", "", "list", "--follow"],
    ["agent-worktrees", "--project", "p", "list", "--follow"],
])
def test_early_selector_declines_without_import_or_path_effects(monkeypatch, argv):
    def forbidden(*args, **kwargs):
        raise AssertionError("selector performed an effect")

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "open", forbidden)
        guarded.setattr(Path, "cwd", forbidden)
        guarded.setattr(builtins, "__import__", forbidden)
        result = entry.select_current_feed(argv)
    assert result is None


def test_selected_callback_is_deferred_and_input_is_captured(monkeypatch):
    argv = ["--project", "p", "list", "--follow", "--classify", "--all"]
    calls = []
    monkeypatch.setattr(entry, "_run_current_feed", lambda args: calls.append(args) or 7)
    selected = entry.select_current_feed(argv)
    assert callable(selected) and not calls
    argv[-1] = "--unknown"
    assert selected() == 7
    assert calls == [("--project", "p", "list", "--follow", "--classify", "--all")]


def test_shared_list_declaration_and_all_default_values():
    assert list_cli.add_parsers is list_arguments.add_parsers
    parser = argparse.ArgumentParser()
    list_arguments.add_parsers(parser.add_subparsers(dest="command"))
    default = vars(parser.parse_args(["list", "--follow"]))
    assert default["tracking_status"] == "all" and default["codename"] is None
    for flag, options in list_arguments.ARGUMENTS:
        name = flag[2:].replace("-", "_")
        if options.get("action") == "store_true":
            assert default[name] is (flag == "--follow")
        elif name != "tracking_status":
            assert default[name] is None


def test_context_preserves_anchor_transition_and_vanished_cwd(tmp_path, monkeypatch):
    old = cfg.active_project()
    before = Path.cwd()
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    try:
        assert context.resolve_invocation_project(
            ["list", "--follow"], "project", lambda _: ("project", anchor),
            lambda _: False, lambda _: False,
        )
        assert Path.cwd() == anchor and cfg.project_name() == "project"
        with monkeypatch.context() as guarded:
            guarded.setattr(Path, "cwd", lambda: (_ for _ in ()).throw(FileNotFoundError()))
            core = sys.modules.get("agent_worktrees.__main__")
            if core is not None:
                guarded.setattr(core, "_safe_cwd", context._safe_cwd)
            result = context._safe_cwd()
        assert result is None
    finally:
        context.os.chdir(before)
        cfg.set_active_project(old)


def test_existing_context_overrides_are_used_without_importing_core(monkeypatch, tmp_path):
    fake_core = SimpleNamespace(_anchor_for_project=lambda _: tmp_path)
    monkeypatch.setitem(sys.modules, "agent_worktrees.__main__", fake_core)
    assert context._resolve_active_project("chosen") == ("chosen", tmp_path)
    monkeypatch.delitem(sys.modules, "agent_worktrees.__main__")
    monkeypatch.delitem(sys.modules, "agent_worktrees.front_door_cli", raising=False)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    assert context._safe_cwd() == tmp_path
    assert "agent_worktrees.__main__" not in sys.modules


@pytest.mark.parametrize("failure,expected", [(ValueError("invalid"), 1), (KeyboardInterrupt(), 130)])
def test_early_handler_error_and_interrupt_parity(monkeypatch, failure, expected):
    old = cfg.active_project()
    monkeypatch.setattr(context, "_resolve_active_project", lambda _: ("project", None))

    def fail(args):
        raise failure

    monkeypatch.setattr(worktrees_client, "emit_current", fail)
    try:
        assert entry.select_current_feed(["--project", "project", "list", "--follow"])() == expected
    finally:
        cfg.set_active_project(old)


def test_console_decline_calls_existing_entry_exactly_once(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "argv", ["agent-worktrees", "--help"])
    monkeypatch.setitem(sys.modules, "agent_worktrees.__main__",
                        SimpleNamespace(console_entry=lambda: calls.append("legacy") or 3))
    assert entry.console_entry() == 3
    assert calls == ["legacy"]


def test_module_entry_decline_has_no_root_import_or_exit(monkeypatch):
    from agent_worktrees import _shutdown_exit

    imports = []
    original_import = builtins.__import__

    def record_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"__main__", "agent_worktrees.__main__"} or "__main__" in (fromlist or ()):
            imports.append(name)
            raise AssertionError("declined module entry imported the root")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(sys, "argv", ["agent-worktrees", "--help"])
    monkeypatch.setattr(entry, "select_current_feed", lambda _: None)
    monkeypatch.setattr(_shutdown_exit, "run_and_exit",
                        lambda _: pytest.fail("declined module entry exited"))
    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", record_import)
        result = entry.run_module_if_selected()
    assert result is None and imports == []


def test_module_entry_passes_selected_callable_to_current_exit_policy_once(monkeypatch):
    from agent_worktrees import _shutdown_exit

    def selected():
        pytest.fail("handler ran outside the exit policy")

    calls = []
    monkeypatch.setattr(sys, "argv", ["agent-worktrees", "--project", "p", "list", "--follow"])
    monkeypatch.setattr(entry, "select_current_feed", lambda _: selected)
    monkeypatch.setattr(_shutdown_exit, "run_and_exit", lambda handler: calls.append(handler))
    assert entry.run_module_if_selected() is None
    assert calls == [selected]


def test_root_console_entry_preserves_identity_and_late_bound_main(monkeypatch):
    import inspect
    from agent_worktrees import __main__ as core, _shutdown_exit

    def replacement():
        pytest.fail("main ran outside the exit policy")

    calls = []
    monkeypatch.setattr(core, "main", replacement)
    monkeypatch.setattr(_shutdown_exit, "run_and_exit", lambda handler: calls.append(handler))
    assert core.console_entry.__name__ == "console_entry"
    assert core.console_entry.__module__ == "agent_worktrees.__main__"
    assert not inspect.signature(core.console_entry).parameters
    assert core.console_entry() is None
    assert calls == [replacement]


def test_current_handler_preserves_config_cell_and_selector_arguments(monkeypatch):
    old = cfg.active_project()
    seen = []
    monkeypatch.setattr(context, "_resolve_active_project", lambda _: ("project", None))
    monkeypatch.setattr(worktrees_client, "emit_current",
                        lambda args: seen.append((cfg.project_name(), vars(args))) or 0)
    try:
        assert entry.select_current_feed([
            "--project", "project", "list", "--follow", "--all",
            "--include-other-platforms", "--worktree-id", "tree", "--tracking-status", "active",
        ])() == 0
        project, args = seen[0]
        assert project == "project"
        assert args["all"] and args["include_other_platforms"]
        assert args["worktree_id"] == "tree" and args["tracking_status"] == "active"
    finally:
        cfg.set_active_project(old)


def test_malformed_installation_cell_is_not_bypassed_before_auth(monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "invalid-context")
    monkeypatch.setattr(worktrees_client.routing, "read_active_endpoint",
                        lambda *a, **kw: pytest.fail("invalid cell reached endpoint"))
    with pytest.raises(ValueError):
        worktrees_client._resident({"contract_version": 1, "project": "project"})


def test_selected_context_preserves_existing_task_cwd_when_anchor_differs(tmp_path):
    old = cfg.active_project()
    before = Path.cwd()
    anchor = tmp_path / "different-anchor"
    anchor.mkdir()
    try:
        assert context.resolve_invocation_project(
            ["list", "--follow"], "project", lambda _: ("project", anchor),
            lambda selected: selected == anchor, lambda _: False,
        )
        assert Path.cwd() == before
    finally:
        cfg.set_active_project(old)


def test_library_main_import_never_selects_current_argv(monkeypatch):
    import importlib
    from agent_worktrees import __main__ as core

    saved = vars(core).copy()
    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(sys, "argv", ["agent-worktrees", "--project", "p", "list", "--follow"])
            scoped.setattr(entry, "select_current_feed",
                           lambda _: pytest.fail("library import attempted CLI dispatch"))
            importlib.reload(core)
    finally:
        vars(core).clear()
        vars(core).update(saved)


@pytest.mark.parametrize("option", ["--recovery-report", "--rec", "--recovery-report=request.json"])
def test_g3_recovery_context_does_not_discover_project_or_change_cwd(monkeypatch, option):
    from agent_worktrees import front_door_cli

    def forbidden(*args, **kwargs):
        pytest.fail("exact recovery report entered project lookup or CWD mutation")

    assert front_door_cli.resolve_invocation_project is context.resolve_invocation_project
    with monkeypatch.context() as scoped:
        scoped.setattr(context.os, "chdir", forbidden)
        scoped.setattr(cfg, "set_active_project", forbidden)
        result = context.resolve_invocation_project(
            ["doctor", option, "--json"], "ignored-project", forbidden, forbidden, forbidden,
        )
    assert result is False


def test_process_module_reexport_and_public_selector_parameter_are_preserved():
    import inspect
    from agent_worktrees import __main__ as core, procs

    assert core.procs is procs
    assert "procs" in inspect.signature(core.select_orphan_launcher_shells).parameters


def test_materialized_procutil_preserves_private_async_module_and_sync_boundary(tmp_path):
    import hashlib
    import json
    import os
    import subprocess

    import agent_procutil
    import materialize_main

    repo = Path(__file__).resolve().parents[3]
    canonical = repo / "libs" / "agent-procutil"
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        '[tool.uv.sources]\nagent-procutil = { path = "../../libs/agent-procutil", editable = true }\n',
    )
    destination = consumer / "libs" / "agent-procutil"
    outcome = materialize_main._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=destination, pyproject=pyproject,
        name="agent-procutil", raw_path="../../libs/agent-procutil", lib="agent-procutil",
    )
    assert outcome.startswith("OK")
    private = destination / "src" / "agent_procutil" / "_async.py"
    assert private.read_bytes() == (canonical / "src" / "agent_procutil" / "_async.py").read_bytes()
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
assert 'asyncio' not in sys.modules
import agent_procutil as p
from agent_procutil import no_window_kwargs, direct_python, spawn_sync_in_kill_on_close_job
assert isinstance(no_window_kwargs(), dict)
assert callable(direct_python) and callable(spawn_sync_in_kill_on_close_job)
assert 'asyncio' not in sys.modules and 'agent_procutil._async' not in sys.modules
assert 'spawn_in_kill_on_close_job' in dir(p)
import inspect
assert inspect.iscoroutinefunction(p.spawn_in_kill_on_close_job)
assert 'agent_procutil._async' in sys.modules
print(json.dumps({'sync_did_not_load_asyncio': True, 'private_async_available': True}))
"""
    argv = [sys.executable, "-I", "-S", "-c", code, str(destination / "src")]
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    if report_dir := os.environ.get("F1_NATIVE_REPORT_DIR"):
        report = Path(report_dir).resolve()
        assert report.is_relative_to(repo / ".test-venvs")
        (report / "materialized-import.json").write_text(json.dumps({
            "source_commit": os.environ.get("F1_SOURCE_COMMIT"),
            "materializer_result": outcome,
            "private_module_sha256": hashlib.sha256(private.read_bytes()).hexdigest(),
            "argv": argv, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
            "workload": "materialized synchronous-import contract; -I -S, not a startup benchmark",
        }, indent=2), encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "sync_did_not_load_asyncio": True, "private_async_available": True,
    }
