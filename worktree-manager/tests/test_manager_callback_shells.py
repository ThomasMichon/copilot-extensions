"""Execute source/legacy fallback resolution with an owned uv stand-in."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

BIN = Path(__file__).resolve().parents[1] / "bin"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
def test_source_and_legacy_callbacks_keep_project_entry(tmp_path, legacy, shell):
    payload = tmp_path / "versions" / "1.2.3" if legacy else tmp_path / "source with spaces"
    payload.mkdir(parents=True)
    if shell == "sh":
        bash = Path(shutil.which("git")).parent.parent / "bin" / "bash.exe" if os.name == "nt" else shutil.which("bash")
        code = (
            'source "$1"; uv() { printf "%s\\0" "$@"; }; '
            'aw_manager_callback "$2" register "--project=two words"'
        )
        process = subprocess.run([
            str(bash), "-c", code, "_", (BIN / "manager-runtime.sh").as_posix(), payload.as_posix(),
        ], capture_output=True, timeout=15, **no_window_kwargs())
        assert process.returncode == 0, process.stderr
        arguments = process.stdout.decode().rstrip("\0").split("\0")
        assert arguments[:4] == ["run", "--quiet", "--project", payload.as_posix()]
    else:
        pwsh = shutil.which("pwsh")
        assert pwsh
        (tmp_path / "run").write_text(
            "import json,sys; from pathlib import Path;"
            "Path('arguments.json').write_text(json.dumps(sys.argv[1:]))",
        )
        script = tmp_path / "invoke.ps1"
        script.write_text(
            f". '{BIN / 'manager-runtime.ps1'}'\n"
            "function Get-Command { param($Name,$CommandType,$ErrorAction);"
            f"[pscustomobject]@{{Source='{sys.executable}'}} }}\n"
            f"Invoke-ManagerMuxCallback -PayloadRoot '{payload}' "
            "-CallbackArguments @('register','--project=two words')",
        )
        process = subprocess.run([
            pwsh, "-NoProfile", "-NonInteractive", "-File", str(script),
        ], cwd=tmp_path, capture_output=True, timeout=15, **no_window_kwargs())
        assert process.returncode == 0, process.stderr
        arguments = ["run", *json.loads((tmp_path / "arguments.json").read_text())]
        assert arguments[:4] == ["run", "--quiet", "--project", str(payload)]
    assert arguments[4:] == ["-m", "worktree_manager.mux_callback", "register", "--project=two words"]
