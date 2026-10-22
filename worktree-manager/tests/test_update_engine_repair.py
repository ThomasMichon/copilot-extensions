"""Explicit native-policy RED witnesses for fresh Manager engine repair."""
from __future__ import annotations

import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins/agent-worktrees"
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real installer/lifecycle policy lane; not promotion-unit evidence",
)
REAL_RUN = subprocess.run


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repair_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
):
    # This module is intentionally independent of _conftest_sandbox's roster.
    # No source/lifecycle capability from the driving session may enter it.
    for name in tuple(os.environ):
        if name.startswith(("COPILOT_", "CLAUDE_", "AGENT_", "WORKTREE_MANAGER_", "GIT_", "UV_")):
            monkeypatch.delenv(name, raising=False)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE", "VIRTUAL_ENV"):
        monkeypatch.delenv(name, raising=False)
    previous_cwd = Path.cwd()
    temporary = ExitStack()
    request.addfinalizer(temporary.close)
    if os.name == "nt":
        root = tmp_path.parent / ("er-" + secrets.token_hex(4))
        if os.path.lexists(root):
            raise FileExistsError(f"Private fixture already exists: {root}")
        leases = _load(
            REPO / "libs/versioned-runtime/versioned_runtime_leases.py",
            "engine_repair_fixture_leases",
        )
        descriptor = leases._windows_private_lease_file(tmp_path.parent, root, "root-fixture")
        temporary.callback(shutil.rmtree, root)
        os.close(descriptor)
    else:
        root = Path(temporary.enter_context(
            tempfile.TemporaryDirectory(prefix="er-", dir=tmp_path.parent),
        ))
    home = root / "h"
    home.mkdir()
    runtime = home / ".agent-worktrees"
    runtime.mkdir()
    manager = home / ".worktree-manager"
    manager.mkdir()
    tools = root / "t"
    tools.mkdir()
    for key, value in {
        "HOME": home, "USERPROFILE": home, "AGENT_HOME": home,
        "WORKTREE_MANAGER_ROOT": manager,
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR": runtime / "control-plane-providers.d",
        "XDG_CONFIG_HOME": home / ".config", "APPDATA": home / "AppData/Roaming",
        "LOCALAPPDATA": home / "AppData/Local", "PROGRAMDATA": root / "programdata",
        "TMPDIR": root / "tmp", "TEMP": root / "tmp", "TMP": root / "tmp",
    }.items():
        Path(value).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(value))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(home)
    temporary.callback(os.chdir, previous_cwd)
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("UV_DEFAULT_INDEX", "https://index.invalid/simple")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    empty_config = root / "gitconfig"
    empty_config.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_config))
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    effect = root / "construction.json"
    monkeypatch.setenv("ENGINE_REPAIR_CONSTRUCTION_FENCE", str(effect))
    monkeypatch.setenv("ENGINE_REPAIR_FORBIDDEN_UV", str(root / "uv-called"))
    if os.name == "nt":
        (tools / "uv.cmd").write_text(
            '@echo off\r\nif "%~1"=="--version" if "%~2"=="" (\r\n'
            'echo uv 0.8.22\r\nexit /b 0\r\n)\r\n'
            '(>> "%ENGINE_REPAIR_FORBIDDEN_UV%" echo %*)\r\nexit /b 96\r\n',
        )
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    else:
        uv = tools / "uv"
        uv.write_text(
            '#!/bin/sh\nif [ "$#" = 1 ] && [ "$1" = --version ]; then\n'
            "printf 'uv 0.8.22\\n'\nexit 0\nfi\n"
            'printf "%s\\n" "$*" > "$ENGINE_REPAIR_FORBIDDEN_UV"\nexit 96\n'
        )
        uv.chmod(0o700)
        shell = shutil.which("bash")
    if not shell:
        temporary.close()
        pytest.skip("matching native installer shell unavailable")
    if not shutil.which("git"):
        temporary.close()
        pytest.skip("real installer prerequisite Git unavailable")
    source = manager / "staging"
    fresh = source / "plugins/agent-worktrees"
    ignore = shutil.ignore_patterns(
        "tests", "__pycache__", "*.pyc", "*.pyo", ".git", ".test-venvs", "*.egg-info",
    )
    shutil.copytree(PLUGIN, fresh, ignore=ignore, symlinks=True)
    shutil.copytree(REPO / "libs", source / "libs", ignore=ignore, symlinks=True)
    policy = source / "worktree-manager/scripts"
    policy.mkdir(parents=True)
    shutil.copy2(REPO / "worktree-manager/scripts/update_entry.py", policy / "update_entry.py")
    roster = source / ".github/plugin"
    roster.mkdir(parents=True)
    shutil.copy2(REPO / ".github/plugin/marketplace.json", roster / "marketplace.json")
    profile = _load(
        PLUGIN / "tests/runtime_install_fixture.py", "engine_repair_fixture_profile",
    )
    # The established fixture helper redirects copied OS-account lookups only;
    # all canonical source, owner, lifecycle and installer-action policy stays real.
    profile.isolate_account_profile(fresh, home)
    actual = fresh / "scripts/runtime_install_real.py"
    shutil.copy2(fresh / "scripts/runtime_install.py", actual)
    (fresh / "scripts/runtime_install.py").write_text(
        "import json, os, pathlib, sys\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))\n"
        "import runtime_install_real as real\n"
        "def construction_fence(*args, **kwargs):\n"
        "    pathlib.Path(os.environ['ENGINE_REPAIR_CONSTRUCTION_FENCE']).write_text(\n"
        "        json.dumps({'boundary': 'engine.numbered_candidate', "
        "'plugin': str(kwargs.get('plugin'))}))\n"
        "    raise SystemExit(79)\n"
        "real.engine.numbered_candidate = construction_fence\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(real.main())\n",
        encoding="utf-8",
    )
    incumbent = home / ".copilot/installed-plugins/fixture/agent-worktrees"
    shutil.copytree(fresh, incumbent, symlinks=True)
    published = root / "registered-payload"
    shutil.copytree(incumbent, published, symlinks=True)
    cli = tools / ("copilot.exe" if os.name == "nt" else "copilot")
    cli.write_text("synthetic acquisition executable; never launched\n", encoding="utf-8")
    cli.chmod(0o700)
    settings = home / ".copilot/settings.json"
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "fixture": {"source": {"source": "github", "repo": "Example/Extensions"}},
        },
    }), encoding="utf-8")
    ledger = home / ".copilot/config.json"
    ledger.write_text(json.dumps({"installedPlugins": [{
        "name": "agent-worktrees", "marketplace": "fixture",
        "version": json.loads((incumbent / "plugin.json").read_text())["version"],
        "cache_path": str(incumbent),
    }]}), encoding="utf-8")
    from worktree_manager import engine_origin

    acquisitions = []

    def acquire_registered_payload(command, environment, cwd):
        assert command[-3:] == ["plugin", "update", "agent-worktrees@fixture"]
        assert environment["COPILOT_HOME"] == str(home / ".copilot")
        acquisitions.append(list(command))
        shutil.copytree(published, incumbent, symlinks=True, dirs_exist_ok=True)
        return subprocess.CompletedProcess(command, 0, "updated successfully, already at latest", "")

    monkeypatch.setattr(engine_origin, "_native_acquisition", acquire_registered_payload)
    binding = _load(fresh / "scripts/command_binding.py", "engine_repair_fresh_binding")
    primitive = binding._load_primitive(fresh)
    receipt = binding.publish_legacy_binding(
        primitive, payload_root=incumbent, plugin_id="agent-worktrees", legacy_root=runtime,
        environment=dict(os.environ),
    )
    manifest_path = runtime / "deploy-manifest.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 3, "service": "agent-worktrees", "commandBinding": receipt,
    }), encoding="utf-8")
    selected = binding.resolve_command_binding(
        primitive, payload_root=incumbent, plugin_id="agent-worktrees", legacy_root=runtime,
    )
    assert selected["status"] == "selected", selected
    assert selected["source"]["identity"]["canonical"].startswith("github:")
    checkout_identity = primitive.resolve_payload_source(
        payload_root=fresh, plugin_id="agent-worktrees",
    )
    assert checkout_identity["source"]["canonical"].startswith("directory:")
    assert receipt["runtimeRoot"] == str(runtime)
    from worktree_manager import update_bootstrap

    calls = []

    def execute_actual_installer(command, **options):
        assert not options.get("start_new_session")
        cwd = options.pop("cwd", home)
        # Scoped preflight performs the real repeated governance reads before
        # the fence; its measured native path needs headroom beyond shared admission.
        timeout = 45 if options.get("env", {}).get("COPILOT_EXTENSIONS_CONTEXT") else 30
        result = REAL_RUN(
            command, **options, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        calls.append((list(command), result))
        return result

    def observe_lifecycle(command, environment, cwd):
        return execute_actual_installer(
            command, env=environment, cwd=cwd, **no_window_kwargs(),
        ).returncode

    # Observe the actual canonical CLI/native dispatch, not a fake result.
    monkeypatch.setattr(update_bootstrap, "_wait", observe_lifecycle)

    @contextmanager
    def admitted_incumbent():
        lifecycle = binding._load_lifecycle()
        with lifecycle.legacy_lifecycle(
            binding._rules(), primitive, payload_root=incumbent, plugin_id="agent-worktrees",
            legacy_root=runtime, project_root=home, environment=dict(os.environ),
            managed_deployment=True,
        ) as lease:
            # These are produced/verified by the actual supported lifecycle,
            # never a forged ticket or an authority-changing source descriptor.
            with monkeypatch.context() as child:
                child.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
                child.setenv("COPILOT_PLUGIN_STAGED_FROM", str(incumbent))
                yield lease

    case = SimpleNamespace(
        home=home, runtime=runtime, source=source, fresh=fresh, incumbent=incumbent,
        effect=effect, binding=binding, primitive=primitive, receipt=receipt,
        manifest=manifest_path, update=update_bootstrap, calls=calls,
        admitted_incumbent=admitted_incumbent,
        execute=execute_actual_installer,
        acquisitions=acquisitions,
    )
    try:
        yield case
    finally:
        uv_calls = root / "uv-called"
        assert not uv_calls.exists(), (
            "construction fence did not contain UV effects: " + uv_calls.read_text()
        )
        temporary.close()


def _diagnostic(case, rc: int) -> str:
    command, result = case.calls[-1]
    return f"repair rc={rc}; command={command!r}\n{result.stdout}\n{result.stderr}"


def test_repair_engine_marketplace_incumbent_reaches_canonical_construction(repair_case):
    case = repair_case
    (case.runtime / "bin").mkdir()
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert json.loads(case.effect.read_text())["boundary"] == "engine.numbered_candidate"
    assert json.loads(before)["commandBinding"]["installationId"] == case.receipt["installationId"]
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    script = "scripts/install.ps1" if os.name == "nt" else "scripts/install.sh"
    assert str(case.incumbent / script) in command
    assert "update" in command
    assert len(case.acquisitions) == 1


@pytest.mark.parametrize("damage", ["corrupt-installer", "missing-entire-payload"])
def test_repair_engine_rehydrates_source_before_executing_installer(repair_case, damage):
    case = repair_case
    if damage == "missing-entire-payload":
        case.incumbent.rename(case.incumbent.with_name("retained-source-backup"))
    else:
        name = "install.ps1" if os.name == "nt" else "install.sh"
        (case.incumbent / "scripts" / name).write_text("exit 97\n", encoding="utf-8")
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert case.effect.is_file()


def test_repair_engine_missing_bin_selects_constructing_action_after_admission(repair_case):
    case = repair_case
    assert not (case.runtime / "bin").exists()
    with case.admitted_incumbent():
        assert not (case.runtime / "bin").exists()
        rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert "provision" in case.calls[-1][0]


def test_repair_engine_supported_lifecycle_install_control_reaches_boundary(repair_case):
    case = repair_case
    assert not (case.runtime / "bin").exists()
    suffix = "install.ps1" if os.name == "nt" else "install.sh"
    script = case.fresh / "scripts" / suffix
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell.exe")
        command = [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "install"]
    else:
        command = [shutil.which("bash"), str(script), "install"]
    with case.admitted_incumbent():
        result = case.execute(
            command, env=dict(os.environ), **no_window_kwargs(),
        )
    assert case.effect.is_file(), _diagnostic(case, result.returncode)
    assert json.loads(case.effect.read_text())["boundary"] == "engine.numbered_candidate"
    assert result.returncode != 0, "fenced construction must not claim full repair success"


def test_repair_engine_unsealed_origin_hint_does_not_bypass_ownership(repair_case, monkeypatch):
    case = repair_case
    forged = str(case.home / "foreign-unsealed-origin")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", forged)
    rc = case.update.repair_engine(case.source)
    assert rc != 0
    assert case.effect.is_file(), _diagnostic(case, rc)
    command = case.calls[-1][0]
    assert command[command.index("--source-payload-root") + 1] == str(case.incumbent)
    assert forged not in command
    assert os.environ["COPILOT_PLUGIN_STAGED_FROM"] == forged


@pytest.mark.parametrize("mismatch", ["source", "installation", "environment"])
def test_repair_engine_refuses_foreign_incumbent_identity(repair_case, monkeypatch, mismatch):
    case = repair_case
    manifest = json.loads(case.manifest.read_text())
    if mismatch == "source":
        manifest["commandBinding"]["sourceFingerprint"] = "sha256:" + "0" * 64
    elif mismatch == "installation":
        manifest["commandBinding"]["runtimeRoot"] = str(case.home / "foreign-root")
    else:
        manifest["commandBinding"]["environment"]["homeRealPath"] = str(case.home / "foreign-home")
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(case.incumbent))
    selected = case.binding.resolve_command_binding(
        case.primitive, payload_root=case.incumbent, plugin_id="agent-worktrees",
        legacy_root=case.runtime,
    )
    assert selected["status"] == "blocked"
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert rc != 0
    assert not case.effect.exists()
    assert case.manifest.read_bytes() == before
    assert not case.calls
    assert not case.acquisitions


def test_repair_engine_refuses_unbound_context_even_with_source_hint(repair_case, monkeypatch):
    case = repair_case
    manifest = json.loads(case.manifest.read_text())
    manifest.pop("commandBinding")
    case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_STAGED_FROM", str(case.incumbent))
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before


def test_repair_engine_never_relabels_arbitrary_checkout_as_incumbent(repair_case):
    case = repair_case
    script = case.fresh / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    script.write_text("exit 97\n", encoding="utf-8")
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert str(script) not in case.calls[-1][0]
    assert case.calls[-1][0][case.calls[-1][0].index("--source-payload-root") + 1] == str(
        case.incumbent,
    )


def test_repair_engine_scoped_context_never_falls_back_to_shared_root(repair_case, monkeypatch):
    case = repair_case
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(case.home / "unbound/install.json"))
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before


def test_repair_engine_partial_success_resumes_normal_update(repair_case, monkeypatch):
    case = repair_case
    observed = case.update._wait
    phases = []

    def successful_construction_effect(command, environment, cwd):
        case.effect.unlink(missing_ok=True)
        rc = observed(command, environment, cwd)
        assert rc != 0
        assert case.effect.is_file(), _diagnostic(case, rc)
        phases.append("provision" if "provision" in command else "update")
        # Only the already-reached construction effect's result is simulated;
        # admission and the native action/dir-creation policy above were real.
        return 0

    monkeypatch.setattr(case.update, "_wait", successful_construction_effect)
    assert case.update.repair_engine(case.source) == 0
    assert phases == ["provision", "update"]
    assert (case.runtime / "bin").is_dir()
    assert json.loads(case.manifest.read_text())["commandBinding"] == case.receipt


def test_repair_engine_invalid_bin_is_not_removed_or_reinstalled(repair_case):
    case = repair_case
    path = case.runtime / "bin"
    path.write_text("unrelated retained file", encoding="utf-8")
    assert case.update.repair_engine(case.source) != 0
    assert path.read_text() == "unrelated retained file"
    assert not case.calls
    assert not case.effect.exists()


def test_repair_engine_uses_management_policy_for_managed_incumbent(repair_case):
    case = repair_case
    arguments = {
        "payload_root": case.incumbent, "plugin_id": "agent-worktrees",
        "legacy_root": case.runtime, "environment": dict(os.environ),
    }
    decision = case.primitive.resolve_runtime_deployment(**arguments)
    (case.runtime / "runtime-deployment.json").write_text(json.dumps({
        "schema": case.primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
        "owner": case.primitive._deployment_owner(decision, arguments),
    }), encoding="utf-8")
    assert case.primitive.resolve_runtime_deployment(**arguments)["deployment"][
        "allowPluginPublication"
    ] is False
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), _diagnostic(case, rc)
    assert "--managed-deployment" in case.calls[-1][0]


def test_repair_engine_revalidates_captured_owner_after_acquisition(repair_case, monkeypatch):
    from worktree_manager import engine_origin

    case = repair_case
    acquire = engine_origin._native_acquisition

    def change_owner(command, environment, cwd):
        result = acquire(command, environment, cwd)
        manifest = json.loads(case.manifest.read_text())
        manifest["commandBinding"]["runtimeRoot"] = str(case.home / "changed-root")
        case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        return result

    monkeypatch.setattr(engine_origin, "_native_acquisition", change_owner)
    assert case.update.repair_engine(case.source) != 0
    assert len(case.acquisitions) == 1
    assert not case.calls
    assert not case.effect.exists()
    assert not (case.runtime / "bin").exists()
