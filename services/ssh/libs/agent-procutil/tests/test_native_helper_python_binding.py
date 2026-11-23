"""Native helpers consume an explicit caller selection, never ambient Python."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]
PROCUTIL = ROOT / "libs/agent-procutil/src/agent_procutil"
BASH = shutil.which("bash")


def _shell_python(path: Path, marker: Path, code: int) -> None:
    assert BASH is not None
    path.write_text(
        f"#!{BASH}\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(marker))}\n"
        f"printf 'selected-output\\n'\nexit {code}\n",
        encoding="utf-8",
    )
    path.chmod(0o700)


def _shell_environment(area: Path) -> tuple[dict[str, str], Path]:
    directory = area / "ambient"
    directory.mkdir()
    marker = area / "ambient-trace"
    for name in ("python", "python3"):
        _shell_python(directory / name, marker, 97)
    environment = {**os.environ, "PATH": str(directory) + os.pathsep + os.environ.get("PATH", "")}
    environment.pop("COPILOT_EXTENSIONS_DAEMON_PYTHON", None)
    return environment, marker


@pytest.mark.skipif(sys.platform != "linux" or BASH is None, reason="native Linux lifecycle binding")
@pytest.mark.parametrize("binding", ["unset", "empty", "relative", "selected"])
def test_lifecycle_helper_never_selects_path_python(tmp_path: Path, binding: str) -> None:
    assert BASH is not None
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    shutil.copyfile(PROCUTIL / "daemon_instances.sh", helpers / "daemon_instances.sh")
    archive = helpers / "daemon_instance_runtime.pyz"
    archive.write_bytes(b"fixture-owned archive")
    archive.chmod(0o600)
    selected = tmp_path / "selected python"
    trace = tmp_path / "selected-trace"
    _shell_python(selected, trace, 7)
    environment, ambient = _shell_environment(tmp_path)
    if binding != "unset":
        environment["COPILOT_EXTENSIONS_DAEMON_PYTHON"] = {
            "empty": "", "relative": "./selected python", "selected": str(selected),
        }[binding]
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c",
         '. "$1"; _di_lifecycle reconcile-contract', "fixture", str(helpers / "daemon_instances.sh")],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=5,
    )
    assert not ambient.exists()
    if binding == "selected":
        assert result.returncode == 7 and result.stdout == "selected-output\n", result.stderr
        assert trace.read_text().splitlines() == ["-I", "-S", "-B", str(archive), "reconcile-contract"]
    else:
        assert result.returncode != 0 and result.stderr
        assert not trace.exists()


def _policy_arguments(mode: str, selected: Path, area: Path) -> list[str]:
    if mode == "missing-option":
        return ["example-package"]
    if mode == "invalid-option":
        return ["--python", str(area / "missing interpreter"), "example-package"]
    if mode == "relative-option":
        return ["--python", "./" + selected.name, "example-package"]
    if mode == "selected-equals":
        return ["--python=" + str(selected), "example-package"]
    return ["--python", str(selected), "example-package"]


def _materialized_engine(area: Path) -> Path:
    materializer = importlib.import_module("materialize_installer_engine")
    candidate = area / "payload"
    candidate.mkdir()
    files = frozenset({
        "installer-engine.sh", "installer-engine.ps1", "installer_dependency_policy.py",
    })
    changes = materializer.materialize_derived_files(
        area, canonical_root=ROOT,
        copied_consumer=(ROOT / "plugins/budget-guidance", candidate), file_names=files,
    )
    assert len(changes) == len(files) and not any(line.startswith("SKIP ") for line in changes), changes
    return candidate / "scripts"


POLICY_MODES = ["missing-option", "invalid-option", "relative-option", "selected", "selected-equals"]


@pytest.mark.skipif(sys.platform != "linux" or BASH is None, reason="native Bash policy binding")
@pytest.mark.parametrize("mode", POLICY_MODES)
def test_bash_policy_uses_only_the_explicit_interpreter(tmp_path: Path, mode: str) -> None:
    assert BASH is not None
    selected = tmp_path / "selected python"
    trace = tmp_path / "selected-trace"
    _shell_python(selected, trace, 7)
    environment, ambient = _shell_environment(tmp_path)
    arguments = _policy_arguments(mode, selected, tmp_path)
    engine = _materialized_engine(tmp_path)
    result = subprocess.run(
        [BASH, "--noprofile", "--norc", "-c",
         '. "$1"; shift; invoke_uv_pip_install_resilient fixture-uv "$@"',
         "fixture", str(engine / "installer-engine.sh"), *arguments],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=5,
    )
    assert not ambient.exists()
    if mode.startswith("selected"):
        assert result.returncode == 7 and result.stdout == "selected-output\n", result.stderr
        assert trace.read_text().splitlines() == [
            "-I", str(engine / "installer_dependency_policy.py"), "--uv", "fixture-uv", "--",
            *arguments,
        ]
    else:
        assert result.returncode != 0 and result.stderr
        assert not trace.exists()


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.parametrize("host", ["pwsh", "powershell.exe"])
@pytest.mark.parametrize("mode", POLICY_MODES)
def test_powershell_policy_uses_only_the_explicit_interpreter(
    tmp_path: Path, host: str, mode: str,
) -> None:
    if host == "powershell.exe" and os.name != "nt":
        pytest.skip("Windows PowerShell requires a native Windows runner")
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    engine = _materialized_engine(tmp_path)
    selected = Path(sys.executable)
    trace = tmp_path / "selected-trace"
    policy = tmp_path / "selected policy.py"
    policy.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        f"Path({str(trace)!r}).write_text(json.dumps({{'isolated':sys.flags.isolated,"
        "'argv':sys.argv[1:]}),encoding='utf-8')\n"
        "print('selected-output')\nsys.exit(7)\n",
        encoding="utf-8",
    )
    if mode == "relative-option":
        selected = tmp_path / "relative python.ps1"
        selected.write_text(
            f"[IO.File]::WriteAllText({_quote(trace)}, 'relative')\nexit 7\n", encoding="utf-8",
        )
    ambient = tmp_path / "ambient python.ps1"
    ambient_trace = tmp_path / "ambient-trace"
    discovery = tmp_path / "discovery-trace"
    ambient.write_text(
        f"[IO.File]::WriteAllText({_quote(ambient_trace)}, 'ambient')\nexit 97\n",
        encoding="utf-8",
    )
    arguments = _policy_arguments(mode, selected, tmp_path)
    harness = tmp_path / "policy.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_quote(engine / 'installer-engine.ps1')}\n"
        f"$script:InstallerDependencyPolicy={_quote(policy)}\n"
        "function Get-Command {\n"
        "  param($Name, $CommandType, $ErrorAction)\n"
        f"  [IO.File]::WriteAllText({_quote(discovery)}, 'discovery')\n"
        f"  [pscustomobject]@{{ Source={_quote(ambient)} }}\n"
        "}\n"
        f"$arguments=@({','.join(_quote(value) for value in arguments)})\n"
        "$result=Invoke-UvPipInstallResilient -Arguments $arguments -UvCommand fixture-uv\n"
        "$result | ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not discovery.exists()
    assert not ambient_trace.exists()
    response = json.loads(result.stdout)
    if mode.startswith("selected"):
        assert response == {"ExitCode": 7, "Output": "selected-output"}
        assert json.loads(trace.read_text(encoding="utf-8")) == {
            "isolated": 1, "argv": ["--uv", "fixture-uv", "--", *arguments],
        }
    else:
        assert response["ExitCode"] == 1
        assert not trace.exists()
