"""Controls for the native application's private interpreter prerequisite."""

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest
from agent_procutil.owned_process import run_caller_owned
from application_python_fixture import managed_python_environment


def test_managed_python_is_prepared_privately_before_downloads_are_disabled(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "managed-python"
    original = {
        "HOME": str(tmp_path),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON": sys.executable,
        "UV_PYTHON_DOWNLOADS": "never",
        "NATIVE_FIXTURE_OWNER": "original-owner",
    }
    deadline = time.monotonic() + 10
    calls = []

    def run(
        arguments: Sequence[str | Path | None],
        *,
        cwd: Path,
        env: dict[str, str],
        deadline: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(arguments), cwd, dict(env), deadline))
        assert directory.is_dir()
        return subprocess.CompletedProcess(arguments, 0)

    prepared = managed_python_environment(
        original,
        directory=directory,
        uv="fixture-uv",
        deadline=deadline,
        run=run,
    )
    version = ".".join(map(str, sys.version_info[:3]))
    assert calls == [
        (
            ["fixture-uv", "python", "install", "--no-bin", "--no-registry", f"cpython@{version}"],
            directory,
            {key: value for key, value in prepared.items() if key != "UV_PYTHON_DOWNLOADS"},
            deadline,
        )
    ]
    assert prepared == {**original, "UV_PYTHON_INSTALL_DIR": str(directory)}
    assert "UV_PYTHON_INSTALL_DIR" not in original
    assert original["UV_PYTHON_DOWNLOADS"] == "never"


@pytest.mark.parametrize("expired", [False, True])
def test_failed_preparation_cannot_return_a_ready_environment(
    tmp_path: Path,
    expired: bool,
) -> None:
    environment = {"UV_PYTHON_DOWNLOADS": "never"}
    calls = []

    def run(
        arguments: Sequence[str | Path | None],
        *,
        cwd: Path,
        env: dict[str, str],
        deadline: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        raise subprocess.CalledProcessError(2, arguments)

    with pytest.raises(TimeoutError if expired else subprocess.CalledProcessError):
        managed_python_environment(
            environment,
            directory=tmp_path / "managed-python",
            uv="fixture-uv",
            deadline=time.monotonic() + (-1 if expired else 10),
            run=run,
        )
    assert bool(calls) is not expired
    assert environment == {"UV_PYTHON_DOWNLOADS": "never"}


def test_generated_application_managed_selector_works_without_downloads(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("a real uv executable is required")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(
        HOME=str(tmp_path),
        USERPROFILE=str(tmp_path),
        UV_CACHE_DIR=str(tmp_path / "cache"),
        UV_PYTHON_DOWNLOADS="never",
    )
    deadline = time.monotonic() + 60

    def run(
        arguments: Sequence[str | Path | None],
        *,
        cwd: Path,
        env: dict[str, str],
        deadline: float,
    ) -> subprocess.CompletedProcess[str]:
        result = run_caller_owned(
            list(map(str, arguments)),
            cwd=cwd,
            env=env,
            timeout=max(0.01, deadline - time.monotonic()),
            max_stdout_bytes=8192,
            max_stderr_bytes=8192,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result

    prepared = managed_python_environment(
        environment,
        directory=tmp_path / "managed-python",
        uv=uv,
        deadline=deadline,
        run=run,
    )
    version = ".".join(map(str, sys.version_info[:3]))
    request = f"cpython@{version}"
    selected = run(
        [
            uv,
            "run",
            "--managed-python",
            "--no-project",
            "--no-python-downloads",
            "--python",
            request,
            "python",
            "-I",
            "-B",
            "-c",
            "import json,sys; print(json.dumps({'executable':sys.executable,"
            "'version':list(sys.version_info[:3])}))",
        ],
        cwd=tmp_path,
        env=prepared,
        deadline=deadline,
    )
    facts = json.loads(selected.stdout)
    assert (
        Path(facts["executable"]).resolve().is_relative_to((tmp_path / "managed-python").resolve())
    )
    assert facts["version"] == list(sys.version_info[:3])
