"""Execute the shipped creation boundary without starting a real mux."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "bin" / "launch-session.ps1"
PWSH = shutil.which("pwsh")


def _creation_source():
    source = SOURCE.read_text("utf-8")
    start = source.index("                try {\n", source.index("$savedAuth = $null"))
    end = source.index("\n                    $newSessionExit", start)
    helper = ""
    marker = "function Invoke-AwPsmuxFromHome {"
    if marker in source:
        first = source.index(marker)
        last = source.index("\n}\n", first) + 3
        helper = source[first:last]
    return helper, source[start + len("                try {\n"):end]


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("exit_code", [0, 17])
def test_native_creation_uses_home_and_restores_caller(tmp_path, exit_code):
    home = tmp_path / "home with spaces"
    caller = tmp_path / "caller checkout"
    worktree = tmp_path / "selected pane worktree"
    for directory in (home, caller, worktree):
        directory.mkdir()
    stub = tmp_path / "mux-stub.py"
    captured = tmp_path / "captured.json"
    stub.write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['MUX_CAPTURE']).write_text(json.dumps({"
        "'cwd':os.getcwd(),'argv':sys.argv[1:]}))\n"
        "raise SystemExit(int(os.environ['MUX_EXIT']))\n",
        encoding="utf-8",
    )
    command = tmp_path / "psmux.cmd"
    command.write_text(
        '@echo off\n"' + sys.executable + '" "' + str(stub) + '" %*\n',
        encoding="utf-8",
    )
    helper, creation = _creation_source()
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:AwPsmuxBin=$args[0]\n"
        "$plan=@{work_dir=$args[1]}\n"
        "$sessName='fixture-session'\n"
        "$envFlags=@('-e','FIXTURE=two words')\n"
        "$paneCmd=@('child','two words','--allow-all')\n"
        + helper + "\n" + creation
        + "\n$code=$LASTEXITCODE\n"
        "ConvertTo-Json -Compress @{cwd=(Get-Location).ProviderPath;code=$code}\n",
        encoding="utf-8",
    )
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home),
               MUX_CAPTURE=str(captured), MUX_EXIT=str(exit_code))
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script), str(command), str(worktree)],
        cwd=caller, env=env, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(captured.read_text("utf-8"))
    assert Path(observed["cwd"]) == home
    assert observed["argv"] == [
        "new-session", "-d", "-s", "fixture-session", "-c", str(worktree),
        "-e", "FIXTURE=two words", "child", "two words", "--allow-all",
    ]
    restored = json.loads(result.stdout)
    assert Path(restored["cwd"]) == caller
    assert restored["code"] == exit_code


def test_manager_creation_uses_the_home_scoped_boundary():
    helper, creation = _creation_source()
    assert "Push-Location -LiteralPath $HOME" in helper
    assert "finally" in helper and "Pop-Location" in helper
    assert "Invoke-AwPsmuxFromHome" in creation
    assert "$plan.work_dir" in creation


@pytest.mark.skipif(os.name != "nt" or not PWSH, reason="Native Windows PowerShell")
@pytest.mark.parametrize("failure", ["missing-executable", "missing-home"])
def test_creation_refusal_restores_caller_without_a_fallback(tmp_path, failure):
    home = tmp_path / "home"
    caller = tmp_path / "caller checkout"
    caller.mkdir()
    if failure != "missing-home":
        home.mkdir()
    helper, creation = _creation_source()
    script = tmp_path / "refusal.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "$script:AwPsmuxBin=$args[0]\n"
        "$plan=@{work_dir=$args[1]}\n$sessName='fixture'\n"
        "$envFlags=@()\n$paneCmd=@('child')\n"
        + helper + "\n$refused=$false\ntry {\n" + creation
        + "\n} catch {$refused=$true}\n"
        "ConvertTo-Json -Compress @{cwd=(Get-Location).ProviderPath;"
        "refused=$refused}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script),
         str(tmp_path / "missing-psmux.exe"), str(caller)],
        cwd=caller, capture_output=True, text=True, timeout=20,
        env=dict(os.environ, HOME=str(home), USERPROFILE=str(home)),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {"cwd": str(caller), "refused": True}
