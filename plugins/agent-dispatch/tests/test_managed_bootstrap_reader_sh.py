"""POSIX installer helpers inspect prepared managed Python, never an incumbent."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture
from .test_managed_bootstrap_reader import (
    prepared_managed_helper as prepared_managed_helper,
    prepared_reader_payload as prepared_reader_payload,
)


@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "unprepared", "missing-uv", "missing-provider",
    "missing-request", "different-request",
])
def test_posix_bootstrap_reader_uses_prepared_owner_without_ambient_python(
    tmp_path: Path, scenario: str, prepared_reader_payload: Path,
    prepared_managed_helper: tuple[Path, Path, Path],
) -> None:
    shell = shutil.which("bash")
    if os.name == "nt" or not shell:
        pytest.skip("This control requires native POSIX Bash")
    uv, selected, managed_root = prepared_managed_helper
    scripts = prepared_reader_payload / "scripts"
    if scenario == "raw":
        engine = fixture.REPO / "libs/installer-engine/installer-engine.sh"
    else:
        scripts = tmp_path / "scripts"
        shutil.copytree(prepared_reader_payload / "scripts", scripts)
        engine = scripts / "installer-engine.sh"
        if scenario.startswith("missing-") and scenario != "missing-uv":
            name = (
                "bootstrap-uv.sh" if scenario == "missing-provider"
                else "installer-instance-python-version.txt"
            )
            (scripts / name).rename(scripts / (name + ".unavailable"))
        if scenario == "different-request":
            (scripts / "installer-instance-python-version.txt").write_text(
                "cpython@3.11.999\n", encoding="utf-8",
            )
    env = fixture.environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    for name in ("dirname", "wc", "cat"):
        utility = shutil.which(name)
        assert utility, name
        (commands / name).symlink_to(utility)
    if scenario != "missing-uv":
        (commands / "uv").symlink_to(uv)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").symlink_to(commands / "python")
    empty_root = tmp_path / "unprepared-python"
    empty_root.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(empty_root if scenario == "unprepared" else managed_root),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        UV_PYTHON=str(commands / "python"), UV_NO_MANAGED_PYTHON="1",
        VIRTUAL_ENV=str(runtime), CONDA_PREFIX=str(runtime),
    )
    body = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine))}\n"
        f"LINK_DIR={shlex.quote(str(runtime))}\n"
        + fixture.function("sh", "_bootstrap_python") + "\n"
        'python="$(_bootstrap_python)" || exit $?\n'
        '[[ "$UV_CONFIG_FILE" == missing-relative.toml && "$UV_NO_CONFIG" == false && '
        '"$UV_NO_MANAGED_PYTHON" == 1 && "$VIRTUAL_ENV" == "$LINK_DIR" ]] || exit 92\n'
        'printf "%s\\n" "$python"\n'
    )
    result = subprocess.run(
        [shell, "-c", body], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=15,
    )
    assert not marker.exists(), result.stdout + result.stderr
    if scenario in {"raw", "packaged"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == str(selected)
        assert not result.stderr
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not result.stdout
        assert "requires prepared uv-managed Python" in result.stderr
    assert not list(empty_root.iterdir())
    assert list(runtime.iterdir()) == [runtime / "bin"]
