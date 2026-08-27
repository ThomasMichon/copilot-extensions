"""Windows lifecycle regression tests for incomplete runtime recovery."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALL = PLUGIN / "scripts" / "install.ps1"
PWSH = shutil.which("pwsh")
VERSION = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

pytestmark = pytest.mark.skipif(
    os.name != "nt" or PWSH is None,
    reason="Windows PowerShell lifecycle coverage",
)


def _write_fake_uv(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    driver = tmp_path / "fake_uv.py"
    driver.write_text(
        """
import os
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
mode = os.environ["TEST_UV_MODE"]
if mode == "fail-all":
    raise SystemExit(23)
if args and args[0] == "run":
    script_index = next(i for i, arg in enumerate(args) if arg.endswith(".py"))
    result = subprocess.run(
        [os.environ["TEST_BASE_PYTHON"], *args[script_index:]],
        check=False,
    )
    raise SystemExit(result.returncode)
if args and args[0] == "venv":
    target = Path(args[1])
    result = subprocess.run(
        [
            os.environ["TEST_BASE_PYTHON"],
            "-m",
            "venv",
            "--without-pip",
            "--copies",
            str(target),
        ],
        check=False,
    )
    raise SystemExit(result.returncode)
if args[:2] == ["pip", "install"]:
    if mode == "sleep-pip":
        Path(os.environ["TEST_UV_SIGNAL"]).write_text("started", encoding="ascii")
        import time
        time.sleep(4)
        raise SystemExit(29)
    if mode == "fail-pip":
        raise SystemExit(29)
    python = args[args.index("--python") + 1]
    site = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    package = Path(site) / "agent_codespaces"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import sys\\nraise SystemExit(0)\\n",
        encoding="utf-8",
    )
    raise SystemExit(0)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    (fake_bin / "uv.cmd").write_text(
        '@"%TEST_BASE_PYTHON%" "%TEST_FAKE_UV%" %*\n',
        encoding="ascii",
    )
    return fake_bin


def _environment(
    home: Path,
    fake_bin: Path,
    driver: Path,
    mode: str,
    *,
    uv_only: bool = False,
) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("COPILOT_PLUGIN_INSTALL_STAGED", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    env.pop("PYTHONPATH", None)
    env.update(
        {
            "USERPROFILE": str(home),
            "OS": "Windows_Test",
            "PATH": str(fake_bin) if uv_only else str(fake_bin) + os.pathsep + env["PATH"],
            "TEST_BASE_PYTHON": sys.executable,
            "TEST_FAKE_UV": str(driver),
            "TEST_UV_MODE": mode,
            "TEST_UV_SIGNAL": str(fake_bin.parent / "uv-started"),
        }
    )
    return env


def _run(
    install: Path,
    action: str,
    *,
    home: Path,
    fake_bin: Path,
    mode: str,
    force: bool = False,
    uv_only: bool = False,
) -> subprocess.CompletedProcess[str]:
    args = [PWSH, "-NoProfile", "-File", str(install), action]
    if force:
        args.append("-Force")
    return subprocess.run(
        args,
        cwd=home,
        env=_environment(
            home,
            fake_bin,
            fake_bin.parent / "fake_uv.py",
            mode,
            uv_only=uv_only,
        ),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )


def _slot(home: Path) -> Path:
    return home / ".agent-codespaces" / "versions" / VERSION


def test_uv_only_clean_host_installs_without_precreating_slot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)

    result = _run(
        INSTALL,
        "install",
        home=home,
        fake_bin=fake_bin,
        mode="success",
        uv_only=True,
    )

    assert result.returncode == 0, result.stdout
    assert (_slot(home) / ".install-complete.json").is_file()


def test_uv_only_host_rebuilds_malformed_current_slot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    slot = _slot(home)
    (slot / "Scripts").mkdir(parents=True)
    (slot / "Scripts" / "python.exe").write_text("malformed", encoding="ascii")
    (slot / "partial.txt").write_text("discard", encoding="ascii")
    root = slot.parents[1]
    (root / "current-version").write_text(VERSION, encoding="ascii")

    result = _run(
        INSTALL,
        "install",
        home=home,
        fake_bin=fake_bin,
        mode="success",
        uv_only=True,
    )

    assert result.returncode == 0, result.stdout
    assert not (slot / "partial.txt").exists()
    assert (slot / ".install-complete.json").is_file()


def _seed_healthy_slot(home: Path) -> Path:
    slot = _slot(home)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "venv",
            "--without-pip",
            "--copies",
            str(slot),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    python = slot / "Scripts" / "python.exe"
    site = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    package = Path(site) / "agent_codespaces"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import sys\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": VERSION}),
        encoding="utf-8",
    )
    return slot


def test_install_rebuilds_slot_with_incomplete_metadata(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    slot = _slot(home)
    (slot / "Scripts").mkdir(parents=True)
    (slot / "Scripts" / "python.exe").write_text("not a Python runtime", encoding="ascii")
    (slot / "partial.txt").write_text("corpse", encoding="ascii")
    runtime = home / ".agent-codespaces"
    (runtime / "current-version").write_text(VERSION, encoding="ascii")
    (runtime / "last-known-good").write_text(VERSION, encoding="ascii")

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode="success")

    assert result.returncode == 0, result.stdout
    assert not (slot / "partial.txt").exists()
    assert (slot / "pyvenv.cfg").is_file()
    marker = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    assert marker["version"] == VERSION
    assert (runtime / "current-version").read_text(encoding="utf-8").strip() == VERSION


def test_install_rebuilds_slot_with_malformed_completion_state(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    slot = _seed_healthy_slot(home)
    (slot / ".install-complete.json").write_text("{not-json", encoding="ascii")
    (slot / "stale.txt").write_text("discard me", encoding="ascii")

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode="success")

    assert result.returncode == 0, result.stdout
    assert not (slot / "stale.txt").exists()
    marker = json.loads((slot / ".install-complete.json").read_text(encoding="utf-8"))
    assert marker["version"] == VERSION


@pytest.mark.parametrize(
    ("mode", "seed_slot", "force"),
    [
        ("fail-all", False, False),
        ("fail-pip", True, False),
        ("fail-pip", True, True),
    ],
)
def test_install_stage_failures_return_nonzero(
    tmp_path: Path,
    mode: str,
    seed_slot: bool,
    force: bool,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    if seed_slot:
        _seed_healthy_slot(home)

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode=mode, force=force)

    assert result.returncode != 0, result.stdout
    assert "[FAIL]" in result.stdout


def test_failed_activation_returns_nonzero(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    _seed_healthy_slot(home)
    (home / ".agent-codespaces" / "current-version").mkdir()

    result = _run(INSTALL, "install", home=home, fake_bin=fake_bin, mode="success")

    assert result.returncode != 0, result.stdout
    assert "Runtime activation failed" in result.stdout


def test_first_use_propagates_provision_failure(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    stamped = _run(INSTALL, "stamp", home=home, fake_bin=fake_bin, mode="fail-all")
    assert stamped.returncode == 0, stamped.stdout

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(home / ".local" / "bin" / "agent-codespaces.ps1"),
            "version",
        ],
        cwd=home,
        env=_environment(home, fake_bin, fake_bin.parent / "fake_uv.py", "fail-all"),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode != 0, result.stdout
    assert "provisioning failed" in result.stdout


def test_marketplace_staged_uninstall_removes_runtime_and_binstubs(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "example-marketplace"
        / "agent-codespaces"
    )
    shutil.copytree(PLUGIN, installed)
    stamped = _run(installed / "scripts" / "install.ps1", "stamp", home=home, fake_bin=fake_bin, mode="success")
    assert stamped.returncode == 0, stamped.stdout

    result = _run(
        installed / "scripts" / "install.ps1",
        "uninstall",
        home=home,
        fake_bin=fake_bin,
        mode="success",
    )

    assert result.returncode == 0, result.stdout
    assert not (home / ".agent-codespaces").exists()
    assert not (home / ".local" / "bin" / "agent-codespaces.ps1").exists()
    assert not (home / ".local" / "bin" / "agent-codespaces.cmd").exists()


def test_marketplace_staged_uninstall_waits_without_holding_runtime_cwd(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = _write_fake_uv(tmp_path)
    installed = (
        home
        / ".copilot"
        / "installed-plugins"
        / "example-marketplace"
        / "agent-codespaces"
    )
    shutil.copytree(PLUGIN, installed)
    env = _environment(home, fake_bin, fake_bin.parent / "fake_uv.py", "sleep-pip")
    install_process = subprocess.Popen(
        [PWSH, "-NoProfile", "-File", str(installed / "scripts" / "install.ps1"), "install"],
        cwd=home,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    signal = tmp_path / "uv-started"
    deadline = time.monotonic() + 60
    while not signal.exists() and install_process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert signal.exists(), install_process.communicate(timeout=10)[0]

    uninstall = _run(
        installed / "scripts" / "install.ps1",
        "uninstall",
        home=home,
        fake_bin=fake_bin,
        mode="success",
    )
    install_output = install_process.communicate(timeout=30)[0]

    assert install_process.returncode != 0, install_output
    assert uninstall.returncode == 0, uninstall.stdout
    assert not (home / ".agent-codespaces").exists()
