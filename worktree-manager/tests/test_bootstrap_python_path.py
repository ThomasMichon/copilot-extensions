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
SOURCE = Path(__file__).resolve().parents[1] / "src"


def _literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["powershell", "pwsh"])
def test_update_python_path_prefers_the_physical_base_and_preserves_other_entries(tmp_path, host):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    unrelated = str(tmp_path / "other tools")
    preferred = str(Path(sys._base_executable).resolve().parent)
    inherited = f"{unrelated};;{preferred.upper()}\\;{preferred};{unrelated}\\secondary"
    helper = re.search(
        r"^function Add-BootstrapPythonPath\b.*?^}",
        (SOURCE.parent / "bootstrap.ps1").read_text(encoding="utf-8"),
        re.M | re.S,
    )
    assert helper is not None
    program = (
        "import json,os,sys;"
        f"sys.path.insert(0,{str(SOURCE)!r});"
        "from worktree_manager.update_python_path import finish_update;"
        "assert finish_update(['update','--wait'],0)==0;"
        "print(json.dumps({'python':sys._base_executable,'path':os.environ['PATH']}))"
    )
    script = (
        "$ErrorActionPreference='Stop';\n"
        + helper[0] + "\n"
        f"if ((Add-BootstrapPythonPath '' {_literal(preferred)}) -cne {_literal(preferred)}) "
        "{ throw 'An initially empty PATH gained an empty component' };\n"
        f"$env:PATH=Add-BootstrapPythonPath {_literal(inherited)} {_literal(preferred)};\n"
        f"$details=(& {_literal(sys.executable)} -I -S -B -c {_literal(program)}) | ConvertFrom-Json;\n"
        "if ($LASTEXITCODE -ne 0) { throw 'Python completion failed' };\n"
        "$python=$details.python;$env:PATH=$details.path;\n"
        "@{python=$python;path=$env:PATH;selected=(Get-Command python -CommandType Application | Select-Object -First 1).Source;"
        "}"
        " | ConvertTo-Json -Compress\n"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        cwd=tmp_path, env={**os.environ, "PATH": "", "COPILOT_EXTENSIONS_TEST_CONTAINED": "1"},
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    details = json.loads(result.stdout)
    assert Path(details["python"]).samefile(sys._base_executable)
    assert Path(details["selected"]).samefile(sys._base_executable)
    assert details["path"].split(";") == [
        preferred, unrelated, "", preferred.upper() + "\\", unrelated + "\\secondary",
    ]
