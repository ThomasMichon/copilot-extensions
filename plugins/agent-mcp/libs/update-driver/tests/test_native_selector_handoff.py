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
        # Match a source checkout's colocated payload and declared libraries.
        libraries = area / "libraries"
        for directory in packages.values():
            source = ROOT / "libs" / directory
            destination = libraries / directory
            destination.mkdir(parents=True)
            for name in ("pyproject.toml", "README.md"):
                shutil.copyfile(source / name, destination / name)
            shutil.copytree(
                source / "src", destination / "src",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        (payload / "pyproject.toml").write_text(
            "[project]\nname=" + json.dumps(plugin) + '\nversion="1.0.0"\n'
            + "dependencies=" + json.dumps(list(packages)) + "\n[tool.uv.sources]\n"
            + "".join(
                json.dumps(name) + "={path=" + json.dumps(str(libraries / directory)) + "}\n"
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


@pytest.fixture
def index_consumer(boundary, area):
    prepare, project = boundary

    def prepare_consumer(unsupported=None):
        _, args, _, _, paths, stage = prepare("agent-index")
        scripts = args.payload / "scripts"
        shutil.copyfile(
            ROOT / "libs" / "versioned-runtime" / "versioned_runtime_native.ps1",
            scripts / "versioned_runtime_native.ps1",
        )
        if unsupported:
            provider = scripts / "installer_driver.py"
            text = provider.read_text()
            start, end = text.index("CONTEXT_KEYS = ("), text.index("\nTRANSIENT_KEYS = (")
            declaration = text[start:end]
            assert declaration.count(f'"{unsupported}"') == 1
            provider.write_text(
                text[:start] + declaration.replace(f'"{unsupported}",', "") + text[end:],
            )
        profile = area / "profile"
        profile.mkdir()
        trace = area / "consumer.jsonl"
        consumer = ROOT / "plugins" / "agent-index" / "scripts" / "install.ps1"
        # Select the actual consumer body, not the top-level test-inhibited installer.
        # Its guard and native entry stay intact; no contained-test flag is cleared.
        extraction = f"""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$source=[IO.File]::ReadAllText({quote(consumer)})
$ast=[Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'Index source parse failed'}}
$entry=@($ast.EndBlock.Statements|Where-Object {{
 $_ -is [Management.Automation.Language.IfStatementAst] -and
 $_.Extent.Text.StartsWith("if (`$env:OS -eq 'Windows_NT' -and") -and
 $_.Extent.Text.Contains('Native Index driver cannot retain')
}})
if($entry.Count -ne 1 -or $entry[0].Clauses.Count -ne 1){{throw 'Index admission boundary is ambiguous'}}
$start=$source.IndexOf('# Capture caller-relative selectors before driver entry')
if($start -lt 0 -or $start -ge $entry[0].Extent.StartOffset){{throw 'Index selector boundary is unavailable'}}
$freeze=$source.Substring($start,$entry[0].Extent.StartOffset-$start)
$admit=($entry[0].Clauses[0].Item2.Statements|ForEach-Object {{$_.Extent.Text}})-join "`n"
@{{freeze=$freeze;admit=$admit}}|ConvertTo-Json -Compress
"""
        extracted = subprocess.run(
            [str(args.shell), "-NoProfile", "-NonInteractive", "-Command", extraction],
            capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert extracted.returncode == 0, extracted.stderr
        fragments = json.loads(extracted.stdout)

        def admitted_consumer(phase):
            return f"""
$inputValues=@{{installation=$InstallDir; policy=$InstallationConfig;
 policy_environment=$env:AGENT_INDEX_INSTALLATION_CONFIG; config_root=$env:AGENT_INDEX_CONFIG_ROOT}}
{fragments['freeze']}
$row=@{{phase='{phase}'; incoming=$inputValues; installation=$InstallDir;
 policy=$env:AGENT_INDEX_INSTALLATION_CONFIG; config_root=$env:AGENT_INDEX_CONFIG_ROOT;
 cwd=(Get-Location).Path; native=$env:COPILOT_EXTENSIONS_NATIVE_INSTALLER;
 contained=$env:COPILOT_EXTENSIONS_TEST_CONTAINED}}
[IO.File]::AppendAllText({quote(trace)},($row|ConvertTo-Json -Depth 4 -Compress)+"`n")
{fragments['admit']}
if(-not $driverEntry.Participant -or $driverEntry.ExitCode -ne 0){{throw 'consumer did not authenticate'}}
"""

        (scripts / "install.ps1").write_text(f"""
param([string]$Action,[string]$InstallDir,[string]$InstallationConfig)
$ErrorActionPreference='Stop'
Set-StrictMode -Version 2
if($env:COPILOT_EXTENSIONS_TEST_CONTAINED -ne '1'){{throw 'fixture requires native containment'}}
function Start-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Register-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Stop-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Unregister-ScheduledTask {{throw 'host scheduling effects are excluded'}}
function Start-Process {{throw 'installer business effects are excluded'}}
function Stop-Process {{throw 'worker retirement is excluded'}}
. (Join-Path $PSScriptRoot 'versioned_runtime_native.ps1')
$ScheduledTaskNames=$null;$DryRun=$false
{admitted_consumer('entry')}
$before=$row
Set-Location -LiteralPath {quote(stage)}
{admitted_consumer('staged')}
$after=$row
[IO.File]::WriteAllText((Join-Path $before.installation 'boundary.json'),
 (@{{before=$before;after=$after}}|ConvertTo-Json -Depth 5 -Compress))
""", encoding="utf-8", newline="")
        parse = (
            "$tokens=$null;$errors=$null;"
            "[void][Management.Automation.Language.Parser]::ParseFile("
            + quote(scripts / "install.ps1")
            + ",[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors|Out-String)}"
        )
        for shell in dict.fromkeys((str(args.shell), shutil.which("pwsh"))):
            if shell is None:
                continue
            parsed = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", parse],
                capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert parsed.returncode == 0, parsed.stderr
        environment = dict(os.environ, HOME=str(profile), USERPROFILE=str(profile))
        environment[KEYS[1]] = str(paths[1].with_name("alternate.json").relative_to(project))
        command = [
            str(args.shell), "-NoProfile", "-NonInteractive", "-File", str(scripts / "install.ps1"),
            "-Action", "update", "-InstallDir", os.path.relpath(args.root, project),
            "-InstallationConfig", str(paths[1].relative_to(project)),
        ]
        return command, environment, args, paths, stage, trace, profile / ".copilot-extensions" / "update-driver"

    return prepare_consumer, project


def test_actual_index_consumer_freezes_selectors_through_native_driver_and_reentry(index_consumer):
    prepare, project = index_consumer
    command, environment, args, paths, stage, trace, execution = prepare()
    result = subprocess.run(
        command, cwd=project, env=environment, capture_output=True, text=True, timeout=25,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in trace.read_text().splitlines()]
    assert len(rows) == 3
    assert rows[0]["incoming"] == {
        "installation": os.path.relpath(args.root, project),
        "policy": str(paths[1].relative_to(project)),
        "policy_environment": str(paths[1].with_name("alternate.json").relative_to(project)),
        "config_root": str(paths[2].relative_to(project)),
    }
    for row in rows:
        assert row["installation"] == str(args.root)
        assert row["policy"] == str(paths[1])
        assert row["config_root"] == str(paths[2])
        assert row["contained"] == "1"
    assert [row["cwd"] for row in rows] == [str(project), str(project), str(stage)]
    assert [row["native"] for row in rows] == [None, "agent-index", "agent-index"]
    receipt = json.loads((args.root / "boundary.json").read_text())
    assert receipt["before"] == rows[1]
    assert receipt["after"] == rows[2]
    request, = core.status(root=execution)
    assert request.state == "completed"
    assert request.spec.project == str(args.root)
    assert Path(request.spec.cwd) == project
    context = dict(request.spec.context)
    assert context[KEYS[1]] == str(paths[1])
    assert context[KEYS[2]] == str(paths[2])
    assert set(path.name for path in args.root.iterdir()) == {"boundary.json"}


@pytest.mark.parametrize("unsupported", KEYS[1:])
def test_actual_index_consumer_refuses_downlevel_provider_before_queue(index_consumer, unsupported):
    prepare, project = index_consumer
    command, environment, args, _, _, trace, execution = prepare(unsupported)
    result = subprocess.run(
        command, cwd=project, env=environment, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode != 0
    assert f"Native Index driver cannot retain {unsupported}" in result.stderr
    assert len(trace.read_text().splitlines()) == 1
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
