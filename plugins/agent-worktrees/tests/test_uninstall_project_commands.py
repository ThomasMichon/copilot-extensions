"""Real venv selection and project receipts, without acquiring runtime packages.

The bounded native probes execute the actual frontend project-cleanup blocks
and real CLI against a copied package plus the declared source/dependency graph.
Only terminal-profile integration is isolated. These are not installed-wheel
closure or whole-uninstall qualification; separate entrypoint cases retain the
real native admission and its non-quiescent-namespace gate.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import site
import sys
import venv

import pytest

from native_shell_support import bash_path
from test_native_uninstall_footprint import (
    PLUGIN,
    _host,
    _load,
    _native_arguments,
    _preserved,
    _run,
    _skip_unrelated_module_consumer,
    _snapshot,
    native_install as native_install,
)

pytestmark = [
    pytest.mark.guard,
    pytest.mark.parametrize("native_install", ["short-project-home"], indirect=True),
]


@pytest.fixture
def project_install(native_install, monkeypatch: pytest.MonkeyPatch):
    home, payload, root, _, durable = native_install
    for path, data in {
        root / "config.yaml": b"{}\n",
        root / "repos.yaml": b"schema_version: 1\nrepos: {}\n",
        root / "projects.yaml": b"schema_version: 1\nprojects:\n  fixture: {}\n",
        home / ".fixture/config.yaml": b"project: fixture\n",
    }.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        durable[path] = (data, path.stat().st_dev, path.stat().st_ino)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.syspath_prepend(str(PLUGIN / "scripts"))
    engine = _load(PLUGIN / "scripts/installer_engine.py")
    packages = engine._package_roots(PLUGIN)
    graph = [
        str(payload / "src"),
        *(str(package / "src") for package in packages
          if package.resolve() != PLUGIN.resolve() and (package / "src").is_dir()),
        *site.getsitepackages(),
    ]
    environment = {
        "AGENT_HOME": "",
        "PYTHONPATH": os.pathsep.join(value for value in graph if value),
        "AGENT_WORKTREES_PAYLOAD_ROOT": str(payload),
        "COPILOT_PLUGIN_ROOT": str(payload),
        "COPILOT_PLUGIN_STAGED_FROM": str(payload),
        "AGENT_WORKTREES_STATUS_MONITOR": "0",
    }
    return home, payload, root, durable, environment


def test_selected_slot_imports_declared_cli_graph_without_ambient_pythonpath(project_install) -> None:
    home, payload, root, _, environment = project_install
    assert not os.environ.get("PYTHONPATH")
    python, _ = _slot(home, payload, root, True)
    result = _run([
        str(python), "-B", "-c",
        "import json,sys,machine_transport,agent_worktrees.installer;"
        "print(json.dumps([sys.prefix,machine_transport.__file__,"
        "agent_worktrees.installer.__file__]))",
    ], home, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    prefix, transport, installer = map(Path, json.loads(result.stdout))
    assert prefix == python.parent.parent
    assert transport.is_relative_to(PLUGIN.parents[1] / "libs/machine-transport/src")
    assert installer.is_relative_to(payload / "src")


def test_project_fixture_binding_matches_fresh_native_account(project_install) -> None:
    home, payload, root, _, environment = project_install
    before = (root / "deploy-manifest.json").read_bytes()
    script = """
import importlib.util,json,pathlib,sys
path=pathlib.Path(sys.argv[1])/'scripts/installation-context/installation_context.py'
spec=importlib.util.spec_from_file_location('fixture_account_probe',path)
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
result=module.probe_legacy_entrypoint(payload_root=pathlib.Path(sys.argv[1]),
    plugin_id='agent-worktrees',legacy_root=pathlib.Path(sys.argv[2]),
    project_root=pathlib.Path(sys.argv[3]))
print(json.dumps(result['environment']))
"""
    result = _run([
        sys.executable, "-I", "-S", "-B", "-c", script, str(payload), str(root), str(home),
    ], home, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == json.loads(before)["commandBinding"]["environment"]
    assert (root / "deploy-manifest.json").read_bytes() == before


def _slot(home, payload, root, qualified):
    source = _load(payload / "scripts/versioned_runtime_source.py")
    version = source.runtime_version("1.0.0", "a" * 40, "b" * 64) if qualified else "1.0.0"
    slot = root / "versions" / version
    venv.EnvBuilder(with_pip=False, symlinks=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime = _load(payload / "scripts/versioned_runtime.py")
    runtime.mark_complete(root, version, pid=0)
    for name in ("current-version", "last-known-good"):
        (root / name).write_text(version + "\n", encoding="utf-8")
    return python, version


def _publish_project(home, payload, root, python, environment, foreign):
    owner = payload
    if foreign:
        owner = home / ".copilot/installed-plugins/foreign/agent-worktrees"
        owner.mkdir(parents=True)
        shutil.copyfile(payload / "plugin.json", owner / "plugin.json")
    result = _run([
        str(python), "-B", "-c",
        "from agent_worktrees import installer; installer._deploy_project_binstub('fixture')",
    ], home, env={**environment, "AGENT_WORKTREES_PAYLOAD_ROOT": str(owner)})
    assert result.returncode == 0, result.stdout + result.stderr
    paths = sorted((home / ".local/bin").glob("fixture*"))
    assert paths
    receipt = root / "binstub-receipts/fixture.json"
    assert receipt.is_file()
    return paths, receipt


def _quote(value, shell):
    if shell == "bash":
        return shlex.quote(bash_path(_host("bash"), Path(value)))
    return "'" + str(value).replace("'", "''") + "'"


def _native_project_probe(shell, home, payload, root):
    suffix = "sh" if shell == "bash" else "ps1"
    source = (payload / f"scripts/install.{suffix}").read_text(encoding="utf-8-sig")
    quote = lambda value: _quote(value, shell)  # noqa: E731
    if shell == "bash":
        body = source.split("\n    uninstall)\n", 1)[1]
        body = body[body.index("\n        if $HAS_PROJECT; then"):body.index(
            '\n        "$__aw_admission_python" "${__aw_uninstall_args[@]}" --remove-runtime'
        )]
        prefix = f"""
set -euo pipefail
HAS_PROJECT=true
INSTALL_DIR={quote(root)}
SCRIPT_DIR={quote(payload / 'scripts')}
PLUGIN_DIR={quote(payload)}
VENV_PYTHON={quote(home / 'unqualified-missing-python')}
__aw_admission_python={quote(sys.executable)}
PROJECT_NAME=fixture
remove_tabby_profile() {{ :; }}
warn() {{ printf '%s\\n' "$*"; }}
"""
        start = source.index("__aw_uninstall_native_path() {")
        end = source.index("\n}\n", start) + len("\n}\n")
        prefix += "\n__aw_action=uninstall\n" + source[start:end]
    else:
        body = source.split("\n    'uninstall' {\n", 1)[1]
        body = body[body.index("\n        if ($HasProject) {"):body.index(
            "\n        # Remove Windows Terminal fragment"
        )]
        prefix = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$HasProject = $true
$InstallDir = {quote(root)}
$ScriptDir = {quote(payload / 'scripts')}
$PluginDir = {quote(payload)}
$VenvPython = {quote(home / 'unqualified-missing-python')}
$admissionPython = {quote(sys.executable)}
$ProjectName = 'fixture'
function Write-ServiceWarn($message) {{ [Console]::Out.WriteLine($message) }}
"""
    assert "--project-python" in body
    assert "reconcile-binstubs --remove" in body
    script = home / f"project-probe.{suffix}"
    script.write_text(prefix + body + ("\nexit 0\n" if shell != "bash" else "\n"), encoding="utf-8")
    executable = _host(shell)
    return ([executable, bash_path(executable, script)] if shell == "bash" else
            [executable, "-NoProfile", "-NonInteractive", "-File", str(script)])


def _project_invocation(home, payload, root, shell, inferred):
    arguments = _native_arguments(shell, _host(shell), payload, root)
    if inferred:
        cwd = home / "fixture"
        cwd.mkdir(exist_ok=True)
    else:
        cwd = home
        arguments += ["--project-name", "fixture"] if shell == "bash" else ["-ProjectName", "fixture"]
    return arguments, cwd


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("qualified", [False, True], ids=["package-slot", "source-slot"])
@pytest.mark.parametrize("foreign", [False, True], ids=["owned", "foreign"])
def test_native_project_cleanup_uses_completed_installed_slot(
    project_install, shell, qualified, foreign,
):
    home, payload, root, durable, environment = project_install
    python, version = _slot(home, payload, root, qualified)
    paths, receipt = _publish_project(home, payload, root, python, environment, foreign)
    before_commands = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
                       for path in (*paths, receipt)}
    selection = {path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
                 for path in (root / "current-version", root / "last-known-good",
                              python.parent.parent / ".install-complete.json")}
    result = _run([
        sys.executable, "-I", "-S", "-B", str(payload / "scripts/uninstall-state.py"),
        "--project-python", str(root),
    ], home, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(json.loads(result.stdout)) == python
    assert python.parent.parent.name == version
    result = _run(_native_project_probe(shell, home, payload, root), home, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    if foreign:
        _preserved(before_commands)
        assert "ownership" in result.stdout + result.stderr
    else:
        assert not any(path.exists() for path in (*paths, receipt))
    _preserved(selection)
    _preserved(durable)
    assert not (root / "tool").exists()


@pytest.mark.parametrize("case", ["missing", "ambiguous"])
def test_project_python_refuses_missing_or_ambiguous_without_mutation(project_install, case):
    home, payload, root, durable, environment = project_install
    if case == "ambiguous":
        _slot(home, payload, root, True)
        for marker in ("current-version", "last-known-good"):
            (root / marker).write_text("", encoding="utf-8")
    else:
        root = home / "absent-project-runtime"
    before = _snapshot(root) if root.exists() else None
    result = _run([
        sys.executable, "-I", "-S", "-B", str(payload / "scripts/uninstall-state.py"),
        "--project-python", str(root),
    ], home, env=environment)
    assert result.returncode != 0
    assert "No unambiguous completed installed runtime" in result.stderr
    if before is None:
        assert not root.exists()
    else:
        _preserved(before[0])
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_project_cleanup_without_command_or_receipt_needs_no_runtime(project_install, shell):
    home, payload, root, durable, environment = project_install
    before = _snapshot(root)
    result = _run(_native_project_probe(shell, home, payload, root), home, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    _preserved(before[0])
    _preserved(durable)
    assert not (root / ".venv").exists()
    assert not (root / "tool").exists()


@pytest.mark.parametrize("candidate", ["command", "opaque-receipt"])
def test_existing_project_candidate_still_refuses_without_completed_runtime(project_install, candidate):
    home, payload, root, durable, environment = project_install
    if candidate == "command":
        templates = _load(payload / "src/agent_worktrees/project_command_templates.py")
        name = next(iter(templates.render("fixture", payload, windows=os.name == "nt")))
        path = home / ".local/bin" / name
    else:
        path = root / "binstub-receipts/fixture.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unknown existing project candidate")
    before = _snapshot(root)
    before[0][path] = (path.read_bytes(), path.stat().st_dev, path.stat().st_ino)
    result = _run([
        sys.executable, "-I", "-S", "-B", str(payload / "scripts/uninstall-state.py"),
        "--project-python", str(root), "fixture",
    ], home, env=environment)
    assert result.returncode != 0
    assert "No unambiguous completed installed runtime" in result.stderr
    _preserved(before[0])
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("inferred", [False, True], ids=["explicit", "inferred-cwd"])
@pytest.mark.parametrize("candidate", ["command", "receipt"])
def test_named_project_command_missing_runtime_refuses_before_absent_return(
    project_install, shell, inferred, candidate,
):
    home, payload, default_root, durable, environment = project_install
    root = home / "missing-named-runtime"
    if candidate == "command":
        templates = _load(payload / "src/agent_worktrees/project_command_templates.py")
        name = next(iter(templates.render("fixture", payload, windows=os.name == "nt")))
        command = home / ".local/bin" / name
    else:
        command = default_root / "binstub-receipts/fixture.json"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_bytes(b"unknown existing project command")
    before = {command: (command.read_bytes(), command.stat().st_dev, command.stat().st_ino)}
    arguments, cwd = _project_invocation(home, payload, root, shell, inferred)
    result = _run(arguments, home, env=environment, cwd=cwd)
    assert result.returncode != 0
    assert "No unambiguous completed installed runtime" in result.stderr
    assert not root.exists()
    _preserved(before)
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("inferred", [False, True], ids=["explicit", "inferred-cwd"])
def test_named_project_stamp_without_command_or_receipt_uninstalls(project_install, shell, inferred):
    home, payload, root, durable, environment = project_install
    before = _snapshot(root)
    arguments, cwd = _project_invocation(home, payload, root, shell, inferred)
    result = _run(arguments, home, env=environment, cwd=cwd)
    _skip_unrelated_module_consumer(result, before, durable)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (root / "snapshots/1.0.0").exists()
    assert not (root / ".venv").exists()
    assert not (root / "tool").exists()
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
@pytest.mark.parametrize("inferred", [False, True], ids=["explicit", "inferred-cwd"])
def test_whole_native_uninstall_retires_source_qualified_project_command(project_install, shell, inferred):
    home, payload, root, durable, environment = project_install
    python, _ = _slot(home, payload, root, True)
    paths, receipt = _publish_project(home, payload, root, python, environment, False)
    before = _snapshot(root)
    before[0].update({
        path: (path.read_bytes(), path.stat().st_dev, path.stat().st_ino) for path in paths
    })
    arguments, cwd = _project_invocation(home, payload, root, shell, inferred)
    result = _run(arguments, home, env=environment, cwd=cwd)
    _skip_unrelated_module_consumer(result, before, durable)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(path.exists() for path in (*paths, receipt, python))
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_inferred_project_without_candidates_is_cold_noop(project_install, shell):
    home, payload, default_root, durable, environment = project_install
    root = home / "missing-inferred-runtime"
    before = _snapshot(default_root)
    arguments, cwd = _project_invocation(home, payload, root, shell, True)
    result = _run(arguments, home, env=environment, cwd=cwd)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()
    _preserved(before[0])
    _preserved(durable)


@pytest.mark.parametrize("shell", ["bash", "powershell", "pwsh"])
def test_explicit_project_wins_over_cwd_inference(project_install, shell):
    home, payload, _, durable, environment = project_install
    root = home / "missing-explicit-runtime"
    templates = _load(payload / "src/agent_worktrees/project_command_templates.py")
    name = next(iter(templates.render("fixture", payload, windows=os.name == "nt")))
    command = home / ".local/bin" / name
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_bytes(b"unknown existing project command")
    before = {command: (command.read_bytes(), command.stat().st_dev, command.stat().st_ino)}
    arguments, cwd = _project_invocation(home, payload, root, shell, True)
    arguments += ["--project-name", "other"] if shell == "bash" else ["-ProjectName", "other"]
    result = _run(arguments, home, env=environment, cwd=cwd)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not root.exists()
    _preserved(before)
    _preserved(durable)
