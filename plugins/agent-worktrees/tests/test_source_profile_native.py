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
import importlib.util, json, os, shutil, sqlite3, subprocess, sys
from pathlib import Path

repository, home, profile = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
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
artifact = home / "prepared"
preparation._materialize(repository, "agent-containers", artifact)
sys.path.insert(0, str(repository / "plugins/agent-worktrees/tests"))
from runtime_install_fixture import isolate_account_profile
isolate_account_profile(artifact, home, runtime_package=None)
owner = home / ".copilot/installed-plugins/example/agent-containers"
owner.parent.mkdir(parents=True)
shutil.copytree(artifact, owner)
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
    result = subprocess.run(arguments, cwd=home, capture_output=True, text=True,
                            timeout=100, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout + result.stderr
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
version = preparation.payload_package_version(artifact, "agent-containers")
digest = preparation._assembly_hash(artifact)
identity = preparation.SourceIdentity(
    "agent-containers", version, "a" * 40, "b" * 40, digest,
    preparation.runtime_version(version, "a" * 40, digest),
)
(artifact / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
fields = {"runtime_source": identity.document(), "runtime_version": identity.runtime_version}
plan = json.loads(run([
    sys.executable, "-I", "-B", str(owner / "scripts/runtime_profile.py"), "--plan-source",
    "--root", str(root), "--payload", str(artifact),
    "--expected-source-version", identity.runtime_version,
    "--expected-source-identity", json.dumps(fields, sort_keys=True),
]))
assert plan["profile"] == profile and plan["source"] == fields
os.environ["COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"] = json.dumps(fields, sort_keys=True)
os.environ["COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET"] = json.dumps(plan, sort_keys=True)
from agent_worktrees import reconcile, update_runtime, update_runtime_plan
from agent_worktrees.update_runtime_source import PreparedRuntimeSource
reconcile.core_installed_payload_dir = lambda name: owner
reconcile.runtime_installer_environment = lambda *args: (dict(os.environ), root)
update_runtime._machine_gate_blocks = lambda *args: False
source = PreparedRuntimeSource(artifact, json.dumps(fields))
step = update_runtime_plan.plan_registered(
    "agent-containers", "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
)
assert not step.status, step.status
assert step.source_target.document == json.dumps(plan, sort_keys=True)
status = update_runtime._execute_runtime_step(step)
assert status == "OK", status
manifest = json.loads((root / "deploy-manifest.json").read_text())
assert manifest["runtime_source"] == identity.document()
assert manifest["runtime_profile"] == profile
assert manifest["runtime_version"] == plan["runtime_version"] != identity.runtime_version
assert (root / "current-version").read_text().strip() == plan["runtime_version"]
slot = root / "versions" / plan["runtime_version"]
assert Path(manifest["venv"]) == slot and (root / "versions" / previous).is_dir()
assert source.deployed_version(root, owner, target=step.source_target) == plan["runtime_version"]
assert update_runtime_plan.plan_registered(
    "agent-containers", "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
).status == "SKIPPED (current)"
python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
probe = (
    "import importlib.util,json,sys;from pathlib import Path;"
    "import agent_containers; print(json.dumps([str(Path(agent_containers.__file__).resolve()),"
    "importlib.util.find_spec('paramiko') is not None,sys.prefix]))"
)
module, paramiko, prefix = json.loads(run([str(python), "-I", "-B", "-c", probe]))
assert Path(module).is_relative_to(slot) and Path(prefix) == slot
assert paramiko == (profile == "provider-exec")
launcher = home / ".local/bin" / ("agent-containers.ps1" if os.name == "nt" else "agent-containers")
assert launcher.is_file()
help_output = run([shell, "-NoProfile", "-File", str(launcher), "--help"] if os.name == "nt"
                  else [str(launcher), "--help"])
assert "agent-containers" in help_output
completion = slot / ".install-complete.json"
complete_before = (completion.read_bytes(), completion.stat().st_mtime_ns)
provision(artifact)
assert (completion.read_bytes(), completion.stat().st_mtime_ns) == complete_before
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
@pytest.mark.timeout(270)
@pytest.mark.parametrize("profile", ["base", "provider-exec"])
def test_native_source_profile_preserves_same_root_state_and_global_command(tmp_path_factory, profile):
    if shutil.which("uv") is None or shutil.which("pwsh" if os.name == "nt" else "bash") is None:
        pytest.skip("native profile construction requires uv and the native shell")
    home = tmp_path_factory.mktemp("sp") / "home"
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", NATIVE_PROGRAM, str(ROOT), str(home), profile],
        capture_output=True, text=True, encoding="utf-8", timeout=250,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "profile": profile, "sameRoot": True, "installedCode": True,
        "globalCommand": True, "reuse": True, "retainedState": True, "collectedUpdater": True,
    }
