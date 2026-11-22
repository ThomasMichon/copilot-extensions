"""Actual SSH hook admission, deadline and canonical caller-independent results."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import agent_procutil
import pytest
from update_driver import foundation_status, status

from test_first_use import primitive
from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_pull_requests_command import files
from test_first_use_ssh import materialized_ssh as materialized_ssh
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt, owned_hook, receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux SSH bootstrap",
)


def hook_command(payload, project, native_directory):
    hook = payload / "scripts" / (
        "bootstrap-check.ps1" if os.name == "nt" else "bootstrap-check.sh"
    )
    if os.name != "nt":
        return ["/bin/bash", str(hook)]
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    return [
        str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
        "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
        f"Set-Location -LiteralPath {quote(project)}; "
        f"[IO.Directory]::SetCurrentDirectory({quote(native_directory)}); "
        f"& {quote(hook)}; exit $LASTEXITCODE",
    ]


@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("reason", [
    "managed", "no-self", "pinned", "nested", "missing-driver", "noninstalled", "malformed",
])
def test_actual_ssh_bootstrap_refuses_before_queue_or_installation_writes(
    materialized_ssh, monkeypatch, area, phase, reason,
):
    payload, installation, project = materialized_ssh
    hook_payload = payload
    if reason == "noninstalled":
        copied = area / "copy" / "agent-ssh"
        shutil.copytree(payload, copied)
        if phase == "cold":
            hook_payload = copied
        payload = copied
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(json.dumps({
            "source": {"version": "0.0.0", "path": str(payload)},
        }), encoding="utf-8")
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    if reason in {"managed", "malformed"}:
        api = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-ssh",
            legacy_root=installation, project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_SSH_NO_SELFPROVISION", "0")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_SSH_PINNED_CONTEXT", "{}")
    elif reason == "nested":
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "{}")
    elif reason == "missing-driver":
        (payload / "libs" / "update-driver" / "scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )).unlink()
    before, source = files(installation), files(payload)
    root = installation.parent / ".copilot-extensions" / "update-driver"
    result = subprocess.run(
        hook_command(hook_payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == (0 if reason in {"no-self", "pinned"} else 3), (
        result.stdout + result.stderr
    )
    assert result.stdout == "{}" and result.stderr
    assert not root.exists() and files(installation) == before and files(payload) == source


@pytest.mark.parametrize("source", ["unavailable", "snapshot"])
def test_ssh_bootstrap_never_reselects_current_payload(materialized_ssh, source):
    payload, installation, project = materialized_ssh
    installation.mkdir()
    selected = installation / "snapshots" / "retained"
    if source == "snapshot":
        shutil.copytree(payload, selected)
    (installation / "deploy-manifest.json").write_text(json.dumps({
        "source": {"version": "0.0.0", "path": str(selected)},
    }), encoding="utf-8")
    before = files(installation)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 3 and result.stdout == "{}" and result.stderr
    assert files(installation) == before
    assert not (installation.parent / ".copilot-extensions" / "update-driver").exists()


@pytest.mark.parametrize("reason", ["namespaced", "killswitch"])
def test_actual_ssh_bootstrap_retains_nonpublication_noops(materialized_ssh, monkeypatch, reason):
    payload, installation, project = materialized_ssh
    if reason == "namespaced":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{}")
    else:
        guard = payload / "scripts" / (
            "bootstrap-killswitch-guard.ps1" if os.name == "nt" else "bootstrap-killswitch-guard.sh"
        )
        # Isolate the guard decision without writing global killswitch state.
        guard.write_text("exit 0\n", encoding="utf-8")
    before = files(installation)
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}" and not result.stderr
    assert files(installation) == before
    assert not (installation.parent / ".copilot-extensions" / "update-driver").exists()


@native_genesis
def test_actual_ssh_bootstrap_deadline_caller_loss_and_healthy_reentry(
    materialized_ssh, area, native_window_observation,
):
    payload, installation, project = materialized_ssh
    profile = installation.parent
    root = profile / ".copilot-extensions" / "update-driver"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    deadline = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"][0]["timeoutSec"]
    assert deadline == 15
    command = hook_command(payload, project, profile)

    def diagnostic():
        return "\n".join(
            f"{path.relative_to(area)}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in area.rglob("*")
            if path.is_file() and (path.suffix == ".log" or path.name in {"stdout", "stderr"})
        )

    previous = set()
    for count, action in ((1, "stamp"), (3, "provision")):
        if count == 3:
            project_file = payload / "pyproject.toml"
            project_file.write_text(re.sub(
                r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"',
                project_file.read_text(),
            ), encoding="utf-8")
            manifest = json.loads((payload / "plugin.json").read_text())
            manifest["version"] = "99.0.0"
            (payload / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        with owned_hook(
            command, env=env, project=project, directory=area / f"hook-{count}",
            timeout=deadline, diagnostic=diagnostic,
            acknowledgement_prefix="[agent-ssh] driver launch acknowledgement: ",
        ) as caller:
            observe(lambda: foundation_status(root=root) is not None, 90, diagnostic)
            operation = foundation_status(root=root).operation_id
            if count == 1:
                assert foundation_status(root=root).state == "running" and not caller.exited(), diagnostic()
            caller.close_caller()
            if count == 1:
                assert not caller.exited(), diagnostic()
            observe(
                lambda: receipt_completed_count(root, count, caller.receipt_log) or caller.exited(),
                850, diagnostic,
            )
            receipt = ordinary_receipt(caller.receipt_log)
            assert receipt and receipt not in previous, diagnostic()
            requests = status(root=root)
            assert len(requests) == count and all(
                item.state == "completed" and item.exit_code == 0 for item in requests
            ), diagnostic()
            admission = json.loads(status(receipt, root=root).spec.source)
            assert admission.get("installer_action", "provision") == action
            assert Path(admission["payload"]) == payload
            assert Path(admission["runtime_root"]) == installation
            assert Path(admission["project"]) == project
            assert admission["plugin"] == "agent-ssh"
            assert foundation_status(root=root).operation_id == operation
            previous.add(receipt)
            observe(caller.exited, 60, diagnostic)
        if count == 1:
            assert not (installation / "current-version").exists()
            assert not list((installation / "versions").glob("*/pyvenv.cfg"))
            # Prepare through the already-qualified recipe, not the unadopted command.
            provision_command = native_command(
                root, payload, installation, project, source=payload / "libs" / "update-driver",
            )
            provision_command += ["-Wait"] if os.name == "nt" else ["--wait"]
            provision = subprocess.run(
                provision_command, env=env, cwd=project, capture_output=True, text=True,
                timeout=850, **agent_procutil.no_window_kwargs(),
            )
            assert provision.returncode == 0, provision.stdout + provision.stderr + diagnostic()
            deployed = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
            assert Path(deployed["source"]["path"]) == payload
            requests = status(root=root)
            assert len(requests) == 2 and all(
                item.state == "completed" and item.exit_code == 0 for item in requests
            ), diagnostic()
            previous.update(item.id for item in requests)
            count = 2
            if os.name != "nt":
                # The first stamp was genuinely Python-free; drift retains its reader.
                (Path(env["PATH"]) / "python3").symlink_to(sys._base_executable)
        selected = (installation / "current-version").read_text().strip()
        if count == 3:
            assert selected == "99.0.0"
        completion = installation / "versions" / selected / ".install-complete.json"
        completed = completion.read_bytes()
        healthy = subprocess.run(
            command, env=env, cwd=project, capture_output=True, text=True,
            timeout=deadline, **agent_procutil.no_window_kwargs(),
        )
        assert healthy.returncode == 0 and healthy.stdout == "{}" and not healthy.stderr, (
            healthy.stdout + healthy.stderr + diagnostic()
        )
        assert len(status(root=root)) == count and completion.read_bytes() == completed
    assert not (installation / "reconcile.lock").exists()
    primary = profile / ".local" / "bin" / (
        "agent-ssh.ps1" if os.name == "nt" else "agent-ssh"
    )
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(primary), "version"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(primary), "version"]
    )
    dispatch = subprocess.run(
        argv, env={**env, "AGENT_SSH_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert dispatch.returncode == 0, dispatch.stdout + dispatch.stderr + diagnostic()
    slot = installation / "versions" / selected
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    origin = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_ssh,json; from importlib.metadata import version; "
         "print(json.dumps({'origin':agent_ssh.__file__,'version':version('agent-ssh')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert origin.returncode == 0, origin.stdout + origin.stderr
    distribution = json.loads(origin.stdout)
    assert Path(distribution["origin"]).is_relative_to(slot)
    assert dispatch.stdout.strip() == "agent-ssh " + distribution["version"]
    assert len(status(root=root)) == 3 and completion.read_bytes() == completed
