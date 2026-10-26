"""Exercise the generated description without starting a supervisor or enrolling services."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

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
        "-and $node.Name -eq 'Write-SupervisorLauncher'},$true))\n"
        "if($functions.Count -ne 1){throw 'Expected one launcher writer'}\n"
        "Invoke-Expression $functions[0].Extent.Text\n"
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
    args = description["arguments"]
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
