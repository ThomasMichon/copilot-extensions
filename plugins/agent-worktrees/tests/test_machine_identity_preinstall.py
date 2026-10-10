"""Both installer adapters preinstall the portable transport dependency."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("suffix", ["sh", "ps1"])
@pytest.mark.parametrize("layout", ["payload", "canonical"])
def test_machine_transport_preinstall(tmp_path, suffix, layout):
    executable = shutil.which("bash" if suffix == "sh" else "pwsh")
    if executable is None or (suffix == "sh" and os.name == "nt"):
        pytest.skip(f"{suffix} interpreter is unavailable")
    plugin = tmp_path / "plugins" / "agent-worktrees"
    dependency = (
        plugin / "libs" / "machine-transport" if layout == "payload"
        else tmp_path / "libs" / "machine-transport"
    )
    dependency.mkdir(parents=True)
    plugin.mkdir(parents=True, exist_ok=True)
    (dependency / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    source = (PLUGIN / "scripts" / f"install.{suffix}").read_text(encoding="utf-8")
    start = source.index("    # Vendored machine-transport lib")
    end = source.index(
        "    if ! uv pip install" if suffix == "sh" else "    $installRes =",
        source.index("\n\n", start) + 2,
    )
    block = source[start:end]
    if suffix == "sh":
        script = (
            "set -euo pipefail\n"
            "err() { echo ERROR; }\n"
            'uv() { printf "INSTALL:%s\\n" "${@: -2:1}"; }\n'
            f"PLUGIN_DIR={shlex.quote(str(plugin))}\nVENV_PYTHON=fixture-python\n"
            f"probe() {{\n{block}\n}}\nprobe\n"
        )
        command = [executable, "-c", script]
    else:
        script = (
            "function Invoke-VenvPackageInstall { param($VenvPython,$PkgName,$PkgDir) "
            "[Console]::Out.WriteLine('INSTALL:' + $PkgDir); "
            "return @{ ExitCode = 0; Output = '' } }\n"
            f"$PluginDir = '{plugin}'\n$VenvPython = 'fixture-python'\n"
            "$prevEAP = $ErrorActionPreference\n" + block
        )
        command = [executable, "-NoProfile", "-Command", script]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert "ERROR" not in result.stdout
    line, = result.stdout.strip().splitlines()
    assert line.startswith("INSTALL:")
    assert Path(line.removeprefix("INSTALL:")).resolve() == dependency.resolve()
