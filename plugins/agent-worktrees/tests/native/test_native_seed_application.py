"""First-touch native handoff admission through the real wheel application."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from agent_procutil import no_window_kwargs
from agent_procutil._interpreter import physical_executable
from agent_procutil.daemon_instances import daemon_test_lifecycle
from agent_procutil.owned_process import run_caller_owned
import pytest

ROOT = Path(__file__).resolve().parents[4]
SOURCE = ROOT / "plugins" / "agent-worktrees"


def _run(arguments, *, cwd, env, deadline):
    result = subprocess.run(
        list(map(str, arguments)), cwd=cwd, env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=max(0.01, deadline - time.monotonic()),
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _copy_application_inputs(source, destination):
    for entry in source.iterdir():
        if entry.is_file():
            shutil.copyfile(entry, destination / entry.name)
        elif entry.name in {"src", "scripts", "libs"}:
            shutil.copytree(
                entry, destination / entry.name,
                ignore=shutil.ignore_patterns(
                    ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "*.egg-info",
                ),
            )


def test_application_inputs_preserve_materialized_local_dependencies(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import build_python_artifacts as builder
    import materialize_main as materializer

    original, copied = tmp_path / "materialized", tmp_path / "copied"
    module = original / "libs" / "widget" / "src" / "widget" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text("VALUE = 'fixture'\n", encoding="utf-8")
    (original / "libs" / "widget" / "pyproject.toml").write_text(
        '[project]\nname = "demo-widget"\nversion = "0.1.0"\n', encoding="utf-8",
    )
    (original / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n'
        '[tool.uv.sources]\ndemo-widget = { path = "libs/widget" }\n',
        encoding="utf-8",
    )
    copied.mkdir()
    _copy_application_inputs(original, copied)
    materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=original, dest_consumer_dir=copied, canonical_root=tmp_path,
    )
    dependencies = dict(builder.resolve_vendored_libs(copied))
    assert dependencies["widget"] == (copied / "libs" / "widget").resolve()
    assert (dependencies["widget"] / "src" / "widget" / "__init__.py").read_bytes() == module.read_bytes()


@pytest.mark.parametrize("backends", [0, 1, 2])
def test_native_backend_receipt_selection_preserves_launcher_stages(tmp_path, backends):
    from test_headed_worktrees_copilot import _backend_execution

    documents = {}
    for name in ("launcher", *(f"backend-{index}" for index in range(backends))):
        path = tmp_path / (name + ".execution.json")
        document = json.dumps({"events": [{
            "phase": "process-spawned",
            "details": {"boundary": "launcher-command" if name == "launcher" else "backend-command"},
        }]})
        path.write_text(document, encoding="utf-8")
        documents[path] = document
    if backends > 1:
        with pytest.raises(AssertionError, match="more than one native backend"):
            _backend_execution(tmp_path)
    elif backends:
        path, receipt = _backend_execution(tmp_path)
        assert path.name == "backend-0.execution.json"
        assert receipt["events"][0]["details"]["boundary"] == "backend-command"
    else:
        assert _backend_execution(tmp_path) is None
    assert {path: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()} == documents


@pytest.mark.skipif(os.name != "nt", reason="Native Windows command-line quoting")
def test_private_mux_adapter_preserves_census_command_shape(tmp_path):
    from agent_remote_driver import windows_mux_processes
    from test_headed_worktrees_copilot import _prepare_private_mux_adapter

    compiler = next((path for framework in ("Framework64", "Framework") if (
        path := Path(os.environ.get("WINDIR", "")) / "Microsoft.NET" /
        framework / "v4.0.30319" / "csc.exe"
    ).is_file()), None)
    assert compiler, "The fixture's private mux adapter requires the system C# compiler"
    backend = tmp_path / "echo tools"
    backend.mkdir()
    source, binary = backend / "Echo.cs", backend / "psmux.exe"
    source.write_text(
        "using System; using System.Text;\n"
        "class Echo { static void Main(string[] args) {\n"
        "Console.WriteLine(Environment.CommandLine);\n"
        "foreach (var value in args) Console.WriteLine("
        "Convert.ToBase64String(Encoding.UTF8.GetBytes(value)));\n"
        "} }\n",
        encoding="utf-8",
    )
    compiled = run_caller_owned(
        [str(compiler), "/nologo", f"/out:{binary}", str(source)], cwd=tmp_path, timeout=30,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    directory, namespace_file = _prepare_private_mux_adapter(
        tmp_path, binary=binary, manager_target="wt-fixture", environment=dict(os.environ),
    )
    arguments = ["--version"]
    result = run_caller_owned(
        [str(directory / "psmux.exe"), *arguments], cwd=tmp_path, timeout=15,
        max_stdout_bytes=8192, max_stderr_bytes=8192,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    command, *encoded = result.stdout.splitlines()
    assert [
        base64.b64decode(value).decode("utf-8") for value in encoded
    ] == ["-L", namespace_file.read_text(encoding="utf-8"), *arguments]
    match = windows_mux_processes._COMMAND.match(command)
    assert match is not None, command
    assert match[1] == "-L" and match[2] == "--version"


def test_public_fixture_git_mutations_ignore_inherited_repository_selectors(tmp_path):
    from agent_worktrees.git_repository_environment import repository_identity_env
    from test_headed_worktrees_copilot import _initialize_fixture_repository

    environment = repository_identity_env()
    git = shutil.which("git", path=environment.get("PATH"))
    assert git

    def command(root, *arguments):
        result = run_caller_owned(
            [git, *arguments], cwd=root, env=environment, timeout=15,
            max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    foreign, checkout = tmp_path / "foreign", tmp_path / "checkout"
    foreign.mkdir()
    checkout.mkdir()
    command(foreign, "init", "--initial-branch=dev")
    (foreign / "untouched.txt").write_text("outside fixture", encoding="utf-8")
    command(foreign, "add", "untouched.txt")
    command(
        foreign, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-m", "Unrelated fixture",
    )
    before = command(foreign, "rev-parse", "HEAD"), (foreign / ".git/index").read_bytes()
    authority = checkout / ".copilot-extensions/agent-worktrees/config.yaml"
    authority.parent.mkdir(parents=True)
    authority.write_text("default_branch: dev\n", encoding="utf-8")
    _initialize_fixture_repository(checkout, {
        **environment, "GIT_DIR": str(foreign / ".git"),
        "GIT_COMMON_DIR": str(foreign / ".git"), "GIT_WORK_TREE": str(foreign),
        "GIT_INDEX_FILE": str(foreign / ".git/index"),
    })
    assert Path(command(checkout, "rev-parse", "--show-toplevel")).samefile(checkout)
    assert command(checkout, "show", "HEAD:.copilot-extensions/agent-worktrees/config.yaml")
    assert (command(foreign, "rev-parse", "HEAD"), (foreign / ".git/index").read_bytes()) == before
    assert (foreign / "untouched.txt").read_text(encoding="utf-8") == "outside fixture"


@pytest.mark.timeout(300)
def test_fresh_application_admits_its_current_native_runtime(tmp_path, monkeypatch):
    _qualify_native_application(tmp_path, monkeypatch)


@pytest.mark.timeout(480)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed native Windows Copilot broker lane",
)
def test_fresh_application_owns_native_broker_fresh_and_resume(tmp_path, monkeypatch):
    _qualify_native_application(tmp_path, monkeypatch, broker=True)


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed public Windows Worktrees embodiment lane",
)
def test_public_embody(monkeypatch):
    _qualify_public_application(monkeypatch, "embody")


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("WORKTREES_NATIVE_CALLER_JOBS") != "1",
    reason="Explicitly select the leased native caller-Job lifetime owner",
)
def test_public_caller_job_lifetime(monkeypatch):
    _qualify_public_application(monkeypatch, "embody", caller_jobs=True)


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed public Windows Worktrees TTY lane",
)
def test_public_copilot(monkeypatch):
    _qualify_public_application(monkeypatch, "copilot")


def _qualify_public_application(monkeypatch, caller, *, caller_jobs=False):
    from plugin_test_containment import TestSandbox

    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"])
    if caller_jobs:
        # The outer owner settles residents before removing this sandbox.
        directory = sandbox / "caller"
        directory.mkdir()
        _qualify_native_application(directory, monkeypatch, broker=caller, caller_jobs=True)
        return
    with TestSandbox(prefix="p-", dir=sandbox) as directory:
        _qualify_native_application(Path(directory), monkeypatch, broker=caller)


def test_public_application_workspace_is_outside_source_only_repository(tmp_path, monkeypatch):
    from plugin_test_command_safety import private_mux_args
    from repository_policy import AgentHostingDenied, require_agent_hosting

    candidate = tmp_path / "candidate"
    (candidate / ".git").mkdir(parents=True)
    configuration = candidate / ".agent-worktrees" / "config.yaml"
    configuration.parent.mkdir()
    configuration.write_text("expose_agent: false\n", encoding="utf-8")
    with pytest.raises(AgentHostingDenied, match="expose_agent: false"):
        require_agent_hosting(candidate, operation="fixture launch")
    monkeypatch.setitem(_qualify_public_application.__globals__, "ROOT", candidate)
    observed = []

    def qualify(root, _patch, *, broker):
        caller = root / "caller"
        caller.mkdir()
        assert require_agent_hosting(caller, operation="fixture launch").expose_agent is None
        assert private_mux_args(root)
        observed.append((root, broker))

    monkeypatch.setitem(_qualify_public_application.__globals__, "_qualify_native_application", qualify)
    for caller in ("embody", "copilot"):
        _qualify_public_application(monkeypatch, caller)
    assert [caller for _, caller in observed] == ["embody", "copilot"]
    assert all(not root.exists() for root, _ in observed)


@pytest.mark.parametrize("failed", [False, True])
def test_native_fixture_directory_remains_owned_by_outer_settlement(tmp_path, monkeypatch, failed):
    sandbox = tmp_path / "outer-sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    observed = []

    def qualify(directory, _patch, *, broker, caller_jobs):
        assert broker == "embody" and caller_jobs is True
        assert directory.is_relative_to(sandbox)
        observed.append(directory)
        (directory / "resident-owned.log").write_text("held until outer settlement", encoding="utf-8")
        if failed:
            raise RuntimeError("native failed")

    monkeypatch.setitem(_qualify_public_application.__globals__, "_qualify_native_application", qualify)
    if failed:
        with pytest.raises(RuntimeError, match="native failed"):
            _qualify_public_application(monkeypatch, "embody", caller_jobs=True)
    else:
        _qualify_public_application(monkeypatch, "embody", caller_jobs=True)
    assert len(observed) == 1
    assert (observed[0] / "resident-owned.log").read_text() == "held until outer settlement"


def test_withdrawn_fixture_application_is_restored_after_broker_failure(tmp_path, monkeypatch):
    from public_caller_jobs_fixture import application_unavailable

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path))
    application = tmp_path / "application"
    python = application / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (application / "application.json").write_text("ordinary fixture", encoding="utf-8")
    moved = application.with_name("application-temporarily-unavailable")
    with pytest.raises(RuntimeError, match="broker failed"):
        with application_unavailable(python):
            assert not application.exists() and moved.is_dir()
            raise RuntimeError("broker failed")
    assert python.is_file() and not moved.exists()
    assert (application / "application.json").read_text() == "ordinary fixture"
    moved.mkdir()
    with pytest.raises(AssertionError):
        with application_unavailable(python):
            pytest.fail("A retained alias must not be overwritten")
    assert python.is_file() and moved.is_dir()


def test_application_withdrawal_refuses_an_unowned_slot(tmp_path, monkeypatch):
    from public_caller_jobs_fixture import application_unavailable

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    application = tmp_path / "foreign"
    python = application / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (application / "application.json").write_text("foreign", encoding="utf-8")
    with pytest.raises(AssertionError):
        with application_unavailable(python):
            pytest.fail("An unrelated application must not move")
    assert python.is_file()


@pytest.mark.parametrize("failure", [None, "install", "selection", "retained"])
def test_generation_replacement_keeps_installer_and_cleanup_owners(tmp_path, monkeypatch, failure):
    import public_caller_jobs_fixture as fixture

    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(tmp_path))
    root = tmp_path / "runtime"
    outgoing, incoming = (root / "versions" / name for name in ("1.0", "2.0"))
    python = outgoing / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    incoming.mkdir()
    (incoming / "application.json").write_text("fixture", encoding="utf-8")
    (root / "current-version").write_text(outgoing.name, encoding="utf-8")
    (root / "projects.yaml").write_text("projects: {fixture: retained}\n", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["env"] == {"FIXTURE": "selected"}
        if len(calls) == 1:
            assert command == [
                "selected-pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                str(incoming / "install.ps1"),
            ]
            assert not (incoming / ".venv").exists()
            if failure == "install":
                return subprocess.CompletedProcess(command, 1, "", "installation failed")
            for selector in ("current-version", "last-known-good"):
                (root / selector).write_text(
                    outgoing.name if failure == "selection" else incoming.name, encoding="utf-8",
                )
            (incoming / "install-success.json").write_text(json.dumps({
                "state": "installed", "version": incoming.name,
            }), encoding="utf-8")
            installed_python = incoming / ".venv" / "Scripts" / "python.exe"
            installed_python.parent.mkdir(parents=True)
            installed_python.touch()
            return subprocess.CompletedProcess(command, 0, 'setup callback\n{"state":"installed"}', "")
        assert command == [
            str(incoming / ".venv" / "Scripts" / "python.exe"), "-I", "-B",
            str(incoming / "runtime" / "versioned_runtime.py"),
            "--root", str(root), "--json", "gc", "--protect-pids", "--min-age-days", "0",
        ]
        if failure == "retained":
            return subprocess.CompletedProcess(command, 0, '{"removed":[]}', "")
        shutil.rmtree(outgoing)
        return subprocess.CompletedProcess(command, 0, json.dumps({"removed": [outgoing.name]}), "")

    monkeypatch.setattr(fixture.subprocess, "run", run)
    arguments = dict(shell="selected-pwsh", cwd=tmp_path, environment={"FIXTURE": "selected"})
    if failure:
        with pytest.raises(AssertionError):
            fixture.replace_application(python, incoming, **arguments)
        assert outgoing.is_dir()
        assert len(calls) == (2 if failure == "retained" else 1)
        if failure == "retained":
            assert (root / "current-version").read_text() == incoming.name
            assert (incoming / "install-success.json").is_file()
    else:
        selected = fixture.replace_application(python, incoming, **arguments)
        assert selected == incoming / ".venv" / "Scripts" / "python.exe"
        assert not outgoing.exists() and len(calls) == 2


def test_generation_replacement_refuses_unowned_or_prebuilt_input(tmp_path, monkeypatch):
    from public_caller_jobs_fixture import replace_application

    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_SANDBOX", str(sandbox))
    outgoing = sandbox / "runtime" / "versions" / "1.0"
    python = outgoing / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    incoming = outgoing.with_name("2.0")
    incoming.mkdir()
    (incoming / "application.json").write_text("fixture", encoding="utf-8")
    (incoming / ".venv").mkdir()
    for selected in (incoming, tmp_path / "foreign"):
        with pytest.raises(AssertionError):
            replace_application(python, selected, shell="must-not-run", cwd=sandbox, environment={})


def test_lifetime_selection_cannot_disable_an_existing_contained_owner(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    with pytest.raises(AssertionError):
        _qualify_native_application(tmp_path, monkeypatch, broker="embody", caller_jobs=True)
    assert list(tmp_path.iterdir()) == []


def test_manager_install_retains_only_the_selected_runner_lifecycle(tmp_path, monkeypatch):
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV
    from test_manager_application_launch import _manager_install_environment

    monkeypatch.setenv(TEST_CONTEXT_ENV, "unselected ambient context")
    arguments = tmp_path / "home", tmp_path / "manager", tmp_path, sys.executable
    selected = {TEST_CONTEXT_ENV: "selected runner context"}
    environment = _manager_install_environment(
        *arguments, caller_jobs=True, worktrees_environment=selected,
    )
    assert environment[TEST_CONTEXT_ENV] == selected[TEST_CONTEXT_ENV]
    assert environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
    assert TEST_CONTEXT_ENV not in _manager_install_environment(
        *arguments, caller_jobs=False, worktrees_environment=selected,
    )
    with pytest.raises(AssertionError):
        _manager_install_environment(*arguments, caller_jobs=True, worktrees_environment={})


def _qualify_native_application(tmp_path, monkeypatch, *, broker=False, caller_jobs=False):
    if caller_jobs:
        from agent_procutil import contained_test_mode
        from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

        assert broker == "embody" and not contained_test_mode()
        assert os.environ.get(TEST_CONTEXT_ENV), "The native runner must own the daemon lease"
    started = time.monotonic()
    construction_budget = 420 if broker in {"embody", "copilot"} else 300
    deadline = started + construction_budget
    print("Application construction starting", flush=True)
    sys.path.insert(0, str(ROOT / "tools"))
    import build_python_artifacts as builder
    import materialize_main as materializer
    import local_install_profile as profiles
    from governed_feed_trust import _governed_feed_configured

    print("Application build/profile owners imported", flush=True)
    if not _governed_feed_configured():
        pytest.skip("native application construction requires the configured governed feed")
    assert os.environ.get("PYTEST_CURRENT_TEST")
    uv = shutil.which("uv")
    shell = shutil.which("pwsh") or shutil.which("powershell.exe") if os.name == "nt" else None
    assert uv and (os.name != "nt" or shell)
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    inputs = tmp_path / "source-inputs"
    source, artifacts = inputs / "plugins" / "agent-worktrees", inputs / "wheels"
    source.mkdir(parents=True)
    artifacts.mkdir()
    _copy_application_inputs(SOURCE, source)
    materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=SOURCE, dest_consumer_dir=source, canonical_root=ROOT,
    )
    dependencies = builder.resolve_vendored_libs(source)
    for _, directory in dependencies:
        tests = directory / "tests"
        if tests.is_dir():
            shutil.rmtree(tests)
    print(f"Application inputs materialized: {time.monotonic() - started:.2f}s", flush=True)
    with monkeypatch.context() as patch:
        patch.setattr(builder, "REPO", inputs)
        artifact = builder.build_source_artifacts(
            identity={"plugin": "agent-worktrees"}, source_dir=source,
            dependencies=dependencies,
            out_dir=artifacts, python=interpreter, toolchain=None,
        )
    print(f"Application wheels built: {time.monotonic() - started:.2f}s", flush=True)
    engine = profiles.source_inputs.engine
    project = builder.tomllib.loads((source / "pyproject.toml").read_text())["project"]
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "agent-worktrees", project["version"], "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version(project["version"], "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    (inputs / profiles.source_inputs.MANIFEST).write_text(json.dumps({
        "schema": profiles.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"agent-worktrees": {
            "payload": "plugins/agent-worktrees", "wheels": "wheels",
            "source": identity.document(), "artifact": artifact,
        }},
    }), encoding="utf-8")
    prepared = profiles.prepare_registry_lock(
        inputs, tmp_path / "profile", uv=uv, deadline=deadline,
        prepare_profile=True, component="agent-worktrees",
    )
    print(f"Application profile prepared: {time.monotonic() - started:.2f}s", flush=True)
    assert prepared["install_ready"] is True
    application = Path(prepared["snapshot"]) / "applications" / "agent-worktrees"
    declaration = json.loads((application / "application.json").read_text())
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".agent-worktrees"
    root.mkdir()
    (root / "projects.yaml").write_text("projects: {}\n", encoding="utf-8")
    slot = root / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(application, slot)
    replacement = None
    if caller_jobs:
        assert not (application / ".venv").exists()
        assert not (application / engine.runtime.COMPLETE_MARKER).exists()
        replacement = slot.with_name(slot.name + ".repair." + uuid4().hex)
        shutil.copytree(application, replacement)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_RT_",
                               "AGENT_WORKTREES_"))
        and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        AGENT_WORKTREES_ROOT=str(root), AGENT_WORKTREES_STATUS_MONITOR="0",
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1",
    )
    if caller_jobs:
        environment.pop("COPILOT_EXTENSIONS_TEST_CONTAINED")
        environment[TEST_CONTEXT_ENV] = os.environ[TEST_CONTEXT_ENV]
    assert environment.get("PYTEST_CURRENT_TEST")
    install = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", slot / "install.ps1"] \
        if os.name == "nt" else [slot / "install"]
    installed = _run(install, cwd=caller, env=environment, deadline=deadline)
    print(f"Application installed: {time.monotonic() - started:.2f}s", flush=True)
    assert json.loads(installed.stdout)["state"] == "installed"
    assert (root / "current-version").read_text().strip() == slot.name
    assert (root / "projects.yaml").read_text() == "projects: {}\n"
    completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    shutil.rmtree(inputs)
    assert not inputs.exists()
    print(f"Application source inputs removed: {time.monotonic() - started:.2f}s", flush=True)
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    checked = _run([
        python, "-I", "-B", "-c",
        "import argparse,json,os,sys;from pathlib import Path;"
        "from agent_worktrees import config,launch_seed_native,status_monitor_runtime;"
        "assert not status_monitor_runtime._status_monitor_enabled();"
        "launch_seed_native.require_current_runtime(Path(sys.argv[1]));"
        "from agent_worktrees import __main__ as cli;"
        "cwd=str(Path.cwd());platform='windows' if os.name=='nt' else 'linux';"
        "configuration=config.Config(srcroot=cwd,machine='fixture',platform=platform,"
        "repo_name='fixture',repos={'fixture':config.RepoConfig(anchor=cwd,worktree_root=cwd)});"
        "command=cli._build_launch_cmd(configuration,"
        "argparse.Namespace(recovery=True,copilot_args=[]),cwd);"
        "print(json.dumps({'root':str(config.runtime_install_dir()),'prefix':sys.prefix,"
        "'command':command}))",
        root,
    ], cwd=caller, env=environment, deadline=deadline)
    facts = json.loads(checked.stdout)
    assert Path(facts["prefix"]) == slot / ".venv"
    assert Path(facts["root"]) == root
    command = facts["command"]
    scripts = [Path(argument) for argument in command if argument.endswith((".ps1", ".sh"))]
    assert len(scripts) == 2, command
    assert all(script.is_file() for script in scripts), command
    assert all(script.is_relative_to(slot / ".venv") for script in scripts), command
    boundary = scripts[0].with_name("native-seed-boundary.ps1" if os.name == "nt" else "native-seed-boundary.sh")
    if os.name == "nt":
        described = _run([
            shell, "-NoProfile", "-NoLogo", "-Command",
            f". {_literal(boundary)}; $reply=Invoke-AwNativeSeedPhase {_literal(root)} "
            "@('--describe-contract'); if (-not $reply) { throw 'Packaged native provider unavailable' }; "
            "ConvertTo-Json -InputObject $reply -Depth 10 -Compress",
        ], cwd=caller, env=environment, deadline=deadline)
    else:
        described = _run([
            "bash", "-c", '. "$1"; aw_native_seed_phase "$2" --describe-contract',
            "application", boundary, root,
        ], cwd=caller, env=environment, deadline=deadline)
    assert json.loads(described.stdout)["contract"] == "agent-worktrees.native-seed"
    print(f"Application packaged admission checked: {time.monotonic() - started:.2f}s", flush=True)
    if broker in {"embody", "copilot"}:
        assert time.monotonic() < deadline, "Public caller preparation exceeded 420 seconds"
        from test_manager_application_launch import _qualify_manager_application

        manager = tmp_path / "manager"
        manager.mkdir()
        _qualify_manager_application(
            manager, monkeypatch, broker, worktrees_python=python,
            worktrees_root=root, worktrees_environment=environment,
            caller_jobs=caller_jobs, replacement_slot=replacement,
        )
    elif broker:
        assert time.monotonic() < deadline, "Application construction exceeded 300 seconds"
        native_root = tmp_path / "native-flow"
        native_root.mkdir()
        cleanup = []
        try:
            with daemon_test_lifecycle(
                owner="worktrees-application-broker-fixture",
                registry_root=tmp_path / "daemon-instances", deadline=time.monotonic() + 180,
                cleanup_results=cleanup,
            ) as lifecycle:
                qualified = run_caller_owned(
                    [sys.executable, "-I", "-B", "-X", "utf8", "-c",
                     "import os,sys;from pathlib import Path;"
                     "sys.path[:0]=sys.argv[1:3];"
                     "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
                     "_qualify_native_worktrees(Path(sys.argv[3]),'broker',"
                     "worktrees_python=Path(sys.argv[4]),worktrees_root=Path(sys.argv[5]),"
                     "worktrees_environment=dict(os.environ))",
                     str(Path(__file__).parent), str(ROOT / "tools"),
                     str(native_root), str(python), str(root)],
                    env={
                        **environment, **lifecycle,
                        "COPILOT_EXTENSIONS_TEST_SANDBOX": os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"],
                    },
                    cwd=caller, timeout=180,
                    max_stdout_bytes=65536, max_stderr_bytes=65536,
                )
                print(qualified.stdout, flush=True)
                assert qualified.returncode == 0, qualified.stdout + qualified.stderr
        finally:
            assert all(result.status == "settled" for result in cleanup), cleanup
        assert cleanup, "The installed broker did not admit its owned native processes"
    elif os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE"):
        from test_native_startup_prompt import _qualify_native_startup

        def packaged_command(native, native_environment, directory):
            selected = _run([
                python, "-I", "-B", "-c",
                "import argparse,json,os,sys;from pathlib import Path;"
                "from agent_worktrees import __main__ as cli,config;"
                "native=json.loads(sys.argv[1]);cwd=str(Path.cwd());"
                "platform='windows' if os.name=='nt' else 'linux';"
                "configuration=config.Config(srcroot=cwd,machine='fixture',platform=platform,"
                "repo_name='fixture',repos={'fixture':config.RepoConfig(anchor=cwd,worktree_root=cwd,"
                "copilot_path={platform:native[0]})});"
                "print(json.dumps(cli._build_launch_cmd(configuration,"
                "argparse.Namespace(recovery=True,copilot_args=native[1:]),cwd)))",
                json.dumps(native),
            ], cwd=directory, env=native_environment, deadline=deadline)
            return json.loads(selected.stdout)

        native_root = tmp_path / "native-flow"
        native_root.mkdir()
        _qualify_native_startup(
            native_root, False, command_wrapper=packaged_command, environment=environment,
        )
    if replacement is None:
        assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
    else:
        assert not slot.exists()
        assert (root / "current-version").read_text().strip() == replacement.name
        assert (root / "last-known-good").read_text().strip() == replacement.name
        assert (replacement / engine.runtime.COMPLETE_MARKER).is_file()
        assert (root / "projects.yaml").is_file()
