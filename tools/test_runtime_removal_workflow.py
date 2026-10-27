"""The Darwin uninstall qualification is explicit, immutable and bounded."""
from __future__ import annotations

import os
import io
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
from types import SimpleNamespace

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
    assert '"PYTEST_ADDOPTS=--maxfail=1 --tb=short"' in command
    for name in ("Bounded libproc and exact-argv inspection",
                 "Complete native Worktrees uninstall entrypoints"):
        command = STEPS[name]["run"]
        assert not command.startswith("cd ")
        assert '"$REMOVAL_SOURCE"' in command
        assert 'sudo -n -H -u "$REMOVAL_USER" -- env -i' in command
        assert '"HOME=$REMOVAL_HOME" "PATH=$REMOVAL_PATH"' in command


@pytest.mark.parametrize("failure", [None, "account", "ownership", "probe"])
def test_dedicated_account_setup_is_bounded_and_publishes_only_verified_paths(
    tmp_path, monkeypatch, failure,
):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(
        getpwall=lambda: [SimpleNamespace(pw_uid=501), SimpleNamespace(pw_uid=60001)],
    ))
    output = tmp_path / "github-env"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_ENV", str(output))
    binary = tmp_path / "pinned-uv"
    binary.write_bytes(b"fixture-pinned-tool")
    monkeypatch.setattr(shutil, "which", lambda name: str(binary) if name == "uv" else None)
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as stream:
        member = tarfile.TarInfo("tools/witness.py")
        member.size = 10
        stream.addfile(member, io.BytesIO(b"# fixture\n"))
    calls = []

    def archived(command, *, timeout):
        assert command == ["git", "archive", "HEAD"] and timeout == 60
        return archive.getvalue()

    def run(command, *, check, timeout):
        calls.append(command)
        assert command[:2] == ["sudo", "-n"] and check
        scope = ("account" if command[2] == "/usr/bin/dscl" else
                 "ownership" if command[2] == "/usr/sbin/chown" else "probe")
        assert timeout == (60 if scope == "ownership" else 30)
        if scope == failure:
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "check_output", archived)
    monkeypatch.setattr(subprocess, "run", run)
    body = _python_body(STEPS["Prepare an isolated native qualification account"])
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            exec(body, {})
        assert not output.exists()
        return
    exec(body, {})
    assert len(calls) == 6
    values = dict(line.split("=", 1) for line in output.read_text("utf-8").splitlines())
    assert set(values) == {
        "REMOVAL_USER", "REMOVAL_HOME", "REMOVAL_SOURCE", "REMOVAL_PYTHON", "REMOVAL_PATH",
    }
    source, home = Path(values["REMOVAL_SOURCE"]), Path(values["REMOVAL_HOME"])
    assert source.parent == home.parent and home.parent.parent == tmp_path
    assert (source / "tools/witness.py").read_bytes() == b"# fixture\n"
    assert (home.parent / "bin/uv").read_bytes() == binary.read_bytes()
    assert calls[0][-2:] == ["UniqueID", "60000"]
    assert calls[-1][:6] == ["sudo", "-n", "-H", "-u", values["REMOVAL_USER"], values["REMOVAL_PYTHON"]]
    assert calls[-1][-2:] == ["60000", str(home)]
    probe = calls[-1][-3]
    assert "(20,28,36)" in probe and "pwd.getpwuid" in probe
    compile(probe, "<isolated-credential-probe>", "exec")


@pytest.mark.parametrize("platform_name,hosted", [("win32", "true"), ("darwin", "false")])
def test_dedicated_account_setup_refuses_other_execution_contexts(monkeypatch, platform_name, hosted):
    monkeypatch.setattr(sys, "platform", platform_name)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace())
    monkeypatch.setenv("GITHUB_ACTIONS", hosted)
    with pytest.raises(SystemExit, match="only for hosted macOS"):
        exec(_python_body(STEPS["Prepare an isolated native qualification account"]), {})


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
    monkeypatch.setattr(sys, "argv", [sys.executable, str(root)])
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
        assert "tools/test_plugin_test_containment_darwin.py" in command
        assert "--timeout=30" in command and "--noconftest" in command
        assert "-p" in command and "pytest_portfolio_guard" in command
        if outcome == "unsettled":
            raise RuntimeError("fixture containment remains unverified")
        return outcome

    monkeypatch.setattr(containment, "run_contained", run)
    monkeypatch.setitem(sys.modules, "plugin_test_containment", containment)
    command = STEPS["Bounded libproc and exact-argv inspection"]["run"]
    prefix, separator, body = command.partition(" <<'PY'\n")
    assert separator and prefix.endswith('"$REMOVAL_PYTHON" -B - "$REMOVAL_SOURCE"')
    assert body.endswith("\nPY\n")
    body = compile(body[:-4], "<native-removal-workflow-step>", "exec")
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
