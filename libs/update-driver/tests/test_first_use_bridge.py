"""Bridge's literal legacy recipes retain native service and construction owners."""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import runpy
import re
import shutil
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, drain, status
from update_driver import foundation_status
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive
from test_first_use_native import native_command
from test_first_use_genesis import observe
from test_first_use_logger import registration_effects
from test_first_use_vault import cold_environment, native_genesis


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Bridge recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


def set_payload_version(payload, version):
    project = payload / "pyproject.toml"
    project.write_text(
        re.sub(r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"', project.read_text()),
        encoding="utf-8",
    )
    identity = payload / "plugin.json"
    manifest = json.loads(identity.read_text())
    manifest["version"] = version
    identity.write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
def test_bridge_fixed_action_retains_context_staging_and_native_result(
    owning_bridge, area, monkeypatch, action,
):
    payload, installation, project = owning_bridge
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    assert request.runner.context_keys == tuple(sorted(provision.BRIDGE_CONTEXT_KEYS))
    for key in ("ACTION", "DRY_RUN", "FORCE", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_BRIDGE_" + key, "foreign")
    for key in ("COPILOT_PROJECT_DIR", "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
                "AGENT_BRIDGE_CONFIG_DIR", "AGENT_BRIDGE_INSTALL_DIR"):
        monkeypatch.setenv(key, str(area / "foreign"))
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "client-only").read_text().strip() == "0"
    assert Path((installation / "project-path").read_text()) == project
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-bridge" and frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "payload-invocation.json").read_bytes() == (payload / "payload-invocation.json").read_bytes()
    admission = json.loads(request.spec.source)
    assert (admission["plugin"], admission["payload"], admission["runtime_root"], admission["project"]) == (
        "agent-bridge", str(payload), str(installation), str(project),
    )


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "copy", "custom-root",
    "snapshot", "dispatcher", "module", "provision-mode", "compound",
    "nested", "cell", "missing-helper", "missing-native-ps1", "missing-native-sh",
])
def test_bridge_refusal_precedes_acquisition_queue_and_plugin_writes(
    owning_bridge, area, monkeypatch, native, action, reason,
):
    payload, installation, project = owning_bridge
    if reason in {"copy", "snapshot"}:
        copied = installation / "snapshots/retained" if reason == "snapshot" else area / "copy/agent-bridge"
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-bridge")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_BRIDGE_NO_SELFPROVISION", "pinned": "AGENT_BRIDGE_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_BRIDGE_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-bridge",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason in {"missing-helper", "missing-native-ps1", "missing-native-sh"}:
        name = {
            "missing-helper": "versioned_runtime_publication.py",
            "missing-native-ps1": "installer-engine.ps1",
            "missing-native-sh": "installer-engine.sh",
        }[reason]
        (payload / "scripts" / name).unlink()
    elif reason in {"dispatcher", "module", "provision-mode"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "module":
            manifest["module"] = "foreign_module"
        else:
            manifest["provisionMode"] = "snapshot"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    if reason == "compound":
        action = "stamp-provision"
    root = area / "cold"
    before = {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()}
    source = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    if native:
        command = native_command(root, payload, installation, project, client_only=reason == "client-only")
        command += ["-InstallerAction", action] if os.name == "nt" else ["--installer-action", action]
        result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, **no_window_kwargs())
        assert result.returncode != 0, result.stdout + result.stderr
    else:
        with pytest.raises(provision.ProvisionError):
            provision.enqueue(payload, installation, project, root=root,
                              client_only=reason == "client-only", installer_action=action)
    assert not root.exists()
    assert {path: path.read_bytes() for path in installation.rglob("*") if path.is_file()} == before
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == source


@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
def test_bridge_native_admits_exact_recipe_before_source_comparison(owning_bridge, area, action):
    payload, installation, project = owning_bridge
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    command += (
        ["-Mode", "owner", "-ExpectedSource", "sha256:" + "0" * 64, "-InstallerAction", action]
        if os.name == "nt" else
        ["--run-owner", "sha256:" + "0" * 64, "--installer-action", action]
    )
    result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=60, **no_window_kwargs())
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, result.stderr
    assert not root.exists() and not installation.exists()


def test_bridge_install_action_is_distinct_and_native_support_is_declared(owning_bridge, area):
    payload, installation, project = owning_bridge
    root = area / "queue"
    actions = ("stamp", "provision", "install")
    requests = [
        provision.enqueue(payload, installation, project, root=root, installer_action=action)
        for action in actions
    ]
    assert len({request.id for request in requests}) == len(actions)
    assert [json.loads(request.spec.source).get("installer_action", "provision")
            for request in requests] == list(actions)
    for suffix in ("ps1", "sh"):
        native = REPOSITORY / f"libs/update-driver/scripts/first-use.{suffix}"
        assert "# Installation driver lifecycle contract: bridge-install-v1" in native.read_text()
    assert not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_bridge_never_substitutes_accepted_source(owning_bridge, area, change):
    payload, installation, project = owning_bridge
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-bridge"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="native GNU installer tool admission")
@pytest.mark.parametrize("native", [False, True])
def test_bridge_missing_fingerprint_tool_refuses_before_acquisition(
    owning_bridge, area, monkeypatch, native,
):
    payload, installation, project = owning_bridge
    root = area / "cold"
    env = cold_environment(installation, project, area)
    assert not (Path(env["PATH"]) / "xargs").exists()
    command = native_command(root, payload, installation, project)
    if native:
        result = subprocess.run(command, env=env, cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=30, **no_window_kwargs())
        assert result.returncode != 0 and "Bridge first use requires native tool: xargs" in result.stderr
    else:
        monkeypatch.setenv("PATH", env["PATH"])
        with pytest.raises(provision.ProvisionError, match="Bridge first use requires native tool: xargs"):
            provision.enqueue(payload, installation, project, root=root)
    assert not root.exists() and not installation.exists()


@pytest.fixture
def materialized_bridge(owning_bridge, area):
    payload, installation, project = owning_bridge
    candidate = area / "materialized/plugins/agent-bridge"
    shutil.copytree(REPOSITORY / "plugins/agent-bridge", candidate, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", "tests", "build", "dist", "*.egg-info",
    ))
    previous = sys.path[:]
    try:
        release = runpy.run_path(str(REPOSITORY / "tools/materialize_main.py"))
        result = release["materialize"](candidate.parents[1], canonical_root=REPOSITORY)
    finally:
        sys.path[:] = previous
    assert not any(line.startswith("SKIP ") for line in result), result
    shutil.rmtree(payload)
    shutil.copytree(candidate, payload)
    return payload, installation, project


def test_actual_bridge_stamp_retains_deferred_commands_without_service_effects(materialized_bridge, area):
    payload, installation, project = materialized_bridge
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.is_dir()
    command = installation.parent / ".local/bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    assert command.is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert not (installation / "start-agent-bridge.ps1").exists()
    assert not (installation.parent / ".config/systemd").exists()
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


def bridge_service_adapter(env):
    """Only native service effects are synthetic; construction/CLI remain real."""
    if os.name == "nt":
        adapter = Path(env["PSModulePath"]) / "ScheduledTasks"
        adapter.mkdir()
        shutil.copyfile(
            Path(__file__).parent / "fixtures/bridge-scheduled-tasks.psm1",
            adapter / "ScheduledTasks.psm1",
        )
        env["USERNAME"] = "fixture-user"
    else:
        adapter = Path(env["PATH"])
        for name in ("seq", "xargs"):
            executable = shutil.which(name)
            assert executable, f"native installer requires {name}"
            (adapter / name).symlink_to(executable)
        real_curl = (adapter / "curl").resolve()
        (adapter / "curl").unlink()
        (adapter / "curl").write_text(
            '#!/usr/bin/env bash\nset -eu\n'
            'base="$(dirname -- "${BASH_SOURCE[0]}")"\n'
            'for arg in "$@"; do\n'
            '  if [[ "$arg" == http://127.0.0.1:9280/health ]]; then\n'
            '    printf \'{"operation":"synthetic-health"}\\n\' >>"$base/effects.jsonl"\n'
            '    [[ -f "$base/started" ]] || exit 22\n'
            '    printf \'{"service":"agent-bridge","fixture":true}\\n\'\n'
            '    exit 0\n'
            '  fi\n'
            'done\n'
            f'exec "{real_curl}" "$@"\n', encoding="utf-8",
        )
        (adapter / "curl").chmod(0o700)
        (adapter / "systemctl").write_text(
            '#!/usr/bin/env bash\nset -eu\n'
            'base="$(dirname -- "${BASH_SOURCE[0]}")"\n'
            'printf \'{"operation":"systemctl","arguments":"%s"}\\n\' "$*" >>"$base/effects.jsonl"\n'
            'case "$*" in\n'
            '  "--user daemon-reload"|"--user enable agent-bridge.service")\n'
            '    if [[ -f "$base/mode" && "$(cat "$base/mode")" == registration-denied ]]; then\n'
            '      printf \'{"operation":"registration-denied"}\\n\' >>"$base/effects.jsonl"\n'
            '      exit 37\n'
            '    fi\n'
            '    exit 0 ;;\n'
            '  "--user start agent-bridge.service")\n'
            '    printf synthetic >"$base/started"; exit 0 ;;\n'
            '  "--user is-active agent-bridge.service") [[ -f "$base/started" ]]; exit $? ;;\n'
            '  *) echo "fixture refuses unexpected service operation" >&2; exit 91 ;;\n'
            'esac\n', encoding="utf-8",
        )
        (adapter / "systemctl").chmod(0o700)
    return adapter


def assert_bridge_registration(installation, adapter):
    effects = registration_effects(adapter)
    if os.name == "nt":
        task = json.loads((adapter / "task.json").read_text(encoding="utf-8-sig"))
        assert task["TaskName"] == "Agent Bridge"
        assert task["Actions"][0]["Execute"] == "conhost.exe"
        assert str(installation / "start-agent-bridge.ps1") in task["Actions"][0]["Arguments"]
        assert task["Actions"][0]["WorkingDirectory"] == str(installation)
        assert task["Trigger"]["AtLogOn"] and not task["Trigger"]["AtStartup"]
        assert task["Trigger"]["Delay"] == "PT15S"
        assert task["Principal"]["LogonType"] == "Interactive"
        assert task["Settings"] == {"LimitSeconds": 0, "MultipleInstances": "IgnoreNew"}
        assert any(item["operation"] == "register" for item in effects)
        assert any(item["operation"] == "start" for item in effects)
    else:
        service = (installation.parent / ".config/systemd/user/agent-bridge.service").read_text()
        assert f"WorkingDirectory={installation}" in service
        assert f"Environment=AGENT_BRIDGE_CONFIG_DIR={installation}" in service
        assert "KillMode=process" in service and "Restart=on-failure" in service
        assert f"ExecStart={installation}/versions/" in service and "/bin/agent-bridge start" in service
        assert {"operation": "systemctl", "arguments": "--user start agent-bridge.service"} in effects
    assert any(item["operation"] == "synthetic-health" for item in effects)


@pytest.fixture
def native_recipe_runner(area):
    calls, owners, descriptors, roots = [], [], [], {}

    def start(command, *, env, cwd, root, diagnostic):
        path = area / f"native-recipe-{len(calls)}.log"
        output = path.open("wb")
        process = subprocess.Popen(
            command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT, **no_window_kwargs(),
        )
        calls.append((process, output, path))
        roots[root] = diagnostic
        prefix = "[installation-driver] launch acknowledgement: "

        def acknowledgement():
            return next((line.partition(prefix)[2]
                         for line in path.read_text(errors="replace").splitlines(keepends=True)
                         if line.startswith(prefix) and line.endswith("\n")), None)

        observe(lambda: acknowledgement() is not None or process.poll() is not None, 120, diagnostic)
        frame = acknowledgement()
        assert frame is not None, path.read_text(errors="replace") + diagnostic()
        if os.name == "nt":
            from test_foundation_genesis_process import pinned_worker
            owners.append(pinned_worker(json.loads(frame)))
        else:
            from agent_procutil._linux_pidfd import open_pidfd
            descriptors.append(open_pidfd(int(frame.split()[0].partition(":")[2])))
        return process, path

    def run(command, **kwargs):
        process, path = start(command, **kwargs)
        code = process.wait(timeout=900)
        return subprocess.CompletedProcess(command, code, path.read_text(errors="replace"), "")

    try:
        yield SimpleNamespace(start=start, run=run)
    finally:
        with ExitStack() as settlement:
            for _process, output, _path in calls:
                settlement.callback(output.close)
            for owner in owners:
                settlement.callback(owner.close)
                settlement.callback(owner.wait, timeout=900)
            if descriptors:
                from agent_procutil._caller_owned import _pidfd_exited
                for descriptor in descriptors:
                    settlement.callback(os.close, descriptor)
                    settlement.callback(
                        observe, lambda descriptor=descriptor: _pidfd_exited(descriptor), 900,
                        lambda: "native supervisor still running",
                    )
            for process, _output, _path in calls:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=30)
            for root, diagnostic in roots.items():
                observe(lambda: foundation_status(root=root) is None or
                        foundation_status(root=root).state != "running", 900, diagnostic)
                observe(lambda: all(item.state not in {"pending", "running"}
                                    for item in status(root=root)), 900, diagnostic)
                def custody_released():
                    lock = provision.core.OperationLock(root / "execution.lock")
                    try:
                        lock.acquire()
                    except provision.core.OperationLockedError:
                        return False
                    lock.release()
                    return True

                observe(custody_released, 900, diagnostic)


@native_genesis
@pytest.mark.parametrize("cold_action", ["provision", "install"])
def test_actual_bridge_native_caller_loss_concurrent_cold_actions_and_immutable_reuse(
    materialized_bridge, area, native_window_observation, native_recipe_runner, cold_action,
):
    import agent_procutil

    payload, installation, project = materialized_bridge
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(REPOSITORY / "libs" / name, inputs / name, ignore=lambda _path, names: {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        })
    source = inputs / "update-driver"
    root = area / "cold"
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    adapter = bridge_service_adapter(env)
    log = area / "caller.log"
    caller = job = owner = descriptor = follower = None

    def diagnostic():
        return "\n".join(f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
                         for path in (log, *root.rglob("*.log")) if path.is_file())

    def command(action):
        return native_command(root, payload, installation, project, source=source) + (
            ["-Wait", "-InstallerAction", action] if os.name == "nt" else
            ["--wait", "--installer-action", action]
        )

    def acknowledgement():
        prefix = "[installation-driver] launch acknowledgement: "
        return next((line.partition(prefix)[2] for line in log.read_text(errors="replace").splitlines(keepends=True)
                     if line.startswith(prefix) and line.endswith("\n")), None)

    with log.open("w", encoding="utf-8") as output:
        try:
            kwargs = dict(env=env, cwd=project, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                          **no_window_kwargs())
            if os.name == "nt":
                caller, job = agent_procutil.spawn_sync_in_kill_on_close_job(
                    command("stamp" if cold_action == "provision" else "install"),
                    require_job=True, allow_breakaway=False, **kwargs,
                )
            else:
                caller = subprocess.Popen(
                    command("stamp" if cold_action == "provision" else "install"),
                    start_new_session=True, **kwargs,
                )
            observe(lambda: acknowledgement() is not None or caller.poll() is not None, 120, diagnostic)
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
            follower, _follower_log = native_recipe_runner.start(
                command("stamp" if cold_action == "provision" else "install"),
                env=env, cwd=project, root=root, diagnostic=diagnostic,
            )
            if job is not None:
                job.close()
                job = None
            else:
                os.killpg(caller.pid, signal.SIGKILL)
            caller.wait(timeout=10)
            assert follower.wait(timeout=900) == 0, diagnostic()
            observe(lambda: bool(status(root=root)) and all(
                item.state not in {"pending", "running"} for item in status(root=root)
            ), 900, diagnostic)
            assert all(item.state == "completed" and item.exit_code == 0 for item in status(root=root)), diagnostic()
            assert foundation_status(root=root).state == "completed"
            if cold_action == "provision":
                assert not registration_effects(adapter)
                assert not (installation / "current-version").exists()
            else:
                assert_bridge_registration(installation, adapter)
            if owner is not None:
                owner.wait(timeout=900)
            if descriptor is not None:
                observe(lambda: _pidfd_exited(descriptor), 900, diagnostic)
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

    callers = [native_recipe_runner.start(
        command(cold_action), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
               for _ in range(2)]
    for process, path in callers:
        assert process.wait(timeout=900) == 0, path.read_text(errors="replace") + diagnostic()
    assert_bridge_registration(installation, adapter)
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    effects = registration_effects(adapter)
    repeated = native_recipe_runner.run(
        command(cold_action), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr + diagnostic()
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    writes = {"register", "set", "start", "systemctl"}
    assert [item for item in registration_effects(adapter) if item["operation"] in writes] == [
        item for item in effects if item["operation"] in writes
    ]
    if os.name == "nt" and cold_action == "provision":
        task_path = adapter / "task.json"
        stale = json.loads(task_path.read_text(encoding="utf-8-sig"))
        stale["Actions"][0]["Arguments"] = "fixture stale registration"
        task_path.write_text(json.dumps(stale), encoding="utf-8")
        (adapter / "mode").write_text("registration-denied", encoding="utf-8")
        denied = native_recipe_runner.run(
            command("provision"), env=env, cwd=project, root=root, diagnostic=diagnostic,
        )
        assert denied.returncode == 0, denied.stdout + denied.stderr + diagnostic()
        assert json.loads(task_path.read_text(encoding="utf-8-sig")) == stale
        assert "fixture registration denied" in status(root=root)[-1].log_path.read_text(encoding="utf-8")
        (adapter / "mode").unlink()
        repaired = native_recipe_runner.run(
            command("provision"), env=env, cwd=project, root=root, diagnostic=diagnostic,
        )
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr + diagnostic()
        assert registration_effects(adapter)[-1]["operation"] == "set"
        interactive = json.loads(task_path.read_text(encoding="utf-8-sig"))
        repair_path = payload / "scripts/repair-scheduled-task.ps1"
        canonical_repair = repair_path.read_bytes()
        repair_effect = installation / "frozen-repair.txt"
        try:
            for mode, exit_code in (("repair-source-replaced", 37), ("repair-source-removed", 0)):
                repair_path.write_text(
                    "$ErrorActionPreference='Stop'\n"
                    "if ([Environment]::GetCommandLineArgs() -inotcontains '-NonInteractive') { exit 99 }\n"
                    "[IO.File]::WriteAllText((Join-Path $env:AGENT_BRIDGE_CONFIG_DIR 'frozen-repair.txt'), $PSScriptRoot)\n"
                    f"exit {exit_code}\n", encoding="utf-8",
                )
                task = json.loads(json.dumps(interactive))
                task["Principal"]["LogonType"] = "S4U"
                task_path.write_text(json.dumps(task), encoding="utf-8")
                (adapter / "mode").write_text(mode, encoding="utf-8")
                (adapter / "source-tampered").unlink(missing_ok=True)
                repair_effect.unlink(missing_ok=True)
                repaired = native_recipe_runner.run(
                    command("provision"), env=env, cwd=project, root=root, diagnostic=diagnostic,
                )
                assert repaired.returncode == exit_code, repaired.stdout + repaired.stderr + diagnostic()
                assert (adapter / "source-tampered").read_text() == mode
                assert Path(repair_effect.read_text()).is_relative_to(installation / ".install-stage")
                result = status(root=root)[-1]
                assert result.state == "completed" and result.exit_code == exit_code
                assert "mutable original repair executed" not in result.log_path.read_text(encoding="utf-8")
                assert completion.read_bytes() == complete
        finally:
            repair_path.write_bytes(canonical_repair)
            task_path.write_text(json.dumps(interactive), encoding="utf-8")
            (adapter / "mode").unlink(missing_ok=True)
    if cold_action == "install":
        old_selected, old_complete = selected, complete
        old_stub = installation.parent / ".local/bin" / (
            "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
        )
        original_stub = old_stub.read_bytes()
        for version, failure in (("98.0.0", "registration-denied"), ("98.0.1", "effect-fatal")):
            set_payload_version(payload, version)
            unit = installation.parent / ".config/systemd/user/agent-bridge.service"
            unit_content = None
            if os.name == "nt":
                task_path = adapter / "task.json"
                task = json.loads(task_path.read_text(encoding="utf-8-sig"))
                task["Actions"][0]["Arguments"] = "fixture stale registration"
                task_path.write_text(json.dumps(task), encoding="utf-8")
            elif failure == "effect-fatal":
                # A real unit-publication failure stops before service fallback.
                unit_content = unit.read_bytes()
                unit.unlink()
                unit.mkdir()
            mode = "start-fatal" if failure == "effect-fatal" and os.name == "nt" else failure
            (adapter / "mode").write_text(mode, encoding="utf-8")
            effects_before = registration_effects(adapter)
            try:
                outcome = native_recipe_runner.run(
                    command("install"), env=env, cwd=project, root=root, diagnostic=diagnostic,
                )
                result = status(root=root)[-1]
                assert result.state == "completed" and result.exit_code == outcome.returncode
                assert (outcome.returncode == 0) is (failure == "registration-denied"), (
                    outcome.stdout + outcome.stderr + diagnostic()
                )
                observed = registration_effects(adapter)[len(effects_before):]
                if failure == "registration-denied":
                    if os.name == "nt":
                        assert json.loads(task_path.read_text(encoding="utf-8-sig")) == task
                        assert "fixture registration denied" in result.log_path.read_text(encoding="utf-8")
                    else:
                        assert sum(item["operation"] == "registration-denied" for item in observed) == 2
                    assert any(item["operation"] in {"start", "systemctl"} for item in observed)
                elif os.name == "nt":
                    assert "fixture start failure" in result.log_path.read_text(encoding="utf-8")
                    assert observed[-1]["operation"] == "start"
                else:
                    assert not any(item["operation"] == "systemctl" for item in observed)
                # Native activation precedes these later effects; do not invent rollback.
                assert (installation / "current-version").read_text().strip() == version
                assert (installation / "versions" / old_selected / ".install-complete.json").read_bytes() == old_complete
                assert old_stub.read_bytes() == original_stub
                assert not (installation / "active.json").exists()
                assert not (installation / "agent-bridge.pid").exists()
            finally:
                (adapter / "mode").unlink(missing_ok=True)
                if unit_content is not None:
                    unit.rmdir()
                    unit.write_bytes(unit_content)
        selected = (installation / "current-version").read_text().strip()
        slot = installation / "versions" / selected
        completion = slot / ".install-complete.json"
        complete = completion.read_bytes()
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    assert manifest["source"]["kind"] == "marketplace"
    (payload / "src/agent_bridge/__init__.py").write_text(
        'raise RuntimeError("fixture failed Bridge candidate")\n', encoding="utf-8",
    )
    changed = native_recipe_runner.run(
        command(cold_action), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    # Only Windows prepared provision is task-only; install on either platform
    # refuses different content for the already-completed immutable version.
    assert (changed.returncode == 0) is (os.name == "nt" and cold_action == "provision"), (
        changed.stdout + changed.stderr + diagnostic()
    )
    assert status(root=root)[-1].state == "completed"
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    set_payload_version(payload, "99.0.0")
    # A new version has no prepared target slot on either native installer.
    failed = native_recipe_runner.run(
        command(cold_action), env=env, cwd=project, root=root, diagnostic=diagnostic,
    )
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    failed_request = status(root=root)[-1]
    assert failed_request.state == "completed" and failed_request.exit_code != 0
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert not (installation / "versions/99.0.0/.install-complete.json").exists()
    payload.rename(payload.with_name("unavailable-bridge"))
    inputs.rename(area / "unavailable-foundation-source")
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_bridge,json;from importlib.metadata import version;"
         "print(json.dumps({'origin':agent_bridge.__file__,'version':version('agent-bridge')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot) and Version(actual["version"]) == Version(selected)
    consumer = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_bridge", "--help"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), consumer.stdout + consumer.stderr
    stub = installation.parent / ".local/bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(stub), "--help"]
        if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(stub), "--help"]
    )
    dispatched = subprocess.run(
        argv, env={**env, "AGENT_BRIDGE_NO_SELFPROVISION": "1"}, cwd=project,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert dispatched.returncode == 0 and "usage:" in dispatched.stdout.lower(), dispatched.stdout + dispatched.stderr
    assert completion.read_bytes() == complete
    assert not (installation / "active.json").exists()
    assert not (installation / "agent-bridge.pid").exists()
    assert all(item.state == "completed" for item in status(root=root)), diagnostic()
