"""Index's custom constructor retains slot custody and selects managed Python."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest

from test_install_engine_adoption import function, stage_index_installer
from test_managed_bootstrap_reader_sh import _environment, prepared_helper as prepared_helper

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX construction")


@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "partial", "unprepared", "uv-failure", "healthy-reuse", "missing-bound-uv",
])
def test_managed_construction_preserves_receipt_and_independent_runtime(
    tmp_path: Path, scenario: str, prepared_helper: tuple[Path, Path, Path],
    request: pytest.FixtureRequest,
) -> None:
    uv, _helper, managed_root = prepared_helper
    probe_environment = {
        **os.environ, "UV_PYTHON_INSTALL_DIR": str(managed_root), "UV_NO_CONFIG": "1",
    }
    selected = subprocess.run(
        [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "3.10"],
        env=probe_environment, capture_output=True, text=True, timeout=15,
    )
    if selected.returncode:
        pytest.skip("This acquisition-free control requires prepared managed Python 3.10")
    base = Path(selected.stdout.strip()).resolve()
    assert base.is_relative_to(managed_root.resolve())
    private = tmp_path / "managed-python"
    private.mkdir()
    if scenario != "unprepared":
        shutil.copytree(base.parent.parent, private / base.parent.parent.name, symlinks=True)
    materialized = scenario != "raw"
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=materialized,
    )
    engine = (
        payload / "scripts" if materialized else payload.parents[1] / "libs/installer-engine"
    )
    env = _environment(tmp_path, prepared_helper, "managed")
    commands = Path(env["PATH"])
    for name in ("sleep", "rm"):
        utility = shutil.which(name)
        assert utility, name
        (commands / name).symlink_to(utility)
    wrapper = commands / "owned-uv"
    calls = tmp_path / "constructor-uv-calls"
    wrapper.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CONSTRUCTOR_UV_LOG"\n'
        + ("exit 19\n" if scenario == "uv-failure" else f'exec {shlex.quote(str(uv))} "$@"\n'),
        encoding="utf-8",
    )
    wrapper.chmod(0o700)
    bound_uv = commands / "missing-bound-uv" if scenario == "missing-bound-uv" else wrapper
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    python = candidate / "bin/python"
    receipt = candidate / ".construction-receipt.json"
    receipt.write_text('{"owner":"retained-construction"}\n', encoding="utf-8")
    if scenario == "partial":
        python.parent.mkdir()
        python.symlink_to(commands / "python")
    if scenario == "healthy-reuse":
        subprocess.run(
            [str(uv), "--no-config", "venv", str(candidate), "--python", "3.10",
             "--managed-python", "--no-project", "--allow-existing"],
            env={**probe_environment, "UV_PYTHON_INSTALL_DIR": str(private),
                 "UV_OFFLINE": "1", "UV_CACHE_DIR": str(tmp_path / "initial-cache")},
            capture_output=True, text=True, timeout=15, check=True,
        )
    (tmp_path / ".python-version").write_text("3.9\n", encoding="utf-8")
    env.update(
        UV_PYTHON_INSTALL_DIR=str(private), CONSTRUCTOR_UV_LOG=str(calls),
        UV_WORKING_DIR=str(tmp_path / "missing-working-directory"),
        UV_PROJECT=str(tmp_path / "missing-project"),
    )
    body = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine / 'installer-engine.sh'))}\n"
        + function("_test_index_venv", "sh") + "\n"
        + function("_new_index_venv", "sh") + "\n"
        f"VENV_DIR={shlex.quote(str(tmp_path / 'other-slot'))}\n"
        f"UV_COMMAND={shlex.quote(str(bound_uv))}\n"
        '_warn() { printf "%s\\n" "$*" >&2; }\n'
        f"_new_index_venv {shlex.quote(str(candidate))} {shlex.quote(str(python))} "
        f"{shlex.quote(str(commands / 'python'))} 1 || exit $?\n"
        '[[ "$UV_CONFIG_FILE" == missing-relative.toml && "$UV_NO_CONFIG" == false && '
        '"$UV_NO_MANAGED_PYTHON" == 1 && "$UV_WORKING_DIR" == *missing-working-directory && '
        '"$UV_PROJECT" == *missing-project ]] || exit 92\n'
        f"{shlex.quote(str(python))} -I -c "
        + shlex.quote("import json,sys; print(json.dumps(list(sys.version_info[:2])))") + "\n"
    )
    successful = scenario in {"raw", "packaged", "partial", "healthy-reuse"}
    samples = []
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        result = subprocess.run(
            ["/bin/bash", "-c", body], env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=20,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
        assert receipt.read_text(encoding="utf-8") == '{"owner":"retained-construction"}\n'
        if successful:
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.splitlines()[-1] == "[3, 10]"
        else:
            assert result.returncode != 0
            diagnostic = (
                "uv venv requires the selected UV executable"
                if scenario == "missing-bound-uv" else "uv venv failed health validation"
            )
            assert diagnostic in result.stderr
            assert not (candidate / "pyvenv.cfg").exists()
    expected_calls = 0 if scenario in {"healthy-reuse", "missing-bound-uv"} else 1
    assert (calls.read_text().splitlines() if calls.exists() else []) == [
        f"venv {candidate} --python 3.10 --managed-python --no-project --allow-existing",
    ] * expected_calls
    request.node.user_properties.append(("index_posix_construction_performance", json.dumps({
        "scenario": scenario, "status": "measured", "completed": len(samples),
        "samples_seconds": samples, "uv_calls": expected_calls,
        "network": "disabled", "runtime_python": "3.10", "helper_python": "3.11.18",
        "cache": "fresh/retained private UV cache; copied prepared Python; OS caches uncontrolled",
        "unavailable": ["full package install latency", "child CPU", "tail qualification"],
    })))
