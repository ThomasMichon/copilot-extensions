"""Exercise the generated description without starting a supervisor or enrolling services."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import re
import sys

import pytest
from agent_procutil import python_cache_args

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="generated Windows launcher")


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("mode", ["serve", "legacy", "unlabeled"])
def test_generated_description_preserves_policy_and_slot_selection(tmp_path, mode):
    shell = shutil.which("powershell.exe")
    assert shell
    runtime = tmp_path / "runtime's directory with spaces"
    slot = runtime / "versions" / "fixture" / "Scripts"
    slot.mkdir(parents=True)
    python = slot / "python.exe"
    python.touch()  # Description must never try to execute this sentinel.
    (runtime / "current-version").write_text("fixture")
    log = runtime / "supervise-service.log"
    log.write_bytes(b"x" * (1024 * 1024 + 1))
    profile = runtime / "custom profile.env"
    profile.write_text(
        "AGENT_DISPATCH_SUPERVISE_INTERVAL=41\n"
        + ("AGENT_DISPATCH_SUPERVISE_MODE=serve\n"
           "AGENT_DISPATCH_SUPERVISE_MACHINE=fixture-host\n" if mode == "serve" else "")
        + ("AGENT_DISPATCH_SUPERVISE_LABELS=one,two\n"
           "AGENT_DISPATCH_SUPERVISE_HEADLESS_AGENT=fixture-agent\n" if mode == "legacy" else "")
    )
    writer = runtime / "write.ps1"
    writer.write_text(
        "$ErrorActionPreference='Stop'\n"
        f"$InstallDir={quote(runtime)};$LinkDir={quote(runtime / '.venv')};"
        f"$LinkPython={quote(python)};$utf8NoBom=[Text.UTF8Encoding]::new($false)\n"
        "$tokens=$null;$errors=$null\n"
        "$ast=[Management.Automation.Language.Parser]::ParseFile("
        f"{quote(INSTALLER)},[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'Installer parse failed'}\n"
        "$functions=@($ast.FindAll({param($node) "
        "$node -is [Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -in @('Enter-PluginSnapshotLock','Publish-FileAtomically',"
        "'Publish-ServiceLauncher','Write-SupervisorLauncher')},$true))\n"
        "if($functions.Count -ne 4){throw 'Expected launcher writer and publication helpers'}\n"
        "foreach($function in $functions){Invoke-Expression $function.Extent.Text}\n"
        "Write-SupervisorLauncher | Out-Null\n", encoding="utf-8",
    )
    common = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    created = subprocess.run([*common, str(writer)], capture_output=True, text=True,
                             timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    assert created.returncode == 0, created.stdout + created.stderr
    launcher = runtime / "supervise-service.ps1"
    assert launcher.read_text(encoding="utf-8-sig").splitlines()[0] == (
        "# agent-dispatch-supervisor-launch-v1"
    )
    result = subprocess.run([*common, str(launcher), "-EnvFile", str(profile), "-DescribeLaunch"],
                            capture_output=True, text=True, timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if mode == "unlabeled":
        assert result.returncode != 0
        assert "refusing to run with no opt-in label" in result.stderr
        return
    assert result.returncode == 0, result.stdout + result.stderr
    description = json.loads(base64.b64decode(result.stdout.strip(), validate=True))
    assert set(description) == {"version", "executable", "arguments", "log_path"}
    assert description["version"] == 1
    assert Path(description["executable"]).samefile(python)
    assert description["log_path"] == str(runtime / "supervise-service.log")
    assert description["arguments"][0] == "-X"
    cache = Path(description["arguments"][1].removeprefix("pycache_prefix="))
    assert cache.parent == Path(python_cache_args(runtime)[1].partition("=")[2]).parent
    assert re.fullmatch(r"unsealed-[0-9a-f]{32}", cache.name)
    args = description["arguments"][2:]
    assert args[:3] == ["-m", "agent_dispatch", "supervise"]
    assert args[args.index("--interval") + 1] == "41"
    if mode == "serve":
        assert args[3:5] == ["serve", "--legacy-env"]
        assert args[args.index("--machine") + 1] == "fixture-host"
        assert "--label" not in args
    else:
        assert args[3] == "--all-repos"
        assert [args[i + 1] for i, arg in enumerate(args) if arg == "--label"] == ["one", "two"]
        assert args[args.index("--headless-agent") + 1] == "fixture-agent"
    assert not log.exists()
    assert log.with_suffix(".log.1").stat().st_size == 1024 * 1024 + 1


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("layout", ["nested-slot", "installation-root-only"])
def test_coordinator_launcher_resolves_physical_nested_slot_not_root_alias(tmp_path, host, layout):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    root = tmp_path / "application"
    (root / "bin").mkdir(parents=True)
    (root / "versions").mkdir()
    shutil.copyfile(INSTALLER.with_name("resolve-runtime.ps1"), root / "bin" / "resolve-runtime.ps1")
    version = "1.0.0"
    slot = root / "versions" / version
    prefix = slot / ".venv" if layout == "nested-slot" else root / ".venv"
    (prefix / "Scripts").mkdir(parents=True)
    python = prefix / "Scripts" / "python.exe"
    python.touch()
    (root / "current-version").write_text(version)
    if layout == "nested-slot":
        (slot / ".install-complete.json").write_text(json.dumps({
            "version": version, "completed_at": "fixture", "pid": 0,
        }))
    publisher = root / "publish.ps1"
    publisher.write_text(f"""
$ErrorActionPreference='Stop'
$InstallDir={quote(root)};$LinkDir={quote(root / '.venv')};$LinkPython={quote(python)}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(INSTALLER)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'source parse failed'}}
$owner=@($ast.FindAll({{param($node)
 $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-CoordinatorTask'
}},$true))
if($owner.Count -ne 1){{throw 'coordinator owner is ambiguous'}}
$assignment=@($owner[0].FindAll({{param($node)
 $node -is [Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$launcherBody'
}},$true))
if($assignment.Count -ne 1){{throw 'coordinator launcher is ambiguous'}}
. ([scriptblock]::Create($assignment[0].Extent.Text))
[IO.File]::WriteAllText((Join-Path $InstallDir 'serve-service.ps1'),$launcherBody)
""")
    common = [shell, "-NoProfile", "-NonInteractive", "-File"]
    published = subprocess.run(
        [*common, str(publisher)], capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert published.returncode == 0, published.stderr
    resolved = subprocess.run(
        [*common, str(root / "serve-service.ps1"), "-ResolveOnly"],
        capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if layout == "nested-slot":
        assert resolved.returncode == 0, resolved.stderr
        assert Path(resolved.stdout.strip()).samefile(python)
    else:
        assert resolved.returncode != 0
        assert "Task runtime unresolved" in resolved.stderr
    assert not (root / "serve-service.log").exists()


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_selected_runtime_decodes_real_hidden_probe_output(tmp_path, host):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    root = tmp_path / "runtime's \u00e9 directory"
    slot = root / "versions" / "1.0"
    python = slot / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "current-version").write_text("1.0")
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": "1.0", "completed_at": "fixture", "pid": 0,
    }))
    selected = root / "selected.ps1"
    selected.write_text(
        "$ErrorActionPreference='Stop'\n"
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)\n"
        f". {quote(INSTALLER.with_name('versioned_runtime_native.ps1'))}\n"
        f". {quote(INSTALLER.parents[1] / 'src' / 'agent_dispatch' / 'windows-scheduling.ps1')}\n"
        f"$InstallDir={quote(root)};$ScriptDir={quote(INSTALLER.parent)}\n"
        f"function Get-BootstrapPython {{ {quote(sys.executable)} }}\n"
        "@{selected=(Get-DispatchSelectedPython)}|ConvertTo-Json -Compress\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(selected)],
        capture_output=True, encoding="utf-8", timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(json.loads(result.stdout)["selected"]).samefile(python)
