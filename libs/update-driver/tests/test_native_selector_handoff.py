"""Real native driver/participant/PowerShell boundaries, without install effects."""
from __future__ import annotations

from functools import partial
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from update_driver import core

ROOT = Path(__file__).resolve().parents[3]
CANONICAL = ROOT / "libs" / "installer-engine"
KEYS = (
    "AGENT_DISPATCH_INSTALLATION_CONFIG",
    "AGENT_INDEX_INSTALLATION_CONFIG",
    "AGENT_INDEX_CONFIG_ROOT",
)
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows driver boundary")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def configuration(path, root):
    path.write_text(json.dumps({
        "version": 1, "installation": {
            "root": str(root), "local": {"windows_scheduled_task": "preserve"},
        },
    }), encoding="utf-8")


@pytest.fixture
def boundary(area, monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    project = area / "original project"
    project.mkdir()
    monkeypatch.chdir(project)

    def prepare(plugin, mode="valid", selectors=True):
        payload = area / plugin
        scripts = payload / "scripts"
        scripts.mkdir(parents=True)
        for directory in (CANONICAL, ROOT / "libs" / "versioned-runtime"):
            for source in directory.glob("*.py"):
                shutil.copyfile(source, scripts / source.name)
        packages = {
            "agent-update-driver": "update-driver",
            "agent-single-instance-lease": "single-instance-lease",
            "agent-procutil": "agent-procutil",
            "agent-zdd": "zdd",
        }
        (payload / "pyproject.toml").write_text(
            "[project]\nname=" + json.dumps(plugin) + '\nversion="1.0.0"\n'
            + "dependencies=" + json.dumps(list(packages)) + "\n[tool.uv.sources]\n"
            + "".join(
                json.dumps(name) + "={path=" + json.dumps(str(ROOT / "libs" / directory)) + "}\n"
                for name, directory in packages.items()
            ),
        )
        stage = payload / "stage"
        stage.mkdir()
        runtime = area / "selected installation"
        runtime.mkdir()
        policies = project / "policy files"
        policies.mkdir()
        dispatch = policies / "dispatch.json"
        index = policies / "index.json"
        config_root = policies / "index configuration"
        config_root.mkdir()
        configuration(dispatch, runtime if plugin == "agent-dispatch" else area / "other dispatch")
        configuration(index, runtime if plugin == "agent-index" else area / "other index")
        alternate = policies / "alternate.json"
        configuration(alternate, runtime if mode != "foreign" else area / "foreign installation")
        changed_root = policies / "changed configuration"
        changed_root.mkdir()
        if selectors:
            for key, path in zip(KEYS, (dispatch, index, config_root), strict=True):
                monkeypatch.setenv(key, str(path.relative_to(project)))
        shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        active = KEYS[0] if plugin == "agent-dispatch" else KEYS[1]
        reentry_root = area / "foreign execution target" if mode == "foreign-project" else runtime
        mutation = ""
        if mode in {"changed", "foreign"}:
            mutation = f"$env:{active}={quote(alternate)}\n"
        elif mode == "changed-directory":
            mutation = f"$env:{KEYS[2]}={quote(changed_root)}\n"
        elif mode == "deleted":
            mutation = f"[IO.File]::Delete($env:{active})\n"
        script = f"""
param([string]$Action,[string]$InstallDir)
$ErrorActionPreference='Stop'
function Start-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Register-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Start-Process {{throw 'runtime installation effects are excluded'}}
function Stop-Process {{throw 'worker retirement is excluded'}}
$before=@{{
 action=$Action; installation=$InstallDir; cwd=(Get-Location).Path;
 native=$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER;
 dispatch=$env:{KEYS[0]}; index=$env:{KEYS[1]}; config_root=$env:{KEYS[2]}
}}
Set-Location -LiteralPath {quote(stage)}
{mutation}
& {quote(sys.executable)} -I -S -B {quote(scripts / 'installer_driver.py')} `
 --payload {quote(payload)} --root {quote(reentry_root)} --shell {quote(shell)} `
 --plugin {quote(plugin)} --action $Action --check-native
$code=$LASTEXITCODE
$before.reentry_exit=$code
$before.after_cwd=(Get-Location).Path
[IO.File]::WriteAllText((Join-Path $InstallDir 'boundary.json'),($before|ConvertTo-Json -Compress))
exit $code
"""
        (scripts / "install.ps1").write_text(script, encoding="utf-8")
        spec = importlib.util.spec_from_file_location("_selector_driver", scripts / "installer_driver.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        args = SimpleNamespace(
            payload=payload, root=runtime, shell=shell, plugin=plugin, action="update",
            options=[], cell_context=None, retained_runtime=None, retained_runtime_sha256=None,
            install_source=None, check_native=False, remove_broker=False,
        )
        execution = area / "execution"
        driver = SimpleNamespace(
            RequestSpec=core.RequestSpec, TrustedRunner=core.TrustedRunner,
            enqueue=partial(core.enqueue, root=execution),
            drain=partial(core.drain, root=execution),
            wait=partial(core.wait, root=execution),
        )
        return module, args, driver, execution, (dispatch, index, config_root), stage

    return prepare, project


@pytest.mark.parametrize("plugin", ["agent-dispatch", "agent-index"])
def test_all_selectors_survive_real_native_child_and_staged_reentry(boundary, plugin):
    prepare, project = boundary
    module, args, driver, execution, paths, stage = prepare(plugin)
    assert module.foreground(args, driver) == 0
    receipt = json.loads((args.root / "boundary.json").read_text())
    assert [receipt["dispatch"], receipt["index"], receipt["config_root"]] == [str(path) for path in paths]
    assert receipt["native"] == plugin
    assert receipt["installation"] == str(args.root)
    assert receipt["cwd"] == str(project)
    assert receipt["after_cwd"] == str(stage)
    assert receipt["reentry_exit"] == 0
    request, = core.status(root=execution)
    assert request.spec.project == str(args.root)
    assert Path(request.spec.cwd) == project
    assert dict(request.spec.context)[KEYS[0]] == str(paths[0])
    assert request.state == "completed"


def test_absent_selectors_remain_absent_through_native_reentry(boundary):
    prepare, _ = boundary
    module, args, driver, execution, _, _ = prepare("agent-dispatch", selectors=False)
    assert module.foreground(args, driver) == 0
    receipt = json.loads((args.root / "boundary.json").read_text())
    assert [receipt["dispatch"], receipt["index"], receipt["config_root"]] == [None, None, None]
    request, = core.status(root=execution)
    assert all(key not in dict(request.spec.context) for key in KEYS)


@pytest.mark.parametrize("plugin,mode", [
    ("agent-dispatch", "changed"),
    ("agent-index", "changed-directory"),
    ("agent-index", "foreign"),
    ("agent-dispatch", "deleted"),
    ("agent-dispatch", "foreign-project"),
])
def test_native_reentry_refuses_changed_foreign_or_missing_selectors(boundary, plugin, mode):
    prepare, _ = boundary
    module, args, driver, execution, _, _ = prepare(plugin, mode, selectors=mode != "foreign-project")
    with pytest.raises(RuntimeError, match="did not complete|unclosed participants"):
        module.foreground(args, driver)
    receipt = json.loads((args.root / "boundary.json").read_text())
    assert receipt["reentry_exit"] != 0
    request, = core.status(root=execution)
    assert request.state != "completed"
    evidence = request.log_path.read_text(encoding="utf-8")
    expected = {
        "changed": "selectors changed after driver admission",
        "changed-directory": "selectors changed after driver admission",
        "foreign": "foreign root",
        "deleted": "FileNotFoundError",
        "foreign-project": "changed after driver admission",
    }[mode]
    assert expected in evidence
    assert set(path.name for path in args.root.iterdir()) == {"boundary.json"}


@pytest.mark.parametrize("failure", ["missing", "foreign", "empty", "unknown"])
def test_explicit_selector_refusal_precedes_queue_and_native_child(boundary, monkeypatch, failure):
    prepare, _ = boundary
    module, args, driver, execution, paths, _ = prepare("agent-dispatch")
    if failure == "missing":
        paths[0].unlink()
    elif failure == "foreign":
        configuration(paths[0], args.root.parent / "foreign")
    elif failure == "empty":
        monkeypatch.setenv(KEYS[0], "")
    else:
        monkeypatch.setenv(KEYS[0] + "_UNKNOWN", str(paths[0]))
    with pytest.raises((ValueError, FileNotFoundError)):
        module.foreground(args, driver)
    assert not execution.exists()
    assert not list(args.root.iterdir())


def test_restored_stage_copy_remains_pure_before_selector_admission(area):
    start = "# === install-contract:v4 payload-copy"
    end = "# === end install-contract:v4 payload-copy ==="

    def block(plugin):
        text = (ROOT / "plugins" / plugin / "scripts" / "install.ps1").read_text()
        return text[text.index(start):text.index(end) + len(end)]

    body = block("agent-dispatch")
    assert body == block("agent-worktrees")
    source = area / "source tree"
    source.mkdir()
    (source / "input.txt").write_text("fixture source")
    destination = area / "staged tree"
    script = area / "copy.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop';Set-StrictMode -Version 2\n"
        "function Test-DispatchStartIntent {throw 'copy must not query startup policy'}\n"
        + body + "\nCopy-InstallerStageDirectory $args[0] $args[1]\n",
    )
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    result = subprocess.run(
        [str(shell), "-NoProfile", "-NonInteractive", "-File", str(script), str(source), str(destination)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stderr
    assert (destination / "input.txt").read_text() == "fixture source"
