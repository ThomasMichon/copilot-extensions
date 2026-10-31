"""Fresh repair keeps cell-only ownership through actual suite command resolution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
import pytest
from test_update_engine_repair import repair_case as shared_repair_case
from test_update_engine_scope import _activate_scope

repair_case = shared_repair_case
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real repair and runtime-resolution policy; not promotion-unit evidence",
)


@pytest.mark.parametrize("source_state", ["present", "missing-payload"])
def test_cell_only_repair_does_not_require_a_shared_manifest(
    repair_case, monkeypatch, source_state,
):
    case = repair_case
    root, _durable, _context = _activate_scope(case, monkeypatch)
    case.manifest.rename(case.manifest.with_name("retained-manifest.json"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(case.incumbent))
    if source_state == "missing-payload":
        case.incumbent.rename(case.incumbent.with_name("retained-payload"))
    assert case.update.repair_engine(case.source) != 0
    assert case.effect.is_file()
    assert str(root) in case.calls[-1][0]
    assert not case.manifest.exists()
    assert not (case.runtime / "bin").exists()


def test_independent_source_repair_refuses_cell_before_native_refresh(repair_case, monkeypatch):
    case = repair_case
    _activate_scope(case, monkeypatch)
    before = case.manifest.read_bytes()
    with pytest.raises(RuntimeError, match="authorized legacy installation"):
        case.update._refresh_source(case.source, dict(os.environ), independent=True)
    assert case.acquisitions == [] and not case.effect.exists()
    assert case.manifest.read_bytes() == before


def _complete_slot(root: Path, version: str) -> Path:
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("fixture interpreter; suite execution is intercepted\n")
    (slot / ".install-complete.json").write_text("{}")
    (root / "current-version").write_text(version)
    return python


def test_successful_automatic_repair_keeps_cell_for_real_suite_resolution(
    repair_case, monkeypatch,
):
    from worktree_manager import __main__ as manager
    from worktree_manager import agent_plugin_runtime, engine_client, self_install

    case = repair_case
    root, _durable, context = _activate_scope(case, monkeypatch)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(case.incumbent))
    shared_python = _complete_slot(case.runtime, "0.9.0")
    selected = []

    def finish_admitted_repair(command, environment, cwd):
        # This is successful-repair routing, not the separate native abort control.
        flag = "-InstallDir" if os.name == "nt" else "--install-dir"
        assert command[command.index(flag) + 1] == str(root)
        assert environment["COPILOT_EXTENSIONS_CONTEXT"] == str(context)
        selected.append(_complete_slot(root, "1.0.0"))
        (root / "bin").mkdir()
        return 0

    monkeypatch.setattr(case.update, "_wait", finish_admitted_repair)
    monkeypatch.setattr(agent_plugin_runtime, "_load_installation_context", lambda: case.primitive)
    monkeypatch.setattr(
        engine_client, "_INHERITED_ENGINE_COMMAND",
        [str(shared_python), "-m", "agent_worktrees"],
    )
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.setattr(
        self_install, "self_update",
        lambda **kwargs: pytest.fail("fresh recovery must not fetch Manager again"),
    )
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=5)

    monkeypatch.setattr(
        engine_client, "subprocess",
        SimpleNamespace(run=execute, TimeoutExpired=subprocess.TimeoutExpired),
    )
    assert manager._cmd_update([
        "--repair-engine", str(case.source), "--project", "sample", "--force",
    ]) == 5
    assert len(calls) == 1
    command, options = calls[0]
    assert command == [
        str(selected[0]), "-m", "agent_worktrees",
        "--project", "sample", "update", "--no-manager", "--force",
    ]
    assert options["env"]["COPILOT_EXTENSIONS_CONTEXT"] == str(context)
    assert "COPILOT_EXTENSIONS_CONTEXT" not in os.environ
    assert not (case.runtime / "bin").exists()
    assert not case.effect.exists()


def test_successful_shared_repair_uses_new_selection_not_inherited_old_argv(
    repair_case, monkeypatch,
):
    from worktree_manager import __main__ as manager
    from worktree_manager import engine_client, self_install

    case = repair_case
    (case.runtime / "bin").mkdir()
    selected = []

    def finish_admitted_repair(command, environment, cwd):
        flag = "-InstallDir" if os.name == "nt" else "--install-dir"
        assert command[command.index(flag) + 1] == str(case.runtime)
        assert "COPILOT_EXTENSIONS_CONTEXT" not in environment
        selected.append(_complete_slot(case.runtime, "1.0.0"))
        manifest = json.loads(case.manifest.read_text())
        manifest["source"] = {"plugin": "agent-worktrees", "version": "1.0.0"}
        case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return 0

    monkeypatch.setattr(case.update, "_wait", finish_admitted_repair)
    monkeypatch.setattr(
        engine_client, "_INHERITED_ENGINE_COMMAND",
        [str(case.runtime / "versions/stale/bin/python"), "-m", "agent_worktrees"],
    )
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.setattr(
        self_install, "self_update", lambda **kwargs: pytest.fail("unexpected second Manager fetch"),
    )
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(
        engine_client, "subprocess",
        SimpleNamespace(run=execute, TimeoutExpired=subprocess.TimeoutExpired),
    )
    assert manager._cmd_update(["--repair-engine", str(case.source)]) == 0
    assert calls[0][0] == [
        str(selected[0]), "-m", "agent_worktrees", "update", "--no-manager",
    ]
    assert "COPILOT_EXTENSIONS_CONTEXT" not in calls[0][1]["env"]
    assert not case.effect.exists()


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell update-entry inspection contract")
@pytest.mark.parametrize("arguments", [
    ["update", "--request-status", "synthetic-request"],
    ["update", "--request-status=synthetic-request"],
    ["update", "--recovery-status"],
    ["update", "--recover-after-restart"],
    ["update", "--recover-for-repair"],
])
def test_native_inspection_does_not_enter_fresh_installation(
    repair_case, arguments,
):
    case = repair_case
    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert shell is not None
    entry = (
        Path(__file__).resolve().parents[2]
        / "plugins/agent-worktrees/bin/agent-worktrees-update-entry.ps1"
    )
    quoted_entry = str(entry).replace("'", "''")
    (case.home / ".worktree-manager/config.toml").write_text(
        "[source]\nrepo = ???\n", encoding="utf-8",
    )
    harness = case.home / "inspection.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "function Invoke-WebRequest { throw 'inspection attempted acquisition' }\n"
        f". '{quoted_entry}' @args\n"
        "if ($_awUpdateHandled) { throw 'inspection was treated as installation' }\n"
        "[Console]::WriteLine('inspection-returned')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(harness), *arguments],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "inspection-returned"
