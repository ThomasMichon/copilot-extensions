"""Fixed CodeSpaces recipes, not adoption of its ordinary publishers."""

import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, drain, status
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis

pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native CodeSpaces recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_codespaces_fixed_action_keeps_context_options_custody_and_result(
    owning_codespaces, area, monkeypatch, action,
):
    payload, installation, project = owning_codespaces
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    assert request.runner.context_keys == tuple(sorted(provision.CODESPACES_CONTEXT_KEYS))
    for key in ("ACTION", "FORCE", "DRY_RUN", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_CODESPACES_" + key, "foreign")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "client-only").read_text().strip() == "0"
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-codespaces"
    assert frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "plugin.json").read_bytes() == (payload / "plugin.json").read_bytes()
    admission = json.loads(request.spec.source)
    assert (admission["plugin"], admission["payload"], admission["runtime_root"], admission["project"]) == (
        "agent-codespaces", str(payload), str(installation), str(project),
    )


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "copy",
    "custom-root", "snapshot", "dispatcher", "dispatcher-array", "module-array",
    "provision-mode", "compound", "nested", "cell",
])
def test_codespaces_refusal_precedes_all_acquisition_queue_and_plugin_writes(
    owning_codespaces, area, monkeypatch, native, reason,
):
    payload, installation, project = owning_codespaces
    if reason in {"copy", "snapshot"}:
        copied = (
            installation / "snapshots" / "retained" if reason == "snapshot"
            else area / "copy" / "agent-codespaces"
        )
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-codespaces")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_CODESPACES_NO_SELFPROVISION",
            "pinned": "AGENT_CODESPACES_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS",
            "cell": "AGENT_CODESPACES_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-codespaces",
            legacy_root=installation, project_root=project,
        )
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"dispatcher", "dispatcher-array", "module-array", "provision-mode"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "dispatcher-array":
            manifest["payloadDispatcher"] = ["scripts/runtime-gate.ps1"]
        elif reason == "module-array":
            manifest["module"] = ["agent_codespaces"]
        else:
            manifest["provisionMode"] = "direct"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    action = "stamp-provision" if reason == "compound" else "provision"
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        command = native_command(root, payload, installation, project, client_only=reason == "client-only")
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(
                payload, installation, project, root=root, client_only=reason == "client-only",
                installer_action=action,
            )
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_codespaces_native_declaration_reaches_exact_source_check_without_writes(
    owning_codespaces, area, action,
):
    payload, installation, project = owning_codespaces
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64, "-InstallerAction", action]
        if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", action]
    )
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and not installation.exists()


def test_codespaces_empty_no_self_control_keeps_value_semantics(owning_codespaces, monkeypatch):
    payload, installation, project = owning_codespaces
    monkeypatch.setenv("AGENT_CODESPACES_NO_SELFPROVISION", "")
    assert provision.inspect(payload, installation, project)["plugin"] == "agent-codespaces"
    assert not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_codespaces_never_replaces_queued_source(owning_codespaces, area, change):
    payload, installation, project = owning_codespaces
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts" / "install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-codespaces"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def materialized_codespaces(owning_codespaces, area):
    payload, installation, project = owning_codespaces
    candidate = area / "materialized" / "plugins" / "agent-codespaces"
    shutil.copytree(
        REPOSITORY / "plugins" / "agent-codespaces", candidate,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
        ),
    )
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(REPOSITORY / "tools" / "materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=REPOSITORY)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    config = project / ".copilot-extensions" / "agent-codespaces" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("connection_owner:\n  enabled: false\n", encoding="utf-8")
    return payload, installation, project


def test_actual_codespaces_stamp_keeps_native_layout_and_defers_runtime(materialized_codespaces, area):
    payload, installation, project = materialized_codespaces
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.parent == installation / "snapshots" if os.name == "nt" else pointer == payload
    assert (pointer / "scripts" / "install.ps1").is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


@native_genesis
def test_actual_codespaces_cold_install_preserves_selection_and_installed_origin(
    materialized_codespaces, area, native_window_observation,
):
    payload, installation, project = materialized_codespaces
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(
            REPOSITORY / "libs" / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    root = area / "cold"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_CODESPACES_HOME"] = str(installation)
    isolate_disabled_owner(installation, project, env)

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in root.rglob("*.log")
        )

    def run_request(action):
        command = native_command(root, payload, installation, project, source=source)
        command += ["-Wait", "-InstallerAction", action] if os.name == "nt" else [
            "--wait", "--installer-action", action,
        ]
        return subprocess.run(
            command, env=env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=900, **no_window_kwargs(),
        )

    stamp = run_request("stamp")
    assert stamp.returncode == 0, stamp.stdout + stamp.stderr + diagnostic()
    installed = run_request("provision")
    assert installed.returncode == 0, installed.stdout + installed.stderr + diagnostic()
    requests = status(root=root)
    assert len(requests) == 2 and all(
        item.state == "completed" and item.exit_code == 0 for item in requests
    ), diagnostic()
    assert {json.loads(item.spec.source)["plugin"] for item in requests} == {"agent-codespaces"}
    if os.name == "nt":
        probes = installation.parent / "task-probes"
        assert probes.is_file(), diagnostic()
        assert probes.read_text() == "agent-codespaces-owner"
    assert not (installation.parent / ".config" / "systemd").exists()
    version = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / version
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    owner = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_codespaces", "owner", "--status"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert owner.returncode == 0, owner.stdout + owner.stderr
    assert json.loads(owner.stdout)["enabled"] is False
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"', project_file.read_text(),
    ), encoding="utf-8")
    (payload / "src" / "agent_codespaces" / "__init__.py").write_text(
        'raise RuntimeError("fixture failed installed CodeSpaces candidate")\n', encoding="utf-8",
    )
    failed = run_request("provision")
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    assert len(status(root=root)) == 3
    assert status(root=root)[-1].state == "completed" and status(root=root)[-1].exit_code != 0
    assert (installation / "current-version").read_text().strip() == version
    assert completion.read_bytes() == complete
    assert not (installation / "versions" / "99.0.0" / ".install-complete.json").exists()
    payload.rename(payload.with_name("removed-codespaces"))
    inputs.rename(area / "removed-foundation-source")
    command = installation.parent / ".local" / "bin" / (
        "agent-codespaces.ps1" if os.name == "nt" else "agent-codespaces"
    )
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command), "version"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(command), "version"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_CODESPACES_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert dispatch.returncode == 0, dispatch.stdout + dispatch.stderr + diagnostic()
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_codespaces,json; from importlib.metadata import version; "
         "from agent_codespaces._build_info import BUILD_INFO; "
         "print(json.dumps({'origin':agent_codespaces.__file__,'version':version('agent-codespaces'),"
         "'build':BUILD_INFO}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    identity = json.loads(origin.stdout)
    assert Path(identity["origin"]).is_relative_to(slot)
    assert str(Version(identity["build"]["version"])) == identity["version"]
    assert dispatch.stdout.strip() == (
        "agent-codespaces " + identity["build"]["version"]
        + " (" + identity["build"]["commit"][:8] + ")"
    )
    assert len(status(root=root)) == 3 and completion.read_bytes() == complete


def isolate_disabled_owner(installation, project, env):
    """Use the native adoption/config reader without exposing host registrations."""
    # Owner queries intentionally ignore CWD and read only adopted repositories.
    installation.mkdir()
    (installation / "adopted-repos.yaml").write_text(
        "repos:\n  - path: " + json.dumps(str(project)) + "\n", encoding="utf-8",
    )
    if os.name == "nt":
        # Never let the canonical disabled-config branch inspect a host task.
        module = Path(env["PSModulePath"]) / "ScheduledTasks"
        module.mkdir()
        (module / "ScheduledTasks.psm1").write_text(
            "function Get-ScheduledTask { param($TaskName) "
            "[IO.File]::AppendAllText((Join-Path $env:USERPROFILE 'task-probes'), $TaskName); $null }\n"
            "Export-ModuleMember -Function Get-ScheduledTask\n",
            encoding="utf-8",
        )


@pytest.mark.parametrize("state", ["enabled", "disabled", "unknown"])
def test_actual_codespaces_service_reconciliation_uses_isolated_native_adapters(area, state):
    scripts = REPOSITORY / "plugins" / "agent-codespaces" / "scripts"
    profile = area / "service-profile"
    profile.mkdir()
    log = area / "effects"
    if os.name == "nt":
        text = (scripts / "install.ps1").read_text(encoding="utf-8")
        functions = "".join(
            "function " + name + text.split("function " + name, 1)[1].split("\n}\n", 1)[0] + "\n}\n"
            for name in ("Get-ConnectionOwnerConfig", "Unregister-ConnectionOwnerService",
                         "Sync-ConnectionOwnerService")
        )
        query = area / "query.ps1"
        query.write_text("Write-Output '" + {
            "enabled": '{"enabled":true,"reconcile_interval":30}',
            "disabled": '{"enabled":false}', "unknown": "{}",
        }[state] + "'\nexit 0\n", encoding="utf-8")
        harness = area / "service.ps1"
        harness.write_text(
            "$ErrorActionPreference='Stop'\n"
            f"$LinkPython='{query}'\n$LocalBin='{profile}'\n$InstallDir='{profile}'\n"
            f"$OwnerTaskName='agent-codespaces-owner'\n$log='{log}'\n"
            "function Write-ServiceWarn { param($m) Add-Content $log ('WARN:'+ $m) }\n"
            "function Write-ServiceOk { param($m) }\n"
            "function Write-ServiceChanged { param($m) }\n"
            "function Get-ScheduledTask { param($TaskName) [pscustomobject]@{Actions=@()} }\n"
            "function Stop-ScheduledTask { param($TaskName) Add-Content $log 'stop' }\n"
            "function Unregister-ScheduledTask { param($TaskName,$Confirm) Add-Content $log 'remove' }\n"
            "function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) "
            "Add-Content $log $Argument; [pscustomobject]@{Execute=$Execute;Arguments=$Argument} }\n"
            "function New-ScheduledTaskTrigger { param([switch]$AtLogOn,$User) }\n"
            "function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) }\n"
            "function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,"
            "[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,"
            "$ExecutionTimeLimit,$RestartCount,$RestartInterval) }\n"
            "function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Principal,"
            "$Settings,[switch]$Force) Add-Content $log 'register' }\n"
            "function Start-ScheduledTask { param($TaskName) Add-Content $log 'start' }\n"
            + functions + "\nSync-ConnectionOwnerService\n",
            encoding="utf-8",
        )
        (profile / "agent-codespaces.ps1").write_text("exit 99\n", encoding="utf-8")
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        argv = [str(shell), "-NoProfile", "-NonInteractive", "-File", str(harness)]
    else:
        text = (scripts / "install.sh").read_text(encoding="utf-8")
        functions = "".join(
            name + "() {" + text.split(name + "() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
            for name in ("_owner_enabled", "_remove_owner_service", "_sync_owner_service")
        )
        query = area / "query.sh"
        query.write_text(
            '#!/usr/bin/env bash\nif [[ "$1" == -m ]]; then\n'
            + "printf '%s\\n' " + shlex.quote({
                "enabled": '{"enabled":true}', "disabled": '{"enabled":false}', "unknown": "{}",
            }[state]) + "\nelse\nexec " + shlex.quote(sys.executable) + ' "$@"\nfi\n',
            encoding="utf-8",
        )
        query.chmod(0o755)
        unit = profile / ".config" / "systemd" / "user" / "agent-codespaces-owner.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("retained-original", encoding="utf-8")
        harness = area / "service.sh"
        harness.write_text(
            "set -euo pipefail\n"
            f"HOME={shlex.quote(str(profile))}\nLINK_PYTHON={shlex.quote(str(query))}\n"
            f"VENV_PYTHON={shlex.quote(str(profile / 'bin' / 'python'))}\nINSTALL_DIR=$HOME\n"
            f"log={shlex.quote(str(log))}\nSYSTEMD_OWNER_UNIT=agent-codespaces-owner.service\n"
            'systemctl() { printf "%s\\n" "$*" >>"$log"; }\n'
            '_warn() { printf "WARN:%s\\n" "$*" >>"$log"; }\n'
            '_changed() { :; }\n_ok() { :; }\n_skip() { exit 98; }\n'
            + functions + "\n_sync_owner_service\n",
            encoding="utf-8",
        )
        argv = [shutil.which("bash"), str(harness)]
    result = subprocess.run(
        argv, env=dict(os.environ), cwd=profile, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    effects = log.read_text()
    if state == "unknown":
        assert "WARN:" in effects and "leaving any existing" in effects
        assert "remove" not in effects and "register" not in effects and "--user" not in effects
        if os.name != "nt":
            assert unit.read_text() == "retained-original"
    elif state == "disabled":
        assert ("stop\nremove\n" if os.name == "nt" else "--user disable --now") in effects
        if os.name != "nt":
            assert not unit.exists()
    else:
        assert ("register\nstart\n" if os.name == "nt" else "--user restart") in effects
        if os.name == "nt":
            assert '--headless "' in effects and 'agent-codespaces.ps1" owner' in effects
        else:
            assert f"ExecStart={profile}/bin/python -m agent_codespaces owner" in unit.read_text()
