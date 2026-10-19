"""Canonical dispatch selection and native bootstrap compatibility contracts."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs
from test_cell_installer_slice import SCRIPTS
from test_cell_payload_upgrade import native_profile
from test_watch_installation import _policy
from test_watch_runtime import completed as completed

from agent_pull_requests import _installation_context as api
from agent_pull_requests.watch_install import _manifest
from agent_pull_requests.watch_invocation import WatchInvocation

pytestmark = pytest.mark.timeout(90)


@pytest.mark.parametrize("actions,runner", [("false", "github-hosted"), ("true", "self-hosted")])
def test_native_account_acceptance_refuses_non_disposable_hosts(monkeypatch, actions, runner):
    monkeypatch.setenv("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", actions)
    monkeypatch.setenv("RUNNER_ENVIRONMENT", runner)
    with pytest.raises(pytest.fail.Exception, match="disposable GitHub-hosted"):
        native_profile.__wrapped__(None)


def test_native_account_acceptance_refuses_environment_profile_substitution(monkeypatch, tmp_path):
    monkeypatch.setenv("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE", "1")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(AssertionError, match="native OS account"):
        native_profile.__wrapped__(None)
    assert not list(tmp_path.iterdir())


def load():
    spec = importlib.util.spec_from_file_location(
        "apr_cell_invocation", SCRIPTS / "cell_invoke.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def selected(completed, monkeypatch):
    installation, runtime = completed
    module = load()
    monkeypatch.setattr(module, "PAYLOAD", installation.payload_root)
    root = installation.plugin_root
    for marker in (api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE):
        (root / marker).write_text(runtime.runtime_version + "\n", encoding="utf-8")
    (root / "deploy-manifest.json").write_bytes(
        _manifest(WatchInvocation(installation, runtime, str(installation.receipt))),
    )
    python = runtime.runtime_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir()
    python.write_bytes(b"selection-unit-test-only-not-an-executable")
    status = installation.require_current(environment={})
    return module, installation, runtime, status, python


def test_selection_requires_exact_complete_target_without_creating_owner(selected):
    module, installation, _, status, python = selected
    assert module.selection(api, status, installation.durable_home) == (
        python, installation.plugin_root,
    )
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.parametrize("damage", [
    "current", "lkg", "manifest", "manifest-runtime", "manifest-payload",
    "manifest-digest", "completion", "snapshot", "interpreter",
])
def test_selection_never_falls_back_from_incoherent_or_incomplete_metadata(selected, damage):
    module, installation, runtime, status, python = selected
    root = installation.plugin_root
    if damage == "current":
        (root / api.CURRENT_VERSION_FILE).unlink()
    elif damage == "lkg":
        (root / api.LAST_KNOWN_GOOD_FILE).write_text("another-version\n", encoding="utf-8")
    elif damage == "manifest":
        (root / "deploy-manifest.json").unlink()
    elif damage.startswith("manifest-"):
        path = root / "deploy-manifest.json"
        manifest = json.loads(path.read_text("utf-8"))
        if damage == "manifest-runtime":
            manifest["venv"] = str(root / "versions" / "foreign")
        elif damage == "manifest-payload":
            manifest["source"]["path"] = str(root / "foreign")
        else:
            manifest["source"]["content_hash"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
    elif damage == "completion":
        (runtime.runtime_root / api.RUNTIME_SLOT_COMPLETION_FILE).unlink()
    elif damage == "snapshot":
        (root / "snapshots" / runtime.snapshot_id / "entry.py").write_text(
            "changed immutable bytes", encoding="utf-8",
        )
    else:
        python.unlink()
    with pytest.raises((OSError, ValueError)):
        module.selection(api, status, installation.durable_home)
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


def test_only_entirely_absent_selection_may_self_provision(selected):
    module, installation, _, status, _ = selected
    root = installation.plugin_root
    for name in (api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE, "deploy-manifest.json"):
        (root / name).unlink()
    assert module.selection(api, status, installation.durable_home) is None
    (root / "deploy-manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PermissionError, match="incomplete or incoherent"):
        module.selection(api, status, installation.durable_home)


def test_selection_refuses_foreign_cell_and_payload(selected, tmp_path, monkeypatch):
    module, installation, _, status, _ = selected
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    monkeypatch.setattr(module, "PAYLOAD", foreign)
    with pytest.raises(ValueError):
        module.selection(api, status, installation.durable_home)
    monkeypatch.setattr(module, "PAYLOAD", installation.payload_root)
    status["marketplaceId"] = "other--0123456789abcdef"
    with pytest.raises(ValueError):
        module.selection(api, status, installation.durable_home)


def test_dispatch_preserves_structured_arguments_and_target_status(selected, monkeypatch):
    module, installation, runtime, status, python = selected
    adapter = SimpleNamespace(
        foundation=lambda: api,
        management_version=lambda: runtime.payload_version,
        process_helpers=lambda: SimpleNamespace(no_window_kwargs=no_window_kwargs),
    )
    monkeypatch.setattr(module, "installer", lambda: adapter)
    monkeypatch.setattr(module, "discover", lambda _: (
        status, installation.durable_home, installation.os_profile, {},
    ))
    commands = []
    cwd = Path.cwd()

    def run(command, **kwargs):
        commands.append((command, kwargs, Path.cwd()))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(module.subprocess, "run", run)
    arguments = ["a spaced argument", 'a"quote', "two\nlines", "", "--flag"]
    assert module.execute(arguments) == 7
    command, kwargs, actual_cwd = commands.pop()
    assert command == [str(python), "-I", "-m", "agent_pull_requests", *arguments]
    assert actual_cwd == cwd
    assert kwargs["env"]["AGENT_RT_ROOT"] == str(installation.plugin_root)
    assert kwargs["env"]["COPILOT_PLUGIN_ROOT"] == str(installation.payload_root)
    assert kwargs["env"]["COPILOT_EXTENSIONS_CONTEXT"] == str(installation.receipt)
    assert not commands


@pytest.mark.parametrize("pointer", ["relative/install.json", '{"installReceipt":"not-a-path"}'])
def test_explicit_context_must_be_absolute_receipt_path(tmp_path, monkeypatch, pointer):
    module = load()
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", pointer)
    with pytest.raises(PermissionError, match="absolute canonical"):
        module.discover(api)
    assert not list(tmp_path.iterdir())


def native(script, arguments=(), *, environment=None):
    if os.name == "nt":
        command = [
            shutil.which("pwsh") or shutil.which("powershell"),
            "-NoProfile", "-NonInteractive", "-File", str(script.with_suffix(".ps1")),
        ]
    else:
        command = ["bash", str(script.with_suffix(".sh"))]
    return subprocess.run(  # noqa: S603 -- test-owned exact native script, argv only
        [*command, *arguments], env=environment, capture_output=True, text=True,
        timeout=30, check=False, **no_window_kwargs(),
    )


@pytest.mark.parametrize("enabled", [None, False, True, "invalid"])
def test_native_mode_needs_no_python_and_never_mutates_legacy(tmp_path, enabled):
    if os.name != "nt":
        pytest.skip("Native POSIX policy must be exercised in an isolated OS account")
    profile = tmp_path / "profile"
    profile.mkdir()
    if enabled is not None:
        _policy(profile, enabled)
    environment = os.environ.copy()
    environment.update(HOME=str(profile), USERPROFILE=str(profile))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    # Native status and the wrapper must not need a Python command on PATH.
    environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
    result = native(SCRIPTS / "payload-mode", environment=environment)
    if enabled is True:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "cell"
    elif enabled in (None, False):
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "legacy"
    else:
        assert result.returncode != 0
        assert "blocks invocation" in result.stderr
    assert not (profile / ".agent-pull-requests").exists()
    assert not (profile / ".local").exists()


@pytest.fixture
def first_touch(tmp_path, monkeypatch):
    module = load()
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    _policy(profile)
    current = api._current_environment
    # Unit fixture only; native OS-account coverage remains a separate lane.
    monkeypatch.setattr(api, "_current_environment", lambda **kwargs: current(
        **{**kwargs, "os_profile": profile},
    ))
    return module, profile


def test_first_touch_uses_canonical_receipt_and_activation_without_owner(first_touch):
    module, profile = first_touch
    status, durable, _, probe = module.discover(api)
    assert status["reason"] == "activation-required"
    module.activate_fresh(
        api, status, durable, profile, probe, version=module.installer().management_version(),
    )
    activated, _, _, _ = module.discover(api)
    module.require_active(activated)
    validated = api.validate_context_receipt(
        activated["context"], durable, expected_plugin_id=module.PLUGIN,
        expected_payload_root=module.PAYLOAD, environment={},
    )
    assert module.selection(api, activated, durable) is None
    for key in ("versionsRoot", "stateRoot", "runRoot", "launchersRoot"):
        assert not Path(validated[key]).exists()
    assert not (profile / ".agent-pull-requests").exists()


def test_existing_unactivated_receipt_requires_explicit_decision(first_touch):
    module, profile = first_touch
    status, durable, _, probe = module.discover(api)
    resolved = api.resolve_context(payload_root=module.PAYLOAD, plugin_id=module.PLUGIN)
    version = json.loads((module.PAYLOAD / "plugin.json").read_text("utf-8"))["version"]
    api.stamp_context(
        payload_root=module.PAYLOAD, plugin_id=module.PLUGIN, durable_home=durable,
        payload_version=version, payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
    )
    with pytest.raises(PermissionError, match="explicit activation"):
        module.activate_fresh(api, status, durable, profile, probe, version=version)
    assert not (Path(resolved["pluginRoot"]) / "installation-activation.json").exists()


@pytest.mark.parametrize("change", ["disabled", "malformed", "legacy", "explicit"])
def test_first_touch_refuses_inadmissible_governance_or_state(first_touch, monkeypatch, change):
    module, profile = first_touch
    if change == "disabled":
        _policy(profile, False)
    elif change == "malformed":
        _policy(profile, "not-a-boolean")
    elif change == "legacy":
        (profile / ".agent-pull-requests").mkdir()
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(profile / "missing.json"))
        with pytest.raises(PermissionError):
            module.discover(api)
        return
    status, durable, _, probe = module.discover(api)
    with pytest.raises(PermissionError):
        module.activate_fresh(
            api, status, durable, profile, probe, version=module.installer().management_version(),
        )
    assert not (durable / "marketplaces").exists()


def test_bootstrap_refresh_advances_only_its_active_source_generation(first_touch):
    module, profile = first_touch
    status, durable, _, probe = module.discover(api)
    module.activate_fresh(
        api, status, durable, profile, probe, version=module.installer().management_version(),
    )
    before, _, _, _ = module.discover(api)
    module.refresh_management(api, before, durable, profile, version="2.0.0")
    after, _, _, _ = module.discover(api)
    module.require_active(after)
    assert after["context"] == before["context"]
    assert after["marketplaceId"] == before["marketplaceId"]
    assert after["installGeneration"] == before["installGeneration"] + 1
    assert after["activationGeneration"] == before["activationGeneration"] + 1
    assert api._payload_identity_from_install(after["context"])["version"] == "2.0.0"
    module.refresh_management(api, after, durable, profile, version="2.0.0")
    assert module.discover(api)[0] == after
    assert module.selection(api, after, durable) is None


@pytest.mark.parametrize("entry", ["payload-mode", "init", "invoke-payload-runtime"])
def test_native_explicit_missing_receipt_never_falls_back(tmp_path, entry):
    environment = os.environ.copy()
    environment.update(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
        COPILOT_EXTENSIONS_CONTEXT=str(
            tmp_path / "marketplaces" / "foreign" / "plugins" / "agent-pull-requests"
            / "install.json",
        ),
    )
    result = native(SCRIPTS / entry, environment=environment)
    assert result.returncode != 0
    assert not (tmp_path / ".agent-pull-requests").exists()
    assert not (tmp_path / ".local").exists()


@pytest.mark.skipif(os.name != "nt", reason="PowerShell forwarding contract")
def test_generated_powershell_preserves_actual_python_stdout_args_cwd_and_failure(tmp_path):
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    (payload / "bin").mkdir()
    (payload / "plugin.json").write_text("{}", encoding="utf-8")
    shutil.copy2(SCRIPTS.parent / "bin" / "agent-pull-requests.ps1", payload / "bin")
    shutil.copy2(SCRIPTS / "invoke-payload-runtime.ps1", scripts)
    (scripts / "payload-mode.ps1").write_text("'cell'\n", encoding="utf-8")
    (scripts / "cell_invoke.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd()}))\n"
        "raise SystemExit(7)\n", encoding="utf-8",
    )
    result = native(payload / "bin" / "agent-pull-requests", [
        "a spaced value", "", 'a"quote', "two\nlines",
    ])
    assert result.returncode == 7, (result.stdout, result.stderr)
    value = json.loads(result.stdout)
    assert value["args"] == ["--", "a spaced value", "", 'a"quote', "two\nlines"]
    assert Path(value["cwd"]) == Path.cwd()


@pytest.mark.skipif(os.name != "nt", reason="Native POSIX policy needs an isolated OS account")
@pytest.mark.parametrize("enabled", [True, "malformed"])
def test_session_start_never_runs_legacy_reconciler_for_governed_or_invalid_policy(
    tmp_path, enabled,
):
    _policy(tmp_path, enabled)
    environment = os.environ.copy()
    environment.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = native(SCRIPTS / "bootstrap-check-admission", environment=environment)
    assert result.returncode == (0 if enabled is True else 126)
    assert json.loads(result.stdout) == {}
    assert not (tmp_path / ".agent-pull-requests").exists()
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / ".copilot-extensions" / "marketplaces").exists()


@pytest.mark.skipif(os.name != "nt", reason="Native POSIX policy needs an isolated OS account")
@pytest.mark.parametrize("action", ["install", "update", "stamp", "provision", "uninstall"])
def test_implicit_legacy_installer_refuses_governed_policy_before_all_writes(tmp_path, action):
    _policy(tmp_path)
    environment = os.environ.copy()
    environment.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = native(SCRIPTS / "install", [action], environment=environment)
    assert result.returncode != 0
    assert "requires explicit" in result.stderr
    assert not (tmp_path / ".agent-pull-requests").exists()
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / ".copilot-extensions" / "marketplaces").exists()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
@pytest.mark.timeout(420)
@pytest.mark.skipif(os.name != "nt", reason="Native POSIX bootstrap needs an isolated OS account")
def test_normal_init_and_generated_command_build_and_use_fresh_cell_without_legacy(
    tmp_path_factory,
):
    # The full test-name prefix exceeds Windows' native build-backend CWD limit.
    tmp_path = tmp_path_factory.mktemp("b")
    profile = tmp_path / "profile"
    profile.mkdir()
    _policy(profile)
    caller = tmp_path / "caller"
    caller.mkdir()
    environment = os.environ.copy()
    environment.update(HOME=str(profile), USERPROFILE=str(profile))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    host = shutil.which("pwsh") or shutil.which("powershell")
    result = subprocess.run(  # noqa: S603 -- exact owning payload, disposable governed profile
        [host, "-NoProfile", "-NonInteractive", "-File", str(SCRIPTS / "init.ps1")],
        env=environment, cwd=caller, capture_output=True, text=True,
        timeout=300, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    receipts = list((profile / ".copilot-extensions" / "marketplaces").glob(
        "*/plugins/agent-pull-requests/install.json",
    ))
    assert len(receipts) == 1
    root = receipts[0].parent
    version = json.loads((SCRIPTS.parent / "plugin.json").read_text("utf-8"))["version"]
    assert (root / "current-version").read_text("utf-8").strip() == version
    assert (root / "last-known-good").read_text("utf-8").strip() == version
    manifest = json.loads((root / "deploy-manifest.json").read_text("utf-8"))
    assert manifest["source"]["version"] == version
    assert not (root / "run" / "active.json").exists()
    assert not (root / "state" / "watch-subscriptions.json").exists()
    command = [
        host, "-NoProfile", "-NonInteractive", "-File",
        str(SCRIPTS.parent / "bin" / "agent-pull-requests.ps1"),
    ]
    result = subprocess.run(  # noqa: S603 -- exact generated payload command
        [*command, "--version"], env=environment, cwd=caller,
        capture_output=True, text=True, timeout=60, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert version in result.stdout
    result = subprocess.run(  # noqa: S603 -- exact generated payload command
        [*command, "not a real subcommand"], env=environment, cwd=caller,
        capture_output=True, text=True, timeout=60, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 2, result.stderr
    assert "not a real subcommand" in result.stderr
    assert not (profile / ".agent-pull-requests").exists()
    assert not (profile / ".local" / "bin").exists()
    assert not list(caller.iterdir())
