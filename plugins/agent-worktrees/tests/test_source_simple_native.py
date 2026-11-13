"""Opt-in collected-source updates without resident-service effects."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

ROOT = Path(__file__).resolve().parents[3]
NATIVE_PROGRAM = r"""
import importlib.util, json, os, re, shutil, sqlite3, subprocess, sys, time
from contextlib import closing
from pathlib import Path

repository, home = map(Path, sys.argv[1:3])
plugin = sys.argv[3]
deadline = time.monotonic() + 320
home.mkdir()
os.environ.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
                  COPILOT_EXTENSIONS_TEST_CONTAINED="1")
for key in list(os.environ):
    if key.startswith(("COPILOT_PLUGIN_", "AGENT_MACHINES_", "AGENT_SSH_", "AGENT_MCP_")) or key in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN",
        "COPILOT_EXTENSIONS_RUNTIME_SOURCE_TARGET", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    ):
        del os.environ[key]
if plugin == "agent-mcp":
    os.environ.update(AGENT_MCP_NO_CUTOVER="1", AGENT_MCP_NO_VERSION_REAP="1")
sys.path.insert(0, str(repository / "tools"))
import prepare_runtime_source as preparation
sys.path.insert(0, str(repository / "plugins/agent-worktrees/tests"))
from source_application_fixture import application_source, assert_application_reuse, prepare_application
checkout, commit = application_source(
    repository, home.parent / "source", home, deadline=deadline,
)
owner = home / ".copilot/installed-plugins/example" / plugin
owner.parent.mkdir(parents=True)
preparation._materialize(checkout, plugin, owner, deadline=deadline)
owner_version = "9.0.0"
metadata = json.loads((owner / "plugin.json").read_text())
metadata["version"] = owner_version
(owner / "plugin.json").write_text(json.dumps(metadata), encoding="utf-8")
pyproject = owner / "pyproject.toml"
content, count = re.subn(r'(?m)^version\s*=\s*"[^"]+"$', f'version = "{owner_version}"',
                         pyproject.read_text(), count=1)
assert count == 1
pyproject.write_text(content, encoding="utf-8")
root = home / ("." + plugin)
project = home / "project"
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
installer = "init" if plugin in {"agent-machines", "agent-mcp"} else "install"

def run(arguments, timeout=120, input_text=None, environment=None):
    result = subprocess.run(arguments, cwd=project, capture_output=True, text=True,
                            encoding="utf-8", timeout=min(timeout, preparation._remaining(deadline)),
                            input=input_text, env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout

def provision(payload):
    if os.name == "nt":
        command = [shell, "-NoProfile", "-File", str(payload / f"scripts/{installer}.ps1"),
                   "provision", "-InstallDir", str(root)]
    else:
        command = [shell, str(payload / f"scripts/{installer}.sh"), "provision",
                   "--install-dir", str(root)]
    return run(command)

os.environ["COPILOT_PLUGIN_ROOT"] = str(owner)
os.environ["COPILOT_PLUGIN_STAGED_FROM"] = str(owner)
provision(owner)
previous = (root / "current-version").read_text().strip()
binding_command = [
    sys.executable, "-I", "-B", str(owner / "scripts/command_binding.py"),
    "--payload-root", str(owner), "--plugin-id", plugin, "--legacy-root", str(root),
]
run([*binding_command, "--project-root", str(project), "--publish-legacy", "--write-manifest"],
    input_text=(root / "deploy-manifest.json").read_text(encoding="utf-8"))
original_binding = json.loads(run(binding_command))
assert original_binding["status"] == "selected", original_binding
spec = importlib.util.spec_from_file_location(
    "simple_source_context", owner / "scripts/installation-context/installation_context.py",
)
assert spec and spec.loader
context = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = context
spec.loader.exec_module(context)
arguments = dict(
    payload_root=owner, plugin_id=plugin, legacy_root=root, project_root=project,
    environment=dict(os.environ), durable_home=home / ".copilot-extensions", os_profile=home,
)
decision = context.resolve_runtime_deployment(**arguments)
policy = root / "runtime-deployment.json"
policy.write_text(json.dumps({
    "schema": context.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
    "owner": context._deployment_owner(decision, arguments),
}), encoding="utf-8")
config = root / "source-simple-fixture.json"
config.write_text('{"preserved":true}\n', encoding="utf-8")
database = root / "source-simple-fixture.sqlite"
with closing(sqlite3.connect(database)) as connection, connection:
    connection.execute("CREATE TABLE retained (value TEXT)")
    connection.execute("INSERT INTO retained VALUES ('existing')")
before = {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
          for path in (config, database, policy)}
snapshot = prepare_application(
    checkout, commit, plugin, home / "prepared",
    uv=shutil.which("uv"), deadline=deadline,
)
assert_application_reuse(
    checkout, commit, plugin, home / "prepared-reuse", snapshot,
    uv=shutil.which("uv"), deadline=deadline,
)
from agent_worktrees import reconcile, update_runtime, update_runtime_plan
from agent_worktrees.update_runtime_application import PreparedApplication
from agent_worktrees.update_runtime_source import PreparedRuntimeSource
reconcile.core_installed_payload_dir = lambda name: owner
reconcile.runtime_installer_environment = lambda *args: (dict(os.environ), root)
update_runtime._machine_gate_blocks = lambda *args: False
application = PreparedApplication(snapshot, plugin, "null")
artifact = application.payload
identity = json.loads(application.identity)["runtime_source"]
version = identity["package_version"]
source = PreparedRuntimeSource(artifact, application.identity, application)
step = update_runtime_plan.plan_registered(
    plugin, "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
)
assert not step.status, step.status
assert step.payload == owner and step.artifact == artifact and step.runtime_root == root
if plugin == "agent-mcp":
    assert step.environment["AGENT_MCP_NO_CUTOVER"] == "1"
    assert step.environment["AGENT_MCP_NO_VERSION_REAP"] == "1"
status = update_runtime._execute_runtime_step(step)
assert status == "OK", status
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
    plugin, "windows" if os.name == "nt" else "linux",
    force=False, gate_context=project, source=source,
).status == "SKIPPED (current)"
venv = slot / ".venv"
python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
module = plugin.replace("-", "_")
probe = (
    "import importlib,importlib.metadata,json,sys;from pathlib import Path;"
    f"m=importlib.import_module({module!r});"
    "print(json.dumps([str(Path(m.__file__).resolve()),sys.prefix,"
    f"importlib.metadata.version({plugin!r}),m.__version__]))"
)
module_path, prefix, installed_version, module_version = json.loads(
    run([str(python), "-I", "-B", "-c", probe])
)
assert Path(module_path).is_relative_to(venv) and Path(prefix) == venv
assert reconcile._versions_equal(installed_version, version), (installed_version, version)
expected_module_version = application.version if plugin == "agent-mcp" else version
assert reconcile._versions_equal(module_version, expected_module_version), (
    module_version, expected_module_version,
)
suffix = ".cmd" if plugin in {"agent-machines", "agent-mcp"} else ".ps1"
launcher = home / ".local/bin" / (plugin + suffix if os.name == "nt" else plugin)
assert launcher.is_file()
if os.name == "nt" and suffix == ".cmd":
    command = [os.environ["COMSPEC"], "/d", "/c", str(launcher), "--help"]
elif os.name == "nt":
    command = [shell, "-NoProfile", "-File", str(launcher), "--help"]
else:
    command = [str(launcher), "--help"]
completion = slot / ".install-complete.json"
complete_before = (completion.read_bytes(), completion.stat().st_mtime_ns)
run(list(application.argv(root, repair=False)), environment=dict(step.environment))
assert (completion.read_bytes(), completion.stat().st_mtime_ns) == complete_before
snapshot.rename(home / "prepared-unavailable")
checkout.rename(home / "source-unavailable")
assert plugin in run(command)
retained_binding = json.loads(run(binding_command))
assert retained_binding == original_binding, {
    "original_binding": original_binding, "retained_binding": retained_binding,
}
assert {path: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
        for path in before} == before
with closing(sqlite3.connect(database)) as connection:
    assert connection.execute("SELECT value FROM retained").fetchall() == [("existing",)]
print(json.dumps({"plugin": plugin, "collectedUpdater": True, "installedCode": True,
                  "globalCommand": True, "reuse": True, "retainedState": True,
                  "sourceUnavailable": True, "retainedBinding": True,
                  "distributionVersion": True}))
"""


def _persistent_user_path_fingerprint() -> str | None:
    if os.name != "nt":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            value = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        return None
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()


@pytest.mark.skipif(
    os.environ.get("COPILOT_TEST_NATIVE_SOURCE_SIMPLE") != "1",
    reason="opt-in native source construction without resident-service effects",
)
@pytest.mark.timeout(360)
@pytest.mark.parametrize("plugin", ["agent-ssh", "agent-machines", "agent-mcp"])
def test_native_collected_source_preserves_service_less_owner(
    tmp_path_factory: pytest.TempPathFactory, plugin: str,
) -> None:
    if shutil.which("uv") is None or shutil.which("pwsh" if os.name == "nt" else "bash") is None:
        pytest.skip("native source construction requires uv and the native shell")
    home = tmp_path_factory.mktemp("ss") / "home"
    user_path = _persistent_user_path_fingerprint()
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-X", "utf8", "-c", NATIVE_PROGRAM,
         str(ROOT), str(home), plugin],
        capture_output=True, text=True, encoding="utf-8", timeout=340, **no_window_kwargs(),
    )
    assert _persistent_user_path_fingerprint() == user_path, "Fixture changed persistent User PATH"
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "plugin": plugin, "collectedUpdater": True, "installedCode": True,
        "globalCommand": True, "reuse": True, "retainedState": True,
        "sourceUnavailable": True, "retainedBinding": True,
        "distributionVersion": True,
    }
