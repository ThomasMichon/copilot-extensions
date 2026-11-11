"""Discover Manager launch assets from its real first-touch wheel application."""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agent_procutil._interpreter import physical_executable
from agent_procutil.daemon_instances import TEST_CONTEXT_ENV, daemon_test_lifecycle
from agent_procutil.owned_process import run_caller_owned
import pytest

from test_native_seed_application import ROOT, _run

_NATIVE_COPILOT = (
    os.name == "nt"
    and os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    and os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") == "1"
)


def _run_lifetime_native(command, *, directory, cwd, environment):
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

    stdout, stderr = directory / "native.stdout.log", directory / "native.stderr.log"
    with stdout.open("wb") as out, stderr.open("wb") as err:
        process, job = spawn_sync_in_kill_on_close_job(
            command, cwd=cwd, env=environment, require_job=True, allow_breakaway=True,
            stdin=subprocess.DEVNULL, stdout=out, stderr=err, **no_window_kwargs(),
        )
    try:
        assert job is not None
        code = process.wait(180)
    finally:
        try:
            if process.poll() is None:
                process.kill()
                process.wait(5)
        finally:
            if job is not None:
                job.close()
    return subprocess.CompletedProcess(
        command, code, stdout.read_text(encoding="utf-8"), stderr.read_text(encoding="utf-8"),
    )


def _manager_install_environment(home, root, tmp_path, interpreter, *, caller_jobs, worktrees_environment):
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "AGENT_RT_",
                               "AGENT_WORKTREES_", "WORKTREE_"))
        and key not in {
            "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME", "TMUX", "TMUX_PANE", "PSMUX_PANE",
            "COPILOT_AGENT_SESSION_ID",
        }
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
        WORKTREE_MANAGER_ROOT=str(root), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
        AGENT_WORKTREES_STATUS_MONITOR="0",
        UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON=interpreter, PYTHONDONTWRITEBYTECODE="1",
        PSMUX_DATA_DIR=str(tmp_path / "mux-data"), TMUX_TMPDIR=str(tmp_path / "mux-data"),
    )
    if caller_jobs:
        assert worktrees_environment and worktrees_environment.get(TEST_CONTEXT_ENV)
        environment[TEST_CONTEXT_ENV] = worktrees_environment[TEST_CONTEXT_ENV]
    return environment


def _run_manager_native(directory, control, *, script, python, root, environment, picker_decision):
    command = [
        sys.executable, "-I", "-B", "-X", "utf8", "-c",
        "import sys;from pathlib import Path;"
        "sys.path[:0]=sys.argv[1:3];"
        "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
        "_qualify_native_worktrees(Path(sys.argv[3]),sys.argv[4],"
        "manager_script=Path(sys.argv[5]),manager_python=Path(sys.argv[6]),"
        "manager_environment={'WORKTREE_MANAGER_ROOT':sys.argv[7],'WORKTREE_PANE_WAIT_TIMEOUT':'3'},"
        "verify_reuse=False,manager_picker=sys.argv[8]=='1')",
        str(Path(__file__).parent), str(ROOT / "tools"), str(directory),
        control, str(script), str(python), str(root), "1" if picker_decision else "0",
    ]
    qualified = run_caller_owned(
        command, cwd=directory, env=environment, timeout=180,
        max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    print(qualified.stdout, flush=True)
    assert qualified.returncode == 0, qualified.stdout + qualified.stderr


@pytest.mark.parametrize(
    "control", ["manager-no-mux", "manager-mux"] if _NATIVE_COPILOT else ["manager-no-mux"],
)
@pytest.mark.timeout(300)
def test_fresh_manager_application_exposes_its_real_launcher_to_worktrees(tmp_path, monkeypatch, control):
    _qualify_manager_application(tmp_path, monkeypatch, control)


@pytest.mark.skipif(not _NATIVE_COPILOT, reason="Select the isolated native Windows Copilot lane")
@pytest.mark.timeout(300)
def test_manager_no_mux_decision(monkeypatch):
    from plugin_test_containment import TestSandbox

    sandbox = Path(os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"])
    with TestSandbox(prefix="m-", dir=sandbox) as directory:
        _qualify_manager_application(
            Path(directory), monkeypatch, "manager-no-mux", picker_decision=True,
        )


def _qualify_manager_application(
    tmp_path, monkeypatch, control, *, worktrees_python=None,
    worktrees_root=None, worktrees_environment=None, caller_jobs=False, replacement_slot=None,
    picker_decision=False,
):
    paired = worktrees_python is not None
    assert not paired or (control in {"embody", "copilot"} and worktrees_environment is not None)
    assert not picker_decision or (not paired and control == "manager-no-mux")
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    import build_python_artifacts as builder
    import local_install_profile as profiles
    import prepare_runtime_source as preparation
    import standalone_python_artifacts as standalone
    from governed_feed_trust import _governed_feed_configured

    if not _governed_feed_configured():
        pytest.skip("native application construction requires the configured governed feed")
    assert os.environ.get("PYTEST_CURRENT_TEST")
    uv = shutil.which("uv")
    shell = (shutil.which("pwsh") or shutil.which("powershell.exe")) if os.name == "nt" else None
    assert uv and (os.name != "nt" or shell)
    interpreter = physical_executable(getattr(sys, "_base_executable", sys.executable))
    assert interpreter is not None
    deadline = time.monotonic() + 300
    inputs = tmp_path / "source-inputs"
    candidate = inputs / "source"
    source, artifacts = candidate / "worktree-manager", inputs / "wheels"
    candidate.mkdir(parents=True)
    preparation._materialize(ROOT, "worktree-manager", source, deadline=deadline)
    for name in ("versioned-runtime", "installer-engine"):
        shutil.copytree(
            ROOT / "libs" / name, candidate / "libs" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    selected, dependencies, resources = standalone.source_closure("worktree-manager", candidate, builder)
    assert selected == source
    with monkeypatch.context() as patch:
        patch.setattr(builder, "REPO", candidate)
        artifact = builder.build_source_artifacts(
            identity={"component": "worktree-manager"}, source_dir=source,
            dependencies=dependencies, resources=resources,
            out_dir=artifacts, python=interpreter, toolchain=None,
        )
    engine = profiles.source_inputs.engine
    project = builder.tomllib.loads((source / "pyproject.toml").read_text())["project"]
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "worktree-manager", project["version"], "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version(project["version"], "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    (inputs / profiles.source_inputs.MANIFEST).write_text(json.dumps({
        "schema": profiles.source_inputs.SCHEMA, "version": 1, "kind": "source",
        "source_commit": identity.source_commit, "source_tree": identity.source_tree,
        "projects": {"worktree-manager": {
            "payload": "source/worktree-manager", "wheels": "wheels",
            "source": identity.document(), "artifact": artifact,
        }},
    }), encoding="utf-8")
    prepared = profiles.prepare_registry_lock(
        inputs, tmp_path / "profile", uv=uv, deadline=deadline,
        prepare_profile=True, component="worktree-manager",
    )
    assert prepared["install_ready"] is True
    application = Path(prepared["snapshot"]) / "applications" / "worktree-manager"
    declaration = json.loads((application / "application.json").read_text())
    assert declaration["setup"] == declaration["start"] == []
    home, caller = tmp_path / "home", tmp_path / "caller"
    home.mkdir()
    caller.mkdir()
    root = home / ".worktree-manager"
    slot = root / "versions" / declaration["construction"]["runtime_version"]
    shutil.copytree(application, slot)
    environment = _manager_install_environment(
        home, root, tmp_path, interpreter,
        caller_jobs=caller_jobs, worktrees_environment=worktrees_environment,
    )
    (tmp_path / "mux-data").mkdir()
    install = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", slot / "install.ps1"] \
        if os.name == "nt" else [slot / "install"]
    cleanup = []
    lifecycle_owner = nullcontext({}) if caller_jobs else daemon_test_lifecycle(
        owner="manager-application-launch-fixture", registry_root=tmp_path / "daemon-instances",
        deadline=deadline + (180 if paired else 0), cleanup_results=cleanup,
    )
    try:
        with lifecycle_owner as lifecycle:
            environment.update(lifecycle)
            installed = _run(install, cwd=caller, env=environment, deadline=deadline)
            assert json.loads(installed.stdout.splitlines()[-1])["state"] == "installed"
            assert (root / "current-version").read_text().strip() == slot.name
            completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
            shutil.rmtree(inputs)
            python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            assets = _run([
                python, "-I", "-B", "-X", "utf8", "-c",
                "import json;from worktree_manager.relocated_launch import _relocated_launch_script;"
                "script=_relocated_launch_script();assert script is not None;"
                "assert (script.parent/'ahp-command.py').is_file();"
                "print(json.dumps({'script':str(script)}))",
            ], cwd=caller, env=environment, deadline=deadline)
            script = Path(json.loads(assets.stdout)["script"])
            assert script.is_relative_to(slot / ".venv") and "_runtime_assets" in script.parts
            consumer = _run([
                str(worktrees_python) if paired else sys.executable, "-I", "-B", "-X", "utf8", "-c",
                "import json;from agent_worktrees.manager_launch_cli import _usable_worktree_manager_launcher_dir;"
                "directory=_usable_worktree_manager_launcher_dir();"
                "assert directory is not None,'Worktrees rejected the real current Manager application';"
                "print(json.dumps({'directory':str(directory)}))",
            ], cwd=caller, env={
                **(worktrees_environment if paired else environment),
                "WORKTREE_MANAGER_ROOT": str(root),
            }, deadline=deadline)
            assert Path(json.loads(consumer.stdout)["directory"]).samefile(script.parent)
            if paired:
                assert time.monotonic() < deadline, "Manager construction exceeded 300 seconds"
                native = tmp_path / "n"
                native.mkdir()
                command = [
                    sys.executable, "-I", "-B", "-X", "utf8", "-c",
                    "import os,sys;from pathlib import Path;"
                    "sys.path[:0]=sys.argv[1:3];"
                    "from test_headed_worktrees_copilot import _qualify_native_worktrees;"
                    "_qualify_native_worktrees(Path(sys.argv[3]),sys.argv[7],"
                    "worktrees_python=Path(sys.argv[4]),worktrees_root=Path(sys.argv[5]),"
                    "worktrees_environment=dict(os.environ),caller_jobs=sys.argv[8]=='1',"
                    "replacement_slot=Path(sys.argv[9]) if sys.argv[9] else None,"
                    "manager_environment={'WORKTREE_MANAGER_ROOT':sys.argv[6]})",
                    str(Path(__file__).parent), str(ROOT / "tools"), str(native),
                    str(worktrees_python), str(worktrees_root), str(root), control,
                    "1" if caller_jobs else "0",
                    str(replacement_slot) if replacement_slot is not None else "",
                ]
                native_environment = {
                    **worktrees_environment, **lifecycle,
                    "COPILOT_EXTENSIONS_TEST_SANDBOX": os.environ["COPILOT_EXTENSIONS_TEST_SANDBOX"],
                }
                if caller_jobs:
                    qualified = _run_lifetime_native(
                        command, directory=native, cwd=caller, environment=native_environment,
                    )
                else:
                    qualified = run_caller_owned(
                        command, cwd=caller, env=native_environment, timeout=180,
                        max_stdout_bytes=65536, max_stderr_bytes=65536,
                    )
                print(qualified.stdout, flush=True)
                assert qualified.returncode == 0, qualified.stdout + qualified.stderr
            elif _NATIVE_COPILOT:
                native = tmp_path / "native-launch"
                native.mkdir()
                _run_manager_native(
                    native, control, script=script, python=python, root=root,
                    environment={**os.environ, **lifecycle}, picker_decision=picker_decision,
                )
            assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion
    finally:
        assert all(result.status == "settled" for result in cleanup), cleanup
    assert caller_jobs or cleanup, "Manager installation did not start its owned resident"
