"""SSH's fixed service-less actions do not adopt its ordinary publishers."""

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, drain, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis

pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="fixed native SSH recipe is qualified only on Windows and Linux",
)


@pytest.mark.parametrize("installer_action", ["provision", "stamp"])
def test_ssh_fixed_recipe_preserves_identity_custody_options_and_result(
    owning_ssh, area, monkeypatch, installer_action,
):
    payload, installation, project = owning_ssh
    root = area / "queue"
    request = provision.enqueue(
        payload, installation, project, root=root, installer_action=installer_action,
    )
    repeated = provision.enqueue(
        payload, installation, project, root=root, installer_action=installer_action,
    )
    assert request.id == repeated.id and not installation.exists()
    admission = json.loads(request.spec.source)
    assert admission["plugin"] == "agent-ssh"
    assert admission["payload"] == str(payload) and admission["runtime_root"] == str(installation)
    assert admission["project"] == str(project)
    assert request.runner.context_keys == tuple(sorted(provision.SSH_CONTEXT_KEYS))
    for key in ("ACTION", "DRY_RUN", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_SSH_" + key, "foreign-worker-value")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == installer_action
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-ssh" and frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "plugin.json").read_bytes() == (payload / "plugin.json").read_bytes()
    assert (installation / "client-only").read_text().strip() == "0"


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "noninstalled",
    "custom-root", "snapshot", "dispatcher", "dispatcher-array", "dispatcher-extra",
    "module-array", "provision-mode", "nested",
])
def test_ssh_refusal_precedes_acquisition_queue_and_plugin_writes(
    owning_ssh, area, monkeypatch, native, reason,
):
    payload, installation, project = owning_ssh
    root = area / "cold"
    if reason == "noninstalled":
        copied = area / "copy" / "agent-ssh"
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "snapshot":
        copied = installation / "snapshots" / "retained"
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-ssh")
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_SSH_NO_SELFPROVISION", "0")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_SSH_PINNED_CONTEXT", "{}")
    elif reason == "nested":
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "foreign")
    elif reason in {
        "dispatcher", "dispatcher-array", "dispatcher-extra", "module-array", "provision-mode",
    }:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "dispatcher-array":
            manifest["payloadDispatcher"]["windows"] = ["scripts/runtime-gate.ps1"]
        elif reason == "dispatcher-extra":
            manifest["payloadDispatcher"]["extra"] = "scripts/extra.ps1"
        elif reason == "module-array":
            manifest["module"] = ["agent_ssh"]
        else:
            manifest["provisionMode"] = "direct"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-ssh",
            legacy_root=installation, project_root=project,
        )
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        result = subprocess.run(
            native_command(root, payload, installation, project, client_only=reason == "client-only"),
            env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=60, **no_window_kwargs(),
        )
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(
                payload, installation, project, root=root, client_only=reason == "client-only",
            )
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.parametrize("installer_action", ["provision", "stamp"])
def test_ssh_native_admission_reaches_exact_source_check_without_writes(
    owning_ssh, area, installer_action,
):
    payload, installation, project = owning_ssh
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64,
         "-InstallerAction", installer_action] if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", installer_action]
    )
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and not installation.exists()


def test_ssh_empty_no_self_control_keeps_value_semantics(owning_ssh, monkeypatch):
    payload, installation, project = owning_ssh
    monkeypatch.setenv("AGENT_SSH_NO_SELFPROVISION", "")
    assert provision.inspect(payload, installation, project)["plugin"] == "agent-ssh"
    assert not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_ssh_source_change_never_replaces_accepted_input(owning_ssh, area, change):
    payload, installation, project = owning_ssh
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts" / "install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-ssh"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def materialized_ssh(owning_ssh, area):
    payload, installation, project = owning_ssh
    repository = Path(__file__).resolve().parents[3]
    candidate = area / "materialized" / "plugins" / "agent-ssh"
    shutil.copytree(
        repository / "plugins" / "agent-ssh", candidate,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
        ),
    )
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(repository / "tools" / "materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=repository)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    return payload, installation, project


def test_actual_ssh_materialized_stamp_preserves_source_and_defers_runtime(
    materialized_ssh, area,
):
    payload, installation, project = materialized_ssh
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    assert not installation.exists()
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.is_relative_to(installation / "snapshots")
    assert (pointer / "scripts" / "installer_engine.py").is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


@native_genesis
def test_actual_ssh_cold_provision_preserves_selection_and_source_independence(
    materialized_ssh, area, native_window_observation,
):
    payload, installation, project = materialized_ssh
    from update_driver.foundation_inputs import GENERATED, PACKAGES

    library = Path(__file__).resolve().parents[2]
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(
            library / name, inputs / name,
            ignore=lambda _path, names: {
                name for name in names if name in GENERATED
                or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
            },
        )
    source = inputs / "update-driver"
    root = area / "cold"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["AGENT_SSH_HOME"] = str(installation)
    tools = Path(env["PATH"])

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(root)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in root.rglob("*.log")
        )

    def run_request(action):
        command = native_command(root, payload, installation, project, source=source)
        command += (
            ["-Wait", "-InstallerAction", action] if os.name == "nt"
            else ["--wait", "--installer-action", action]
        )
        return subprocess.run(
            command, env=env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=900, **no_window_kwargs(),
        )

    for action in ("stamp", "provision"):
        result = run_request(action)
        assert result.returncode == 0, result.stdout + result.stderr + diagnostic()
    requests = status(root=root)
    assert len(requests) == 2 and all(
        item.state == "completed" and item.exit_code == 0 for item in requests
    ), diagnostic()
    assert {json.loads(item.spec.source)["plugin"] for item in requests} == {"agent-ssh"}
    assert {item.spec.argv for item in requests} == {(), ("--installer-action", "stamp")}
    version = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / version
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"', project_file.read_text(),
    ), encoding="utf-8")
    manifest_path = payload / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["version"] = "99.0.0"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (payload / "src" / "agent_ssh" / "__init__.py").write_text(
        'raise RuntimeError("fixture failed installed SSH candidate")\n', encoding="utf-8",
    )
    failed = run_request("provision")
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    requests = status(root=root)
    assert len(requests) == 3 and requests[-1].state == "completed"
    assert requests[-1].exit_code != 0, diagnostic()
    assert (installation / "current-version").read_text().strip() == version
    assert completion.read_bytes() == complete
    assert not (installation / "versions" / "99.0.0" / ".install-complete.json").exists()
    payload.rename(payload.with_name("removed-ssh"))
    inputs.rename(area / "removed-foundation-source")
    command = installation.parent / ".local" / "bin" / (
        "agent-ssh.ps1" if os.name == "nt" else "agent-ssh"
    )
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command), "version"]
        if os.name == "nt" else [str(tools / "bash"), str(command), "version"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_SSH_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert dispatch.returncode == 0, (
        dispatch.stdout, dispatch.stderr, diagnostic(),
    )
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_ssh,json; from importlib.metadata import version; "
         "print(json.dumps({'origin':agent_ssh.__file__,'version':version('agent-ssh')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    distribution = json.loads(origin.stdout)
    assert Path(distribution["origin"]).is_relative_to(slot)
    assert dispatch.stdout.strip() == "agent-ssh " + distribution["version"]
    assert len(status(root=root)) == 3 and completion.read_bytes() == complete
