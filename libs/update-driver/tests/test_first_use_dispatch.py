"""Dispatch's fixed legacy recipes, separately from ordinary publisher adoption."""

import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys

import pytest
from packaging.version import Version

from agent_procutil import no_window_kwargs
from update_driver import (
    ParticipantError, RequestSpec, TrustedRunner, drain, enqueue, foundation_status, status,
)
from update_driver import first_use as provision
from update_driver.foundation_inputs import GENERATED, PACKAGES

from test_first_use import primitive
from test_first_use_native import native_command
from test_first_use_bridge import (
    build_bridge_native_test_assets, native_recipe_runner as native_recipe_runner,
)
from test_first_use_bridge_command import diagnostic_for
from test_first_use_genesis import observe
from test_first_use_pull_requests_command import command_for
from test_first_use_vault import cold_environment, native_genesis
from test_first_use_vault_bootstrap import receipt_completed_count


pytestmark = pytest.mark.skipif(
    os.name != "nt" and sys.platform != "linux",
    reason="native Dispatch recipes are qualified only on Windows and Linux",
)
REPOSITORY = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_dispatch_fixed_action_keeps_native_options_context_custody_and_result(
    owning_dispatch, area, monkeypatch, action,
):
    payload, installation, project = owning_dispatch
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    repeated = provision.enqueue(payload, installation, project, root=root, installer_action=action)
    assert request.id == repeated.id and not installation.exists()
    assert request.runner.context_keys == tuple(sorted(provision.DISPATCH_CONTEXT_KEYS))
    for key in ("ACTION", "DRY_RUN", "FORCE", "PAYLOAD_ROOT", "VALIDATED_CONTEXT", "NO_SELFPROVISION",
                "CONFIG_DIR", "INSTALL_DIR", "ROUTING_DIR", "RUN_DIR", "COMMAND_MODULE"):
        monkeypatch.setenv("AGENT_DISPATCH_" + key, "foreign")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 7, result.log_path.read_text()
    assert (installation / "executed").read_text() == action
    assert (installation / "client-only").read_text().strip() == "0"
    assert Path((installation / "project-path").read_text()) == project
    frozen = Path((installation / "source-path").read_text().strip()).parent
    assert frozen.name == "agent-dispatch"
    assert frozen.parent.parent == installation / ".install-stage"
    assert frozen.parent.name.startswith(request.id + "-")
    assert (frozen / "payload-invocation.json").read_bytes() == (
        payload / "payload-invocation.json"
    ).read_bytes()
    admission = json.loads(request.spec.source)
    assert (admission["plugin"], admission["payload"], admission["runtime_root"], admission["project"]) == (
        "agent-dispatch", str(payload), str(installation), str(project),
    )


@pytest.mark.parametrize("native", [False, True], ids=["python-api", "native-front"])
@pytest.mark.parametrize("action", ["stamp", "provision"])
@pytest.mark.parametrize("reason", [
    "client-only", "no-self", "pinned", "managed", "malformed", "copy", "snapshot",
    "custom-root", "cell", "nested", "dispatcher", "module", "provision-mode",
    "lifecycle", "catalog", "compound", "install", "missing-helper", "missing-native",
    "missing-reconcile", "missing-source",
    "missing-supervisor", "missing-base",
])
def test_dispatch_refusal_precedes_acquisition_queue_and_publication(
    owning_dispatch, area, monkeypatch, native, action, reason,
):
    payload, installation, project = owning_dispatch
    if reason in {"copy", "snapshot"}:
        copied = (
            installation / "snapshots/retained" if reason == "snapshot"
            else area / "copy/agent-dispatch"
        )
        shutil.copytree(payload, copied)
        payload = copied
    elif reason == "custom-root":
        installation = installation.with_name("other-dispatch")
    elif reason in {"no-self", "pinned", "nested", "cell"}:
        monkeypatch.setenv({
            "no-self": "AGENT_DISPATCH_NO_SELFPROVISION",
            "pinned": "AGENT_DISPATCH_PINNED_CONTEXT",
            "nested": "UPDATE_DRIVER_WITNESS",
            "cell": "AGENT_DISPATCH_INSTALLATION_ID",
        }[reason], "foreign")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(payload_root=payload, plugin_id="agent-dispatch",
                         legacy_root=installation, project_root=project)
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    elif reason.startswith("missing-"):
        name = {
            "missing-helper": "versioned_runtime_publication.py",
            "missing-native": "installer-engine.ps1",
            "missing-reconcile": "installer-instance-reconcile.sh",
            "missing-source": "source-runtime.ps1",
            "missing-supervisor": "supervisor-lifecycle.ps1",
            "missing-base": "base-python.ps1",
        }[reason]
        (payload / "scripts" / name).unlink()
    elif reason in {"dispatcher", "module", "provision-mode", "lifecycle", "catalog"}:
        path = payload / "payload-invocation.json"
        manifest = json.loads(path.read_text())
        if reason == "dispatcher":
            manifest["payloadDispatcher"]["windows"] = "scripts/other.ps1"
        elif reason == "module":
            manifest["module"] = "foreign_module"
        elif reason == "provision-mode":
            manifest["provisionMode"] = "snapshot"
        elif reason == "lifecycle":
            manifest["installerLifecycle"] = False
        else:
            manifest["windowsCatalogShim"] = "powershell"
        path.write_text(json.dumps(manifest), encoding="utf-8")
    if reason in {"compound", "install"}:
        action = "stamp-provision" if reason == "compound" else "install"
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


@pytest.mark.parametrize("action", ["stamp", "provision"])
def test_dispatch_native_admits_exact_recipe_before_source_comparison(owning_dispatch, area, action):
    payload, installation, project = owning_dispatch
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


def test_dispatch_stamp_and_provision_are_distinct_requests(owning_dispatch, area):
    payload, installation, project = owning_dispatch
    root = area / "queue"
    requests = [
        provision.enqueue(payload, installation, project, root=root, installer_action=action)
        for action in ("stamp", "provision")
    ]
    assert len({request.id for request in requests}) == 2
    assert [json.loads(request.spec.source).get("installer_action", "provision")
            for request in requests] == ["stamp", "provision"]
    assert not installation.exists()


@pytest.mark.parametrize("change", ["substituted", "unavailable"])
def test_dispatch_never_substitutes_accepted_source(owning_dispatch, area, change):
    payload, installation, project = owning_dispatch
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root)
    if change == "substituted":
        (payload / "scripts/install.ps1").write_text("exit 99\n", encoding="utf-8")
    else:
        payload.rename(payload.with_name("unavailable-dispatch"))
    with pytest.raises(ParticipantError):
        drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "interrupted" and result.exit_code is None
    assert not installation.exists()


@pytest.fixture
def materialized_dispatch(owning_dispatch, area):
    payload, installation, project = owning_dispatch
    candidate = area / "materialized/plugins/agent-dispatch"
    shutil.copytree(REPOSITORY / "plugins/agent-dispatch", candidate, ignore=shutil.ignore_patterns(
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


def test_actual_dispatch_stamp_defers_construction_and_service_effects(materialized_dispatch, area):
    payload, installation, project = materialized_dispatch
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = area / "queue"
    request = provision.enqueue(payload, installation, project, root=root, installer_action="stamp")
    drain(root=root)
    result = status(request.id, root=root)
    assert result.state == "completed" and result.exit_code == 0, result.log_path.read_text()
    pointer = Path((installation / "payload-dir").read_text(encoding="utf-8-sig").strip())
    assert pointer.is_dir()
    command = installation.parent / ".local/bin" / ("agent-dispatch.cmd" if os.name == "nt" else "agent-dispatch")
    assert command.is_file()
    assert not (installation / "current-version").exists()
    assert not list((installation / "versions").glob("*/pyvenv.cfg"))
    assert not (installation.parent / ".config/systemd").exists()
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


def dispatch_effect_adapter(payload, env):
    """Isolate OS registration and the installer's otherwise auto-starting health probe."""
    fixtures = Path(__file__).parent / "fixtures"
    if os.name == "nt":
        adapter = Path(env["PSModulePath"]) / "ScheduledTasks"
        adapter.mkdir()
        shutil.copyfile(fixtures / "dispatch-scheduled-tasks.psm1", adapter / "ScheduledTasks.psm1")
    else:
        adapter = Path(env["PATH"])
        for name in ("seq", "xargs", "cc", "as", "ld"):
            executable = shutil.which(name)
            assert executable, f"native installer requires {name}"
            (adapter / name).symlink_to(executable)
        shutil.copyfile(fixtures / "dispatch-systemctl.sh", adapter / "systemctl")
        (adapter / "systemctl").chmod(0o700)
    main = payload / "src/agent_dispatch/__main__.py"
    body = main.read_text(encoding="utf-8")
    seam = 'if __name__ == "__main__":\n'
    assert body.count(seam) >= 1
    # Change the fixture source before acceptance, never a built or completed slot.
    synthetic = (
        "    if sys.argv[1:] == ['--local', 'health']:\n"
        f"        _effects = Path({str(adapter)!r})\n"
        "        _task = _effects / 'agent-dispatch.json'\n"
        "        if os.name == 'nt' and (not _task.is_file() or "
        "json.loads(_task.read_text(encoding='utf-8-sig'))['State'] != 'Running'):\n"
        "            raise SystemExit(97)\n"
        "        with (_effects / 'effects.jsonl').open('a', encoding='utf-8') as _out:\n"
        "            _out.write(json.dumps({'operation': 'synthetic-health'}) + '\\n')\n"
        "        from . import __version__\n"
        "        print(json.dumps({'version': __version__, 'fixture': True}))\n"
        "        raise SystemExit(0)\n"
        "    if sys.argv[1:] == ['_ensure-coordinator'] or "
        "any(_arg in {'serve', 'supervise'} for _arg in sys.argv[1:]):\n"
        "        raise RuntimeError('fixture refuses actual service launch')\n"
    )
    main.write_text(body.replace(seam, seam + synthetic, 1), encoding="utf-8")
    return adapter


def assert_dispatch_registration(installation, adapter):
    effects = [
        json.loads(line) for line in (adapter / "effects.jsonl").read_text(
            encoding="utf-8-sig",
        ).splitlines()
    ]
    if os.name == "nt":
        coordinator = json.loads((adapter / "agent-dispatch.json").read_text(encoding="utf-8-sig"))
        supervisor = json.loads((adapter / "agent-dispatch-supervisor.json").read_text(encoding="utf-8-sig"))
        assert coordinator["State"] == "Running"
        assert coordinator["Principal"]["LogonType"] == "S4U"
        assert [item["AtStartup"] for item in coordinator["Triggers"]] == [True, False]
        assert [item["AtLogOn"] for item in coordinator["Triggers"]] == [False, True]
        assert supervisor["State"] == "Disabled"
        assert supervisor["Principal"]["LogonType"] == "Interactive"
        for task, launcher in ((coordinator, "serve-service.ps1"), (supervisor, "supervise-service.ps1")):
            assert task["Actions"][0]["Execute"] == "conhost.exe"
            assert str(installation / launcher) in task["Actions"][0]["Arguments"]
            assert task["Actions"][0]["WorkingDirectory"] == str(installation)
            assert task["Settings"]["RestartCount"] == 3
            assert task["Settings"]["RestartSeconds"] == 60
            assert task["Settings"]["LimitSeconds"] == 0
        assert {"operation": "start", "detail": "agent-dispatch"} in effects
        assert {"operation": "disable", "detail": "agent-dispatch-supervisor"} in effects
        assert {"operation": "synthetic-health"} in effects
    else:
        units = installation.parent / ".config/systemd/user"
        coordinator = (units / "agent-dispatch.service").read_text()
        supervisor = (units / "agent-dispatch-supervisor.service").read_text()
        assert f"WorkingDirectory={installation}" in coordinator
        assert f"Environment=AGENT_DISPATCH_INSTALL_DIR={installation}" in coordinator
        assert f"ExecStart={installation}/versions/" in coordinator
        assert "-m agent_dispatch serve" in coordinator
        assert f"WorkingDirectory={installation}" in supervisor
        assert "supervise-service.sh --systemd-unit agent-dispatch-supervisor.service" in supervisor
        for arguments in (
            "--user enable agent-dispatch.service", "--user restart agent-dispatch.service",
            "--user stop agent-dispatch-supervisor.service",
            "--user disable agent-dispatch-supervisor.service",
        ):
            assert {"operation": "systemctl", "arguments": arguments} in effects
        assert (adapter / "agent-dispatch.service.active").read_text() == "active"
        assert (adapter / "agent-dispatch-supervisor.service.enabled").read_text() == "disabled"
    assert not (installation / "tasks.db").exists()
    assert not (installation / "rendezvous.json").exists()


def dispatch_native_setup(materialized_dispatch, area):
    payload, installation, project = materialized_dispatch
    inputs = area / "foundation-source"
    for name in PACKAGES:
        shutil.copytree(REPOSITORY / "libs" / name, inputs / name, ignore=lambda _path, names: {
            name for name in names if name in GENERATED
            or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))
        })
    build_bridge_native_test_assets(payload, inputs / "agent-procutil")
    env = cold_environment(installation, project, area)
    env.pop("AGENT_VAULT_HOME")
    adapter = dispatch_effect_adapter(payload, env)
    before = {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()}
    root = installation.parent / ".copilot-extensions/update-driver"
    diagnostic = diagnostic_for(root, area)
    command = native_command(
        root, payload, installation, project, source=inputs / "update-driver",
    ) + (["-Wait"] if os.name == "nt" else ["--wait"])
    return env, adapter, before, root, diagnostic, command


def assert_dispatch_consumers(payload, installation, project, env):
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    identity = subprocess.run(
        [str(python), "-I", "-B", "-c",
         "import agent_dispatch,json;from importlib.metadata import version;"
         "print(json.dumps({'origin':agent_dispatch.__file__,'version':version('agent-dispatch')}))"],
        env=env, cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert identity.returncode == 0, identity.stdout + identity.stderr
    actual = json.loads(identity.stdout)
    assert Path(actual["origin"]).is_relative_to(slot)
    assert Version(actual["version"]) == Version(json.loads((payload / "plugin.json").read_text())["version"])
    suffix = ".ps1" if os.name == "nt" else ""
    for script in (
        payload / "bin" / ("agent-dispatch" + suffix),
        installation.parent / ".local/bin" / ("agent-dispatch" + suffix),
    ):
        consumer = subprocess.run(
            command_for(script), env={**env, "AGENT_DISPATCH_NO_SELFPROVISION": "1"},
            cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )
        assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), consumer.stdout + consumer.stderr


@native_genesis
def test_actual_dispatch_original_provision_survives_caller_loss(
    materialized_dispatch, area, native_recipe_runner, native_window_observation,
):
    payload, installation, project = materialized_dispatch
    env, adapter, before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    caller, _log = native_recipe_runner.start(
        command, env=env, cwd=project, root=root, diagnostic=diagnostic, contain_caller=True,
    )
    observe(lambda: foundation_status(root=root) is not None, 120, diagnostic)
    assert foundation_status(root=root).state == "running", diagnostic()
    native_recipe_runner.lose_caller(caller)

    def original_completed():
        foundation = foundation_status(root=root)
        if foundation is not None and foundation.state == "completed":
            assert foundation.exit_code == 0, diagnostic()
        logs = list(root.glob("first-use-*.log"))
        assert len(logs) <= 1, diagnostic()
        if native_recipe_runner.owners_finished():
            assert logs and receipt_completed_count(root, 1, logs[0]), diagnostic()
        return bool(logs) and receipt_completed_count(root, 1, logs[0])

    observe(original_completed, 900, diagnostic)
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert result.spec.argv == ()
    admitted = json.loads(result.spec.source)
    assert (admitted["plugin"], admitted["payload"], admitted["runtime_root"], admitted["project"]) == (
        "agent-dispatch", str(payload), str(installation), str(project),
    )
    assert_dispatch_registration(installation, adapter)
    selected = (installation / "current-version").read_text().strip()
    slot = installation / "versions" / selected
    completion = slot / ".install-complete.json"
    completed = completion.read_bytes()
    assert_dispatch_consumers(payload, installation, project, env)
    observe(native_recipe_runner.owners_finished, 120, diagnostic)
    runner = TrustedRunner((sys.executable, "-c", "pass"), str(project))
    failed = enqueue(RequestSpec("retained-dispatch-failure"), runner=runner, root=root)
    drain(root=root, executor=lambda _transaction: 23)
    assert status(failed.id, root=root).exit_code == 23
    pending = enqueue(RequestSpec("retained-dispatch-pending"), runner=runner, root=root)
    history = status(root=root)
    effects = (adapter / "effects.jsonl").read_bytes()
    try:
        assert_dispatch_consumers(payload, installation, project, env)
        assert status(root=root) == history and completion.read_bytes() == completed
        assert (adapter / "effects.jsonl").read_bytes() == effects
        assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before
    finally:
        assert [item.id for item in status(root=root) if item.state == "pending"] == [pending.id]
        drain(root=root, executor=lambda _transaction: 0)


@native_genesis
def test_actual_dispatch_concurrent_provision_coalesces_one_request(
    materialized_dispatch, area, native_recipe_runner, native_window_observation,
):
    payload, installation, project = materialized_dispatch
    env, adapter, before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    first, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    second, _ = native_recipe_runner.start(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    assert first.wait(timeout=900) == second.wait(timeout=900) == 0, diagnostic()
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    assert_dispatch_registration(installation, adapter)
    assert_dispatch_consumers(payload, installation, project, env)
    assert {path: path.read_bytes() for path in payload.rglob("*") if path.is_file()} == before


@native_genesis
def test_actual_dispatch_reuse_failed_candidate_and_unavailable_source_preserve_selection(
    materialized_dispatch, area, native_recipe_runner, native_window_observation,
):
    payload, installation, project = materialized_dispatch
    env, adapter, _before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)

    def provision_once():
        return native_recipe_runner.run(command, env=env, cwd=project, root=root, diagnostic=diagnostic)

    assert provision_once().returncode == 0, diagnostic()
    selected = (installation / "current-version").read_text().strip()
    completion = installation / "versions" / selected / ".install-complete.json"
    bin_dir = installation.parent / ".local/bin"
    retained = {
        path: path.read_bytes() for path in (
            completion, installation / "current-version", installation / "last-known-good",
            installation / "payload-dir", installation / "stamped-version", *bin_dir.glob("agent-dispatch*"),
        ) if path.is_file()
    }
    assert provision_once().returncode == 0, diagnostic()
    assert len(status(root=root)) == 2
    assert {path: path.read_bytes() for path in retained} == retained
    assert_dispatch_consumers(payload, installation, project, env)
    (payload / "src/agent_dispatch/__init__.py").write_text(
        'raise RuntimeError("fixture failed Dispatch candidate")\n', encoding="utf-8",
    )
    manifest = payload / "plugin.json"
    data = json.loads(manifest.read_text())
    data["version"] = "99.0.0"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    metadata = payload / "pyproject.toml"
    body, count = re.subn(r'(?m)^version = "[^"]+"$', 'version = "99.0.0"', metadata.read_text(), count=1)
    assert count == 1
    metadata.write_text(body, encoding="utf-8")
    assert provision_once().returncode != 0, diagnostic()
    failed, = [item for item in status(root=root) if item.exit_code != 0]
    assert failed.state == "completed" and failed.exit_code is not None, diagnostic()
    failure_log = failed.log_path.read_text(errors="replace")
    assert any(message in failure_log for message in (
        "Fresh runtime slot failed its health gate", "fixture failed Dispatch candidate",
    )), diagnostic()
    assert {path: path.read_bytes() for path in retained} == retained
    assert not list((installation / "versions").glob("99.0.0*/.install-complete.json"))
    payload.rename(payload.with_name("unavailable-dispatch"))
    primary = bin_dir / ("agent-dispatch.ps1" if os.name == "nt" else "agent-dispatch")
    history = status(root=root)
    consumer = subprocess.run(
        command_for(primary), env={**env, "AGENT_DISPATCH_NO_SELFPROVISION": "1"},
        cwd=project, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert consumer.returncode == 0 and "usage:" in consumer.stdout.lower(), consumer.stdout + consumer.stderr
    assert status(root=root) == history
    assert {path: path.read_bytes() for path in retained} == retained


@native_genesis
def test_actual_dispatch_tolerated_supervisor_effect_failure_is_visible(
    materialized_dispatch, area, native_recipe_runner, native_window_observation,
):
    payload, installation, project = materialized_dispatch
    env, adapter, _before, root, diagnostic, command = dispatch_native_setup(materialized_dispatch, area)
    (adapter / "mode").write_text("supervisor-disable-denied", encoding="utf-8")
    operation = native_recipe_runner.run(command, env=env, cwd=project, root=root, diagnostic=diagnostic)
    assert operation.returncode == 0, diagnostic()
    result, = status(root=root)
    assert result.state == "completed" and result.exit_code == 0, diagnostic()
    log = result.log_path.read_text(errors="replace")
    assert "supervisor" in log.lower() and "disable" in log.lower(), diagnostic()
    assert "failed" in log.lower() or "denied" in log.lower(), diagnostic()
    effects = [
        json.loads(line) for line in (adapter / "effects.jsonl").read_text(
            encoding="utf-8-sig",
        ).splitlines()
    ]
    assert any(item["operation"] == "supervisor-disable-denied" for item in effects)
    if os.name == "nt":
        task = json.loads((adapter / "agent-dispatch-supervisor.json").read_text(encoding="utf-8-sig"))
        assert task["State"] != "Running"
    else:
        assert not (adapter / "agent-dispatch-supervisor.service.active").exists() or (
            adapter / "agent-dispatch-supervisor.service.active"
        ).read_text() != "active"
    assert_dispatch_consumers(payload, installation, project, env)
    assert not (installation / "tasks.db").exists()
