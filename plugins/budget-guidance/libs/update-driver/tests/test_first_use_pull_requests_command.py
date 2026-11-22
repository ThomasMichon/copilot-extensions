"""Actual generated Pull Requests command admission and native cold execution."""

import json
import os
from pathlib import Path
import signal
import subprocess

import pytest
import agent_procutil
from update_driver import foundation_status, status

from test_first_use_genesis import observe
from test_first_use_pull_requests import materialized_pull_requests as materialized_pull_requests
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt


def command_for(script):
    if os.name == "nt":
        return [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), "--help",
        ]
    return ["/bin/bash", str(script), "--help"]


def owning_command(fixture, entry, *, custom=False):
    payload, installation, project = fixture
    if entry == "primary":
        if custom:
            installation = installation.with_name("custom-pull-requests")
        script = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
        stamp = command_for(script)[:-1]
        stamp += (
            ["-Action", "stamp", "-InstallDir", str(installation)] if os.name == "nt"
            else ["stamp", "--install-dir", str(installation)]
        )
        result = subprocess.run(
            stamp, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        source = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
        script = installation.parent / ".local" / "bin" / (
            "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
        )
    else:
        source = payload
        script = payload / "bin" / (
            "agent-pull-requests.ps1" if os.name == "nt" else "agent-pull-requests"
        )
    return source, installation, project, command_for(script)


def files(root):
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize(("entry", "reason"), [
    (entry, reason) for entry in ("primary", "payload")
    for reason in ("missing-driver", "no-self", "pinned", "nested", "managed", "old-resolver")
] + [("primary", "old-dispatcher"), ("primary", "custom-root")])
def test_actual_cold_commands_refuse_without_foreground_or_plugin_writes(
    materialized_pull_requests, monkeypatch, entry, reason,
):
    source, installation, project, command = owning_command(
        materialized_pull_requests, entry, custom=reason == "custom-root",
    )
    if reason == "missing-driver":
        (source / "libs" / "update-driver" / "scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )).unlink()
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_NO_SELFPROVISION", "1" if os.name == "nt" else "")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_PULL_REQUESTS_PINNED_CONTEXT", "{}")
    elif reason == "nested":
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "{}")
    elif reason == "managed":
        from test_first_use import primitive
        api = primitive(source)
        arguments = dict(
            payload_root=source, plugin_id="agent-pull-requests",
            legacy_root=installation, project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        (installation / "runtime-deployment.json").write_text(json.dumps({
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        }), encoding="utf-8")
    elif reason in {"old-dispatcher", "old-resolver"}:
        name = "invoke-payload-runtime" if reason == "old-dispatcher" else "resolve-runtime"
        directory = installation / "bin" if entry == "primary" and name == "resolve-runtime" else (
            source / "scripts"
        )
        (directory / (name + (".ps1" if os.name == "nt" else ".sh"))).write_text(
            "throw 'Unqualified helper must not execute'\n" if os.name == "nt" else
            "#!/bin/bash\nprintf 'Unqualified helper executed' >&2\nexit 99\n",
            encoding="utf-8",
        )
    before = files(installation)
    payload_before = files(source)
    root = installation.parent / ".copilot-extensions" / "update-driver"
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
        timeout=90, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not root.exists(), result.stdout + result.stderr
    assert files(installation) == before
    assert files(source) == payload_before
    assert not (installation / ".provision.lock").exists()
    assert not (installation / ".provision-status").exists()
    assert "Unqualified helper executed" not in result.stderr


@native_genesis
@pytest.mark.parametrize("entry", ["payload", "primary"])
def test_actual_generated_command_owns_cold_install_and_survives_caller_loss(
    materialized_pull_requests, area, native_window_observation, entry,
):
    source, installation, project, command = owning_command(materialized_pull_requests, entry)
    original = materialized_pull_requests[0]
    if entry == "primary":
        original.rename(original.with_name("unavailable-installed-payload"))
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    root = installation.parent / ".copilot-extensions" / "update-driver"
    assert not root.exists()
    log = area / "command.log"
    caller = None
    job = None
    owner = None
    descriptor = None

    def diagnostic():
        return "\n".join(
            f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in (log, *root.rglob("*.log")) if path.is_file()
        )

    def complete():
        receipts = [
            receipt for path in root.glob("first-use-*.log")
            if (receipt := ordinary_receipt(path)) is not None
        ]
        assert len(receipts) <= 1, diagnostic()
        if not receipts:
            return False
        return status(receipts[0], root=root).state not in {"pending", "running"}

    def acknowledgement():
        prefix = "[installation-driver] launch acknowledgement: "
        return next((
            line.partition(prefix)[2] for line in log.read_text(errors="replace").splitlines(
                keepends=True,
            ) if line.startswith(prefix) and line.endswith("\n")
        ), None)

    with log.open("w", encoding="utf-8") as output:
        try:
            kwargs = dict(
                env=env, cwd=project, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                **agent_procutil.no_window_kwargs(),
            )
            if os.name == "nt":
                caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                    command, require_job=True, allow_breakaway=False, **kwargs,
                )
            else:
                caller = subprocess.Popen(command, start_new_session=True, **kwargs)
            observe(
                lambda: foundation_status(root=root) is not None or caller.poll() is not None,
                120, diagnostic,
            )
            operation = foundation_status(root=root)
            assert operation is not None and operation.state == "running", diagnostic()
            observe(lambda: acknowledgement() is not None or caller.poll() is not None, 30, diagnostic)
            frame = acknowledgement()
            assert frame is not None, diagnostic()
            if os.name == "nt":
                from test_foundation_genesis_process import pinned_worker
                owner = pinned_worker(json.loads(frame))
                assert owner.poll() is None, diagnostic()
            else:
                from agent_procutil._caller_owned import _pidfd_exited
                from agent_procutil._linux_pidfd import open_pidfd
                descriptor = open_pidfd(int(frame.split()[0].partition(":")[2]))
                assert not _pidfd_exited(descriptor), diagnostic()
            if entry == "primary":
                if job is not None:
                    job.close()
                    job = None
                else:
                    os.killpg(caller.pid, signal.SIGKILL)
                caller.wait(timeout=10)
            observe(complete, 900, diagnostic)
            requests = status(root=root)
            assert len(requests) == 1, diagnostic()
            result = requests[0]
            assert result.state == "completed" and result.exit_code == 0, diagnostic()
            assert result.spec.argv == (), "ordinary commands must retain the default provision recipe"
            admission = json.loads(result.spec.source)
            assert admission["payload"] == str(source)
            assert admission["runtime_root"] == str(installation)
            assert admission["project"] == str(project)
            assert foundation_status(root=root).state == "completed"
            if entry == "payload":
                assert caller.wait(timeout=30) == 0, diagnostic()
                assert "usage:" in log.read_text(errors="replace").lower(), diagnostic()
            version = (installation / "current-version").read_text().strip()
            completion = installation / "versions" / version / ".install-complete.json"
            complete_bytes = completion.read_bytes()
            assert not (installation / ".provision.lock").exists()
            healthy = subprocess.run(
                command, env={**env, "AGENT_PULL_REQUESTS_NO_SELFPROVISION": "1"},
                cwd=project, capture_output=True, text=True, timeout=30,
                **agent_procutil.no_window_kwargs(),
            )
            assert healthy.returncode == 0 and "usage:" in healthy.stdout.lower(), (
                healthy.stdout + healthy.stderr + diagnostic()
            )
            assert len(status(root=root)) == 1 and completion.read_bytes() == complete_bytes
        finally:
            if job is not None:
                job.close()
            if caller is not None and caller.poll() is None:
                if os.name == "nt":
                    caller.kill()
                else:
                    os.killpg(caller.pid, signal.SIGKILL)
                caller.wait(timeout=10)
            if root.exists() and foundation_status(root=root) is not None:
                observe(
                    lambda: foundation_status(root=root).state != "running", 900, diagnostic,
                )
            if owner is not None:
                try:
                    owner.wait(timeout=900)
                finally:
                    owner.close()
            if descriptor is not None:
                try:
                    observe(lambda: _pidfd_exited(descriptor), 900, diagnostic)
                finally:
                    os.close(descriptor)
