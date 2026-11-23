"""POSIX installer helpers inspect prepared managed Python, never an incumbent."""
from __future__ import annotations

import os
import json
import shlex
import shutil
import subprocess
import time
from collections.abc import Callable
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
    record_property: Callable[[str, object], None],
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
    calls = tmp_path / "uv-calls"
    if scenario != "missing-uv":
        wrapper = commands / "uv"
        wrapper.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_PROBE_LOG"\n'
            f'exec {shlex.quote(str(uv))} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
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
        UV_PROBE_LOG=str(calls),
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
    expected = 2 if scenario in {"raw", "packaged"} else 1
    samples: list[float] = []
    for _ in range(expected):
        started = time.monotonic()
        result = subprocess.run(
            [shell, "-c", body], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15,
        )
        samples.append(time.monotonic() - started)
        assert not marker.exists(), result.stdout + result.stderr
        if scenario in {"raw", "packaged"}:
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == str(selected)
            assert not result.stderr
        else:
            assert result.returncode != 0, result.stdout + result.stderr
            assert not result.stdout
            assert "requires prepared uv-managed Python" in result.stderr
    if scenario in {"raw", "packaged"}:
        assert calls.read_text(encoding="utf-8").splitlines() == [
            "--version",
            "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
        ] * expected
        record_property("dispatch_posix_reader_performance", json.dumps({
            "status": "measured", "scenario": scenario, "expected": expected,
            "completed": len(samples), "samples_seconds": samples, "max_seconds": max(samples),
            "uv_calls_per_read": 2, "network": "disabled", "python": str(selected),
            "cache": "fresh private uv cache then retained cache; preinstalled interpreter; OS caches uncontrolled",
            "unavailable": ["child CPU", "full stamp/install latency", "tail qualification"],
        }))
    assert not list(empty_root.iterdir())
    assert list(runtime.iterdir()) == [runtime / "bin"]
