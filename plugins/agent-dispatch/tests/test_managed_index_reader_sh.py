"""Dispatch's index bridge shares the selected uv installation context."""
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


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX index reader")
@pytest.mark.parametrize("composed", [False, True])
@pytest.mark.parametrize("setting", [
    "file", "relative-uv-config", "site-prefix", "uv-override", "malformed",
])
def test_index_bridge_preserves_selected_context_without_ambient_commands(
    tmp_path: Path, composed: bool, setting: str, prepared_reader_payload: Path,
    prepared_managed_helper: tuple[Path, Path, Path],
) -> None:
    shell = shutil.which("bash")
    assert shell
    uv, _, managed_root = prepared_managed_helper
    engine = (
        prepared_reader_payload / "scripts/installer-engine.sh" if composed
        else fixture.REPO / "libs/installer-engine/installer-engine.sh"
    )
    env = fixture.environment(tmp_path)
    home = Path(env["HOME"])
    commands = tmp_path / "commands"
    commands.mkdir()
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py", "pip", "pip3"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    (commands / "uv").symlink_to(uv)
    configuration = tmp_path / "pip.conf"
    configuration.write_text(
        "[invalid" if setting == "malformed"
        else "[global]\nindex-url=https://pip.example.invalid/simple\n",
        encoding="utf-8",
    )
    env.update(
        PATH=str(commands) + os.pathsep + os.defpath, AMBIENT_MARKER=str(marker),
        UV_PYTHON_INSTALL_DIR=str(managed_root), UV_PYTHON_DOWNLOADS="never",
        UV_CACHE_DIR=str(tmp_path / "uv-cache"), UV_OFFLINE="1",
        PIP_CONFIG_FILE=str(configuration),
    )
    expected = "https://pip.example.invalid/simple"
    if setting == "relative-uv-config":
        working = home / "working directory"
        working.mkdir()
        (working / "selected.toml").write_text(
            '[[index]]\nurl="https://uv.example.invalid/simple"\ndefault=true\n',
            encoding="utf-8",
        )
        env["UV_WORKING_DIR"] = working.name
        env["UV_CONFIG_FILE"] = "selected.toml"
        expected = ""
    elif setting == "site-prefix":
        active = tmp_path / "active environment"
        active.mkdir()
        (active / "pyvenv.cfg").write_text("home=/not-executed\n", encoding="utf-8")
        (active / "pip.conf").write_text(
            "[global]\nindex-url=https://site.example.invalid/simple\n", encoding="utf-8",
        )
        configuration.write_text("[global]\ntimeout=5\n", encoding="utf-8")
        env["VIRTUAL_ENV"] = str(active)
        expected = "https://site.example.invalid/simple"
    elif setting == "uv-override":
        expected = env["UV_DEFAULT_INDEX"] = "https://override.example.invalid/simple"
    script = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine))}\n"
        '_fail() { printf "%s\\n" "$*" >&2; }\n_step() { :; }\n'
        + fixture.function("sh", "_bootstrap_python") + "\n"
        + fixture.function("sh", "_ensure_uv_index") + "\n"
        + 'before_config="${UV_CONFIG_FILE-}"; before_working="${UV_WORKING_DIR-}"\n'
        + 'before_venv="${VIRTUAL_ENV-}"; before_path="$PATH"\n'
        + f"_ensure_uv_index {shlex.quote(str(commands / 'uv'))}\n"
        + '[[ "${UV_CONFIG_FILE-}" == "$before_config" && '
        '"${UV_WORKING_DIR-}" == "$before_working" && '
        '"${VIRTUAL_ENV-}" == "$before_venv" && "$PATH" == "$before_path" ]]\n'
        + 'printf "%s\\n" "${UV_DEFAULT_INDEX-}"\n'
    )
    result = subprocess.run(
        [shell, "-c", script], env=env, cwd=home, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30,
    )
    assert not marker.exists(), result.stdout + result.stderr
    if setting == "malformed":
        assert result.returncode != 0 and not result.stdout.strip(), result.stdout + result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == expected
    assert not (home / ".local/bin").exists()
