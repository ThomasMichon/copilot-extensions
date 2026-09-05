from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
VERSION = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

pytestmark = pytest.mark.guard


def test_installers_recommend_supported_version_flag():
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "Try: budget-guidance --version" in posix
    assert "Try: budget-guidance --version" in powershell
    assert "Try: budget-guidance version" not in posix
    assert "Try: budget-guidance version" not in powershell


def _fake_uv_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    driver = tmp_path / "fake_uv.py"
    driver.write_text(
        """
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys

args = sys.argv[1:]
base = os.environ["TEST_BASE_PYTHON"]
if args and args[0] == "venv":
    target = Path(args[1])
    log = os.environ.get("TEST_UV_VENV_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as stream:
            stream.write(str(target) + "\\n")
    raise SystemExit(subprocess.run(
        [base, "-m", "venv", "--without-pip", str(target)],
        check=False,
    ).returncode)
if args[:2] == ["pip", "install"]:
    python = args[args.index("--python") + 1]
    plugin = Path(args[-2] if args[-1] == "--quiet" else args[-1])
    site_dir = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    shutil.copytree(
        plugin / "src" / "budget_guidance",
        Path(site_dir) / "budget_guidance",
        dirs_exist_ok=True,
    )
    raise SystemExit(0)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    uv = fake_bin / "uv"
    uv.write_text(
        f"#!/bin/sh\nexec '{sys.executable}' '{driver}' \"$@\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    for name in ("python", "python3", "py"):
        command = fake_bin / name
        command.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
        command.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "TEST_BASE_PYTHON": sys.executable,
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "COMPUTERNAME": "TEST-HOST",
    }
    env.pop("COPILOT_PLUGIN_INSTALL_STAGED", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    Path(env["HOME"]).mkdir()
    return fake_bin, env


def test_posix_installer_is_uv_first_when_ambient_python_is_absent():
    source = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    provisioning = source[source.index("echo '=== budget-guidance install ==='") :]

    assert provisioning.index("if _ensure_uv; then HAVE_UV=1; fi") < provisioning.index(
        'PYTHON_CMD=""'
    )
    assert 'uv venv "$VENV_DIR" --python 3.10 --allow-existing' in provisioning
    assert '[[ "$HAVE_UV" -eq 0 && -z "$PYTHON_CMD" ]]' in provisioning


def test_powershell_installer_is_uv_first_when_ambient_python_is_absent():
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    provisioning = source[source.index("Write-Host '=== budget-guidance install ==='") :]

    assert provisioning.index("$uvCommand = Get-Command uv") < provisioning.index(
        "$pythonCmd = $null"
    )
    assert "venv $VenvDir --python 3.10 --allow-existing" in provisioning
    assert "if (-not $uvCommand -and -not $pythonCmd)" in provisioning


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh self-stage coverage")
def test_powershell_self_stage_preserves_spaced_paths_and_force(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    profile = tmp_path / "profile with spaces"
    profile.mkdir()
    env["HOME"] = str(profile)
    env["USERPROFILE"] = str(profile)
    installed = (
        profile
        / ".copilot"
        / "installed-plugins"
        / "market with spaces"
        / "budget-guidance"
    )
    shutil.copytree(PLUGIN, installed)
    custom_root = tmp_path / "custom runtime with spaces"
    venv_log = tmp_path / "powershell uv venv.log"
    env["TEST_UV_VENV_LOG"] = str(venv_log)

    stamp = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout
    snapshot = Path((custom_root / "payload-dir").read_text(encoding="utf-8"))
    assert snapshot == custom_root / "snapshots" / VERSION
    assert (snapshot / "scripts" / "install.ps1").is_file()

    install = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert install.returncode == 0, install.stdout
    assert venv_log.read_text(encoding="utf-8").splitlines() == [
        str(custom_root / "versions" / VERSION)
    ]

    capture = tmp_path / "forwarded arguments.json"
    forced = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(custom_root),
            "-Force",
        ],
        cwd=tmp_path,
        env={
            **env,
            "BUDGET_GUIDANCE_TEST_ARGUMENT_CAPTURE": str(capture),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert forced.returncode == 0, forced.stdout
    assert json.loads(capture.read_text(encoding="utf-8-sig")) == {
        "action": "install",
        "installDir": str(custom_root),
        "force": True,
        "staged": True,
    }


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
def test_posix_installer_bootstraps_with_uv_and_no_ambient_python(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"

    result = subprocess.run(
        [BASH, str(PLUGIN / "scripts" / "install.sh"), "install", "--install-dir", str(install_dir)],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert (install_dir / "current-version").is_file()


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
def test_posix_stamp_uses_owned_versioned_snapshot_without_marketplace_fallback(
    tmp_path: Path,
):
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"

    result = subprocess.run(
        [
            BASH,
            str(PLUGIN / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    snapshot = Path((install_dir / "payload-dir").read_text(encoding="utf-8").strip())
    assert snapshot == install_dir / "snapshots" / VERSION
    assert snapshot != PLUGIN
    assert (snapshot / "scripts" / "install.sh").is_file()
    assert (snapshot / ".payload-source").read_text(encoding="utf-8").strip() == str(
        PLUGIN
    )

    wrapper = Path(env["HOME"]) / ".local" / "bin" / "budget-guidance"
    wrapper_source = wrapper.read_text(encoding="utf-8")
    assert "installed-plugins/*" not in wrapper_source
    (snapshot / "scripts" / "install.sh").unlink()
    unavailable = subprocess.run(
        [BASH, str(wrapper), "status"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert unavailable.returncode == 127
    assert "owning snapshot installer unavailable" in unavailable.stdout


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
def test_posix_self_stage_forwards_custom_root_and_force(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    installed = (
        Path(env["HOME"])
        / ".copilot"
        / "installed-plugins"
        / "example"
        / "budget-guidance"
    )
    shutil.copytree(PLUGIN, installed)
    custom_root = tmp_path / "custom-runtime"
    venv_log = tmp_path / "uv-venv.log"
    env["TEST_UV_VENV_LOG"] = str(venv_log)

    stamp = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout
    assert (custom_root / "payload-dir").is_file()
    default_root = Path(env["HOME"]) / ".budget-guidance"
    assert not (default_root / "payload-dir").exists()
    assert not (default_root / "current-version").exists()

    install = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "install",
            "--install-dir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert install.returncode == 0, install.stdout
    initial_calls = venv_log.read_text(encoding="utf-8").splitlines()
    assert len(initial_calls) == 1
    assert all(str(custom_root) in call for call in initial_calls)

    forced = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "install",
            "--install-dir",
            str(custom_root),
            "--force",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert forced.returncode == 0, forced.stdout
    forced_calls = venv_log.read_text(encoding="utf-8").splitlines()
    assert len(forced_calls) == 2
    assert all(str(custom_root) in call for call in forced_calls)


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh installer coverage")
def test_powershell_installer_bootstraps_with_uv_and_no_ambient_python(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(install_dir),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert (install_dir / "current-version").is_file()


def _compatibility_powershell_wrapper() -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start_marker = "$ps1Content = @'\n"
    end_marker = "\n'@\n    [System.IO.File]::WriteAllText($ps1Path"
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_windows_compatibility_wrapper_uses_runtime_lock_and_rechecks():
    wrapper = _compatibility_powershell_wrapper()

    lock = wrapper.index("$_lockPath = Join-Path $_root '.provision.lock'")
    acquired = wrapper.index("[IO.File]::Open(", lock)
    recheck = wrapper.index("$_py = _Resolve-Py", acquired)
    provision = wrapper.index("-File $_inst provision", recheck)

    assert lock < acquired < recheck < provision
    assert "[IO.FileShare]::None" in wrapper
    assert "installed-plugins" not in wrapper


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh concurrency coverage")
def test_windows_compatibility_wrapper_serializes_first_use(tmp_path: Path):
    home = tmp_path / "home"
    root = home / ".budget-guidance"
    snapshot = root / "snapshots" / "test"
    (root / "bin").mkdir(parents=True)
    (snapshot / "scripts").mkdir(parents=True)
    (root / "payload-dir").write_text(str(snapshot), encoding="utf-8")

    marker = root / "runtime-ready"
    count = root / "provision-count"
    runtime = root / "runtime"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    (root / "bin" / "resolve-runtime.ps1").write_text(
        "\n".join(
            [
                "$AgentRtPy = $null",
                f"if (Test-Path -LiteralPath '{marker}') {{",
                f"    $AgentRtPy = '{runtime}'",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    (snapshot / "scripts" / "install.ps1").write_text(
        "\n".join(
            [
                "param([string]$Action)",
                "Start-Sleep -Milliseconds 500",
                f"Add-Content -LiteralPath '{count}' -Value provision",
                f"New-Item -ItemType File -Path '{marker}' -Force | Out-Null",
            ]
        ),
        encoding="utf-8",
    )
    wrapper = tmp_path / "budget-guidance.ps1"
    wrapper.write_text(_compatibility_powershell_wrapper(), encoding="utf-8")
    env = {
        **os.environ,
        "USERPROFILE": str(home),
        "HOME": str(home),
    }

    processes = [
        subprocess.Popen(
            [PWSH, "-NoProfile", "-File", str(wrapper), "status"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], outputs
    assert count.read_text(encoding="utf-8").splitlines() == ["provision"]
