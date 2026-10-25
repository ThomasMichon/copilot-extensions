"""Logger's fixed native recipes, without adopting ordinary publishers or timers."""

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tomllib

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import ParticipantError, drain, status
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive
from test_first_use_codespaces_bootstrap import hook_command
from test_first_use_genesis import observe
from test_first_use_native import native_command
from test_first_use_vault import cold_environment, native_genesis


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Logger recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("action", ["stamp", "provision", "install"])
def test_logger_fixed_action_keeps_own_context_native_options_and_result(owning_logger, area, monkeypatch, action):
    payload, installation, project = owning_logger
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    assert request.runner.context_keys == tuple(sorted(provision.LOGGER_CONTEXT_KEYS))
    for key in ("ACTION", "DRY_RUN", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION"):
        monkeypatch.setenv("AGENT_LOGGER_" + key, "foreign")
    foreign = area / "foreign-project"
    foreign.mkdir()
    for key in ("COPILOT_PROJECT_DIR", "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"):
        monkeypatch.setenv(key, str(foreign))
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "client-only").read_text().strip() == "0"
    assert Path((installation / "project-path").read_text()) == project
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-logger"
    assert frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "payload-invocation.json").read_bytes() == (payload / "payload-invocation.json").read_bytes()
    admission = json.loads(request.spec.source)
    assert (admission["plugin"], admission["payload"], admission["runtime_root"], admission["project"]) == (
        "agent-logger", str(payload), str(installation), str(project),
    )
    if action != "stamp":
        manifest = json.loads((installation / "deploy-manifest.json").read_text())
        owner = manifest["commandLifecycle"]["owner"]
        assert owner["sourceFingerprint"] == admission["source_fingerprint"]
        assert not owner.get("unattributed") and str(frozen) not in json.dumps(owner)


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("requested_action", ["provision", "install"])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "copy", "custom-root",
    "snapshot", "dispatcher", "dispatcher-array", "module", "provision-mode", "compound",
    "nested", "cell", "lifecycle", "commands-array", "command-module", "command-count",
    "command-object", "plugin-array", "command", "missing-lifecycle",
])
def test_logger_refusal_precedes_acquisition_queue_and_plugin_writes(
    owning_logger, area, monkeypatch, native, requested_action, reason,
):
    payload, installation, project = owning_logger
    if reason in {"copy", "snapshot"}:
        copied = (
            installation / "snapshots" / "retained" if reason == "snapshot"
            else area / "copy" / "agent-logger"
        )
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-logger")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_LOGGER_NO_SELFPROVISION", "pinned": "AGENT_LOGGER_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS", "cell": "AGENT_LOGGER_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-logger",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason == "missing-lifecycle":
        (payload / "scripts/command_lifecycle.py").unlink()
    elif reason not in {"client-only", "compound"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "dispatcher-array":
            manifest["payloadDispatcher"] = ["scripts/runtime-gate.ps1"]
        elif reason == "module":
            manifest["module"] = "agent_logger"
        elif reason == "provision-mode":
            manifest["provisionMode"] = "snapshot"
        elif reason == "lifecycle":
            manifest["installerLifecycle"] = "true"
        elif reason == "commands-array":
            manifest["commands"] = {}
        elif reason == "command-module":
            manifest["commands"][3]["module"] = ["agent_logger.segmenter.prepare_log"]
        elif reason == "command-count":
            manifest["commands"].pop()
        elif reason == "command-object":
            manifest["commands"][0] = "agent-logger"
        elif reason == "plugin-array":
            manifest["plugin"] = ["agent-logger"]
        else:
            manifest["command"] = "agent-logger"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    action = "stamp-provision" if reason == "compound" else requested_action
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
def test_logger_native_admits_exact_declaration_before_source_check(owning_logger, area, action):
    payload, installation, project = owning_logger
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


def test_logger_empty_no_self_keeps_native_value_semantics(owning_logger, monkeypatch):
    payload, installation, project = owning_logger
    monkeypatch.setenv("AGENT_LOGGER_NO_SELFPROVISION", "")
    assert provision.inspect(payload, installation, project)["plugin"] == "agent-logger"
    assert not installation.exists()


def test_logger_install_request_is_distinct_from_service_less_provision(owning_logger, area):
    payload, installation, project = owning_logger
    root = area / "queue"
    actions = ["stamp", "provision", "install"]
    requests = [
        provision.enqueue(payload, installation, project, root=root, installer_action=action)
        for action in actions
    ]
    assert len({request.id for request in requests}) == len(actions)
    assert [json.loads(request.spec.source).get("installer_action", "provision")
            for request in requests] == actions
    assert not installation.exists()


@pytest.mark.parametrize("payload_fixture", [
    "owning_payload", "owning_pull_requests", "owning_ssh", "owning_codespaces",
])
@pytest.mark.parametrize("native", [False, True])
def test_install_action_refuses_other_plugins_before_mutation(request, payload_fixture, native, area):
    payload, installation, project = request.getfixturevalue(payload_fixture)
    root = area / "cold"
    if native:
        command = native_command(root, payload, installation, project)
        command += ["-InstallerAction", "install"] if os.name == "nt" else ["--installer-action", "install"]
        result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=60, **no_window_kwargs())
        assert result.returncode != 0 and "requires Logger" in result.stderr
    else:
        with pytest.raises(provision.ProvisionError, match="requires Logger"):
            provision.enqueue(payload, installation, project, root=root, installer_action="install")
    assert not root.exists() and not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_logger_never_replaces_queued_source(owning_logger, area, change):
    payload, installation, project = owning_logger
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-logger"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def materialized_logger(owning_logger, area):
    payload, installation, project = owning_logger
    candidate = area / "materialized/plugins/agent-logger"
    shutil.copytree(REPOSITORY / "plugins/agent-logger", candidate, ignore=shutil.ignore_patterns(
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


def assert_no_periodic_worker(installation):
    assert not (installation.parent / "task-effects").exists()
    assert not (installation.parent / ".config/systemd").exists()
    assert not list(installation.rglob("*.timer"))


def logger_registration_adapter(env, area):
    if os.name == "nt":
        adapter = Path(env["PSModulePath"]) / "ScheduledTasks"
        adapter.mkdir()
        shutil.copyfile(
            Path(__file__).parent / "fixtures/logger-scheduled-tasks.psm1",
            adapter / "ScheduledTasks.psm1",
        )
        env["USERNAME"] = "fixture-user"
    else:
        adapter = Path(env["PATH"])
        script = adapter / "systemctl"
        script.write_text(
            '#!/usr/bin/env bash\nset -eu\n'
            'base="$(dirname -- "${BASH_SOURCE[0]}")"\n'
            'case "$*" in\n'
            '  "--user daemon-reload"|"--user enable --now agent-logger-sync.timer") ;;\n'
            '  *) echo "fixture refuses unexpected systemctl operation" >&2; exit 91 ;;\n'
            'esac\n'
            'printf \'{"operation":"systemctl","arguments":"%s"}\\n\' "$*" >>"$base/effects.jsonl"\n'
            'if [[ -f "$base/mode" && "$(cat "$base/mode")" == fatal ]]; then\n'
            '  echo "fixture fatal registration failure" >&2; exit 47\n'
            'fi\n',
            encoding="utf-8",
        )
        script.chmod(0o700)
    return adapter


def registration_effects(adapter):
    path = adapter / "effects.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines()] if path.exists() else []


def assert_logger_registration(installation, adapter):
    effects = registration_effects(adapter)
    if os.name == "nt":
        task = json.loads((adapter / "task.json").read_text(encoding="utf-8-sig"))
        action = task["Actions"][0]
        assert task["TaskName"] == "Agent Logger Session Sync"
        assert Path(action["Execute"]).name == "pythonw.exe"
        assert str(installation / "bin/session-sync-task.py") in action["Arguments"]
        assert str(installation / "bin/session-sync-task.ps1") in action["Arguments"]
        assert task["Trigger"]["IntervalSeconds"] == 14400
        assert task["Trigger"]["Once"] is True
        assert task["Trigger"]["Repetition"]["StopAtDurationEnd"] is False
        assert task["Settings"]["LimitSeconds"] == 1800
        assert task["Settings"]["MultipleInstances"] == "IgnoreNew"
        assert task["Principal"] == {
            "UserId": "fixture-user", "LogonType": "Interactive", "RunLevel": "Limited",
        }
        launcher = (installation / "bin/session-sync-task.ps1").read_text(encoding="utf-8-sig")
        assert str(installation) in launcher
        assert "run" in launcher and "--prune" in launcher
        assert any(item["operation"] == "register" for item in effects)
    else:
        units = installation.parent / ".config/systemd/user"
        service = (units / "agent-logger-sync.service").read_text()
        timer = (units / "agent-logger-sync.timer").read_text()
        assert f"Environment=AGENT_LOGGER_HOME={installation}" in service
        selected = (installation / "current-version").read_text().strip()
        command = f"{installation}/versions/{selected}/bin/session-sync"
        assert f"ExecStart={command} run --prune" in service
        assert Path(command).is_file()
        assert "TimeoutStartSec=1800" in service
        assert "OnUnitActiveSec=4h" in timer and "OnBootSec=5min" in timer
        assert "Persistent=true" in timer and "RandomizedDelaySec=10min" in timer
        assert effects[-2:] == [
            {"operation": "systemctl", "arguments": "--user daemon-reload"},
            {"operation": "systemctl", "arguments": "--user enable --now agent-logger-sync.timer"},
        ]


def test_actual_logger_stamp_keeps_snapshot_and_six_commands_without_runtime(materialized_logger, area):
    payload, installation, project = materialized_logger
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.parent == installation / "snapshots"
    assert provision.inspect(pointer, installation, project)["plugin"] == "agent-logger"
    bootstrap = subprocess.run(
        hook_command(pointer, project, installation.parent),
        env={**os.environ, "COPILOT_PLUGIN_ROOT": str(pointer)}, cwd=project,
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert bootstrap.returncode == 3 and bootstrap.stdout == "{}" and bootstrap.stderr, (
        bootstrap.stdout + bootstrap.stderr
    )
    assert not (installation.parent / ".copilot-extensions/update-driver").exists()
    for command, _module in provision.LOGGER_COMMANDS:
        assert (installation.parent / ".local/bin" / (command + ".ps1" if os.name == "nt" else command)).is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert_no_periodic_worker(installation)
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


def test_logger_raw_checkout_stamp_keeps_complete_dependency_mapping_without_source(owning_logger, area):
    _installed, installation, project = owning_logger
    checkout = area / "checkout"
    canonical = REPOSITORY / "plugins/agent-logger"
    payload = checkout / "plugins/agent-logger"
    ignore = shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache", "tests", "build", "dist", "*.egg-info",
    )
    shutil.copytree(canonical, payload, ignore=ignore)
    shutil.copytree(REPOSITORY / "libs/installer-engine", checkout / "libs/installer-engine", ignore=ignore)
    pending, copied = [canonical], set()
    while pending:
        source = pending.pop()
        if source != canonical:
            assert source.is_relative_to(REPOSITORY / "libs")
            if source in copied:
                continue
            shutil.copytree(source, checkout / source.relative_to(REPOSITORY), ignore=ignore)
            copied.add(source)
        declaration = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
        for spec in declaration.get("tool", {}).get("uv", {}).get("sources", {}).values():
            if "path" in spec:
                pending.append((source / spec["path"]).resolve())
    catalog = checkout / ".github/plugin/marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({
        "name": "fixture-market",
        "plugins": [{"name": "agent-logger", "source": "./plugins/agent-logger"}],
    }), encoding="utf-8")
    (installation.parent / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "fixture-market": {"source": {"source": "directory", "path": str(checkout)}},
        },
    }), encoding="utf-8")
    if os.name == "nt":
        command = [
            str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
            "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts/install.ps1"),
            "stamp", "-InstallDir", str(installation),
        ]
    else:
        command = [shutil.which("bash"), str(payload / "scripts/install.sh"),
                   "stamp", "--install-dir", str(installation)]
    result = subprocess.run(command, env=dict(os.environ), cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=90, **no_window_kwargs())
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    checkout.rename(area / "unavailable-checkout")
    probe = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; from pathlib import Path; root=Path(sys.argv[1]); "
         "sys.path.insert(0,str(root/'scripts')); import installer_engine as engine; "
         "import installer_dependency_policy as policy; "
         "assert all(p.is_relative_to(root) for p in engine._package_roots(root)); "
         "policy._check_project(root,policy._anchor_roots(),set())", str(snapshot)],
        env=dict(os.environ), cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    foundation = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; from pathlib import Path; root=Path(sys.argv[1]); "
         "sys.path.insert(0,str(root/'libs/update-driver/src')); "
         "from update_driver.foundation_inputs import inspect,PACKAGES; "
         "assert {p.name for p in inspect(root/'libs/update-driver').packages} == set(PACKAGES)",
         str(snapshot)],
        env=dict(os.environ), cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert foundation.returncode == 0, foundation.stdout + foundation.stderr
    assert_no_periodic_worker(installation)


@native_genesis
def test_actual_logger_cold_concurrent_install_keeps_selection_and_six_installed_consumers(
    materialized_logger, area, native_window_observation,
):
    payload, installation, project = materialized_logger
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
    env["AGENT_LOGGER_HOME"] = str(installation)
    foreign = area / "foreign-project"
    foreign.mkdir()
    env["COPILOT_PROJECT_DIR"] = env["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(foreign)
    adapter = logger_registration_adapter(env, area)

    def diagnostic():
        return "\n".join(f"{path.name}:\n{path.read_text(errors='replace')[-16000:]}"
                         for path in root.rglob("*.log"))

    def command(action):
        result = native_command(root, payload, installation, project, source=source)
        return result + (["-Wait", "-InstallerAction", action] if os.name == "nt" else [
            "--wait", "--installer-action", action,
        ])

    # Two real cold front doors overlap while acquiring the same foundation.
    callers = [subprocess.Popen(command("stamp"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
               for _ in range(2)]
    try:
        for caller in callers:
            out, err = caller.communicate(timeout=900)
            assert caller.returncode == 0, out.decode() + err.decode() + diagnostic()
    finally:
        for caller in callers:
            if caller.poll() is None:
                caller.kill()
                caller.communicate(timeout=30)
    stamps = status(root=root)
    assert 1 <= len(stamps) <= 2 and all(
        item.state == "completed" and item.exit_code == 0 for item in stamps
    ), diagnostic()
    assert not registration_effects(adapter)
    assert_no_periodic_worker(installation)
    callers = [subprocess.Popen(command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs())
               for _ in range(2)]
    try:
        for caller in callers:
            out, err = caller.communicate(timeout=900)
            assert caller.returncode == 0, out.decode() + err.decode() + diagnostic()
    finally:
        for caller in callers:
            if caller.poll() is None:
                caller.kill()
                caller.communicate(timeout=30)
    assert_logger_registration(installation, adapter)
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    complete = completion.read_bytes()
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    before = registration_effects(adapter)
    provisioned = subprocess.run(command("provision"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=900, **no_window_kwargs())
    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr + diagnostic()
    assert registration_effects(adapter) == before
    manifest = json.loads((installation / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    if os.name == "nt":
        owner = manifest["commandLifecycle"]["owner"]
        admitted = json.loads(status(root=root)[-1].spec.source)
        assert owner["sourceFingerprint"] == admitted["source_fingerprint"]
        assert not owner.get("unattributed") and str(installation / ".install-stage") not in json.dumps(owner)
    repeated = subprocess.run(command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=900, **no_window_kwargs())
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr + diagnostic()
    if os.name == "nt":
        assert [item for item in registration_effects(adapter) if item["operation"] in {"register", "set"}] == [
            item for item in before if item["operation"] in {"register", "set"}
        ]
        task_path = adapter / "task.json"
        stale = json.loads(task_path.read_text(encoding="utf-8-sig"))
        stale["Actions"][0]["Arguments"] = "fixture stale registration"
        task_path.write_text(json.dumps(stale), encoding="utf-8")
        repaired = subprocess.run(command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=900, **no_window_kwargs())
        assert repaired.returncode == 0, repaired.stdout + repaired.stderr + diagnostic()
        assert registration_effects(adapter)[-1]["operation"] == "set"
        assert_logger_registration(installation, adapter)
        task_path.write_text(json.dumps(stale), encoding="utf-8")
    else:
        assert_logger_registration(installation, adapter)
    (adapter / "mode").write_text("fatal", encoding="utf-8")
    failed_registration = subprocess.run(
        command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=900, **no_window_kwargs(),
    )
    assert failed_registration.returncode != 0, failed_registration.stdout + failed_registration.stderr + diagnostic()
    result = status(root=root)[-1]
    assert result.state == "completed" and result.exit_code != 0
    assert "fixture fatal registration failure" in result.log_path.read_text(encoding="utf-8")
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    if os.name == "nt":
        assert json.loads(task_path.read_text(encoding="utf-8-sig")) == stale
        (adapter / "mode").write_text("denied", encoding="utf-8")
        denied = subprocess.run(command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=900, **no_window_kwargs())
        assert denied.returncode == 0, denied.stdout + denied.stderr + diagnostic()
        result = status(root=root)[-1]
        assert result.state == "completed" and result.exit_code == 0
        assert "Access is denied" in result.log_path.read_text(encoding="utf-8")
        assert json.loads(task_path.read_text(encoding="utf-8-sig")) == stale
    (adapter / "mode").unlink()
    registration_before_failure = registration_effects(adapter)
    project_file = payload / "pyproject.toml"
    project_file.write_text(re.sub(r'(?m)^version\s*=\s*"[^"]+"', 'version = "99.0.0"',
                                   project_file.read_text()), encoding="utf-8")
    (payload / "src/agent_logger/__init__.py").write_text(
        'raise RuntimeError("fixture failed Logger candidate")\n', encoding="utf-8",
    )
    failed = subprocess.run(command("install"), env=env, cwd=project, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=900, **no_window_kwargs())
    assert failed.returncode != 0, failed.stdout + failed.stderr + diagnostic()
    assert status(root=root)[-1].state == "completed" and status(root=root)[-1].exit_code != 0
    assert (installation / "current-version").read_text().strip() == selected
    assert completion.read_bytes() == complete
    assert registration_effects(adapter) == registration_before_failure
    payload.rename(payload.with_name("unavailable-logger"))
    inputs.rename(area / "unavailable-foundation-source")
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_logger,json; from importlib.metadata import version; "
         "from agent_logger._build_info import __version__,COMMIT; "
         "print(json.dumps({'origin':agent_logger.__file__,'version':version('agent-logger'),"
         "'build_version':__version__,'commit':COMMIT}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert actual["version"] == str(Version(selected))
    assert str(Version(actual["build_version"])) == actual["version"]
    version = subprocess.run(
        [str(python), "-I", "-B", "-m", "agent_logger", "version"], env=env, cwd=project,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert version.returncode == 0, version.stdout + version.stderr
    assert version.stdout.startswith("agent-logger " + actual["build_version"] + " (commit " + actual["commit"])
    for name, module in provision.LOGGER_COMMANDS:
        consumer = subprocess.run(
            [str(python), "-I", "-B", "-X", "utf8", "-m", module, "--help"], env=env, cwd=project,
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), (
            name, consumer.stdout, consumer.stderr,
        )
        stub = installation.parent / ".local/bin" / (name + ".ps1" if os.name == "nt" else name)
        argv = (
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(stub), "--help"]
            if os.name == "nt" else [str(Path(env["PATH"]) / "bash"), str(stub), "--help"]
        )
        dispatch = subprocess.run(argv, env={**env, "AGENT_LOGGER_NO_SELFPROVISION": "1"},
                                  cwd=project, capture_output=True, text=True, timeout=30,
                                  **no_window_kwargs())
        assert dispatch.returncode == 0 and "usage:" in dispatch.stdout.lower(), (
            name, dispatch.stdout, dispatch.stderr,
        )
    assert not (installation / "logs/session-sync.log").exists()
    assert not (installation.parent / "task-effects").exists()
    assert completion.read_bytes() == complete
    observe(lambda: all(item.state == "completed" for item in status(root=root)), 30, diagnostic)
