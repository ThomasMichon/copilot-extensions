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
import time

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

from agent_worktrees.update_runtime_application import PreparedApplication

from source_application_fixture import application_source
from test_source_python_publication import native_hosts

REPOSITORY = Path(__file__).resolve().parents[3]


def _copy_native_checkout(plugin: Path, repository: Path, checkout: Path, *, ignore=None) -> None:
    assert not checkout.is_relative_to(repository)
    if plugin in (repository / "source", repository / "source/plugins/agent-worktrees"):
        repository = repository / "source"
    assert plugin in (repository, repository / "plugins/agent-worktrees")
    if plugin == repository:
        shutil.copytree(
            plugin, checkout / "plugins/agent-worktrees",
            ignore=ignore or shutil.ignore_patterns(".git"),
        )
    else:
        shutil.copytree(repository, checkout, ignore=ignore)
    marketplace = checkout / ".github/plugin/marketplace.json"
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    marketplace.write_text(json.dumps({
        "name": "runtime-fixture",
        "plugins": [{"name": "agent-worktrees", "source": "plugins/agent-worktrees"}],
    }), encoding="utf-8")


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("standalone", [False, True])
def test_native_checkout_preserves_both_frozen_layouts(tmp_path, standalone, nested):
    repository = tmp_path / "repository"
    source = repository / "source" if nested else repository
    plugin = source if standalone else source / "plugins/agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text("frozen package", encoding="utf-8")
    (repository / ".git").mkdir()
    if not standalone:
        (source / "libs").mkdir()
        (source / "libs/dependency").write_text("frozen dependency", encoding="utf-8")
    (tmp_path / "unrelated").write_text("not source", encoding="utf-8")
    checkout = tmp_path / "checkout"
    _copy_native_checkout(plugin, repository, checkout)
    assert (checkout / "plugins/agent-worktrees/pyproject.toml").read_text() == "frozen package"
    catalog = json.loads((checkout / ".github/plugin/marketplace.json").read_text(encoding="utf-8"))
    assert checkout / catalog["plugins"][0]["source"] == checkout / "plugins/agent-worktrees"
    assert not (checkout / "unrelated").exists()
    assert not (checkout / "plugins/agent-worktrees/.git").exists()
    if not standalone:
        assert (checkout / "libs/dependency").read_text() == "frozen dependency"


@pytest.mark.parametrize("nested", [False, True])
def test_native_checkout_refuses_recursive_destination(tmp_path, nested):
    repository = tmp_path / "repository"
    plugin = repository / "source" if nested else repository
    plugin.mkdir(parents=True)
    checkout = repository / "checkout"
    with pytest.raises(AssertionError):
        _copy_native_checkout(plugin, repository, checkout)
    assert not checkout.exists()


@pytest.mark.parametrize("standalone", [False, True])
def test_native_checkout_preserves_runtime_only_copy_exclusions(tmp_path, standalone):
    repository = tmp_path / "repository"
    source = repository / "source"
    plugin = source if standalone else source / "plugins/agent-worktrees"
    tests = plugin / "tests"
    tests.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text("frozen package", encoding="utf-8")
    (tests / "test_fixture.py").write_text("not a runtime input", encoding="utf-8")
    (repository / ".git").mkdir()
    checkout = tmp_path / "checkout"
    _copy_native_checkout(
        plugin, repository, checkout,
        ignore=shutil.ignore_patterns(".git", "tests", "__pycache__", "*.pyc"),
    )
    assert (checkout / "plugins/agent-worktrees/pyproject.toml").is_file()
    assert not (checkout / "plugins/agent-worktrees/tests").exists()
    assert not (checkout / ".git").exists()


def test_native_checkout_resolves_standalone_git_root(tmp_path, monkeypatch):
    plugin = tmp_path / "source"
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text("frozen package", encoding="utf-8")
    hooks = tmp_path / "empty-hooks"
    hooks.mkdir()
    git = shutil.which("git")
    assert git is not None
    unrelated = tmp_path / "unrelated"
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.check_call(
        [git, "-C", str(plugin), "init", "-q", "--template", str(hooks)],
        env=environment, **no_window_kwargs(),
    )
    repository = Path(subprocess.check_output(
        [git, "-C", str(plugin), "rev-parse", "--show-toplevel"],
        env=environment, text=True, **no_window_kwargs(),
    ).strip()).resolve()
    checkout = tmp_path / "checkout"
    _copy_native_checkout(plugin, repository, checkout)
    assert (checkout / "plugins/agent-worktrees/pyproject.toml").read_bytes() == (
        plugin / "pyproject.toml"
    ).read_bytes()
    assert not unrelated.exists()


@pytest.mark.timeout(1500)
def test_native_prepared_source_preserves_managed_global_owner(
    tmp_path, monkeypatch, capsys,
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
        if (key == TEST_CONTEXT_ENV
            or not key.startswith(("GIT_", "COPILOT_EXTENSIONS_", "AGENT_WORKTREES_")))
        and key not in ("COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM", "VIRTUAL_ENV",
                        "PYTHONPATH", "PYTHONHOME")
    }
    assert environment.get(TEST_CONTEXT_ENV), "native owner requires its runner-owned daemon lifecycle"
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT=str(project),
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
    )

    def run(command, label, timeout=120):
        started = time.monotonic()
        with capsys.disabled():
            print(f"Native source phase: {label} (budget {timeout}s)", flush=True)
        completed = subprocess.run(
            command, cwd=project, env=environment, capture_output=True, text=True,
            timeout=timeout, **no_window_kwargs(),
        )
        assert completed.returncode == 0, (
            f"{label}: {completed.stdout}\n{completed.stderr}"
        )
        with capsys.disabled():
            print(f"Native source phase passed: {label} ({time.monotonic() - started:.3f}s)", flush=True)
        return completed.stdout

    monkeypatch.syspath_prepend(str(REPOSITORY / "tools"))
    checkout, commit = application_source(
        REPOSITORY, tmp_path / "source", home, deadline=time.monotonic() + 120,
    )
    owner = home / ".copilot/installed-plugins/example/agent-worktrees"
    run([
        sys.executable, "-I", "-B", "-c",
        "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);"
        "from prepare_runtime_source import _materialize;"
        "_materialize(Path(sys.argv[2]),'agent-worktrees',Path(sys.argv[3]))",
        str(REPOSITORY / "tools"), str(checkout), str(owner),
    ], "materialize isolated release owner")
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

    native_shell = "ps1" if os.name == "nt" else "sh"
    shell, executable = next(host for host in hosts if host[0] == native_shell)
    run(provision(owner, shell, executable), "provision release owner", 300)
    previous = (root / "current-version").read_text().strip()
    before = json.loads((root / "deploy-manifest.json").read_text())
    owner_python = Path(before["venv"]) / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    from build_toolchain_lock import _query_marker_environment

    owner_target = _query_marker_environment(owner_python, deadline=time.monotonic() + 30)
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
    snapshots = json.loads(run([
        str(owner_python), "-I", "-B", str(REPOSITORY / "tools/local_install_source.py"),
        "--source-checkout", str(checkout), "--source-commit", commit, "--main-head", commit,
        "--output", str(tmp_path / "prepared"), "--prepare-applications",
        "--branch", "dev", "--project", "agent-worktrees", "--uv", uv, "--timeout", "600",
        "--source-descriptor", json.dumps({"source": "github", "repo": "Example/Plugins"}),
    ], "prepare complete application", 660))
    snapshot = Path(snapshots["agent-worktrees"])
    prepared_profile = json.loads((snapshot / "profiles" / "agent-worktrees.json").read_text())
    assert prepared_profile["target"]["environment"] == owner_target
    reuse_probe = (
        "import sys,time;from pathlib import Path;"
        "repository,checkout,output,snapshot=map(Path,sys.argv[1:5]);"
        "sys.path[:0]=[str(repository/'tools'),str(repository/'plugins/agent-worktrees/tests')];"
        "from source_application_fixture import assert_application_reuse;"
        "assert_application_reuse(checkout,sys.argv[5],'agent-worktrees',output,snapshot,"
        "uv=sys.argv[6],deadline=time.monotonic()+120);"
        "print('COMPLETE_PREPARATION_REUSED_WITHOUT_CONSTRUCTION')"
    )
    assert "COMPLETE_PREPARATION_REUSED_WITHOUT_CONSTRUCTION" in run([
        str(owner_python), "-I", "-B", "-c", reuse_probe, str(REPOSITORY), str(checkout),
        str(tmp_path / "prepared-reuse"), str(snapshot), commit, uv,
    ], "reuse complete promoted application without construction", 150)
    application = PreparedApplication(snapshot, "agent-worktrees", "null")
    update_probe = (
        "import json,os,sys;from pathlib import Path;"
        "from agent_worktrees import update_runtime,update_runtime_plan;"
        "from agent_worktrees.update_runtime_application import PreparedApplication;"
        "from agent_worktrees.update_runtime_source import PreparedRuntimeSource;"
        "owner,snapshot,project=map(Path,sys.argv[1:4]);"
        "app=PreparedApplication(snapshot,'agent-worktrees','null');"
        "source=PreparedRuntimeSource(app.payload,app.identity,app);"
        "step=update_runtime_plan.plan_engine(owner,"
        "'windows' if os.name=='nt' else 'linux',force=False,source=source,project_root=project);"
        "assert not step.status,step.status;"
        "assert step.payload==owner and step.artifact==app.payload;"
        "status=update_runtime._execute_runtime_step(step);assert status=='OK',status;"
        "assert source.deployed_version(step.runtime_root,owner)==app.version;"
        "print('PINNED_UPDATE_PRESERVED_GLOBAL_OWNER')"
    )
    assert "PINNED_UPDATE_PRESERVED_GLOBAL_OWNER" in run([
        str(owner_python), "-I", "-B", "-X", "utf8", "-c", update_probe, str(owner), str(snapshot),
        str(project),
    ], "update prepared source through collected runtime plan", 300)
    assert json.loads(run(binding_command, "read updated command binding")) == binding
    environment["COPILOT_PLUGIN_STAGED_FROM"] = str(owner)
    slot = root / "versions" / application.version
    complete = slot / ".install-complete.json"
    completion = (complete.read_bytes(), complete.stat().st_mtime_ns)
    for shell, executable in hosts:
        command = (
            [executable, "-NoProfile", "-File", str(slot / "install.ps1")] if shell == "ps1"
            else [executable, (slot / "install.sh").as_posix()]
        )
        run(command, f"reuse complete application via {shell}", 180)
        assert (complete.read_bytes(), complete.stat().st_mtime_ns) == completion
        assert (root / "current-version").read_text().strip() == application.version
        assert (root / "last-known-good").read_text().strip() == application.version
        assert json.loads((slot / "application.json").read_text()) == json.loads(application.declaration)
        assert json.loads((slot / "runtime-source.json").read_text()) == json.loads(
            application.identity,
        )["runtime_source"]
        assert json.loads((slot / "install-success.json").read_text()) == {
            "state": "installed", "version": application.version,
        }
        assert json.loads(run(binding_command, "read retained command binding")) == binding
        assert (root / "versions" / previous).is_dir()
        assert {
            path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in retained
        } == retained
        installation, info, module = json.loads(run([
            str(slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
            "-I", "-B", "-c",
            "import json,agent_worktrees;from agent_worktrees import config,_build_info;"
            "print(json.dumps([str(config.install_dir()),_build_info.BUILD_INFO,"
            "agent_worktrees.__file__]))",
        ], "read installed identity"))
        assert Path(installation) == root
        assert info["commit"] == commit
        assert info["runtime_version"] == application.version
        assert Path(module).is_relative_to(slot / ".venv")
        shim = home / ".local/bin/agent-worktrees"
        if os.name == "nt":
            powershell = next(exe for kind, exe in hosts if kind == "ps1")
            entry = [powershell, "-NoProfile", "-File", str(shim.with_suffix(".ps1"))]
        else:
            entry = [executable, str(shim)]
        assert run([*entry, "--version"], "execute existing global command").startswith(
            "agent-worktrees " + application.version + " "
        )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM retained").fetchall() == [("existing state",)]
    selected_python = slot / ".venv" / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    missing_owner = tmp_path / "removed-development-checkout"
    run([
        str(selected_python), "-I", "-B", "-c",
        "import pathlib,sys;"
        "sys.path.insert(0,str(pathlib.Path(sys.prefix).parent/'.runtime-bootstrap'));"
        "import versioned_runtime as runtime;"
        "runtime.claim_dev(pathlib.Path(sys.argv[1]),sys.argv[2],previous_version=sys.argv[3])",
        str(root), str(missing_owner), previous,
    ], "claim retained development generation")
    # Exercise standalone restoration after its publisher and source have gone.
    policy_path.rename(tmp_path / "retained-managed-policy.json")
    owner.rename(tmp_path / "removed-installed-publisher")
    checkout.rename(tmp_path / "removed-source-checkout")
    snapshot.rename(tmp_path / "removed-prepared-application")
    assert run([*entry, "--version"], "execute source-absent global command").startswith(
        "agent-worktrees " + application.version + " "
    )
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
