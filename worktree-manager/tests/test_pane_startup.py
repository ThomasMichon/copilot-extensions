"""Picker startup serialization uses the selected copied reader without Python."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_pane_args_file import HELPERS, PWSH, WRAPPER, _test_env

pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 is required")
SETUP_SCRIPTS = WRAPPER.parents[2] / "plugins" / "agent-worktrees" / "scripts"


def _copied_setup(tmp_path):
    shell = tmp_path / "launch-attempts" / ("a" * 32) / ("b" * 32 + ".shell")
    shell.mkdir(parents=True)
    shutil.copy2(SETUP_SCRIPTS / "launch-command.ps1", shell)
    (shell / "default-setup.ps1").write_text(
        "# agent-worktrees:native-setup-owner-v1\n"
        "[IO.File]::WriteAllText($env:TEST_CAPTURE, "
        "(ConvertTo-Json -InputObject @($args) -Compress))\n"
        "exit 17\n",
        encoding="utf-8",
    )
    (shell / "reconcile-machine-settings.ps1").write_text(
        "param([switch]$Recovery)\n"
        "[IO.File]::AppendAllText($env:TEST_RECONCILE, \"$Recovery`n\")\n",
        encoding="utf-8",
    )
    return shell


def _produce(tmp_path, command, *, record=None):
    script = tmp_path / "startup-producer.ps1"
    payload = tmp_path / "command.json"
    payload.write_text(json.dumps(command), encoding="utf-8")
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        + HELPERS.read_text("utf-8")
        + "\n$inputJson=[System.Text.Json.JsonDocument]::Parse([IO.File]::ReadAllText($args[0]))\n"
        "$command=@($inputJson.RootElement.EnumerateArray() | ForEach-Object {$_.GetString()})\n"
        "$inputJson.Dispose()\n$manifest=$null\n"
        "$serialized=New-AwPaneStartupCommand $command ([ref]$manifest) $args[1]\n"
        "ConvertTo-Json -Depth 4 -Compress @{command=@($serialized);manifest=$manifest}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(payload),
         str(record or tmp_path / "fixture.yaml")],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("direct", [True, False])
def test_picker_startup_handoff_reaches_real_copied_reader(tmp_path, direct):
    shell = _copied_setup(tmp_path)
    expected = ["", "two words", "a'quote", 'a"quote', "trailing\\",
                "--allow-all", "\u03bb", "2026-10-08T12:00:00Z", "$literal; & text"]
    command = [PWSH, "-NoProfile", "-NoLogo", "-File", str(shell / "default-setup.ps1"),
               *expected]
    if not direct:
        command = [PWSH, "-NoProfile", "-NoLogo", "-File", str(shell / "launch-command.ps1"),
                   "--", *command]
    state = _produce(tmp_path, command)
    manifest = Path(state["manifest"])
    assert manifest.parent == shell / "pane-args"
    assert state["command"] == [
        *command[:4], str(shell / "launch-command.ps1"), "--startup-manifest", str(manifest),
    ]
    document = json.loads(manifest.read_text("utf-8"))
    assert document["argv"] == (["--recovery", "--", *command] if direct else command[5:])
    capture, reconcile = tmp_path / "capture.json", tmp_path / "reconcile.txt"
    result = subprocess.run(
        state["command"], capture_output=True, text=True, encoding="utf-8", timeout=15,
        env=dict(_test_env(tmp_path), TEST_CAPTURE=str(capture), TEST_RECONCILE=str(reconcile)),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 17, result.stderr
    assert json.loads(capture.read_text("utf-8")) == expected
    assert not reconcile.exists()
    assert not manifest.exists()


@pytest.mark.parametrize("resume_args", [["--resume=original"], ["--resume", "original"]])
def test_manager_serializes_direct_resume_after_authoritative_binding(tmp_path, resume_args):
    shell = _copied_setup(tmp_path)
    record = tmp_path / "fixture.yaml"
    capture = tmp_path / "capture.py"
    capture.write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    literal = "literal\n' \" & $value"
    command = [
        PWSH, "-NoProfile", "-NoLogo", "-File", str(shell / "launch-command.ps1"),
        "--", sys.executable, str(capture), *resume_args, r"-C=C:\two words", literal,
    ]
    source = WRAPPER.with_name("launch-session.ps1").read_text(encoding="utf-8")
    unpack = source[source.index("if ($plan.cmd) {"):source.index("# Apply environment variables")]
    first = source.index("    if (\n        $executionLeg.execution_leg -and")
    last = source.index("# Append copilot passthrough args", first)
    binding = source[first:last].rstrip()
    assert binding.endswith("}")
    binding = binding[:-1]
    mutate = source[last:source.index("# ── psmux pane command", last)]
    first = source.index("        $directStartupManifest = $null")
    last = source.index("        $copilotExit = $LASTEXITCODE", first)
    execute = source[first:last]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({
        "cmd": command, "record_path": str(record), "worktree_id": "fixture",
        "post_exit": True,
    }), encoding="utf-8")
    probe = tmp_path / "binding.ps1"
    probe.write_text(
        "$ErrorActionPreference='Stop'\n"
        "function Write-SetupLog {}\n"
        "function gh { $global:LASTEXITCODE=0; 'fixture-token' }\n"
        + HELPERS.read_text(encoding="utf-8") + "\n"
        "$plan=Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\n"
        "$VenvPython=$args[1]\n"
        + unpack + "\n$cmd=@($plan.cmd)\n"
        "$executionLeg=[pscustomobject]@{execution_leg=[pscustomobject]@{"
        "provider='ahp';state='active';blob=[pscustomobject]@{"
        "auth_account='fixture';endpoint_url='http://127.0.0.1:1234';session_id='authoritative'}}}\n"
        "$ahpArgs=@()\n$CopilotPassthrough=@('--resume=passthrough')\n"
        + binding + "\n" + mutate + "\n" + execute,
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(probe), str(plan), sys.executable],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [
        r"-C=C:\two words", literal, "--experimental",
        "--ahp", "http://127.0.0.1:1234", "--resume=authoritative",
    ]
    assert not list((shell / "pane-args").glob("*.json"))


@pytest.mark.parametrize("recovery", [False, True])
def test_manager_refreshes_the_inner_runtime_before_startup_serialization(tmp_path, recovery):
    shell = _copied_setup(tmp_path)
    record = tmp_path / "fixture.yaml"
    stale = str(tmp_path / "old-runtime" / "python.exe")
    command = [
        PWSH, "-NoProfile", "-NoLogo", "-File", str(shell / "launch-command.ps1"),
        *(["--recovery"] if recovery else []),
        "--", stale, "-m", "agent_worktrees.launch_seed_exec", "--record", str(record),
        "--admission-only", "--", "copilot", "--resume=original",
    ]
    source = WRAPPER.with_name("launch-session.ps1").read_text(encoding="utf-8")
    unpack = source[source.index("if ($plan.cmd) {"):source.index("# Apply environment variables")]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"cmd": command, "record_path": str(record)}), encoding="utf-8")
    probe = tmp_path / "refresh.ps1"
    probe.write_text(
        "$ErrorActionPreference='Stop'\nfunction Write-SetupLog {}\n"
        "$plan=Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\n"
        "$VenvPython=$args[1]\n" + unpack + "\nConvertTo-Json -InputObject @($plan.cmd) -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(probe), str(plan), sys.executable],
        capture_output=True, text=True, encoding="utf-8", timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(result.stdout)
    assert arguments[arguments.index("-m") - 1] == sys.executable
    assert stale not in arguments and arguments[-1] == "--resume=original"
    assert not (shell / "pane-args").exists()


@pytest.mark.parametrize("unsupported", [
    "legacy-reader", "custom-setup", "alternate-options", "not-copied", "empty-command",
    "other-tracking-root",
])
def test_picker_does_not_wrap_unsupported_startup(tmp_path, unsupported):
    shell = _copied_setup(tmp_path)
    record = tmp_path / "fixture.yaml"
    script = shell / "default-setup.ps1"
    command = [PWSH, "-NoProfile", "-NoLogo", "-File", str(script), "payload"]
    if unsupported == "legacy-reader":
        reader = shell / "launch-command.ps1"
        reader.write_text(reader.read_text("utf-8").replace(
            "# agent-worktrees:startup-manifest-v1", "# legacy reader",
        ), encoding="utf-8")
    elif unsupported == "custom-setup":
        script.write_text("# custom setup\n", encoding="utf-8")
    elif unsupported == "alternate-options":
        command.insert(1, "-Mta")
    elif unsupported == "not-copied":
        command[4] = str(SETUP_SCRIPTS / script.name)
    elif unsupported == "empty-command":
        command = []
    else:
        record = tmp_path / "other" / "fixture.yaml"
    assert _produce(tmp_path, command, record=record) == {"command": command, "manifest": None}
    assert not (shell / "pane-args").exists()


def test_picker_concurrent_join_removes_only_unused_attempt_files(tmp_path):
    source = WRAPPER.with_name("launch-session.ps1").read_text("utf-8")
    start = source.index("    if ($joinedDuringCreate) {")
    end = source.index("    if ($newSessionExit -ne 0) {", start)
    files = [tmp_path / name for name in ("outer.json", "receipt.json", "startup.json", "token")]
    for path in files:
        path.write_text("unused", encoding="utf-8")
    retained = tmp_path / "other-attempt.json"
    retained.write_text("keep", encoding="utf-8")
    script = tmp_path / "join.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n$joinedDuringCreate=$true\n"
        "$paneArgsFile=$args[0]\n$paneReceiptFile=$args[1]\n"
        "$startupArgsFile=$args[2]\n$ahpTokenFile=$args[3]\n"
        "function Join-AwExistingPsmuxSession {'joined'}\n"
        + HELPERS.read_text("utf-8") + "\n" + source[start:end],
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), *map(str, files)],
        capture_output=True, text=True, timeout=15, env=_test_env(tmp_path),
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "joined"
    assert all(not path.exists() for path in files)
    assert retained.read_text("utf-8") == "keep"
