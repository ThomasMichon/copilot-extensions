"""Actual payload first-touch controls with no Vault runtime or backend."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from .test_bootstrap_check_reconcile_always_on import _clean_env, _make_fake_plugin
from .test_bootstrap_driver import native_bootstrap
from .test_identity_payload_gate import prepare_payload, invoke
from .test_install_snapshot_ps1 import _isolated_install_env, _stage_payload

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX installer argument validation")
@pytest.mark.parametrize("configuration", [False, True, "malformed"])
@pytest.mark.parametrize(
    "arguments,code,message",
    [(["--not-a-valid-option"], 2, "Unknown option"),
     (["--install-dir"], 1, "--install-dir requires a directory")],
)
def test_native_installer_rejects_arguments_before_activation(
    tmp_path, configuration, arguments, code, message,
):
    home, payload, _runtime, _identity, _context = prepare_payload(tmp_path, "legacy")
    (tmp_path / ".agent-vault.json").write_text(
        "{" if configuration == "malformed" else json.dumps({"enabled": configuration}),
        encoding="utf-8",
    )
    script = payload / "scripts" / "install.sh"
    shutil.copyfile(PLUGIN / "scripts" / "install.sh", script)
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        ["bash", str(script), "update", *arguments], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert message in result.stderr
    assert not (home / ".agent-vault").exists()
    assert not (home / ".copilot-extensions").exists()


@pytest.mark.parametrize("configuration", [None, False, "invalid", "malformed"])
@pytest.mark.parametrize("entry", ["bootstrap", "readiness", "update"])
def test_native_inactive_or_invalid_first_touch_never_installs(tmp_path, configuration, entry):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    _, payload, _, _, _ = prepare_payload(tmp_path, "legacy")
    (tmp_path / ".agent-vault.json").unlink()
    if configuration is not None:
        (project / ".agent-vault.json").write_text(
            "{" if configuration == "malformed"
            else json.dumps({"enabled": configuration}), encoding="utf-8",
        )
    existing = home / ".agent-vault" / "retained-custody.json"
    if configuration is False:
        existing.parent.mkdir()
        existing.write_bytes(b'{"fixture":"retained service and database custody"}')
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(home / "killswitch.json"),
    })
    suffix = "ps1" if os.name == "nt" else "sh"
    if entry == "bootstrap":
        argv = native_bootstrap(payload, project, project)
    else:
        filename = ("installer-readiness." if entry == "readiness" else "install.") + suffix
        shutil.copyfile(PLUGIN / "scripts" / filename, payload / "scripts" / filename)
        if os.name == "nt":
            argv = [
                str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
                "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts" / filename),
            ]
        else:
            argv = ["bash", str(payload / "scripts" / filename)]
        if entry == "update":
            argv.append("update")
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    invalid = configuration in ("invalid", "malformed")
    assert (result.returncode != 0) is invalid, result.stdout + result.stderr
    if entry == "readiness":
        assert json.loads(result.stdout)["state"] == ("failed" if invalid else "configuration-empty")
    elif entry == "bootstrap":
        assert json.loads(result.stdout) == {}
    if configuration is False:
        assert existing.read_bytes() == b'{"fixture":"retained service and database custody"}'
        assert list(existing.parent.iterdir()) == [existing]
    else:
        assert not (home / ".agent-vault").exists()
    assert not (home / ".copilot-extensions").exists()
    assert not (project / "handoff").exists()
    assert not (payload / "scripts" / "inline-entered").exists()


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_actual_command_first_touch_inactive_without_runtime(tmp_path, mode):
    home, payload, runtime, _identity, context = prepare_payload(tmp_path, mode)
    (tmp_path / ".agent-vault.json").unlink(missing_ok=True)
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_HOME": str(runtime),
    })
    if context:
        env["COPILOT_EXTENSIONS_CONTEXT"] = context
    result = invoke(payload, env, tmp_path, "get", "Example", "password")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "inactive" in result.stderr
    readiness = invoke(payload, env, tmp_path, "installer-readiness")
    assert readiness.returncode == 0, readiness.stderr
    assert json.loads(readiness.stdout)["state"] == "configuration-empty"
    assert not (runtime / "versions").exists()
    assert not (runtime / ".provision-status").exists()


def test_actual_payload_reader_uses_bundled_sdk_without_site_packages(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", str(payload / "scripts" / "runtime-activation.py"),
         "--cwd", str(project)], env=env, cwd=project, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configured"


@pytest.mark.parametrize("layout", ["retained", "application", "deployed", "mixed-owner", "service-cwd"])
def test_installed_reader_resolves_owner_without_payload_or_source(tmp_path, layout):
    import venv

    import agent_procutil
    import configuration_context
    import configuration_layers

    home, project, knowledge, staging = (
        tmp_path / name for name in ("home", "project", "knowledge", "stage")
    )
    for path in (home, project, knowledge, staging):
        path.mkdir()
    slots = {
        name: home / ("." + name) / "versions" / "1.0.0"
        for name in ("agent-vault", "agent-worktrees")
    }
    packages = {}
    environments = {}
    for name, slot in slots.items():
        nested = (
            name == "agent-vault" and layout == "application"
            or name == "agent-worktrees" and layout == "mixed-owner"
        )
        environment = slot / ".venv" if nested else slot
        environments[name] = environment
        venv.EnvBuilder(with_pip=False).create(environment)
        packages[name] = (
            environment / "Lib" / "site-packages" if os.name == "nt"
            else next((environment / "lib").glob("python*/site-packages"))
        )
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": "1.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
        }), encoding="utf-8")
        (slot.parent.parent / "current-version").write_text("1.0.0", encoding="utf-8")
    installed = packages["agent-vault"] / "agent_vault"
    installed.mkdir()
    for name in ("__init__.py", "config.py", "configuration.py", "configuration_scope.py", "_peer_launch.py"):
        shutil.copyfile(PLUGIN / "src" / "agent_vault" / name, installed / name)
    if layout == "service-cwd":
        shutil.copytree(
            PLUGIN / "src" / "agent_vault", installed, dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    for package in (agent_procutil, configuration_context, configuration_layers):
        shutil.copytree(
            Path(package.__file__).parent, packages["agent-vault"] / package.__name__,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    if layout == "service-cwd":
        import importlib

        for name in ("zdd", "single_instance_lease", "update_driver"):
            package = importlib.import_module(name)
            shutil.copytree(
                Path(package.__file__).parent, packages["agent-vault"] / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    if layout not in ("deployed", "mixed-owner"):
        bootstrap = slots["agent-vault"] / ".runtime-bootstrap"
        bootstrap.mkdir()
        for source in (PLUGIN / "scripts").glob("versioned_runtime*.py"):
            shutil.copyfile(source, bootstrap / source.name)
    else:
        deployed = home / ".agent-vault" / "bin"
        deployed.mkdir()
        for name in ("resolve-runtime.ps1", "resolve-runtime.sh"):
            shutil.copyfile(PLUGIN / "scripts" / name, deployed / name)
    owner = packages["agent-worktrees"] / "agent_worktrees"
    owner.mkdir()
    (owner / "__init__.py").write_text("", encoding="utf-8")
    (owner / "__main__.py").write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "assert sys.argv[1:] == ['state-root', '--configuration', '--json']\n"
        "report = json.loads(Path(__file__).with_name('report.json').read_text())\n"
        "assert Path.cwd() == Path(report['context']['base_root'])\n"
        "print(json.dumps(report))\n",
        encoding="utf-8",
    )
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "installed-reader-fixture", "execution_machine": "fixture",
            "installation_cell": "legacy", "binding": "bound",
            "base_root": str(project), "knowledge_root": str(knowledge),
        },
    }
    (owner / "report.json").write_text(json.dumps(report), encoding="utf-8")
    python = environments["agent-vault"] / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    for name in tuple(env):
        if name.startswith(("AGENT_VAULT", "VAULT_")) or name in (
            "AGENT_RT_ROOT", "AGENT_CONFIGURATION_CONTEXT", "PYTHONPATH", "PYTHONHOME",
            "COPILOT_EXTENSIONS_CONTEXT", "KPDB",
        ):
            env.pop(name)
    env["AGENT_VAULT_CONFIG"] = str(home / "absent-global.json")
    reader = (
        "import json, sys\nfrom pathlib import Path\n"
        "from agent_vault.configuration import runtime_enabled\n"
        "from agent_vault.config import resolve_context\n"
        "assert Path(sys.prefix) == Path(sys.argv[2])\n"
        "enabled = runtime_enabled(sys.argv[1])\n"
        "selected = resolve_context(sys.argv[1], strict=True)\n"
        "print(json.dumps({'enabled': enabled, 'group': selected.group}))\n"
    )
    if layout == "service-cwd":
        reader = (
            "import json, os, sys\nfrom pathlib import Path\n"
            "from agent_vault import service, config, configuration, launch\n"
            "state = Path.cwd()\nproject = sys.argv[1]\n"
            "captured = {}\n"
            "def capture(argv, **kwargs):\n"
            " captured.update(argv=argv, **kwargs)\n"
            "launch.spawn_daemon = capture\n"
            "os.chdir(project)\n"
            "launch.spawn_service(['--foreground'])\n"
            "assert captured['cwd'] == state\n"
            "os.chdir(state)\n"
            "def probe(args):\n"
            " assert Path.cwd() == state\n"
            " selected = config.resolve_context(strict=True)\n"
            " print(json.dumps({'enabled':configuration.runtime_enabled(), 'group':selected.group}))\n"
            "service._run_resident = probe\n"
            "sys.argv = ['agent_vault.service', *captured['argv'][6:]]\n"
            "service.main()\n"
        )
    for enabled in (True, False, None, "invalid"):
        config_path = knowledge / ".agent-vault.json"
        if enabled is None:
            config_path.unlink()
        else:
            config_path.write_text(json.dumps({
                "enabled": enabled, "group": "portable-group",
            }), encoding="utf-8")
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", reader, str(project), str(environments["agent-vault"])],
            cwd=slots["agent-vault"].parent.parent if layout == "service-cwd" else staging,
            env=env, capture_output=True, text=True, timeout=20,
            **no_window_kwargs(),
        )
        if enabled == "invalid":
            assert result.returncode != 0
            assert "enabled must be a boolean" in result.stderr
        elif layout == "service-cwd" and enabled is not True:
            assert result.returncode == 2, result.stdout + result.stderr
            assert "inactive" in result.stderr
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout) == {
                "enabled": enabled is True,
                "group": "portable-group" if enabled is not None else None,
            }
    assert not (home / ".agent-vault" / "scripts").exists()
    assert not (slots["agent-vault"] / "Lib" / "scripts").exists()
    assert not (installed.parents[1] / "scripts").exists()


@pytest.mark.parametrize("shell", ["python", "powershell", "pwsh"])
def test_invalid_activation_retains_safe_reader_diagnostic(tmp_path, shell):
    if shell != "python" and (os.name != "nt" or shutil.which(shell) is None):
        pytest.skip("native Windows PowerShell reader")
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    (project / ".agent-vault.json").write_text(
        '{"enabled":"private-configuration-sentinel"}', encoding="utf-8",
    )
    env = _clean_env({"HOME": str(home), "USERPROFILE": str(home)})
    if shell == "python":
        argv = [
            sys.executable, "-I", "-B", str(payload / "scripts" / "runtime-activation.py"),
            "--cwd", str(project),
        ]
    else:
        entry = tmp_path / "read-activation.ps1"
        entry.write_text(
            "param($Reader, $Project, $Python)\n"
            "$ErrorActionPreference = 'Stop'\n"
            ". $Reader\n"
            "Get-VaultRuntimeActivation -Project $Project -Python $Python\n",
            encoding="utf-8",
        )
        argv = [
            shutil.which(shell), "-NoProfile", "-NonInteractive", "-File", str(entry),
            str(payload / "scripts" / "runtime-activation.ps1"), str(project), sys.executable,
        ]
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True,
        encoding="utf-8", timeout=30, **no_window_kwargs(),
    )
    assert result.returncode != 0
    diagnostic = json.loads(result.stdout)["detail"] if shell == "python" else result.stderr
    assert "Reader failure: " in diagnostic, result.stdout + result.stderr
    assert "_validate_config_data:" in diagnostic
    assert "private-configuration-sentinel" not in diagnostic
    assert str(project) not in diagnostic
    assert not (home / ".agent-vault").exists()


def test_relative_config_reader_retains_project_after_staging_to_home(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    payload = _make_fake_plugin(tmp_path, "agent-vault")
    (tmp_path / ".agent-vault.json").unlink()
    (project / "activation.json").write_text('{"enabled":true}', encoding="utf-8")
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "AGENT_VAULT_CONFIG": "activation.json",
    })
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(payload / "scripts" / "runtime-activation.py"),
         "--cwd", str(project)], env=env, cwd=home, capture_output=True,
        text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["state"] == "configured"


@pytest.mark.skipif(os.name != "nt", reason="native Windows marketplace self-staging")
@pytest.mark.timeout(90)
def test_relative_config_survives_actual_marketplace_self_staging(tmp_path):
    import runpy

    prepared = _stage_payload(tmp_path / "prepared")
    previous_path = sys.path[:]
    try:
        release = runpy.run_path(str(PLUGIN.parents[1] / "tools" / "materialize_main.py"))
        changes = release["materialize_installer_engine_ref_into"](
            source_consumer_dir=PLUGIN, dest_consumer_dir=prepared,
            canonical_root=PLUGIN.parents[1],
        )
    finally:
        sys.path[:] = previous_path
    assert not any(line.startswith("SKIP ") for line in changes), changes
    home, project = tmp_path / "home", tmp_path / "project"
    project.mkdir()
    env = _isolated_install_env(home)
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    shutil.copytree(prepared, payload)
    (home / ".copilot" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    (project / "activation.json").write_text('{"enabled":true}', encoding="utf-8")
    env.update(AGENT_VAULT_CONFIG="activation.json", COPILOT_PLUGIN_ROOT=str(payload))
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-File",
         str(payload / "scripts" / "install.ps1"), "-Action", "stamp",
         "-InstallDir", str(home / ".agent-vault")],
        cwd=project, env=env, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    runtime = home / ".agent-vault"
    assert (runtime / "payload-dir").is_file(), result.stdout + result.stderr
    assert not (runtime / "current-version").exists()
    assert not (runtime / "service.ps1").exists()


def test_automatic_cell_selection_supplies_qualified_activation_owner(tmp_path):
    home, payload, runtime, _identity, _context = prepare_payload(tmp_path, "namespaced")
    (tmp_path / ".agent-vault.json").unlink()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / ".agent-vault.json").write_text('{"enabled":true}', encoding="utf-8")
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "activation-fixture",
            "execution_machine": "fixture",
            "installation_cell": str(runtime.parent.parent),
            "binding": "bound", "base_root": str(tmp_path),
            "knowledge_root": str(knowledge),
        },
    }
    env = _clean_env({
        "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "AGENT_VAULT_NO_SELFPROVISION": "1",
        "AGENT_CONFIGURATION_CONTEXT": json.dumps(report),
    })
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_VAULT_HOME", "AGENT_RT_ROOT"):
        env.pop(key, None)
    result = invoke(payload, env, tmp_path, "get", "Example", "password")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "runtime not provisioned" in result.stderr
    suffix = "ps1" if os.name == "nt" else "sh"
    script = payload / "scripts" / ("installer-readiness." + suffix)
    argv = (
        [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
         "-NoProfile", "-NonInteractive", "-File", str(script)]
        if os.name == "nt" else ["bash", str(script)]
    )
    readiness = subprocess.run(
        argv, cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert readiness.returncode == 1, readiness.stdout + readiness.stderr
    assert json.loads(readiness.stdout)["state"] == "failed"
    assert "runtime is not installed" in json.loads(readiness.stdout)["detail"]
    assert not (runtime / "versions").exists()
