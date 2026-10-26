"""Run the installer caller through real Windows provenance, without launching a service."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALLER = REPO / "plugins" / "agent-dispatch" / "scripts" / "install.ps1"
pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="native Windows application resolution"
)


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize(
    "resolution",
    ["native", "application-spaces", "missing", "relative", "absent-file", "directory"],
)
def test_logon_application_argv_reaches_canonical_provenance(tmp_path, resolution):
    shell = shutil.which("powershell.exe")
    assert shell is not None, "native Windows PowerShell is required"
    runtime = tmp_path / "runtime with spaces"
    runtime.mkdir()
    witness = runtime / "witness.json"
    launcher = runtime / "fixture launcher.ps1"
    environment = runtime / "profile's environment.env"
    # Execute canonical spawn validation, stopping before reservations and process creation.
    (runtime / "sitecustomize.py").write_text(
        "import json, os\nfrom pathlib import Path\nimport agent_procutil as pu\n"
        "from agent_procutil import daemon\n"
        "class Validated(Exception): pass\n"
        "automatic = daemon._automatic_instance\n"
        "def provenance(*args):\n"
        "    automatic(*args)\n"
        "    raise Validated()\n"
        "daemon._automatic_instance = provenance\noriginal = pu.spawn_daemon\n"
        "def witness(argv, **kwargs):\n"
        "    try: original(argv, **kwargs)\n"
        "    except Validated:\n"
        "        Path(os.environ['LAUNCH_WITNESS']).write_text(json.dumps({\n"
        "            'argv': argv, 'cwd': str(kwargs['cwd']),\n"
        "            'log_path': str(kwargs['log_path']),\n"
        "            'console_children': kwargs['console_children'],\n"
        "            'explicit_instance': 'instance' in kwargs,\n"
        "        }), encoding='utf-8')\n"
        "pu.spawn_daemon = witness\n",
        encoding="utf-8",
    )
    source = INSTALLER.read_text(encoding="utf-8-sig")
    start = source.index("function Start-DispatchDetachedLogonLauncher {")
    function = source[start : source.index("\nfunction ", start + 1)]
    expected = Path(shell)
    if resolution == "application-spaces":
        expected = runtime / "Windows PowerShell" / "powershell.exe"
        expected.parent.mkdir()
        shutil.copyfile(shell, expected)
    override = "function powershell.exe { throw 'fixture command shadow' }\n"
    if resolution != "native":
        candidate = {
            "application-spaces": str(expected),
            "missing": None,
            "relative": "powershell.exe",
            "absent-file": str(runtime / "absent powershell.exe"),
            "directory": str(runtime),
        }[resolution]
        value = "$null" if candidate is None else "@{ Source = " + _quote(candidate) + " }"
        override = (
            "function Get-Command { param($Name, $CommandType, $ErrorAction)\n"
            "if ($Name -ne 'powershell.exe' -or $CommandType -ne 'Application') {\n"
            "throw 'fixture requires the original Windows PowerShell application' }\n"
            f"return {value}\n}}\n"
        )
    harness = runtime / "caller.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version 2.0\n"
        f"$LinkPython={_quote(sys.executable)}\n$InstallDir={_quote(runtime)}\n"
        + override
        + function
        + "\n"
        f"Start-DispatchDetachedLogonLauncher -Launcher {_quote(launcher)} "
        f"-LauncherArguments @('-EnvFile', {_quote(environment)}, '-Name', 'two words')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
        ],
        cwd=runtime,
        env=dict(
            os.environ,
            PYTHONPATH=os.pathsep.join(
                [str(runtime), str(REPO / "libs" / "agent-procutil" / "src")]
            ),
            LAUNCH_WITNESS=str(witness),
        ),
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if resolution not in ("native", "application-spaces"):
        assert result.returncode != 0, result.stdout + result.stderr
        assert "Windows PowerShell application" in result.stderr
        assert not witness.exists(), "unusable application must be refused before spawn"
        return
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(witness.read_text(encoding="utf-8"))
    command = observed.pop("argv")
    assert Path(command[0]).is_absolute()
    assert Path(command[0]).samefile(expected)
    assert Path(command[0]).name.lower() == "powershell.exe"
    assert command[1:] == [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher),
        "-EnvFile",
        str(environment),
        "-Name",
        "two words",
    ]
    assert observed == {
        "cwd": str(runtime),
        "log_path": str(runtime / "supervise-launcher.log"),
        "console_children": True,
        "explicit_instance": False,
    }
