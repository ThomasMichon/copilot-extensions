"""Source/order guard through installer adapters, not dependency-age admission proof."""

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
    remote = dependency.parent / "remote-login-shell"
    remote.mkdir()
    (remote / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    source = (PLUGIN / "scripts" / f"install.{suffix}").read_text(encoding="utf-8")
    start = source.index("    # Vendored remote-login-shell lib")
    end = source.index(
        "    # Vendored update-telemetry lib" if suffix == "sh" else "    $installRes =",
        source.index("    # Vendored machine-transport lib", start),
    )
    block = source[start:end]
    if suffix == "sh":
        scripts = plugin / "scripts"
        scripts.mkdir()
        for helper in ("installer_dependency_policy.py", "installer_dependency_config.py"):
            shutil.copyfile(PLUGIN / "scripts" / helper, scripts / helper)
        script = (
            "set -euo pipefail\n"
            "err() { echo ERROR; }\n"
            'uv() { echo "policy bypass" >&2; return 1; }\n'
            'fixture_python() {\n'
            '  [[ "$#" == 11 && "$1" == -I &&\n'
            '     "$2" == "$SCRIPT_DIR/installer_dependency_policy.py" && -f "$2" &&\n'
            '     "$3" == --uv && "$4" == uv && "$5" == -- &&\n'
            '     "$6" == --python && "$7" == "$VENV_PYTHON" &&\n'
            '     "$8" == --reinstall-package && "${11}" == --quiet ]] || return 1\n'
            '  [[ "${9}" == agent-remote-login-shell || "${9}" == agent-machine-transport ]] || return 1\n'
            '  printf "INSTALL:%s\\n" "${10}"\n'
            '}\n'
            f"PLUGIN_DIR={shlex.quote(str(plugin))}\nVENV_PYTHON=fixture_python\n"
            f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
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
    lines = result.stdout.strip().splitlines()
    assert all(line.startswith("INSTALL:") for line in lines)
    assert [Path(line.removeprefix("INSTALL:")).resolve() for line in lines] == [
        remote.resolve(), dependency.resolve(),
    ]
