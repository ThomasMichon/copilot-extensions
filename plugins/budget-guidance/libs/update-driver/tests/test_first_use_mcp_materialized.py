"""Actual MCP publication remains separate from synthetic recipe controls."""

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import pytest

from agent_procutil import no_window_kwargs
from update_driver import drain, foundation_status, status
from update_driver import first_use as provision

from test_first_use_mcp import REPOSITORY, owning_mcp as owning_mcp
from test_first_use_bridge import (
    build_bridge_native_test_assets as build_native_test_assets,
    native_recipe_runner as native_recipe_runner,
    set_payload_version,
)
from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="materialized native MCP recipes are qualified only on Windows and Linux",
)


@pytest.fixture
def materialized_mcp(owning_mcp, area):
    payload, installation, project = owning_mcp
    candidate = area / "materialized/plugins/agent-mcp"
    shutil.copytree(REPOSITORY / "plugins/agent-mcp", candidate, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
    ))
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(REPOSITORY / "tools/materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=REPOSITORY)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), "\n".join(result)
    build_native_test_assets(candidate, candidate / "libs/agent-procutil")
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    return payload, installation, project


def test_actual_mcp_stamp_publishes_deferred_command_without_runtime(
    materialized_mcp, area,
):
    payload, installation, project = materialized_mcp
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    assert not installation.exists()
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    selected = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert (selected / "scripts/init.ps1").is_file()
    command = installation.parent / ".local/bin" / (
        "agent-mcp.cmd" if os.name == "nt" else "agent-mcp"
    )
    assert command.is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.skipif(os.name != "nt", reason="actual Windows native installer admission")
@pytest.mark.parametrize("default_root", [True, False])
def test_mcp_native_installer_admission_joins_original_queue(
    materialized_mcp, area, default_root,
):
    payload, installation, project = materialized_mcp
    scripts = payload / "scripts"
    (scripts / "installer-engine.ps1").write_text(
        "function Ensure-Uv { param([string]$InstallRoot)\n"
        "return (Get-Command python -CommandType Application).Source\n}\n",
        encoding="utf-8",
    )
    (scripts / "init.ps1").write_text(
        "param([string]$Action,[string]$InstallDir)\n$ErrorActionPreference = 'Stop'\n"
        "if (-not $env:UPDATE_DRIVER_WITNESS) { throw 'missing native participant handoff' }\n"
        ". (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')\n"
        "$result = Enter-NativeInstallerDriver -Payload (Split-Path $PSScriptRoot) "
        "-Root $InstallDir -Plugin agent-mcp -Action $Action\n"
        "if (-not $result.Participant -or $result.ExitCode -ne 0) {\n"
        "  throw 'native installer admission did not join the original request'\n}\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'native-admitted'), $Action)\n"
        "exit 0\n", encoding="utf-8",
    )
    root = installation.parent / ".copilot-extensions/update-driver" if default_root else area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    assert (installation / "native-admitted").read_text() == "provision"
    assert [item.id for item in status(root=root)] == [request.id]
    if not default_root:
        assert not (installation.parent / ".copilot-extensions/update-driver").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows lifetime SDK PATH prerequisite")
def test_actual_mcp_missing_setup_python_refuses_before_supervisor_or_plugin(
    materialized_mcp, area,
):
    payload, installation, project = materialized_mcp
    environment = cold_environment(installation, project, area)
    root = area / "cold"
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    command = native_command(
        root, payload, installation, project, source=payload / "libs/update-driver",
    ) + ["-Wait", "-InstallerAction", "stamp"]
    result = subprocess.run(
        command, env=environment, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=120, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Python 3.10+ must be available as python on PATH" in result.stderr, result.stderr
    assert "launch acknowledgement:" not in result.stderr
    assert not installation.exists() and not status(root=root)
    assert foundation_status(root=root) is None
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


def installed_command(installation):
    command = installation.parent / ".local/bin"
    if os.name == "nt":
        return [os.environ["ComSpec"], "/d", "/c", str(command / "agent-mcp.cmd"), "--help"]
    return [shutil.which("bash"), str(command / "agent-mcp"), "--help"]


def assert_installed_mcp(installation, environment, project):
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import importlib.metadata,json,sys,agent_mcp;"
         "from agent_mcp.config import BRIDGES_DIR,load_config;"
         "configuration=load_config('fixture-preserved');"
         "print(json.dumps({'python':sys.executable,'module':agent_mcp.__file__,"
         "'version':importlib.metadata.version('agent-mcp'),"
         "'bridges':str(BRIDGES_DIR),'command':configuration.server.command}))"],
        env=environment, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    identity = json.loads(result.stdout)
    assert Path(identity["python"]) == python
    assert Path(identity["module"]).is_relative_to(slot)
    assert identity["version"]
    assert Path(identity["bridges"]) == installation / "bridges"
    assert identity["command"] == ["fixture-server-not-started"]
    result = subprocess.run(
        installed_command(installation), env={**environment, "AGENT_MCP_NO_SELFPROVISION": "1"},
        cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0 and "usage:" in result.stdout.lower(), result.stdout + result.stderr
    return slot


@native_genesis
def test_actual_mcp_cold_recipe_survives_caller_exit_and_preserves_healthy_selection(
    materialized_mcp, area, native_window_observation, native_recipe_runner, rename_source, capsys,
):
    payload, installation, project = materialized_mcp
    root = area / "cold"
    environment = cold_environment(installation, project, area, python_prerequisite=True)
    environment.pop("AGENT_VAULT_HOME")
    sentinel = installation / "bridges/fixture-preserved.yaml"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text(json.dumps({
        "server": {"type": "stdio", "command": ["fixture-server-not-started"]},
        "auth": {"kind": "none"},
    }), encoding="utf-8")
    preserved = sentinel.read_bytes()

    def stage(name):
        with capsys.disabled():
            print(f"MCP native qualification: {name}", flush=True)

    def diagnostic():
        return "\n".join(f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
                         for path in root.rglob("*.log") if path.is_file())

    def command(action):
        return native_command(
            root, payload, installation, project, source=payload / "libs/update-driver",
        ) + (
            ["-Wait", "-InstallerAction", action] if os.name == "nt" else
            ["--wait", "--installer-action", action]
        )

    stage("cold stamp")
    caller, caller_log = native_recipe_runner.start(
        command("stamp"), env=environment, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert caller.poll() is None, caller_log.read_text(errors="replace") + diagnostic()
    caller.terminate()
    caller.wait(timeout=30)
    # Keep runner containment: this is caller-process loss, not caller-Job escape.
    receipts = list(root.glob("first-use-*.log"))
    assert len(receipts) == 1, diagnostic()

    def stamp_completed():
        progress = foundation_status(root=root)
        if progress is not None:
            assert progress.state not in {"failed", "interrupted"}, diagnostic()
        receipt = ordinary_receipt(receipts[0])
        if receipt is None and native_recipe_runner.owners_finished():
            receipt = ordinary_receipt(receipts[0])
            assert receipt is not None, "Native owner exited without an ordinary receipt\n" + diagnostic()
        if receipt is None:
            return False
        result = status(receipt, root=root)
        assert result.state != "interrupted", diagnostic()
        return progress is not None and progress.state == "completed" and result.state == "completed"

    observe(stamp_completed, 900, diagnostic)
    results = status(root=root)
    assert all(item.state == "completed" and item.exit_code == 0 for item in results), diagnostic()
    assert not (installation / "current-version").exists()
    stage("concurrent provision")
    callers = [
        native_recipe_runner.start(
            command("provision"), env=environment, cwd=project, root=root, diagnostic=diagnostic,
        ) for _ in range(2)
    ]
    report_at = time.monotonic() + 60

    def provisions_finished():
        nonlocal report_at
        if time.monotonic() >= report_at:
            latest = sorted(root.glob("*.log"), key=lambda path: path.stat().st_mtime)[-3:]
            with capsys.disabled():
                for path in latest:
                    print(f"MCP active log {path.name}:\n"
                          + path.read_text(errors="replace")[-4096:], flush=True)
            report_at = time.monotonic() + 60
        return all(process.poll() is not None for process, _path in callers)

    observe(provisions_finished, 600, diagnostic)
    for process, path in callers:
        assert process.returncode == 0, path.read_text(errors="replace") + diagnostic()
    stage("installed consumers")
    slot = assert_installed_mcp(installation, environment, project)
    selection = (installation / "current-version").read_bytes()
    completion = (slot / ".install-complete.json").read_bytes()
    manifest = (installation / "deploy-manifest.json").read_bytes()
    stage("healthy repeat")
    repeated = native_recipe_runner.run(
        command("provision"), env=environment, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert repeated.returncode == 0, repeated.stdout + diagnostic()
    assert (installation / "current-version").read_bytes() == selection
    assert (slot / ".install-complete.json").read_bytes() == completion
    assert sentinel.read_bytes() == preserved

    stage("incoming health failure")
    set_payload_version(payload, "999.0.0")
    initializer = payload / "src/agent_mcp/__init__.py"
    initializer.write_text("raise RuntimeError('fixture incoming module health failure')\n", encoding="utf-8")
    failed = native_recipe_runner.run(
        command("provision"), env=environment, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert failed.returncode != 0, failed.stdout + diagnostic()
    assert "Fresh runtime slot failed its health gate" in failed.stdout, failed.stdout + diagnostic()
    assert (installation / "current-version").read_bytes() == selection
    assert (slot / ".install-complete.json").read_bytes() == completion
    # A healthy repeat may refresh diagnostics, not the failed candidate's selection.
    assert json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))[
        "runtime_version"
    ] == json.loads(manifest.decode("utf-8-sig"))["runtime_version"]
    assert sentinel.read_bytes() == preserved
    stage("source-independent consumers")
    rename_source(payload, payload.with_name("unavailable-mcp"))
    assert_installed_mcp(installation, environment, project)
    stage("native owner settlement")
    observe(native_recipe_runner.owners_finished, 900, diagnostic)
