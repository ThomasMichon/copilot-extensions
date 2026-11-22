"""Logger's real primary, payload and auxiliary commands share queued provision."""

import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

import agent_procutil
import pytest
from packaging.version import Version
from update_driver import foundation_status, status
from update_driver import first_use as provision

from test_first_use import primitive
from test_first_use_genesis import observe
from test_first_use_logger import (
    assert_no_periodic_worker, logger_registration_adapter,
    materialized_logger as materialized_logger, registration_effects,
)
from test_first_use_pull_requests_command import command_for, files
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import ordinary_receipt


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux", reason="native Windows/Linux Logger commands",
)

@pytest.fixture(autouse=True)
def discard_read_only_command_case(request, area):
    yield
    if request.node.originalname in {
        "test_logger_actual_command_refusal_precedes_cold_writes",
        "test_logger_selected_command_never_substitutes_installed_payload",
    }:
        assert not (area / "profile/.copilot-extensions/update-driver").exists(), (
            "Refusal case acquired installation state; retain it for diagnosis, not live-owner cleanup."
        )
        shutil.rmtree(area)


def owning_command(fixture, entry, *, name="agent-logger"):
    payload, installation, project = fixture
    if entry != "payload":
        installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
        stamp = command_for(installer)[:-1] + (
            ["-Action", "stamp", "-InstallDir", str(installation)] if os.name == "nt"
            else ["stamp", "--install-dir", str(installation)]
        )
        result = subprocess.run(
            stamp, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        source = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
        script = installation.parent / ".local/bin" / (
            name + ".ps1" if os.name == "nt" else name
        )
    else:
        source = payload
        script = payload / "bin" / (name + ".ps1" if os.name == "nt" else name)
    if entry == "cmd":
        assert os.name == "nt"
        command = [os.environ["ComSpec"], "/d", "/c", str(script.with_suffix(".cmd")), "--help"]
    else:
        command = command_for(script)
    return source, installation, project, command


@pytest.mark.parametrize("reason", [
    "missing-driver", "old-driver", "no-self", "pinned", "nested", "managed",
    "malformed-policy", "old-resolver", "custom-root",
])
def test_logger_actual_command_refusal_precedes_cold_writes(
    materialized_logger, monkeypatch, reason,
):
    source, installation, project, primary = owning_command(materialized_logger, "primary")
    suffix = ".ps1" if os.name == "nt" else ""
    commands = [
        primary,
        command_for(source / "bin" / ("agent-logger" + suffix)),
        command_for(installation.parent / ".local/bin" / ("collate-session" + suffix)),
    ]
    if reason in {"missing-driver", "old-driver"}:
        driver = source / "libs/update-driver/scripts" / (
            "first-use.ps1" if os.name == "nt" else "first-use.sh"
        )
        if reason == "missing-driver":
            driver.unlink()
        else:
            driver.write_text("throw 'Unqualified driver executed'\n" if os.name == "nt" else
                              "printf 'Unqualified driver executed' >&2\nexit 99\n", encoding="utf-8")
    elif reason in {"no-self", "pinned", "nested", "custom-root"}:
        key, value = {
            "no-self": ("AGENT_LOGGER_NO_SELFPROVISION", "0"),
            "pinned": ("AGENT_LOGGER_PINNED_CONTEXT", "{}"),
            "nested": ("UPDATE_DRIVER_WITNESS", "{}"),
            "custom-root": ("AGENT_LOGGER_HOME", str(installation.with_name("custom-logger"))),
        }[reason]
        monkeypatch.setenv(key, value)
    elif reason == "old-resolver":
        for directory in (installation / "bin", source / "scripts"):
            (directory / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")).write_text(
                "throw 'Unqualified resolver executed'\n" if os.name == "nt" else
                "printf 'Unqualified resolver executed' >&2\nexit 99\n", encoding="utf-8",
            )
    else:
        api = primitive(source)
        arguments = dict(payload_root=source, plugin_id="agent-logger",
                         legacy_root=installation, project_root=project)
        resolution = api.resolve_installation_mode(**arguments)
        installation.mkdir(exist_ok=True)
        policy = {
            "schema": api.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": api._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    before, source_before = files(installation), files(source)
    root = installation.parent / ".copilot-extensions/update-driver"
    for command in commands:
        result = subprocess.run(
            command, env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode != 0, (command, result.stdout, result.stderr)
        assert not root.exists(), result.stdout + result.stderr
        assert files(installation) == before and files(source) == source_before
        assert "Unqualified driver executed" not in result.stderr
        assert "Unqualified resolver executed" not in result.stderr
        assert not (installation / ".provision.lock").exists()
        assert not (installation / ".provision-status").exists()


@pytest.mark.parametrize("reason", ["old-dispatcher", "unselected", "unavailable", "malformed"])
def test_logger_selected_command_never_substitutes_installed_payload(
    materialized_logger, reason,
):
    source, installation, project, _ = owning_command(materialized_logger, "primary")
    if reason == "old-dispatcher":
        (source / "scripts" / ("runtime-gate.ps1" if os.name == "nt" else "runtime-gate.sh")).write_text(
            "throw 'Unqualified dispatcher executed'\n" if os.name == "nt" else
            "printf 'Unqualified dispatcher executed' >&2\nexit 99\n", encoding="utf-8",
        )
    elif reason == "unselected":
        (installation / "payload-dir").write_text(str(source.with_name("missing")), encoding="utf-8")
    elif reason == "unavailable":
        source.rename(source.with_name("unavailable"))
    else:
        (source / "snapshot-provenance.json").write_text("{", encoding="utf-8")
    before = files(installation)
    original = files(materialized_logger[0])
    for name, _module in provision.LOGGER_COMMANDS:
        script = installation.parent / ".local/bin" / (name + ".ps1" if os.name == "nt" else name)
        result = subprocess.run(
            command_for(script), env=dict(os.environ), cwd=project, capture_output=True, text=True,
            timeout=90, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode != 0, (name, result.stdout, result.stderr)
        assert "Unqualified dispatcher executed" not in result.stderr
        assert not (installation.parent / ".copilot-extensions/update-driver").exists()
        assert files(installation) == before and files(materialized_logger[0]) == original


@native_genesis
@pytest.mark.parametrize("entry", [
    "payload", "primary", pytest.param("cmd", marks=pytest.mark.skipif(os.name != "nt", reason="Windows cmd")),
])
def test_logger_actual_command_survives_observer_loss_and_keeps_all_consumers(
    materialized_logger, area, native_window_observation, entry, record_property,
):
    source, installation, project, command = owning_command(materialized_logger, entry)
    if entry != "payload":
        materialized_logger[0].rename(materialized_logger[0].with_name("unavailable-installed-payload"))
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    if entry == "cmd":
        native_shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0"
        env["PATH"] += os.pathsep + str(native_shell)
    adapter = logger_registration_adapter(env, area)
    root = installation.parent / ".copilot-extensions/update-driver"
    log, follower_log = area / "command.log", area / "follower.log"
    caller = job = owner = descriptor = follower = None
    timings = {"entry": entry, "healthy": []}

    def diagnostic():
        return "\n".join(
            f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
            for path in (log, follower_log, *root.rglob("*.log")) if path.is_file()
        )

    def complete():
        operation = foundation_status(root=root)
        if operation is not None:
            assert operation.state in {"running", "completed"}, diagnostic()
        receipts = {
            receipt for path in root.glob("first-use-*.log")
            if (receipt := ordinary_receipt(path)) is not None
        }
        assert len(receipts) <= 1, diagnostic()
        return bool(receipts) and status(next(iter(receipts)), root=root).state not in {"pending", "running"}

    def acknowledgement():
        prefix = "[installation-driver] launch acknowledgement: "
        return next((
            line.partition(prefix)[2] for line in log.read_text(errors="replace").splitlines(keepends=True)
            if line.startswith(prefix) and line.endswith("\n")
        ), None)

    with log.open("w", encoding="utf-8") as output, follower_log.open("w", encoding="utf-8") as second:
        try:
            started = time.monotonic()
            kwargs = dict(env=env, cwd=project, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                          **agent_procutil.no_window_kwargs())
            if os.name == "nt":
                caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                    command, require_job=True, allow_breakaway=False, **kwargs,
                )
            else:
                caller = subprocess.Popen(command, start_new_session=True, **kwargs)
            observe(lambda: foundation_status(root=root) is not None or caller.poll() is not None,
                    120, diagnostic)
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
            follower_script = source / "bin" / (
                "collate-session.ps1" if os.name == "nt" else "collate-session"
            )
            follower = subprocess.Popen(
                command_for(follower_script), env=env, cwd=project, stdin=subprocess.DEVNULL,
                stdout=second, stderr=second, **agent_procutil.no_window_kwargs(),
            )
            if job is not None:
                job.close()
                job = None
            else:
                os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=10)
            observe(complete, 900, diagnostic)
            timings["cold_seconds"] = time.monotonic() - started
            results = status(root=root)
            assert len(results) == 1, diagnostic()
            result = results[0]
            assert result.state == "completed" and result.exit_code == 0, diagnostic()
            assert result.spec.argv == ()
            admission = json.loads(result.spec.source)
            assert (admission["payload"], admission["runtime_root"], admission["project"]) == (
                str(source), str(installation), str(project),
            )
            assert foundation_status(root=root).state == "completed"
            assert follower.wait(timeout=30) == 0, diagnostic()
            assert "usage:" in follower_log.read_text(errors="replace").lower(), diagnostic()
            selected = (installation / "current-version").read_text().strip()
            slot = installation / "versions" / selected
            completion = slot / ".install-complete.json"
            completion_bytes = completion.read_bytes()
            installed = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            identity = subprocess.run(
                [str(installed), "-I", "-B", "-c",
                 "import agent_logger,json;from importlib.metadata import version;"
                 "print(json.dumps({'origin':agent_logger.__file__,'version':version('agent-logger')}))"],
                env=env, cwd=project, capture_output=True, text=True, timeout=30,
                **agent_procutil.no_window_kwargs(),
            )
            assert identity.returncode == 0, identity.stdout + identity.stderr
            actual = json.loads(identity.stdout)
            assert Path(actual["origin"]).is_relative_to(slot) and Version(actual["version"]) == Version(selected)
            manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
            assert manifest["commandLifecycle"]["owner"]["sourceFingerprint"] == admission["source_fingerprint"]
            assert ".install-stage" not in json.dumps(manifest["commandLifecycle"]["owner"])
            for name, _module in provision.LOGGER_COMMANDS:
                for base in (installation.parent / ".local/bin", source / "bin"):
                    script = base / (name + ".ps1" if os.name == "nt" else name)
                    started = time.monotonic()
                    consumer = subprocess.run(
                        command_for(script), env={**env, "AGENT_LOGGER_NO_SELFPROVISION": "1",
                                                  "AGENT_LOGGER_PINNED_CONTEXT": "{}"},
                        cwd=project, capture_output=True, text=True, timeout=30,
                        **agent_procutil.no_window_kwargs(),
                    )
                    assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), (
                        name, consumer.stdout, consumer.stderr, diagnostic(),
                    )
                    timings["healthy"].append({
                        "command": name, "entry": "compatibility" if base.name == "bin" and base.parent.name == ".local" else "payload",
                        "seconds": time.monotonic() - started,
                    })
            healthy = subprocess.run(
                command[:-1] + ["invalid-command"], env=env, cwd=project, capture_output=True,
                text=True, timeout=30, **agent_procutil.no_window_kwargs(),
            )
            assert healthy.returncode == 2 and healthy.stderr, healthy.stdout + healthy.stderr
            assert len(status(root=root)) == 1 and completion.read_bytes() == completion_bytes
            assert not (installation / ".provision.lock").exists()
            assert not (installation / ".provision-status").exists()
            assert all(effect["operation"] == "stop" for effect in registration_effects(adapter))
            assert_no_periodic_worker(installation)
            assert not (installation / "logs/session-sync.log").exists()
            record_property("logger_command_timings", json.dumps(timings, sort_keys=True))
            print("Logger command timings: " + json.dumps(timings, sort_keys=True))
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
def test_logger_actual_command_records_failed_candidate_without_dispatching_it(
    materialized_logger, area, native_window_observation,
):
    source, installation, project, command = owning_command(materialized_logger, "payload")
    (source / "src/agent_logger/__init__.py").write_text(
        'raise RuntimeError("fixture failed installed Logger candidate")\n', encoding="utf-8",
    )
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    adapter = logger_registration_adapter(env, area)
    root = installation.parent / ".copilot-extensions/update-driver"
    result = subprocess.run(
        command, env=env, cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=900, **agent_procutil.no_window_kwargs(),
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
    assert all(effect["operation"] == "stop" for effect in registration_effects(adapter))
    assert_no_periodic_worker(installation)
