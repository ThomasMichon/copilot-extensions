"""Prepared source replaces code without replacing the global installation owner."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from runtime_install_fixture import isolate_account_profile
import test_python_runtime_install as runtime_fixture
from test_source_python_publication import native_hosts

REPOSITORY = Path(__file__).resolve().parents[3]
prepared_native_runtime_source = runtime_fixture.native_runtime_source


@pytest.mark.timeout(570)
def test_native_prepared_source_preserves_managed_global_owner(
    tmp_path, prepared_native_runtime_source,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native source deployment requires uv")
    hosts = [
        (shell, executable) for shell, executable in native_hosts()
        if os.name == "nt" or shell == "sh"
    ]
    assert hosts, "native source deployment requires a supported shell"
    home = tmp_path / "home"
    home.mkdir()
    project = home / "project"
    settings = project / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }), encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("GIT_", "COPILOT_EXTENSIONS_", "AGENT_WORKTREES_"))
        and key not in ("COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM", "VIRTUAL_ENV",
                        "PYTHONPATH", "PYTHONHOME")
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(project),
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )

    def run(command, label, timeout=120):
        completed = subprocess.run(
            command, cwd=project, env=environment, capture_output=True, text=True,
            timeout=timeout, **no_window_kwargs(),
        )
        assert completed.returncode == 0, (
            f"{label}: {completed.stdout}\n{completed.stderr}"
        )
        return completed.stdout

    plugin, _commit = prepared_native_runtime_source
    checkout = tmp_path / "checkout"
    shutil.copytree(plugin.parents[1], checkout)
    shutil.copytree(
        REPOSITORY / "libs/installer-engine", checkout / "libs/installer-engine",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )
    shutil.copytree(REPOSITORY / "worktree-manager/bin", checkout / "worktree-manager/bin")
    narrative = Path("docs/patterns/entity-relationship-model.md")
    (checkout / narrative).parent.mkdir(parents=True)
    shutil.copyfile(REPOSITORY / narrative, checkout / narrative)
    copied_plugin = checkout / "plugins/agent-worktrees"
    isolate_account_profile(copied_plugin, home)
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    git = shutil.which("git")
    assert git is not None
    git_prefix = [
        git, "-C", str(checkout), "-c", f"core.hooksPath={hooks}",
        "-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
        "-c", "commit.gpgsign=false", "-c", "core.longpaths=true",
    ]
    run([*git_prefix, "add", "--all", "--force", "."], "stage isolated source")
    run([*git_prefix, "commit", "--allow-empty", "-qm", "Isolated native source fixture"],
        "commit isolated source")
    commit = run([*git_prefix, "rev-parse", "HEAD"], "read source commit").strip()
    run([*git_prefix, "update-ref", "refs/remotes/origin/dev", commit], "select merged source")
    prepared = json.loads(run([
        sys.executable, "-I", "-B", str(REPOSITORY / "tools/prepare_runtime_source.py"),
        "--source", str(checkout), "--commit", commit, "--plugin", "agent-worktrees",
        "--output", str(tmp_path / "prepared"),
    ], "prepare admitted source"))
    artifact = Path(prepared["payload"])
    identity = prepared["identity"]
    owner = home / ".copilot/installed-plugins/example/agent-worktrees"
    shutil.copytree(
        artifact, owner, ignore=shutil.ignore_patterns("runtime-source.json"),
    )
    root = home / ".agent-worktrees"

    def provision(payload, shell, executable):
        if shell == "ps1":
            command = [
                executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(payload / "scripts/install.ps1"), "provision",
                "-InstallDir", str(root), "-BootstrapPython", sys.executable,
            ]
        elif os.name == "nt":
            command = [
                executable, "-c",
                'exec bash "$1" provision --install-dir "$(cygpath -u "$2")"',
                "--", (payload / "scripts/install.sh").as_posix(), str(root),
            ]
        else:
            command = [
                executable, str(payload / "scripts/install.sh"), "provision",
                "--install-dir", str(root),
            ]
        return [
            sys.executable, "-I", "-B", str(owner / "scripts/command_binding.py"),
            "--payload-root", str(owner), "--source-payload-root", str(owner),
            "--plugin-id", "agent-worktrees", "--legacy-root", str(root),
            "--project-root", str(project), "--run-lifecycle", "--managed-deployment",
            "--", *command,
        ]

    shell, executable = hosts[0]
    run(provision(owner, shell, executable), "provision release owner", 300)
    previous = (root / "current-version").read_text().strip()
    before = json.loads((root / "deploy-manifest.json").read_text())
    binding_command = [
        sys.executable, "-I", "-B", str(owner / "scripts/command_binding.py"),
        "--payload-root", str(owner), "--plugin-id", "agent-worktrees",
        "--legacy-root", str(root),
    ]
    binding = json.loads(run(binding_command, "read original command binding"))
    assert binding["status"] == "selected"
    helper_path = owner / "scripts/installation-context/installation_context.py"
    spec = importlib.util.spec_from_file_location("native_source_owner_context", helper_path)
    assert spec and spec.loader
    helper = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = helper
    try:
        spec.loader.exec_module(helper)
        arguments = dict(
            payload_root=owner, plugin_id="agent-worktrees", legacy_root=root,
            project_root=project, environment=environment,
            durable_home=home / ".copilot-extensions", os_profile=home,
        )
        decision = helper.resolve_runtime_deployment(**arguments)
        policy = {
            "schema": helper.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
            "owner": helper._deployment_owner(decision, arguments),
        }
    finally:
        del sys.modules[spec.name]
    policy_path = root / "runtime-deployment.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    config = root / "source-owner-fixture.json"
    config.write_text('{"existing":true}\n', encoding="utf-8")
    database = root / "source-owner-fixture.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE retained (value TEXT)")
        connection.execute("INSERT INTO retained VALUES ('existing state')")
    retained = {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (config, database, policy_path)
    }
    owner_python = Path(before["venv"]) / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    update_probe = (
        "import json,os,sys;from pathlib import Path;"
        "from agent_worktrees import update_runtime,update_runtime_plan;"
        "from agent_worktrees.update_runtime_source import PreparedRuntimeSource;"
        "owner,artifact,project=map(Path,sys.argv[1:4]);"
        "source=PreparedRuntimeSource(artifact,sys.argv[4]);"
        "step=update_runtime_plan.plan_engine(owner,"
        "'windows' if os.name=='nt' else 'linux',force=True,source=source,project_root=project);"
        "assert not step.status,step.status;"
        "assert step.payload==owner and step.artifact==artifact;"
        "status=update_runtime._execute_runtime_step(step);assert status=='OK',status;"
        "assert source.deployed_version(step.runtime_root,owner)==source.version;"
        "print('PINNED_UPDATE_PRESERVED_GLOBAL_OWNER')"
    )
    assert "PINNED_UPDATE_PRESERVED_GLOBAL_OWNER" in run([
        str(owner_python), "-I", "-B", "-X", "utf8", "-c", update_probe, str(owner), str(artifact),
        str(project), json.dumps({
            "runtime_source": identity, "runtime_version": identity["runtime_version"],
        }),
    ], "update prepared source through collected runtime plan", 300)
    environment["COPILOT_PLUGIN_STAGED_FROM"] = str(owner)
    for shell, executable in hosts:
        run(provision(artifact, shell, executable), f"deploy prepared source via {shell}", 180)
        assert (root / "current-version").read_text().strip() == identity["runtime_version"]
        manifest = json.loads((root / "deploy-manifest.json").read_text())
        assert manifest["runtime_source"] == identity
        assert json.loads(run(binding_command, "read retained command binding")) == binding
        assert manifest["source"]["path"] == owner.as_posix()
        assert manifest["source"]["version"] == before["source"]["version"]
        assert Path(manifest["venv"]).parent == root / "versions"
        assert (root / "versions" / previous).is_dir()
        assert {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in retained
        } == retained
        installation, info, module = json.loads(run([
            str(Path(manifest["venv"]) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
            "-I", "-B", "-c",
            "import json,agent_worktrees;from agent_worktrees import config,_build_info;"
            "print(json.dumps([str(config.install_dir()),_build_info.BUILD_INFO,"
            "agent_worktrees.__file__]))",
        ], "read installed identity"))
        assert Path(installation) == root
        assert info["commit"] == commit
        assert info["runtime_version"] == identity["runtime_version"]
        assert Path(module).is_relative_to(Path(manifest["venv"]))
        shim = home / ".local/bin/agent-worktrees"
        if os.name == "nt":
            powershell = next(exe for kind, exe in hosts if kind == "ps1")
            entry = [powershell, "-NoProfile", "-File", str(shim.with_suffix(".ps1"))]
        else:
            entry = [executable, str(shim)]
        assert run([*entry, "--version"], "execute existing global command").startswith(
            "agent-worktrees " + identity["runtime_version"] + " "
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM retained").fetchall() == [("existing state",)]
    selected_python = Path(manifest["venv"]) / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    missing_owner = tmp_path / "removed-development-checkout"
    run([
        str(selected_python), "-I", "-B", "-c",
        "import pathlib,sys;"
        "sys.path.insert(0,str(pathlib.Path(sys.prefix)/'.runtime-bootstrap'));"
        "import versioned_runtime as runtime;"
        "runtime.claim_dev(pathlib.Path(sys.argv[1]),sys.argv[2],previous_version=sys.argv[3])",
        str(root), str(missing_owner), previous,
    ], "claim retained development generation")
    # Exercise standalone restoration after its publisher and source have gone.
    policy_path.rename(tmp_path / "retained-managed-policy.json")
    owner.rename(tmp_path / "removed-installed-publisher")
    checkout.rename(tmp_path / "removed-source-checkout")
    run([
        str(selected_python), "-I", "-B", "-m", "agent_worktrees",
        "dev-release", "--owner", str(missing_owner), "--json",
    ], "release attributed development generation", 180)
    restored = json.loads((root / "deploy-manifest.json").read_text())
    assert (root / "current-version").read_text().strip() == previous
    assert restored["commandBinding"] == before["commandBinding"]
    assert {
        path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in (config, database)
    } == {path: retained[path] for path in (config, database)}
