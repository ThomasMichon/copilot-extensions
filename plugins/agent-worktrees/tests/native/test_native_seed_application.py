"""First-touch native handoff admission through the real wheel application."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

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
def test_public_embody(tmp_path, monkeypatch):
    _qualify_native_application(tmp_path, monkeypatch, broker="embody")


@pytest.mark.timeout(900)
@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1",
    reason="Explicitly select the installed public Windows Worktrees TTY lane",
)
def test_public_copilot(tmp_path, monkeypatch):
    _qualify_native_application(tmp_path, monkeypatch, broker="copilot")


def _qualify_native_application(tmp_path, monkeypatch, *, broker=False):
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
    assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
