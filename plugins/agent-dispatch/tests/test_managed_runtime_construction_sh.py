"""POSIX construction uses managed uv without taking over slot cleanup."""
from __future__ import annotations

import json
import os
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
)
from .test_managed_bootstrap_reader import (
    prepared_reader_payload as prepared_reader_payload,
)


@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "partial", "unprepared", "uv-failure",
])
def test_posix_runtime_construction_uses_managed_python_and_preserves_receipt(
    tmp_path: Path, scenario: str, prepared_reader_payload: Path,
    prepared_managed_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    shell = shutil.which("bash")
    if os.name == "nt" or not shell:
        pytest.skip("This control requires native POSIX Bash")
    uv, _, managed_root = prepared_managed_helper
    selected = subprocess.run(  # noqa: S603 - Prepared fixture-owned uv, fixed arguments.
        [str(uv), "--no-config", "python", "find", "--no-project", "--system",
         "--managed-python", "--no-python-downloads", "3.11"],
        capture_output=True, text=True, timeout=15,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared managed Python 3.11")
    base = Path(selected.stdout.strip()).resolve()
    assert base.is_relative_to(managed_root.resolve())
    private_root = tmp_path / "managed-python"
    private_root.mkdir()
    if scenario != "unprepared":
        shutil.copytree(
            base.parent.parent, private_root / base.parent.parent.name, symlinks=True,
        )
    scripts = prepared_reader_payload / "scripts"
    engine = (
        fixture.REPO / "libs/installer-engine/installer-engine.sh" if scenario == "raw"
        else scripts / "installer-engine.sh"
    )
    env = fixture.environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("dirname", "grep", "sleep"):
        utility = shutil.which(name)
        assert utility, name
        (commands / name).symlink_to(utility)
    marker = tmp_path / "ambient-ran"
    for name in ("python", "python3", "py"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    wrapper = commands / "owned-uv"
    wrapper.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_PROBE_LOG"\n'
        + ('exit 19\n' if scenario == "uv-failure" else f'exec {shlex.quote(str(uv))} "$@"\n'),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    runtime = tmp_path / "candidate"
    runtime.mkdir()
    receipt = runtime / ".construction-receipt.json"
    receipt.write_text('{"owner":"existing-construction"}\n', encoding="utf-8")
    if scenario == "partial":
        (runtime / "bin").mkdir()
        (runtime / "bin/python").symlink_to(commands / "python")
    project = tmp_path / ".python-version"
    project.write_text("3.9\n", encoding="utf-8")
    calls = tmp_path / "uv-calls"
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(marker), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(private_root), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        UV_PYTHON=str(commands / "python"), UV_NO_MANAGED_PYTHON="1",
        VIRTUAL_ENV=str(runtime), CONDA_PREFIX=str(runtime), UV_PROBE_LOG=str(calls),
    )
    body = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine))}\n"
        + fixture.function("sh", "_create_runtime_venv") + "\n"
        f"VENV_DIR={shlex.quote(str(runtime))}\nUV_CMD={shlex.quote(str(wrapper))}\n"
        "_warn() { printf '%s\\n' \"$*\" >&2; }\n"
        "_create_runtime_venv || exit $?\n"
        '[[ "$UV_CONFIG_FILE" == missing-relative.toml && "$UV_NO_CONFIG" == false && '
        '"$UV_NO_MANAGED_PYTHON" == 1 && "$VIRTUAL_ENV" == "$VENV_DIR" ]] || exit 92\n'
        '"$VENV_DIR/bin/python" -I -c '
        + shlex.quote('import json,sys; print(json.dumps(list(sys.version_info[:2])))') + "\n"
    )
    success = scenario in {"raw", "packaged", "partial"}
    expected = 2 if success else 1
    samples = []
    for _ in range(expected):
        started = time.monotonic()
        result = subprocess.run(  # noqa: S603 - Native Bash runs the contained source fixture.
            [shell, "-c", body], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15,
        )
        samples.append(time.monotonic() - started)
        assert not marker.exists(), result.stdout + result.stderr
        assert receipt.read_text(encoding="utf-8") == '{"owner":"existing-construction"}\n'
        if success:
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.splitlines()[-1] == "[3, 11]"
            assert (runtime / "pyvenv.cfg").is_file()
        else:
            expected_code = 19 if scenario == "uv-failure" else 2
            assert result.returncode == expected_code, result.stdout + result.stderr
            assert not (runtime / "pyvenv.cfg").exists()
    assert calls.read_text(encoding="utf-8").splitlines() == [
        f"venv {runtime} --python 3.11 --managed-python --no-project --allow-existing",
    ] * expected
    record_property("dispatch_posix_construction_performance", json.dumps({
        "status": "measured", "scenario": scenario, "expected": expected,
        "completed": len(samples), "samples_seconds": samples, "max_seconds": max(samples),
        "uv_calls_per_construction": 1, "network": "disabled", "runtime_python": "3.11",
        "cache": "fresh/retained private uv cache; prepared Python; OS caches uncontrolled",
        "unavailable": ["child CPU", "full stamp/install latency", "tail qualification"],
    }))
