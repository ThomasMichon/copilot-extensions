"""Actual Pull Requests hook deadline, publication admission and caller loss."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import agent_procutil
import pytest
from update_driver import foundation_status, status

from test_first_use import primitive
from test_first_use_genesis import observe
from test_first_use_pull_requests import materialized_pull_requests as materialized_pull_requests
from test_first_use_pull_requests_command import command_for, files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt, owned_hook, receipt_completed_count


def hook_command(payload, project, native_directory):
    hook = payload / "scripts" / (
        "bootstrap-check.admission.ps1" if os.name == "nt" else "bootstrap-check.admission.sh"
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
@pytest.mark.parametrize("reason", ["managed", "no-self", "pinned", "nested", "missing-driver"])
def test_actual_bootstrap_refuses_without_queue_or_installation_writes(
    materialized_pull_requests, monkeypatch, phase, reason,
):
    payload, installation, project = materialized_pull_requests
    if phase == "drift":
        installation.mkdir()
        (installation / "deploy-manifest.json").write_text(
            '{"source":{"version":"0.0.0"}}', encoding="utf-8",
        )
        (installation / "reconcile.lock").write_text("424242", encoding="utf-8")
    if reason == "managed":
        api = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-pull-requests",
            legacy_root=installation, project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        }), encoding="utf-8")
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_NO_SELFPROVISION", "1" if os.name == "nt" else "")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_PINNED_CONTEXT", "{}")
    elif reason == "nested":
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "{}")
    else:
        (payload / "libs" / "update-driver" / "scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )).unlink()
    before, source = files(installation), files(payload)
    root = installation.parent / ".copilot-extensions" / "update-driver"
    result = subprocess.run(
        hook_command(payload, project, installation.parent), env=dict(os.environ), cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == (0 if reason in {"no-self", "pinned"} else 3), (
        result.stdout + result.stderr
    )
    assert json.loads(result.stdout) == {} and result.stderr
    assert not root.exists() and files(installation) == before and files(payload) == source


@native_genesis
def test_actual_bootstrap_deadline_caller_loss_and_healthy_reentry(
    materialized_pull_requests, area, native_window_observation,
):
    payload, installation, project = materialized_pull_requests
    profile = installation.parent
    root = profile / ".copilot-extensions" / "update-driver"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(profile / "bootstrap-killswitch.json")
    if os.name != "nt":
        # Preserve the legacy hook's existing metadata-reader prerequisite.
        (Path(env["PATH"]) / "python3").symlink_to(sys._base_executable)
    deadline = json.loads((payload / "hooks.json").read_text())["hooks"]["sessionStart"][0]["timeoutSec"]
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
            acknowledgement_prefix="[agent-pull-requests] driver launch acknowledgement: ",
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
            request = status(receipt, root=root)
            admission = json.loads(request.spec.source)
            assert admission.get("installer_action", "provision") == action
            assert Path(admission["payload"]) == payload
            assert Path(admission["runtime_root"]) == installation
            assert Path(admission["project"]) == project
            assert request.spec.argv == (("--installer-action", "stamp") if action == "stamp" else ())
            assert foundation_status(root=root).operation_id == operation
            previous.add(receipt)
            observe(caller.exited, 60, diagnostic)
        if count == 1:
            assert not (installation / "current-version").exists()
            assert not list((installation / "versions").glob("*/pyvenv.cfg"))
            # Stamping does not write a deployment manifest. As before, the
            # actual first-use command, not a later hook, builds that runtime.
            payload_command = payload / "bin" / (
                "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
            )
            provision = subprocess.run(
                command_for(payload_command), env=env, cwd=project, capture_output=True,
                text=True, timeout=850, **agent_procutil.no_window_kwargs(),
            )
            assert provision.returncode == 0 and "usage:" in provision.stdout.lower(), (
                provision.stdout + provision.stderr + diagnostic()
            )
            requests = status(root=root)
            assert len(requests) == 2 and all(
                item.state == "completed" and item.exit_code == 0 for item in requests
            ), diagnostic()
            first_use = next(item for item in requests if item.id not in previous)
            assert first_use.spec.argv == ()
            first_admission = json.loads(first_use.spec.source)
            assert Path(first_admission["payload"]) == payload
            assert Path(first_admission["runtime_root"]) == installation
            assert Path(first_admission["project"]) == project
            previous.add(first_use.id)
            count = 2
        selected = (installation / "current-version").read_text().strip()
        if count == 3:
            assert selected == "99.0.0"
        completion = installation / "versions" / selected / ".install-complete.json"
        completed = completion.read_bytes()
        healthy = subprocess.run(
            command, env=env, cwd=project, capture_output=True, text=True,
            timeout=deadline, **agent_procutil.no_window_kwargs(),
        )
        assert healthy.returncode == 0 and json.loads(healthy.stdout) == {}, (
            healthy.stdout + healthy.stderr + diagnostic()
        )
        assert not healthy.stderr and len(status(root=root)) == count
        assert completion.read_bytes() == completed
    primary = profile / ".local" / "bin" / (
        "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
    )
    healthy = subprocess.run(
        command_for(primary), env={**env, "AGENT_PULL_REQUESTS_NO_SELFPROVISION": "1"},
        cwd=project, capture_output=True, text=True, timeout=30, **agent_procutil.no_window_kwargs(),
    )
    assert healthy.returncode == 0 and "usage:" in healthy.stdout.lower(), healthy.stdout + healthy.stderr
    assert len(status(root=root)) == 3 and completion.read_bytes() == completed
    assert not (installation / "reconcile.lock").exists()
