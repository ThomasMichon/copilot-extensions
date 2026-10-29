"""The native installer retains the existing execution owner's custody."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from update_driver import core
from update_driver.participant import ENVIRONMENT_KEY, borrowed_custody, participant

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
REPO = SCRIPTS.parents[2]
pytestmark = pytest.mark.guard


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("_native_installer_driver_test", SCRIPTS / "installer_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(core, "default_root", lambda: tmp_path / "driver")
    monkeypatch.delenv(ENVIRONMENT_KEY, raising=False)
    return module


def arguments(tmp_path, **values):
    return SimpleNamespace(**{
        "payload": SCRIPTS.parent, "root": tmp_path / "runtime", "plugin": "agent-bridge",
        "shell": tmp_path / "powershell.exe", "action": "install", "options": [],
        "check_native": False, "remove_broker": False, **values,
    })


def test_cold_native_entry_uses_the_existing_finite_driver(adapter, tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, "source_digest", lambda payload: "a" * 64)
    args = arguments(tmp_path, options=["NonInteractive"])
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=7)),
    )
    assert adapter.foreground(args, driver) == 7
    spec, runner = queued[0]
    assert spec.backend == "plugin-native-install"
    assert spec.project == str(args.root) and spec.source == "a" * 64
    assert spec.argv == tuple(adapter.native_arguments(args))
    assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" not in dict(spec.context)
    assert adapter.NATIVE_ENV in runner.context_keys
    assert "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION" in runner.context_keys
    assert runner.argv[:7] == (
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(SCRIPTS / "installer_driver.py"),
    )
    assert "--participant" in runner.argv
    driver.drain.assert_called_once_with()
    driver.wait.assert_called_once_with("request", output=sys.stdout)


@pytest.mark.parametrize("ticket", [
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION",
])
def test_driver_admission_precedes_publication_custody(adapter, tmp_path, monkeypatch, ticket):
    monkeypatch.setenv(ticket, "not-journaled")
    driver = SimpleNamespace(enqueue=Mock())
    with pytest.raises(RuntimeError, match="precede publication custody"):
        adapter.foreground(arguments(tmp_path), driver)
    driver.enqueue.assert_not_called()


@pytest.mark.parametrize("options", [{}, ["Command"], ["NonInteractive", "NonInteractive"], [[]], [1]])
def test_native_recipe_does_not_accept_an_arbitrary_argument_vector(adapter, tmp_path, options):
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.native_arguments(arguments(tmp_path, options=options))


@pytest.mark.parametrize("plugin,action", [
    ("agent-mcp", "update"), ("agent-mcp", "uninstall"),
    ("agent-bridge", "init"), ("agent-vault", "broker-remove"),
])
def test_native_recipe_refuses_another_consumers_action(adapter, tmp_path, plugin, action):
    with pytest.raises(ValueError, match="Unsupported native installer action"):
        adapter.native_arguments(arguments(tmp_path, plugin=plugin, action=action))


@pytest.mark.parametrize("plugin,root_key", [
    ("agent-dispatch", "AGENT_DISPATCH_ROUTING_DIR"),
    ("agent-pull-requests", "AGENT_PULL_REQUESTS_INSTALL_DEADLINE_SEC"),
    ("agent-vault", "AGENT_VAULT_HOME"),
    ("agent-mcp", "AGENT_MCP_HOME"),
    ("agent-index", "AGENT_INDEX_ENGINE_HOME"),
])
def test_remaining_native_recipes_preserve_exact_options_and_context(adapter, tmp_path, monkeypatch, plugin, root_key):
    options = sorted(adapter.RECIPES[plugin])
    args = arguments(tmp_path, plugin=plugin, options=options)
    monkeypatch.setenv(root_key, str(tmp_path / "consumer"))
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="request"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    monkeypatch.setattr(adapter, "source_digest", lambda payload: "a" * 64)
    assert adapter.foreground(args, driver) == 0
    spec, runner = queued[0]
    assert spec.argv == ("-Action", "install", "-InstallDir", str(args.root), *("-" + option for option in options))
    assert dict(spec.context)[root_key] == str(tmp_path / "consumer")
    assert root_key in runner.context_keys
    assert runner.argv[runner.argv.index("--plugin") + 1] == plugin
    args.options = ["NonInteractive"]
    with pytest.raises(ValueError, match="Unsupported"):
        adapter.native_arguments(args)


@pytest.mark.skipif(os.name != "nt", reason="Windows native installation")
@pytest.mark.parametrize("backend", ["engine", "engine-launch-update"])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("check", [False, True])
def test_native_handoff_keeps_the_same_owner(adapter, tmp_path, monkeypatch, scoped, check, backend):
    args = arguments(tmp_path, check_native=check)
    request = core.enqueue(
        core.RequestSpec(backend),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )
    calls = []

    def spawn(command, **options):
        assert command[-4:] == ["-Action", "install", "-InstallDir", str(args.root)]
        assert options["creationflags"] == subprocess.CREATE_NO_WINDOW
        assert options["stdout"] is sys.stdout and options["stderr"] is sys.stderr
        assert ENVIRONMENT_KEY not in os.environ
        assert options["env"][adapter.NATIVE_ENV] == "agent-bridge"
        with participant(root=core.default_root(), witness=options["env"][ENVIRONMENT_KEY]) as child:
            child.assert_owned()
        calls.append(command)
        return SimpleNamespace(wait=lambda: 0)

    monkeypatch.setattr(adapter.subprocess, "Popen", spawn)
    monkeypatch.setattr(adapter, "source_digest", lambda payload: pytest.fail("nested submission"))

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        if scoped:
            with borrowed_custody(root=core.default_root()):
                return adapter.execute(args)
        return adapter.execute(args)

    assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == "completed"
    assert len(calls) == (0 if check else 1)


@pytest.mark.parametrize("changed", ["source", "root", "arguments", "backend", None])
def test_native_participant_checks_exact_admission(adapter, tmp_path, monkeypatch, changed):
    args = arguments(tmp_path, check_native=True)
    monkeypatch.setattr(adapter, "source_digest", lambda payload: "a" * 64)
    request = core.enqueue(
        core.RequestSpec(
            "unrelated" if changed == "backend" else "plugin-native-install",
            ("changed",) if changed == "arguments" else tuple(adapter.native_arguments(args)),
            project="changed" if changed == "root" else str(args.root),
            source="changed" if changed == "source" else "a" * 64,
        ),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter.execute(args)

    if changed:
        with pytest.raises(RuntimeError, match="unclosed participants"):
            core.drain(executor=execute)
    else:
        assert core.drain(executor=execute) == 0
    assert core.status(request.id).state == ("interrupted" if changed else "completed")


def test_restore_identity_binds_bytes_not_the_helpers_private_record_path(adapter, tmp_path):
    record = tmp_path / "record.json"
    record.write_bytes(b'{"version":1}\n')
    args = arguments(tmp_path, plugin="agent-dispatch", action="restore", retained_runtime=record)
    identity = adapter.request_arguments(args)
    digest = hashlib.sha256(record.read_bytes()).hexdigest()
    assert args.retained_runtime_sha256 == digest
    assert str(record) not in identity
    assert identity[-2:] == ("-RetainedRuntimeSha256", digest)
    assert adapter.native_arguments(args)[-4:] == [
        "-RetainedRuntime", str(record), "-RetainedRuntimeSha256", digest,
    ]
    pinned = tmp_path / "pinned.json"
    pinned.write_bytes(record.read_bytes())
    args.retained_runtime = pinned
    assert adapter.request_arguments(args) == identity
    pinned.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed after admission"):
        adapter.native_arguments(args)


@pytest.mark.parametrize("case", ["missing", "large", "foreign-action", "foreign-plugin"])
def test_restore_recipe_refuses_unbound_input(adapter, tmp_path, case):
    record = tmp_path / "record.json"
    record.write_bytes(b"x" * (1048577 if case == "large" else 1))
    args = arguments(
        tmp_path, plugin="agent-bridge" if case == "foreign-plugin" else "agent-dispatch",
        action="update" if case == "foreign-action" else "restore",
        retained_runtime=None if case == "missing" else record,
    )
    with pytest.raises(ValueError):
        adapter.native_arguments(args)


def test_restore_foreground_pins_record_hash_in_the_default_runner(adapter, tmp_path, monkeypatch):
    record = tmp_path / "record.json"
    record.write_bytes(b"exact retained input")
    args = arguments(tmp_path, plugin="agent-dispatch", action="restore", retained_runtime=record)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="restore"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    monkeypatch.setattr(adapter, "source_digest", lambda payload: "a" * 64)
    assert adapter.foreground(args, driver) == 0
    spec, runner = queued[0]
    digest = hashlib.sha256(record.read_bytes()).hexdigest()
    assert spec.argv[-2:] == ("-RetainedRuntimeSha256", digest)
    assert runner.argv[-4:] == ("--retained-runtime", str(record), "--retained-runtime-sha256", digest)
    assert spec.backend == "plugin-native-install"


def cell_arguments(tmp_path):
    return arguments(
        tmp_path, plugin="agent-pull-requests",
        cell_context=str(tmp_path / "runtime" / "install.json"),
        cell_marketplace="example--0123456789abcdef", cell_durable_home=str(tmp_path),
        cell_snapshot="snapshot-1", cell_version="1.0.0",
        cell_namespace_generation=2, cell_install_generation=3,
    )


def test_cell_recipe_binds_exact_context_and_generations(adapter, tmp_path):
    args = cell_arguments(tmp_path)
    command = adapter.native_arguments(args)
    assert command[:3] == ["install", "--context", args.cell_context]
    assert command[-4:] == [
        "--expected-namespace-generation", "2", "--expected-install-generation", "3",
    ]
    identity = adapter.request_arguments(args)
    args.cell_install_generation = 4
    assert adapter.request_arguments(args) != identity
    args.cell_context = str(tmp_path / "foreign" / "install.json")
    with pytest.raises(ValueError, match="cell installation recipe"):
        adapter.native_arguments(args)


@pytest.mark.parametrize("field,value", [
    ("plugin", "agent-vault"), ("action", "uninstall"),
    ("cell_namespace_generation", None), ("cell_install_generation", True),
    ("cell_install_generation", -1), ("options", ["Force"]),
])
def test_cell_recipe_refuses_foreign_or_unbound_scope(adapter, tmp_path, field, value):
    args = cell_arguments(tmp_path)
    setattr(args, field, value)
    with pytest.raises(ValueError):
        adapter.native_arguments(args)


def test_cell_foreground_uses_the_existing_finite_driver(adapter, tmp_path, monkeypatch):
    args = cell_arguments(tmp_path)
    queued = []
    driver = SimpleNamespace(
        RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
        enqueue=lambda spec, **kwargs: queued.append((spec, kwargs["runner"])) or SimpleNamespace(id="cell"),
        drain=Mock(), wait=Mock(return_value=SimpleNamespace(state="completed", exit_code=0)),
    )
    monkeypatch.setattr(adapter, "source_digest", lambda payload: "a" * 64)
    assert adapter.foreground(args, driver) == 0
    spec, runner = queued[0]
    assert spec.backend == "plugin-native-install"
    assert spec.argv == tuple(adapter.native_arguments(args))
    assert runner.argv[runner.argv.index("--cell-context") + 1] == args.cell_context
    assert runner.argv[-4:] == (
        "--cell-namespace-generation", "2", "--cell-install-generation", "3",
    )
    driver.drain.assert_called_once_with()


def test_reentry_marker_is_not_installation_authority(adapter, tmp_path, monkeypatch):
    monkeypatch.setenv(adapter.NATIVE_ENV, "agent-bridge")
    with pytest.raises(RuntimeError, match="live driver custody"):
        adapter.execute(arguments(tmp_path, check_native=True))
    assert not core.default_root().exists()


def test_broker_removal_uses_the_canonical_consumer_admission_api(adapter, tmp_path, monkeypatch):
    import installer_engine_uninstall as finite

    calls = []

    def cleanup(payload_root, plugin_id, root, shell, *, profile, check_consumers):
        assert check_consumers is True
        calls.append((payload_root, plugin_id, root, shell, profile))
        return {"status": "ready"}

    monkeypatch.setattr(finite, "cleanup", cleanup)
    args = arguments(tmp_path, action="uninstall")
    adapter.admit_broker_removal(args)
    assert calls == [(args.payload, args.plugin, args.root, str(args.shell), None)]


@pytest.mark.parametrize("plugin,action", [
    ("agent-bridge", "uninstall"), ("agent-mcp", "broker-remove"),
])
@pytest.mark.parametrize("admitted", [False, True])
def test_uninstall_removes_only_the_component_under_the_current_owner(
    adapter, tmp_path, monkeypatch, plugin, action, admitted,
):
    from update_driver import windows_broker

    args = arguments(tmp_path, plugin=plugin, action=action, remove_broker=True)
    remove = Mock()
    monkeypatch.setattr(windows_broker, "remove", remove)
    admission = Mock(side_effect=None if admitted else RuntimeError("live consumer"))
    monkeypatch.setattr(adapter, "admit_broker_removal", admission)
    monkeypatch.setattr(adapter.subprocess, "Popen", Mock(side_effect=AssertionError("second native installer")))
    request = core.enqueue(
        core.RequestSpec("engine"),
        runner=core.TrustedRunner((sys.executable, "-c", "pass"), str(tmp_path)),
    )

    def execute(transaction):
        monkeypatch.setenv(ENVIRONMENT_KEY, transaction.participant_env()[ENVIRONMENT_KEY])
        return adapter.execute(args)

    if admitted:
        assert core.drain(executor=execute) == 0
        assert core.status(request.id).state == "completed"
        remove.assert_called_once_with(args.root)
    else:
        with pytest.raises(RuntimeError, match="unclosed participants"):
            core.drain(executor=execute)
        remove.assert_not_called()
    admission.assert_called_once_with(args)


def test_bridge_adopts_inside_completion_before_publication_or_callbacks():
    source = (SCRIPTS / "install.ps1").read_text(encoding="utf-8")
    assert source.index("-Plugin 'agent-bridge'") < source.index("# === install-contract:v4 self-stage")
    completion = source.split("function Invoke-VersionedMarkComplete {", 1)[1].split("\nfunction ", 1)[0]
    assert "-RetainHelpers" in completion
    update = source.split("function Invoke-Update {", 1)[1].split("# -- Dispatch", 1)[0]
    complete_at = update.index("Invoke-VersionedMarkComplete")
    adopt_at = update.index("Invoke-RuntimeBrokerAdoption", complete_at)
    assert complete_at < adopt_at < update.index('catch {\n        Write-Fail "Update failed:')
    assert adopt_at < update.index("Invoke-VersionedActivate")
    assert update.index("Ensure-RuntimeBrokerAdoption") < update.index("Invoke-ServingRuntimeReconcile")
    uninstall = source.split("function Invoke-Uninstall {", 1)[1].split("\nfunction ", 1)[0]
    assert uninstall.index("--remove-broker") < uninstall.index("Unregister-ScheduledTask")
    assert uninstall.index("Owned Bridge daemon did not stop") < uninstall.index("--remove-broker")
    assert uninstall.index("--remove-broker") < uninstall.index("Invoke-FiniteRuntimeUninstall -InstallRoot $InstallDir\n")
    assert "-not ($Action -eq 'uninstall' -and $DryRun)" in source


def test_codespaces_adopts_before_activation_and_preserves_numbered_action_during_dev():
    source = (REPO / "plugins/agent-codespaces/scripts/install.ps1").read_text(encoding="utf-8")
    assert source.index("-Plugin 'agent-codespaces'") < source.index("# === install-contract:v4 self-stage")
    completion = source.split("function Invoke-VersionedMarkComplete {", 1)[1].split("\nfunction ", 1)[0]
    assert "-RetainHelpers" in completion
    activate = source.split("function Invoke-VersionedActivate {", 1)[1].split("\nfunction ", 1)[0]
    assert activate.index("Invoke-VersionedMarkComplete") < activate.index("Invoke-RuntimeBrokerAdoption")
    assert activate.index("Invoke-RuntimeBrokerAdoption") < activate.index("activate $SrcVersion")
    for action in ("Install", "Update"):
        function = source.split(f"function Invoke-{action} {{", 1)[1].split("\nfunction ", 1)[0]
        assert function.index("Invoke-VersionedActivate") < function.index("Deploy-VersionedRuntimeHelper")
        assert function.index("Invoke-VersionedActivate") < function.index("Sync-ConnectionOwnerService")
    dev = source.split("function Invoke-Dev {", 1)[1].split("\nfunction ", 1)[0]
    assert "Invoke-RuntimeBrokerAdoption" not in dev
    uninstall = source.split("function Invoke-Uninstall {", 1)[1].split("\nfunction ", 1)[0]
    assert uninstall.index("Invoke-StatePreservingUninstall -AdmissionOnly") < uninstall.index("--remove-broker")
    assert uninstall.index("--remove-broker") < uninstall.index("Invoke-StatePreservingUninstall -Preview")
    assert "Unregister-ConnectionOwnerService" not in uninstall
    assert "Stop-ManagedSshConnections" not in uninstall


@pytest.mark.skipif(os.name != "nt", reason="actual Windows native driver entry")
@pytest.mark.timeout(60)
@pytest.mark.parametrize("plugin,action,option", [
    ("agent-bridge", "install", "NonInteractive"),
    ("agent-bridge", "uninstall", "Purge"),
    ("agent-bridge", "uninstall", "DryRun"),
    ("agent-codespaces", "install", "Force"),
    ("agent-codespaces", "update", "Force"),
    ("agent-codespaces", "provision", "Force"),
    ("agent-codespaces", "uninstall", "Force"),
    ("agent-codespaces", "uninstall", "DryRun"),
    ("agent-vault", "install", "NoService"),
    ("agent-vault", "update", "Force"),
    ("agent-vault", "provision", "NoService"),
    ("agent-vault", "uninstall", "Purge"),
    ("agent-vault", "uninstall", "DryRun"),
    ("agent-dispatch", "install", "NoService"),
    ("agent-dispatch", "update", "RequireGracefulCutover"),
    ("agent-dispatch", "provision", "NoSupervisor"),
    ("agent-dispatch", "uninstall", "Purge"),
    ("agent-dispatch", "uninstall", "DryRun"),
    ("agent-dispatch", "restore", "Force"),
    ("agent-pull-requests", "install", "Force"),
    ("agent-pull-requests", "update", "Force"),
    ("agent-pull-requests", "provision", "Force"),
    ("agent-pull-requests", "uninstall", "Force"),
    ("agent-mcp", "install", "Force"),
    ("agent-mcp", "init", "Force"),
    ("agent-mcp", "provision", "Force"),
])
def test_copied_no_install_shell_uses_the_real_default_executor(adapter, tmp_path, monkeypatch, plugin, action, option):
    from agent_procutil.owned_process import run_caller_owned

    shell = shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell is unavailable")
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    minimal = tmp_path / "minimal"
    scripts = minimal / "scripts"
    scripts.mkdir(parents=True)
    for source in SCRIPTS.glob("*.py"):
        if source.name.startswith(("installer_", "versioned_runtime")):
            shutil.copyfile(source, scripts / source.name)
    for name in ("versioned_runtime_native.ps1", "installer-human-output.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    if plugin == "agent-pull-requests":
        source_scripts = REPO / "plugins" / plugin / "scripts"
        shutil.copyfile(source_scripts / "payload-mode.ps1", scripts / "payload-mode.ps1")
        shutil.copytree(source_scripts / "installation-context", scripts / "installation-context")
        shutil.copyfile(source_scripts.parent / "plugin.json", minimal / "plugin.json")
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    for name in ("update-driver", "agent-procutil", "single-instance-lease", "zdd"):
        library = tmp_path / "libs" / name
        library.mkdir(parents=True)
        shutil.copyfile(REPO / "libs" / name / "pyproject.toml", library / "pyproject.toml")
        shutil.copytree(
            REPO / "libs" / name / "src", library / "src",
            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".ruff_cache"),
        )
    (minimal / "pyproject.toml").write_text(
        f'[project]\nname="{plugin}"\nversion="1.0.0"\n'
        '[tool.uv.sources]\nagent-update-driver = { path = "../libs/update-driver" }\n',
        encoding="utf-8",
    )
    payload = minimal
    receipt = tmp_path / "receipt.json"
    entrypoint = adapter.ENTRYPOINTS.get(plugin, "install.ps1")
    native_source = (REPO / "plugins" / plugin / "scripts" / entrypoint).read_text(encoding="utf-8")
    boundary = "# === install-contract:v4 self-stage"
    if plugin == "agent-mcp":
        boundary = "if ($Action -ne 'uninstall') {\n" + boundary
    assert boundary in native_source
    native_entry = native_source.split(boundary, 1)[0]
    assert "Enter-NativeInstallerDriver" in native_entry
    (payload / "scripts" / entrypoint).write_text(
        native_entry
        + f"@{{root=$InstallDir;driver=$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER;"
        f"witness=[bool]$env:UPDATE_DRIVER_WITNESS;option=[bool]${option}}} | ConvertTo-Json | Set-Content -LiteralPath '{receipt}'\nexit 0\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    retained_arguments = []
    if action == "restore":
        record = tmp_path / "retained input.json"
        record.write_text('{"synthetic":"native admission only"}', encoding="utf-8")
        retained_arguments = ["-RetainedRuntime", str(record)]
    try:
        result = run_caller_owned([
            shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(payload / "scripts" / entrypoint),
            "-Action", action, "-InstallDir", str(tmp_path / "runtime"), "-" + option,
            *retained_arguments,
        ], timeout=30, max_stdout_bytes=1048576, max_stderr_bytes=1048576)
    except subprocess.TimeoutExpired:
        execution = profile / ".copilot-extensions" / "update-driver"
        print("Native driver files:", [str(path.relative_to(execution)) for path in execution.rglob("*")])
        for log in execution.rglob("*.log"):
            print(log.name, log.read_text(encoding="utf-8", errors="replace")[-6000:])
        raise
    if result.returncode:
        for log in (profile / ".copilot-extensions" / "update-driver").glob("*.log"):
            print(log.name, log.read_text(encoding="utf-8", errors="replace")[-6000:])
    assert result.returncode == 0, result.stdout + result.stderr
    dry_run = option == "DryRun"
    assert json.loads(receipt.read_text(encoding="utf-8-sig")) == {
        "root": str(tmp_path / "runtime"), "driver": None if dry_run else plugin,
        "witness": not dry_run, "option": True,
    }
    rows = core.status(root=Path.home() / ".copilot-extensions" / "update-driver")
    if dry_run:
        assert rows == ()
    else:
        assert len(rows) == 1 and rows[0].state == "completed" and rows[0].exit_code == 0
    assert not (tmp_path / "runtime").exists()
