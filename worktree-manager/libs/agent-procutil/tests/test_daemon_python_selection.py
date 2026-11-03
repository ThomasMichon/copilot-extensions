"""A confirmed pre-offer version refusal permits a later compatible PATH Python."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent_procutil import no_window_kwargs

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit native PowerShell Python-selection qualification",
)


def _literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
@pytest.mark.parametrize("compatible", [False, True])
def test_python_selection_skips_unsupported_and_alias_candidates(tmp_path, host, compatible):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    if Path(sys.executable) == Path(sys._base_executable):
        pytest.skip("qualification needs the runner's venv and physical base")
    package = Path(__file__).resolve().parents[1] / "src" / "agent_procutil"
    helper = tmp_path / "daemon_instances.ps1"
    source = (package / helper.name).read_text(encoding="utf-8")
    base = base64.b64encode(str(sys._base_executable).encode("utf-8")).decode("ascii")
    anchor = "import base64,json,sys\n"
    assert source.count(anchor) == 1
    source = source.replace(
        anchor, anchor + "import os\n"
        + f"if os.path.samefile(sys.executable,base64.b64decode('{base}').decode('utf-8')):"
        " sys.version_info=(3,9)\n",
    )
    helper.write_text(source, encoding="utf-8")
    alias = tmp_path / "WindowsApps" / "python.exe"
    candidates = [Path(sys._base_executable), alias]
    if compatible:
        candidates.append(Path(sys.executable))
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {_literal(helper)}; "
        f"$script:DaemonInstanceRuntime={_literal(package / 'daemon_instance_runtime.pyz')}; "
        "$state=@{process=$null;worker=$null;job=$null;input=$null;output=$null;error=$null;"
        "diagnostics=$null;nonce=$null;sequence=0;broken=$true;offerPermitted=$false}; "
        "$selected=$null; $failure=$null; $cleanupError=$null; $ended=$false; "
        "try { Start-DaemonInstanceWorker $state; $selected=$state.process.StartInfo.FileName } "
        "catch { $failure=$_.Exception.Message; $ended=$state.process.HasExited } "
        "finally { try { Close-DaemonInstanceWorker $state } "
        "catch { $cleanupError=$_.Exception.Message } }; "
        "@{selected=$selected;error=$failure;cleanupError=$cleanupError;ended=$ended;"
        "jobAdopted=($null -ne $state.job);"
        "stateCreated=($null -ne [AppDomain]::CurrentDomain.GetData('copilot-daemon-instance-shell-v1'))}"
        " | ConvertTo-Json -Compress"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=tmp_path,
        env={**os.environ, "PATH": os.pathsep.join(str(candidate.parent) for candidate in candidates)},
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert details["stateCreated"] is False
    if compatible:
        assert Path(details["selected"]).samefile(sys.executable)
        assert details["error"] is None
        assert details["cleanupError"] is None
        assert details["jobAdopted"] is True
    else:
        assert details["selected"] is None
        assert "Python 3.10+ must be available as python on PATH" in details["error"]
        assert details["ended"] is True
        assert details["jobAdopted"] is False
