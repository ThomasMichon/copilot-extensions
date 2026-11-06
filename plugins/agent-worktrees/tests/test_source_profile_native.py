"""Opt-in native construction, owned command and same-root source-profile witness."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
NATIVE_PROGRAM = r"""
import importlib.util, json, os, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

repository, home, profile = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
deadline = time.monotonic() + (360 if profile == "base" else 230)
home.mkdir()
os.environ.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
                  COPILOT_EXTENSIONS_TEST_CONTAINED="1")
for key in list(os.environ):
    if key.startswith(("COPILOT_PLUGIN_", "AGENT_CONTAINERS_")) or key in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN",
        "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    ):
        del os.environ[key]
sys.path.insert(0, str(repository / "tools"))
import prepare_runtime_source as preparation
sys.path.insert(0, str(repository / "plugins/agent-worktrees/tests"))
from source_application_fixture import (
    application_source, assert_application_reuse, prepare_application, trace_preparation,
)
checkout, commit = application_source(
    repository, home.parent / "source", home, deadline=deadline,
)
owner = home / ".copilot/installed-plugins/example/agent-containers"
owner.parent.mkdir(parents=True)
started = time.monotonic()
print("Native profile phase: materialize owner", flush=True)
preparation._materialize(checkout, "agent-containers", owner, deadline=deadline)
print(f"Native profile phase passed: materialize owner ({time.monotonic()-started:.3f}s)", flush=True)
root = home / ".agent-containers"
project = home / "project"
project.mkdir()
settings = project / ".github/copilot/settings.json"
settings.parent.mkdir(parents=True)
settings.write_text(json.dumps({
    "extraKnownMarketplaces": {
        "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
    },
}), encoding="utf-8")
os.environ["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"] = str(project)
shell = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")
assert shell

def run(arguments):
    started = time.monotonic()
    timeout = preparation._remaining(deadline, 100)
    print(f"Native profile command: {arguments[0]} ({timeout:.3f}s budget)", flush=True)
    result = subprocess.run(arguments, cwd=home, capture_output=True, text=True,
                            timeout=timeout,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout + result.stderr
    print(f"Native profile command passed ({time.monotonic()-started:.3f}s)", flush=True)
    return result.stdout

def provision(payload, requested=False):
    if os.name == "nt":
        return run([shell, "-NoProfile", "-File", str(payload / "scripts/init.ps1"),
                    "provision", "-InstallDir", str(root),
                    *(["-Profile", profile] if requested else [])])
    return run([shell, str(payload / "scripts/init.sh"), "provision",
                "--install-dir", str(root),
                *(["--profile", profile] if requested else [])])

os.environ["COPILOT_PLUGIN_ROOT"] = str(owner)
os.environ["COPILOT_PLUGIN_STAGED_FROM"] = str(owner)
provision(owner, requested=True)
spec = importlib.util.spec_from_file_location(
    "source_profile_context", owner / "scripts/installation-context/installation_context.py",
)
assert spec and spec.loader
context = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = context
spec.loader.exec_module(context)
arguments = dict(
    payload_root=owner, plugin_id="agent-containers", legacy_root=root,
    project_root=project, environment=dict(os.environ),
    durable_home=home / ".copilot-extensions", os_profile=home,
)
decision = context.resolve_runtime_deployment(**arguments)
(root / "runtime-deployment.json").write_text(json.dumps({
    "schema": context.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
    "owner": context._deployment_owner(decision, arguments),
}), encoding="utf-8")
previous = (root / "current-version").read_text().strip()
config = root / "source-profile-fixture.json"
config.write_text('{"preserved":true}\n', encoding="utf-8")
database = root / "source-profile-fixture.sqlite"
with sqlite3.connect(database) as connection:
    connection.execute("CREATE TABLE retained (value TEXT)")
    connection.execute("INSERT INTO retained VALUES ('existing')")
before = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
          for path in (config, database, root / "runtime-deployment.json")}
from agent_worktrees import reconcile, update_runtime, update_runtime_plan
from agent_worktrees.update_runtime_application import PreparedApplication
from agent_worktrees.update_runtime_source import PreparedRuntimeSource
from agent_worktrees.update_runtime_target import application_profile
owned_profile = application_profile("agent-containers", owner, root, dict(os.environ))
assert owned_profile["profile"] == profile
started = time.monotonic()
print("Native profile phase: prepare complete application", flush=True)
with trace_preparation(deadline=deadline):
    snapshot = prepare_application(
        checkout, commit, "agent-containers", home / "prepared",
        uv=shutil.which("uv"), deadline=deadline, extras=tuple(owned_profile["extras"]),
    )
print(f"Native profile phase passed: prepare complete application ({time.monotonic()-started:.3f}s)",
      flush=True)
started = time.monotonic()
print("Native profile phase: reuse complete application", flush=True)
with trace_preparation(deadline=deadline):
    assert_application_reuse(
        checkout, commit, "agent-containers", home / "prepared-reuse", snapshot,
        uv=shutil.which("uv"), deadline=deadline, extras=tuple(owned_profile["extras"]),
    )
print(f"Native profile phase passed: reuse complete application ({time.monotonic()-started:.3f}s)",
      flush=True)
application = PreparedApplication(
    snapshot, "agent-containers", json.dumps(owned_profile, sort_keys=True),
)
artifact = application.payload
identity = json.loads(application.identity)["runtime_source"]
reconcile.core_installed_payload_dir = lambda name: owner
reconcile.runtime_installer_environment = lambda *args: (dict(os.environ), root)
update_runtime._machine_gate_blocks = lambda *args: False
source = PreparedRuntimeSource(artifact, application.identity, application)
step = update_runtime_plan.plan_registered(
    "agent-containers", "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
)
assert not step.status, step.status
assert step.source.application.profile == json.dumps(owned_profile, sort_keys=True)
started = time.monotonic()
print("Native profile phase: collected update", flush=True)
status = update_runtime._execute_runtime_step(step)
assert status == "OK", status
print(f"Native profile phase passed: collected update ({time.monotonic()-started:.3f}s)", flush=True)
assert (root / "current-version").read_text().strip() == application.version
assert (root / "last-known-good").read_text().strip() == application.version
slot = root / "versions" / application.version
assert json.loads((slot / "runtime-source.json").read_text()) == identity
assert json.loads((slot / "application.json").read_text()) == json.loads(application.declaration)
assert json.loads((slot / "install-success.json").read_text()) == {
    "state": "installed", "version": application.version,
}
assert (root / "versions" / previous).is_dir()
assert source.deployed_version(root, owner) == application.version
assert update_runtime_plan.plan_registered(
    "agent-containers", "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
).status == "SKIPPED (current)"
venv = slot / ".venv"
python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
probe = (
    "import importlib.util,json,sys;from pathlib import Path;"
    "import agent_containers; print(json.dumps([str(Path(agent_containers.__file__).resolve()),"
    "importlib.util.find_spec('paramiko') is not None,sys.prefix]))"
)
module, paramiko, prefix = json.loads(run([str(python), "-I", "-B", "-c", probe]))
assert Path(module).is_relative_to(venv) and Path(prefix) == venv
assert paramiko == (profile == "provider-exec")
launcher = home / ".local/bin" / ("agent-containers.ps1" if os.name == "nt" else "agent-containers")
assert launcher.is_file()
command = ([shell, "-NoProfile", "-File", str(launcher), "--help"] if os.name == "nt"
           else [str(launcher), "--help"])
help_output = run(command)
assert "agent-containers" in help_output
completion = slot / ".install-complete.json"
complete_before = (completion.read_bytes(), completion.stat().st_mtime_ns)
run(list(application.argv(root, repair=False)))
assert (completion.read_bytes(), completion.stat().st_mtime_ns) == complete_before
snapshot.rename(home / "prepared-unavailable")
checkout.rename(home / "source-unavailable")
assert "agent-containers" in run(command)
assert {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in before} == before
with sqlite3.connect(database) as connection:
    assert connection.execute("SELECT value FROM retained").fetchall() == [("existing",)]
print(json.dumps({"profile": profile, "sameRoot": True, "installedCode": True,
                  "globalCommand": True, "reuse": True, "retainedState": True,
                  "collectedUpdater": True}))
"""


@pytest.mark.skipif(
    os.environ.get("COPILOT_TEST_NATIVE_SOURCE_PROFILE") != "1",
    reason="opt-in native profile construction",
)
@pytest.mark.parametrize("profile", [
    pytest.param("base", marks=pytest.mark.timeout(400)),
    pytest.param("provider-exec", marks=pytest.mark.timeout(270)),
])
def test_native_source_profile_preserves_same_root_state_and_global_command(tmp_path_factory, profile):
    if shutil.which("uv") is None or shutil.which("pwsh" if os.name == "nt" else "bash") is None:
        pytest.skip("native profile construction requires uv and the native shell")
    home = tmp_path_factory.mktemp("sp") / "home"
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", "-c", NATIVE_PROGRAM,
             str(ROOT), str(home), profile],
            capture_output=True, text=True, encoding="utf-8",
            timeout=380 if profile == "base" else 250,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired as error:
        for label, output in (("stdout", error.stdout), ("stderr", error.stderr)):
            if output:
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                print(f"Native source profile timeout {label}:\n{output[-16384:]}", flush=True)
        raise
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "profile": profile, "sameRoot": True, "installedCode": True,
        "globalCommand": True, "reuse": True, "retainedState": True, "collectedUpdater": True,
    }
