"""The Darwin uninstall qualification is explicit, immutable and bounded."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

import pytest
import yaml

from tools import plugin_test_containment as containment
from tools.test_guardian_followthrough_workflow import _enabled, _python_body


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/installation-context-acceptance.yml").read_text("utf-8")
)
JOB = WORKFLOW["jobs"]["runtime-removal-macos"]
STEPS = {step["name"]: step for step in JOB["steps"] if "name" in step}


def test_native_removal_is_an_isolated_manual_selection():
    trigger = WORKFLOW.get("on", WORKFLOW.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["runtime_removal"] == {
        "description": "Qualify bounded native macOS runtime removal and process inspection",
        "type": "boolean", "default": False,
    }
    defaults = {name: item["default"] for name, item in inputs.items() if item["type"] == "boolean"}
    enabled = {
        name for name, job in WORKFLOW["jobs"].items()
        if _enabled(job["if"], {**defaults, "runtime_removal": True})
    }
    assert enabled == {"runtime-removal-macos"}
    assert JOB["runs-on"] == "macos-latest" and JOB["timeout-minutes"] == 12
    assert WORKFLOW["permissions"] == {"contents": "read"}
    checkout = next(step for step in JOB["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
    assert not JOB.get("continue-on-error") and "strategy" not in JOB
    command = STEPS["Complete native Worktrees uninstall entrypoints"]["run"]
    assert "tools/run-plugin-tests.py agent-worktrees" in command
    assert "--test-file tests/test_native_uninstall_footprint.py" in command
    assert "--test-file tests/test_uninstall_project_commands.py" in command


@pytest.mark.parametrize("matches,darwin", [(True, True), (False, True), (True, False)])
def test_actual_native_source_check_refuses_another_subject(monkeypatch, capsys, matches, darwin):
    monkeypatch.setenv("EXPECTED_SHA", "a" * 40)
    monkeypatch.setattr(subprocess, "check_output", lambda *_a, **_k: ("a" if matches else "b") * 40 + "\n")
    monkeypatch.setattr(sys, "platform", "darwin" if darwin else "linux")
    monkeypatch.setattr(platform, "platform", lambda: "Darwin-fixture")
    body = _python_body(STEPS["Record exact native qualification source"])
    if matches and darwin:
        exec(body, {})
        assert "Checkout: " + "a" * 40 in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit, match="selected macOS source"):
            exec(body, {})
        assert capsys.readouterr().out == ""


@pytest.mark.parametrize("outcome", [0, 1, "unsettled"])
def test_actual_backend_packet_uses_verified_finite_containment(tmp_path, monkeypatch, outcome):
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    parent_environment = dict(os.environ)
    observed = {}

    def run(command, *, cwd, env, sandbox, limits):
        observed.update(sandbox=sandbox, command=command)
        assert cwd == root and sandbox.parent == root and sandbox.is_dir()
        assert env[containment.CONTAINED_ENV] == "1"
        assert limits.wall_seconds == 240
        assert command[:4] == [sys.executable, "-B", "-m", "pytest"]
        assert "tools/test_installer_engine_uninstall_darwin.py" in command
        assert "--timeout=30" in command and "--noconftest" in command
        assert "-p" in command and "pytest_portfolio_guard" in command
        if outcome == "unsettled":
            raise RuntimeError("fixture containment remains unverified")
        return outcome

    monkeypatch.setattr(containment, "run_contained", run)
    monkeypatch.setitem(sys.modules, "plugin_test_containment", containment)
    body = _python_body(STEPS["Bounded libproc and exact-argv inspection"])
    if outcome == "unsettled":
        with pytest.raises(RuntimeError, match="unverified"):
            exec(body, {})
        assert observed["sandbox"].is_dir()
        shutil.rmtree(observed["sandbox"])
    else:
        with pytest.raises(SystemExit) as result:
            exec(body, {})
        assert result.value.code == outcome
        assert not observed["sandbox"].exists()
    assert dict(os.environ) == parent_environment
