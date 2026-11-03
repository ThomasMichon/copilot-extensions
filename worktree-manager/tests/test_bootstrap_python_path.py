"""Windows update PATH uses the physical prerequisite, not its private venv."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit Windows bootstrap PATH qualification",
)
BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.ps1"


def _literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
def test_update_python_path_prefers_the_physical_base_and_preserves_other_entries(tmp_path, host):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    source = BOOTSTRAP.read_text(encoding="utf-8")
    functions = []
    for name in (
        "Resolve-BootstrapExecutable", "Add-BootstrapPythonPath",
        "Get-BootstrapBasePython", "Test-BootstrapPythonPathPublication",
    ):
        match = re.search(rf"^function {name}\b.*?^}}", source, re.M | re.S)
        assert match is not None
        functions.append(match[0])
    unrelated = str(tmp_path / "other tools")
    preferred = str(Path(sys._base_executable).resolve().parent)
    inherited = f"{unrelated};{preferred.upper()}\\;{unrelated}\\secondary"
    script = (
        "$ErrorActionPreference='Stop';\n" + "\n".join(functions) + "\n"
        f"$python=Get-BootstrapBasePython {_literal(sys.executable)};\n"
        f"$env:PATH=Add-BootstrapPythonPath {_literal(inherited)} ([IO.Path]::GetDirectoryName($python));\n"
        "$modes=@{};\n"
        "foreach ($mode in @('--auto','--detach','--auth-report','--request-status=example',"
        "'--recovery-status','--recover-after-restart','--recover-for-repair','--target=example','--help')) {\n"
        "  $modes[$mode]=Test-BootstrapPythonPathPublication @('update',$mode)\n"
        "};\n"
        "@{python=$python;path=$env:PATH;selected=(Get-Command python -CommandType Application | Select-Object -First 1).Source;"
        "modes=$modes;ordinary=(Test-BootstrapPythonPathPublication @('update'));"
        "waited=(Test-BootstrapPythonPathPublication @('update','--wait'))}"
        " | ConvertTo-Json -Compress\n"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=tmp_path, env={**os.environ, "PATH": ""},
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert Path(details["python"]).samefile(sys._base_executable)
    assert Path(details["selected"]).samefile(sys._base_executable)
    assert details["path"].split(";") == [preferred, unrelated, unrelated + "\\secondary"]
    assert details["ordinary"] is details["waited"] is True
    assert set(details["modes"].values()) == {False}
