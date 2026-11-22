"""Actual SSH commands retain stamp/snapshot/provision under one queued owner."""

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys

import agent_procutil
import pytest
from update_driver import foundation_status, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_pull_requests_command import command_for, files
from test_first_use_ssh import materialized_ssh as materialized_ssh
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt

pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux SSH commands",
)


def owning_command(fixture, entry, *, custom=False):
    payload, installation, project = fixture
    if entry == "primary":
        if custom and os.name == "nt":
            installation = installation.with_name("custom-ssh")
        installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
        command = command_for(installer)[:-1] + (
            ["-Action", "stamp", "-InstallDir", str(installation)] if os.name == "nt"
            else ["stamp", "--install-dir", str(installation)]
        )
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        source = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
        script = installation.parent / ".local" / "bin" / (
            "agent-ssh.ps1" if os.name == "nt" else "agent-ssh"
        )
        if custom and os.name != "nt":
            # POSIX's retained installer shifts custom options before staging.
            # Exercise the actual shared renderer's custom-root contract instead.
            installation = installation.with_name("custom-ssh")
            result = subprocess.run(
                ["/bin/bash", "-c",
                 '_ok() { printf "  [OK] %s\\n" "$1"; }; '
                 '_fail() { printf "  [FAIL] %s\\n" "$1" >&2; }; '
                 'source "$1"; write_simple_binstub agent-ssh agent_ssh "$2" "$3" '
                 '"$2/bin" scripts/install.sh AGENT_SSH_NO_SELFPROVISION '
                 '"$4/resolve-runtime.ps1" "$4/resolve-runtime.sh"',
                 "bash", str(source / "scripts" / "installer-engine.sh"),
                 str(installation), str(script.parent), str(source / "scripts")],
                env=dict(os.environ), cwd=project, capture_output=True, text=True,
                timeout=30, **agent_procutil.no_window_kwargs(),
            )
            assert result.returncode == 0, result.stdout + result.stderr
            (installation / "payload-dir").write_text(str(source), encoding="utf-8")
    else:
        source = payload
        script = payload / "bin" / ("agent-ssh.ps1" if os.name == "nt" else "agent-ssh")
    return source, installation, project, command_for(script)


@pytest.mark.parametrize("fixture", ["owning_payload", "owning_pull_requests"])
@pytest.mark.parametrize("native", [False, True])
def test_compound_ssh_recipe_cannot_adopt_another_plugin(request, area, fixture, native):
    payload, installation, project = request.getfixturevalue(fixture)
    root = area / "queue"
    if native:
        command = native_command(root, payload, installation, project) + (
            ["-InstallerAction", "stamp-provision"] if os.name == "nt"
            else ["--installer-action", "stamp-provision"]
        )
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=30, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode != 0
        assert "requires SSH" in result.stderr, result.stderr
    else:
        with pytest.raises(provision.ProvisionError, match="requires SSH"):
            provision.enqueue(
                payload, installation, project, root=root, installer_action="stamp-provision",
            )
    assert not root.exists() and not installation.exists()


@pytest.mark.parametrize("relative", ["plugin.json", "scripts/install.ps1", "libs/extra/data.txt"])
def test_stamp_composition_compares_all_inputs_except_canonical_sidecars(
    owning_ssh, area, relative, monkeypatch,
):
    payload, _, _ = owning_ssh
    snapshot = area / "snapshot"
    shutil.copytree(payload, snapshot)
    for sidecar in ("snapshot-provenance.json", ".source-payload-path", ".snapshot-version"):
        (snapshot / sidecar).write_text("new publication metadata", encoding="utf-8")
    (payload / "tests").mkdir()
    (payload / "tests" / "test_only.py").write_text("omitted by canonical stamp", encoding="utf-8")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.syspath_prepend(str(payload / "scripts"))
    expected = provision._ssh_stamp_hash(payload)
    assert provision._ssh_stamp_hash(snapshot) == expected
    target = snapshot / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("substituted snapshot input", encoding="utf-8")
    assert provision._ssh_stamp_hash(snapshot) != expected


@pytest.mark.parametrize(("entry", "reason"), [
    (entry, reason) for entry in ("primary", "payload")
    for reason in (
        "missing-driver", "old-driver", "no-self", "pinned", "nested", "managed",
        "malformed-policy", "old-resolver",
    )
] + [("primary", "old-dispatcher"), ("primary", "custom-root"), ("payload", "custom-root")])
def test_actual_ssh_cold_command_refusal_is_read_only(
    materialized_ssh, monkeypatch, entry, reason,
):
    source, installation, project, command = owning_command(
        materialized_ssh, entry, custom=entry == "primary" and reason == "custom-root",
    )
    if reason in {"missing-driver", "old-driver"}:
        driver = source / "libs" / "update-driver" / "scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )
        if reason == "missing-driver":
            driver.unlink()
        else:
            driver.write_text("throw 'Unqualified driver executed'\n" if os.name == "nt" else
                              "printf 'Unqualified driver executed' >&2\nexit 99\n", encoding="utf-8")
    elif reason == "no-self":
        monkeypatch.setenv("AGENT_SSH_NO_SELFPROVISION", "0")
    elif reason == "pinned":
        monkeypatch.setenv("AGENT_SSH_PINNED_CONTEXT", "{}")
    elif reason == "nested":
        monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "{}")
    elif reason == "custom-root" and entry == "payload":
        monkeypatch.setenv("AGENT_SSH_HOME", str(installation.with_name("custom-ssh")))
    elif reason in {"managed", "malformed-policy"}:
        api = primitive(source)
        arguments = dict(
            payload_root=source, plugin_id="agent-ssh", legacy_root=installation,
            project_root=project,
        )
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"old-dispatcher", "old-resolver"}:
        name = "runtime-gate" if reason == "old-dispatcher" else "resolve-runtime"
        directory = installation / "bin" if entry == "primary" and name == "resolve-runtime" else (
            source / "scripts"
        )
        (directory / (name + (".ps1" if os.name == "nt" else ".sh"))).write_text(
            "throw 'Unqualified helper executed'\n" if os.name == "nt" else
            "printf 'Unqualified helper executed' >&2\nexit 99\n", encoding="utf-8",
        )
    before, source_before = files(installation), files(source)
    root = installation.parent / ".copilot-extensions" / "update-driver"
    result = subprocess.run(
        command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
        timeout=90, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not root.exists(), result.stdout + result.stderr
    assert files(installation) == before and files(source) == source_before
    assert "Unqualified helper executed" not in result.stderr
    assert "Unqualified driver executed" not in result.stderr
    assert not (installation / ".provision.lock").exists()
    assert not (installation / ".provision-status").exists()


@native_genesis
@pytest.mark.parametrize("entry", ["payload", "primary"])
def test_actual_ssh_command_retains_whole_transaction_after_caller_loss(
    materialized_ssh, area, native_window_observation, entry,
):
    source, installation, project, command = owning_command(materialized_ssh, entry)
    original = materialized_ssh[0]
    if entry == "primary":
        original.rename(original.with_name("unavailable-installed-payload"))
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    root = installation.parent / ".copilot-extensions" / "update-driver"
    log = area / "command.log"
    caller = job = owner = descriptor = None
    follower = None
    follower_log = area / "follower.log"

    def diagnostic():
        return "\n".join(
            f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in (log, follower_log, *root.rglob("*.log")) if path.is_file()
        )

    def complete():
        receipts = {
            receipt for path in root.glob("first-use-*.log")
            if (receipt := ordinary_receipt(path)) is not None
        }
        assert len(receipts) <= 1, diagnostic()
        return bool(receipts) and status(next(iter(receipts)), root=root).state not in {
            "pending", "running",
        }

    def acknowledgement():
        prefix = "[installation-driver] launch acknowledgement: "
        return next((
            line.partition(prefix)[2] for line in log.read_text(errors="replace").splitlines(
                keepends=True,
            ) if line.startswith(prefix) and line.endswith("\n")
        ), None)

    with log.open("w", encoding="utf-8") as output, follower_log.open("w", encoding="utf-8") as second:
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
            if entry == "payload":
                follower = subprocess.Popen(
                    command, env=env, cwd=project, stdin=subprocess.DEVNULL,
                    stdout=second, stderr=second, **agent_procutil.no_window_kwargs(),
                )
            if job is not None:
                job.close()
                job = None
            else:
                os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=10)
            observe(complete, 900, diagnostic)
            results = status(root=root)
            assert len(results) == 1, diagnostic()
            result = results[0]
            assert result.state == "completed" and result.exit_code == 0, diagnostic()
            assert result.spec.argv == ("--installer-action", "stamp-provision")
            admission = json.loads(result.spec.source)
            assert admission["payload"] == str(source)
            assert admission["runtime_root"] == str(installation)
            assert admission["project"] == str(project)
            assert foundation_status(root=root).state == "completed"
            if follower is not None:
                assert follower.wait(timeout=30) == 0, diagnostic()
                assert "usage:" in follower_log.read_text(errors="replace").lower(), diagnostic()
            version = (installation / "current-version").read_text().strip()
            completion = installation / "versions" / version / ".install-complete.json"
            completion_bytes = completion.read_bytes()
            assert not (installation / ".provision.lock").exists()
            assert not (installation / ".provision-status").exists()
            for arguments, expected in [(["version"], 0), (["invalid-command"], 2)]:
                healthy = subprocess.run(
                    command[:-1] + arguments, env={
                        **env, "AGENT_SSH_NO_SELFPROVISION": "1", "AGENT_SSH_PINNED_CONTEXT": "{}",
                    }, cwd=project, capture_output=True, text=True, timeout=30,
                    **agent_procutil.no_window_kwargs(),
                )
                assert healthy.returncode == expected, healthy.stdout + healthy.stderr + diagnostic()
            assert len(status(root=root)) == 1 and completion.read_bytes() == completion_bytes
        finally:
            if job is not None:
                job.close()
            for process in (caller, follower):
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            if root.exists() and foundation_status(root=root) is not None:
                observe(lambda: foundation_status(root=root).state != "running", 900, diagnostic)
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


@native_genesis
def test_actual_ssh_command_records_failed_candidate_without_selecting_it(
    materialized_ssh, area, native_window_observation,
):
    source, installation, project, command = owning_command(materialized_ssh, "payload")
    (source / "src" / "agent_ssh" / "__init__.py").write_text(
        'raise RuntimeError("fixture failed installed SSH candidate")\n', encoding="utf-8",
    )
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    root = installation.parent / ".copilot-extensions" / "update-driver"
    result = subprocess.run(
        command, env=env, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=900, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    requests = status(root=root)
    assert len(requests) == 1
    failed = requests[0]
    assert failed.state == "completed" and failed.exit_code != 0, failed.log_path.read_text()
    assert "Fresh runtime slot failed its health gate" in failed.log_path.read_text()
    assert not (installation / "current-version").exists()
    assert not (installation / "last-known-good").exists()
    assert not list((installation / "versions").glob("*/.install-complete.json"))
    assert not (installation / ".provision.lock").exists()
    assert not (installation / ".provision-status").exists()
