"""Real Windows provider qualification, retained input custody and cleanup."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[3]
NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


@pytest.mark.skipif(os.name != "nt", reason="actual Windows provider qualification")
@pytest.mark.parametrize("case", ["long-cwd", "quoted-wrapper"])
def test_native_uv_policy_qualification_retains_selected_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    source = ROOT / "libs/installer-engine/installer_pip_index.py"
    spec = importlib.util.spec_from_file_location("installer_pip_index", source)
    assert spec and spec.loader
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    policy = reader._dependency_policy()
    selected = shutil.which("uv.exe")
    if selected is None:
        pytest.skip("native uv prerequisite is unavailable")
    uv = policy._configuration.select_uv_executable(selected, error_type=RuntimeError)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("UV_", "PIP_", "PYTHON"))
        and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__")
    }
    home = tmp_path / "home"
    home.mkdir()
    environment.update(
        UV_NO_SYSTEM_CONFIG="1", UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        UV_CACHE_DIR=str(tmp_path / "cache"), HOME=str(home), USERPROFILE=str(home),
        APPDATA=str(home / "appdata"),
    )
    version = subprocess.run(
        [uv, "--version"], env=environment, cwd=Path(tmp_path.anchor),
        capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW,
    )
    match = re.match(r"uv ([0-9]+)\.([0-9]+)\.([0-9]+)", version.stdout)
    assert version.returncode == 0 and match is not None, version.stdout + version.stderr
    if tuple(map(int, match.group(1, 2, 3))) < (0, 11, 16):
        pytest.skip("native qualification requires uv 0.11.16 or newer")
    project = tmp_path / "project"
    if case == "long-cwd":
        while len(str(project)) < 280:
            project /= "deep-" + "x" * 60
    project.mkdir(parents=True)
    original = {
        "pyproject.toml": b'[project]\nname = "native-provider-sample"\nversion = "1.0.0"\n',
        "uv.toml": b'[[index]]\nurl = "https://index.example/simple"\ndefault = true\n',
        "sample.py": b"VALUE = 1\n",
    }
    for name, content in original.items():
        (project / name).write_bytes(content)
    environment.update(UV_NO_CONFIG="yes", UV_CONFIG_FILE=str(project / "uv.toml"))
    expected = Path(uv)
    log = tmp_path / "wrapper.log"
    if case == "quoted-wrapper":
        directory = tmp_path / "provider with spaces"
        directory.mkdir()
        expected = directory / "uv.cmd"
        expected.write_text(
            '@echo off\n>>"%TEST_UV_WRAPPER_LOG%" echo %~1\n'
            '"%TEST_NATIVE_UV%" %*\nexit /b %errorlevel%\n',
            encoding="ascii",
        )
        environment.update(
            PATH=f'"{directory}"' + os.pathsep + environment.get("PATH", ""),
            TEST_NATIVE_UV=uv, TEST_UV_WRAPPER_LOG=str(log),
        )
        uv = "uv.cmd"
    processes: list[subprocess.Popen[str]] = []
    real_popen = subprocess.Popen

    def native_launch(command: list[str], **options: Any) -> subprocess.Popen[str]:
        assert options.get("creationflags") == NO_WINDOW
        process = cast("subprocess.Popen[str]", real_popen(command, **options))
        processes.append(process)
        return process

    copies: tuple[Path, ...] = ()
    try:
        with monkeypatch.context() as native:
            native.setattr(subprocess, "Popen", native_launch)
            with policy.retain_uv_install(
                uv, ["--python", sys.executable, str(project)],
                env=environment, cwd=project, trusted_source_roots=(project,),
            ) as retained:
                assert os.path.samefile(retained.argv[0], expected)
                copies = tuple(
                    Path(policy._native_retained_path(path)) for path in retained.sources.values()
                )
                assert copies and all(path.is_dir() for path in copies)
                assert all(not os.path.samefile(path, project) for path in copies)
        assert len(processes) >= 3
        for process in processes:
            assert isinstance(process.args, list)
            assert os.path.samefile(process.args[0], expected)
            assert process.poll() == 0
            assert process.stdout is not None and process.stdout.closed
            assert process.stderr is not None and process.stderr.closed
        assert all(not path.exists() for path in copies)
        assert all((project / name).read_bytes() == content for name, content in original.items())
        if case == "quoted-wrapper":
            calls = log.read_text(encoding="ascii").splitlines()
            assert "--version" in calls and calls.count("pip") >= 2
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        processes.clear()
