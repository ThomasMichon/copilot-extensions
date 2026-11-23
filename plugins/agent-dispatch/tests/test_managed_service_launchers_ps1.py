"""Actual slot-local service ingress selects its prepared environment before Python."""
from __future__ import annotations

import json
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import sys
import zipfile
from collections.abc import Callable

import pytest

from .service_launcher_fixture import PLUGIN, REPOSITORY, stage_service_launchers
from .test_managed_bootstrap_reader import prepared_managed_helper as prepared_managed_helper

HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_managed_service_packaged_closure_survives_real_wheel(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate"
    shutil.copytree(PLUGIN / "src", candidate / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("pyproject.toml", "README.md", "readiness-isolation.json"):
        shutil.copy2(PLUGIN / name, candidate / name)
    sys.path.insert(0, str(REPOSITORY / "tools"))
    materializer = importlib.import_module("materialize_launch_wrapper_assets")
    report = materializer.materialize_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=candidate,
        canonical_root=REPOSITORY, dest_root=candidate,
    )
    assert report and not any(line.startswith("SKIP") for line in report), report
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    built = subprocess.run(
        [sys.executable, "-I", "-B", "-c",
         "import sys; from setuptools import build_meta; build_meta.build_wheel(sys.argv[1])",
         str(wheels)],
        capture_output=True, text=True, timeout=30, creationflags=HEADLESS,
        cwd=candidate, env={**os.environ, "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"},
    )
    assert built.returncode == 0, built.stdout + built.stderr
    archives = list(wheels.glob("*.whl"))
    assert len(archives) == 1
    with zipfile.ZipFile(archives[0]) as wheel:
        for name, source in (
            ("bootstrap-uv.ps1", REPOSITORY / "libs/installer-engine/bootstrap-uv.ps1"),
            ("bootstrap-capture.ps1", REPOSITORY / "libs/installer-engine/bootstrap-capture.ps1"),
            ("bootstrap-capture.exe", REPOSITORY / "libs/installer-engine/bootstrap-capture.exe"),
            ("runtime.ps1", PLUGIN / "src/agent_dispatch/service_launchers/runtime.ps1"),
        ):
            assert wheel.read(f"agent_dispatch/service_launchers/{name}") == source.read_bytes()


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("role", ["coordinator", "supervisor"])
@pytest.mark.parametrize("scenario", [
    "run", "policy-denied", "missing-uv", "missing-provider", "missing-capture-script", "missing-capture-host",
])
@pytest.mark.skipif(os.name != "nt", reason="native Windows service capture and worker identity")
def test_managed_service_ingress_preserves_selected_runtime_and_policy(
    tmp_path: Path, shell: str, role: str, scenario: str,
    prepared_managed_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    host = shutil.which(shell)
    if not host:
        pytest.skip(f"{shell} is unavailable")
    uv, prepared, managed_root = prepared_managed_helper
    root = tmp_path / "application's \u03bb directory"
    slot = root / "versions" / "fixture"
    environment = {
        **os.environ, "UV_OFFLINE": "1", "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never",
        "UV_PYTHON_INSTALL_DIR": str(managed_root), "UV_CACHE_DIR": str(tmp_path / "cache"),
    }
    environment.pop("UV_CONFIG_FILE", None)
    built = subprocess.run(
        [str(uv), "venv", "--no-project", "--no-python-downloads", "--python", str(prepared), str(slot)],
        capture_output=True, text=True, timeout=20, creationflags=HEADLESS, env=environment,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = slot / ("Lib/site-packages" if os.name == "nt" else "lib/python3.11/site-packages")
    package = site / "agent_dispatch"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "installation_policy.py").write_text(
        "import os\n"
        "def windows_policy(*args, **kwargs):\n"
        " return {key:os.environ['SERVICE_SCENARIO']!='policy-denied' "
        "for key in ('start_coordinator','start_supervisor')}\n",
        encoding="utf-8",
    )
    recorder = (
        "import json,os,sys\nfrom pathlib import Path\n"
        "def record(extra):\n"
        " Path(os.environ['SERVICE_EVIDENCE']).write_text(json.dumps({\n"
        "  'executable':sys.executable,'prefix':sys.prefix,'argv':sys.argv,\n"
        "  'root':os.environ.get('AGENT_DISPATCH_INSTALL_DIR'),\n"
        "  'marker':sys._xoptions.get('agent_dispatch_install_dir'),\n"
        "  'custom':os.environ.get('SERVICE_CUSTOM'),**extra}),encoding='utf-8')\n"
    )
    (package / "fixture_record.py").write_text(recorder, encoding="utf-8")
    (package / "__main__.py").write_text(
        "from .fixture_record import record\nrecord({'kind':'coordinator'})\n"
        "import sys\nprint('fixture stdout')\nprint('fixture stderr',file=sys.stderr)\n",
        encoding="utf-8",
    )
    (package / "supervisor_lane_status.py").write_text(
        "from .fixture_record import record\n"
        "def ensure(root,profile,arguments,*,generation):\n"
        " record({'kind':'supervisor','profile':str(profile),"
        "'arguments':arguments,'generation':generation})\n",
        encoding="utf-8",
    )
    shutil.copytree(
        REPOSITORY / "libs/agent-procutil/src/agent_procutil", site / "agent_procutil",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    launchers = package / "service_launchers"
    stage_service_launchers(launchers)
    if scenario in {"missing-provider", "missing-capture-script", "missing-capture-host"}:
        provider = launchers / {
            "missing-provider": "bootstrap-uv.ps1",
            "missing-capture-script": "bootstrap-capture.ps1",
            "missing-capture-host": "bootstrap-capture.exe",
        }[scenario]
        provider.rename(provider.with_suffix(".unavailable"))
    profile = root / "supervisor.env"
    profile.write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS=one,two\nAGENT_DISPATCH_SUPERVISE_INTERVAL=41\n",
        encoding="utf-8",
    )
    (root / "service.env").write_text("SERVICE_CUSTOM=preserved environment\n", encoding="utf-8")
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        poison = commands / (name + (".cmd" if os.name == "nt" else ""))
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo invoked\nexit /b 91\n'
            if os.name == "nt" else '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        poison.chmod(0o700)
    if scenario != "missing-uv":
        shutil.copy2(uv, commands / uv.name)
    evidence = root / "execution.json"
    environment.update(
        PATH=str(commands), AMBIENT_MARKER=str(ambient), SERVICE_SCENARIO=scenario,
        SERVICE_EVIDENCE=str(evidence), UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        UV_PYTHON="foreign", VIRTUAL_ENV="foreign", UV_WORKING_DIR="missing-directory",
    )
    command = [
        host, "-NoProfile", "-NonInteractive", "-File", str(launchers / f"{role}.ps1"),
        "-InstallDir", str(root), "-RuntimePython", str(python), "-RuntimeSlot", str(slot),
    ]
    if role == "supervisor":
        command += ["-EnvFile", str(profile)]
    samples = []
    for _cycle in range(2 if scenario == "run" else 1):
        started = time.monotonic()
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", timeout=30,
            creationflags=HEADLESS, env=environment, cwd=tmp_path,
        )
        samples.append(time.monotonic() - started)
        assert not ambient.exists(), result.stdout + result.stderr
        if scenario != "run":
            assert result.returncode == (3 if scenario == "policy-denied" else 1), result.stdout + result.stderr
            assert not evidence.exists()
            message = {
                "missing-uv": "requires prepared UV prerequisites",
                "missing-provider": "bootstrap-uv.ps1",
                "missing-capture-script": "bootstrap-capture.ps1",
                "missing-capture-host": "bootstrap-capture.exe",
            }.get(scenario)
            if message:
                assert message in result.stderr
            continue
        assert result.returncode == 0, result.stdout + result.stderr
        observed = json.loads(evidence.read_text(encoding="utf-8"))
        assert Path(observed["prefix"]).samefile(slot)
        assert Path(observed["root"]).samefile(root)
        assert observed["kind"] == role
        if role == "coordinator":
            assert observed["custom"] == "preserved environment"
            assert observed["marker"] == str(root)
            log = (root / "serve-service.log").read_text(encoding="utf-8-sig")
            assert "fixture stdout" in log and "fixture stderr" in log
        else:
            assert Path(observed["executable"]).samefile(python)
            assert Path(observed["profile"]).samefile(profile)
            assert observed["arguments"] == [
                "supervise", "--all-repos", "--interval", "41", "--max-concurrent", "1",
                "--max-attempts", "3", "--label", "one", "--label", "two",
            ]
            assert len(observed["generation"]) == 64
    assert profile.read_text(encoding="utf-8").startswith("AGENT_DISPATCH_SUPERVISE_LABELS=one,two\n")
    if scenario == "run":
        record_property("dispatch_managed_service_performance", json.dumps({
            "shell": shell, "role": role, "samples_seconds": samples, "network": "disabled",
        }))


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_managed_supervisor_description_does_not_bypass_uv(
    tmp_path: Path, shell: str,
) -> None:
    host = shutil.which(shell)
    if not host:
        pytest.skip(f"{shell} is unavailable")
    launchers = tmp_path / "package/service_launchers"
    stage_service_launchers(launchers)
    python = tmp_path / "unexecuted-python"
    python.touch()
    profile = tmp_path / "supervisor.env"
    profile.write_text("AGENT_DISPATCH_SUPERVISE_LABELS=one\n", encoding="utf-8")
    commands = tmp_path / "empty-path"
    commands.mkdir()
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(launchers / "supervisor.ps1"),
         "-InstallDir", str(tmp_path), "-RuntimePython", str(python), "-RuntimeSlot", str(tmp_path),
         "-EnvFile", str(profile), "-DescribeLaunch"],
        capture_output=True, text=True, encoding="utf-8", timeout=15, creationflags=HEADLESS,
        env={**os.environ, "PATH": str(commands)},
    )
    assert result.returncode != 0
    assert "requires prepared UV prerequisites" in result.stderr
    assert not result.stdout
