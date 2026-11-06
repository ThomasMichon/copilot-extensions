"""Native ownership queries with a controlled census, not desktop admission."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil

import pytest

from agent_procutil.owned_process import run_caller_owned

REPO = Path(__file__).resolve().parents[4]
SOURCE = REPO / "libs/installer-engine/installer_engine_uninstall_windows.py"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native Windows token API")


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("case", ["native", "unavailable", "access-error", "identity-changed"])
def test_native_owner_preserves_fallback_and_identity(shell, case, tmp_path):
    executable = shutil.which(shell)
    assert executable is not None, f"Required native host unavailable: {shell}"
    spec = importlib.util.spec_from_file_location("_native_census_owner", SOURCE)
    assert spec is not None and spec.loader is not None
    census = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(census)
    captured = []

    def run(arguments):
        script = arguments[-1]
        before = (
            "$fallbackCalls=0\n"
            "function Invoke-CimMethod {\n"
            "    $script:fallbackCalls++\n"
            "    return [pscustomobject]@{Sid=$sid;ReturnValue=0}\n"
            "}\n"
        )
        if case == "unavailable":
            before += "function Add-Type { throw 'fixture: compilation unavailable' }\n"
        created = (
            "([Diagnostics.Process]::GetCurrentProcess()).StartTime"
            ".ToUniversalTime().ToFileTimeUtc()"
        )
        fake_rows = (
            f"$created={created}\n"
            "$created=$created-($created%10)\n"
            + ("$created+=10\n" if case == "identity-changed" else "")
            + "$raw=@([pscustomobject]@{\n"
            + ("ProcessId=0;" if case == "access-error" else "ProcessId=$PID;")
            + "ParentProcessId=0;Name='bash.exe';"
            "ExecutablePath='C:\\fixture\\bash.exe';CommandLine='fixture';"
            "CreationDate=[datetime]::FromFileTimeUtc($created)\n"
            "})"
        )
        script = before + script.replace(
            "$raw=@(Get-CimInstance Win32_Process -ErrorAction Stop)", fake_rows,
        ).replace(
            "ConvertTo-Json -InputObject $rows -Compress",
            "@{rows=$rows;fallbackCalls=$fallbackCalls;"
            "nativeAvailable=$nativeOwnerAvailable} | ConvertTo-Json -Depth 5 -Compress",
        )
        result = run_caller_owned(
            [*arguments[:-1], script], timeout=10,
            max_stdout_bytes=65536, max_stderr_bytes=65536,
        )
        captured.append(result)
        if result.returncode:
            raise RuntimeError(result.stderr)
        result = json.loads(result.stdout)
        assert result["fallbackCalls"] == (1 if case in {"unavailable", "access-error"} else 0)
        assert result["nativeAvailable"] is (case != "unavailable")
        return json.dumps(result["rows"])

    if case == "identity-changed":
        with pytest.raises(RuntimeError, match="Process identity changed during inspection"):
            census.inspect_processes(tmp_path, executable, "fixture_module", run)
        assert len(captured) == 1
        assert captured[0].returncode != 0
    else:
        rows = census.inspect_processes(tmp_path, executable, "fixture_module", run)
        assert len(rows) == 1
        assert rows[0]["image"] == r"C:\fixture\bash.exe"
