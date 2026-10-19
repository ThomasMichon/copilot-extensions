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


def test_running_selection_admission_reuses_the_same_completed_validation(selected, monkeypatch):
    _, installation, runtime, _, _ = selected
    validate = api.validate_runtime_slot_completion
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return validate(**kwargs)

    monkeypatch.setattr(api, "validate_runtime_slot_completion", capture)
    runtime.require_current(installation, environment={}, require_selected=True)
    assert len(calls) == 1
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


@pytest.mark.parametrize("overrides", ["absent", {}, {"fixture": {"enabled": True}}, None, "invalid"])
def test_posix_legacy_admission_distinguishes_missing_and_invalid_marketplaces(tmp_path, overrides):
    from test_source_install_transactions import _host, _quote

    profile = tmp_path / "profile"
    profile.mkdir()
    policy = profile / "installation-mode.json"
    mode = {"enabled": False}
    if overrides != "absent":
        mode["marketplaces"] = overrides
    policy.write_text(json.dumps({"installationMode": mode}), encoding="utf-8")
    scripts = tmp_path / "payload" / "scripts"
    primitives = scripts / "installation-context"
    primitives.mkdir(parents=True)
    shutil.copyfile(SCRIPTS / "payload-mode.sh", scripts / "payload-mode.sh")
    shutil.copyfile(
        SCRIPTS / "installation-context" / "json-query.awk", primitives / "json-query.awk",
    )
    # Isolate wrapper admission while retaining the actual shell and JSON query.
    status = {
        "status": "provenance-blocked", "reason": "fixture-unattributed-source",
        "policy": {"state": "valid", "enabled": False, "path": policy.as_posix()},
        "legacy": {"disposition": "active", "tombstone": None},
    }
    (primitives / "installation-context.sh").write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' " + _quote(json.dumps(status), "bash") + "\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(HOME=str(profile), USERPROFILE=str(profile))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = subprocess.run(  # noqa: S603 -- owned wrapper and canonical query, status-only fixture
        [_host("bash"), str(scripts / "payload-mode.sh")],
        env=environment, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    allowed = overrides == "absent" or overrides == {}
    assert (result.returncode == 0) == allowed, (result.stdout, result.stderr)
    assert (result.stdout.strip() == "legacy") == allowed
    assert not (profile / ".agent-pull-requests").exists()


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


@pytest.mark.skipif(
    os.name != "nt" and os.environ.get("APR_NATIVE_OS_ACCOUNT_ACCEPTANCE") != "1",
    reason="Native POSIX policy needs an isolated OS account",
)
@pytest.mark.parametrize("enabled", [True, "malformed"])
def test_session_start_never_runs_legacy_reconciler_for_governed_or_invalid_policy(
    tmp_path, native_profile, enabled,
):
    profile = native_profile or tmp_path
    _policy(profile, enabled)
    environment = os.environ.copy()
    environment.update(HOME=str(profile), USERPROFILE=str(profile))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    try:
        result = native(SCRIPTS / "bootstrap-check.admission.ps1", environment=environment)
        assert result.returncode == (0 if enabled is True else 126)
        assert result.stdout == "{}"
        assert not (profile / ".agent-pull-requests").exists()
        assert not (profile / ".local").exists()
        assert not (profile / ".copilot-extensions" / "marketplaces").exists()
    finally:
        if native_profile is not None:
            policy = profile / ".copilot-extensions" / "installation-mode.json"
            policy.unlink()
            policy.parent.rmdir()


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("mode", ["cell", "legacy", "legacy-fails", "refused", "unknown"])
def test_bootstrap_admission_always_emits_one_empty_json_and_preserves_failures(tmp_path, shell, mode):
    from test_source_install_transactions import _host

    suffix = "sh" if shell == "bash" else "ps1"
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    entry = scripts / f"bootstrap-check.admission.{suffix}"
    shutil.copyfile(SCRIPTS / entry.name, entry)
    selected = "legacy" if mode.startswith("legacy") else mode
    status = 19 if mode == "legacy-fails" else 0
    if shell == "bash":
        admission = (
            "printf 'native admission refused\\n' >&2\nexit 1\n" if mode == "refused"
            else f"printf '%s\\n' '{selected}'\n"
        )
        bootstrap = (
            f"printf '{{}}unexpected context\\n'\n"
            f"printf 'legacy bootstrap diagnostic\\n' >&2\nexit {status}\n"
        )
        command = [_host(shell), str(entry)]
    else:
        admission = (
            "throw 'native admission refused'\n" if mode == "refused"
            else f"'{selected}'\n"
        )
        bootstrap = (
            "[Console]::Out.Write('{}')\nWrite-Output 'unexpected context'\n"
            f"[Console]::Error.WriteLine('legacy bootstrap diagnostic')\nexit {status}\n"
        )
        command = [_host(shell), "-NoProfile", "-NonInteractive", "-File", str(entry)]
    (scripts / f"payload-mode.{suffix}").write_text(admission, encoding="utf-8")
    (scripts / f"bootstrap-check.{suffix}").write_text(bootstrap, encoding="utf-8")
    result = subprocess.run(  # noqa: S603 -- actual wrapper with bounded native policy/delegate fixtures
        command, capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.stdout == "{}"
    expected = 126 if mode == "refused" else status
    if mode == "unknown":
        assert result.returncode != 0
    else:
        assert result.returncode == expected, result.stderr
    if mode.startswith("legacy"):
        assert "legacy bootstrap diagnostic" in result.stderr
    elif mode == "refused":
        assert "native admission refused" in result.stderr


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


@pytest.fixture
def legacy_staging(tmp_path, native_profile):
    if os.name != "nt" and native_profile is None:
        pytest.skip("Native POSIX admission requires a disposable OS account")
    profile = native_profile or tmp_path / "profile"
    profile.mkdir(exist_ok=True)
    marketplace = profile / ".copilot" / "installed-plugins" / "staging-fixture"
    payload = marketplace / "agent-pull-requests"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in ("install.ps1", "install.sh", "payload-mode.ps1", "payload-mode.sh",
                 "versioned_runtime_native.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    shutil.copytree(SCRIPTS / "installation-context", scripts / "installation-context")
    (payload / "plugin.json").write_text(
        json.dumps({"name": "agent-pull-requests", "version": "1.0.0"}), encoding="utf-8",
    )
    (payload / "content.txt").write_text("original bytes\n", encoding="utf-8")
    descriptor = {"source": "github", "repo": "example/staging-fixture"}
    settings = profile / ".copilot" / "settings.json"
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {"staging-fixture": {"source": descriptor}},
    }), encoding="utf-8")
    identity = api.source_identity(api.normalize_source(descriptor), "staging-fixture")
    _policy(profile, False)
    policy = profile / ".copilot-extensions" / "installation-mode.json"
    policy.write_text(json.dumps({
        "schema": api.POLICY_SCHEMA, "version": 1,
        "installationMode": {
            "enabled": True,
            "marketplaces": {identity["marketplaceId"]: {"enabled": False}},
        },
    }), encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
            "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
            "COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD",
        }
    }
    environment.update(
        HOME=str(profile), USERPROFILE=str(profile),
        COPILOT_PLUGIN_INSTALL_SMOKE="1", COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP="0",
    )
    try:
        yield profile, payload, policy, settings, environment
    finally:
        # Each exact subtree belongs to this fixture, including on a disposable
        # native-account runner; never remove the account or shared .copilot root.
        shutil.rmtree(marketplace)
        settings.unlink()
        if (profile / ".agent-pull-requests").exists():
            shutil.rmtree(profile / ".agent-pull-requests")
        shutil.rmtree(profile / ".copilot-extensions")


@pytest.mark.parametrize("action", ["install", "update", "stamp", "provision", "uninstall"])
@pytest.mark.parametrize("policy_form", ["marketplace-opt-out", "disabled-with-other-override"])
def test_legacy_staging_installer_revalidates_marketplace_opt_out(
    legacy_staging, action, policy_form,
):
    profile, payload, policy, _, environment = legacy_staging
    if policy_form == "disabled-with-other-override":
        policy.write_text(json.dumps({
            "schema": api.POLICY_SCHEMA, "version": 1,
            "installationMode": {
                "enabled": False,
                "marketplaces": {"another-source--0123456789abcdef": {"enabled": True}},
            },
        }), encoding="utf-8")
    result = native(payload / "scripts" / "install", [action], environment=environment)
    assert result.returncode == 0, (result.stdout, result.stderr)
    smoke = json.loads((profile / ".agent-pull-requests" / "smoke.json").read_text("utf-8-sig"))
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == payload
    assert Path(smoke["ran_from"]).parent.parent.parent == (
        profile / ".agent-pull-requests" / ".install-stage"
    )
    assert not (profile / ".local").exists()
    assert not (profile / ".copilot-extensions" / "marketplaces").exists()


@pytest.mark.parametrize("damage", [
    "none", "stage-content", "origin-content", "extra-stage-entry", "missing-origin",
    "missing-flag", "relative-origin", "noninstalled-origin", "governance", "settings",
    "ordinary-payload-hint", "linked-entry",
])
def test_legacy_staging_reentry_never_trusts_flags_or_stale_admission(
    legacy_staging, tmp_path, damage,
):
    profile, payload, policy, settings, environment = legacy_staging
    staged = profile / ".agent-pull-requests" / ".install-stage" / "fixture-0" / payload.name
    shutil.copytree(payload, staged)
    environment.update(COPILOT_PLUGIN_INSTALL_STAGED="1", COPILOT_PLUGIN_STAGED_FROM=str(payload))
    if damage == "stage-content":
        (staged / "content.txt").write_text("changed stage", encoding="utf-8")
    elif damage == "origin-content":
        (payload / "content.txt").write_text("changed origin", encoding="utf-8")
    elif damage == "extra-stage-entry":
        (staged / "extra").write_text("not in origin", encoding="utf-8")
    elif damage == "missing-origin":
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(payload.parent / "missing")
    elif damage == "missing-flag":
        environment.pop("COPILOT_PLUGIN_INSTALL_STAGED")
    elif damage == "relative-origin":
        environment["COPILOT_PLUGIN_STAGED_FROM"] = "relative/origin"
    elif damage == "noninstalled-origin":
        environment["COPILOT_PLUGIN_STAGED_FROM"] = str(staged)
    elif damage == "governance":
        _policy(profile, True)
    elif damage == "settings":
        settings.write_text("{}", encoding="utf-8")
    elif damage == "ordinary-payload-hint":
        # An ordinary unattributed payload may not borrow a legacy source's
        # identity just by exporting the shared staging hints.
        staged = tmp_path / "ordinary" / payload.name
        shutil.copytree(payload, staged)
    elif damage == "linked-entry":
        try:
            for root in (payload, staged):
                (root / "linked.txt").symlink_to(root / "content.txt")
        except OSError as error:
            pytest.skip(f"Native symlink creation unavailable: {error}")

    def owned_files():
        return {
            path.relative_to(profile): path.read_bytes()
            for root in (payload.parent, policy.parent, profile / ".agent-pull-requests")
            for path in root.rglob("*") if path.is_file()
        } | {settings.relative_to(profile): settings.read_bytes()}

    before = owned_files()
    result = native(staged / "scripts" / "install", ["stamp"], environment=environment)
    assert (result.returncode == 0) == (damage == "none"), (result.stdout, result.stderr)
    if damage != "none":
        assert not (profile / ".agent-pull-requests" / "smoke.json").exists()
        assert owned_files() == before
    assert not (profile / ".local").exists()
    assert not (profile / ".copilot-extensions" / "marketplaces").exists()


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
