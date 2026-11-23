"""The controller's mutation binstub must select prepared managed Python."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

GUARD = Path(__file__).resolve().parents[1] / "bootstrap-killswitch-guard.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(os.name == "nt" or BASH is None, reason="native POSIX binstub")


@pytest.mark.parametrize("action", ["on", "off"])
def test_mutation_uses_managed_selection_not_poisoned_python(
    tmp_path: Path, prepared_controller_uv: Path, action: str,
) -> None:
    assert BASH is not None
    marker = tmp_path / "path-python-called"
    for name in ("python", "python3"):
        path = prepared_controller_uv / name
        path.write_text(f"#!{BASH}\nprintf called >\"$INTERPRETER_PROBE\"\nexit 95\n", encoding="utf-8")
        path.chmod(0o755)
    state = tmp_path / "hold with spaces.json"
    baseline = b'{"active":true,"reason":"retained"}'
    state.write_bytes(baseline)
    trace = tmp_path / "uv-trace"
    reason = 'quoted "reason" \\ path\n\u03bb'
    result = subprocess.run(
        [BASH, str(GUARD), action, *([reason] if action == "on" else [])],
        capture_output=True, text=True, timeout=5,
        env={
            **os.environ, "HOME": str(tmp_path), "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(state),
            "BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT": str(tmp_path / "absent"),
            "CONTROLLER_UV_TRACE": str(trace), "INTERPRETER_PROBE": str(marker),
            "UV_PYTHON": "unrelated", "UV_PYTHON_PREFERENCE": "only-system",
            "UV_NO_MANAGED_PYTHON": "1", "VIRTUAL_ENV": str(tmp_path / "foreign"),
            "PYTHONHOME": str(tmp_path / "poisoned"), "PYTHONPATH": str(tmp_path / "poisoned"),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()
    assert trace.read_text().splitlines() == [
        "--version",
        "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
    ]
    if action == "on":
        assert json.loads(state.read_bytes())["reason"] == reason
    else:
        assert not state.exists()


@pytest.mark.parametrize("action", ["on", "off"])
@pytest.mark.parametrize("failure", ["missing-uv", "unprepared", "failed-version"])
def test_unprepared_mutation_preserves_state_without_fallback(
    tmp_path: Path, prepared_controller_uv: Path, action: str, failure: str,
) -> None:
    assert BASH is not None
    uv = prepared_controller_uv / "uv"
    if failure == "missing-uv":
        uv.unlink()
    else:
        uv.write_text(
            f"#!{BASH}\n"
            + ('[[ "$*" != --version ]] || { printf "uv 0.8.0\\n"; exit 0; }\n'
               if failure == "unprepared" else "")
            + "exit 94\n",
            encoding="utf-8",
        )
    for name in ("dirname", "wc", "cat"):
        source = shutil.which(name)
        assert source is not None
        (prepared_controller_uv / name).symlink_to(source)
    state = tmp_path / "hold.json"
    baseline = b'{"active":true,"reason":"retained audit hold"}'
    state.write_bytes(baseline)
    result = subprocess.run(
        [BASH, str(GUARD), action], capture_output=True, text=True, timeout=5,
        env={"PATH": str(prepared_controller_uv), "HOME": str(tmp_path),
             "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(state)},
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert state.read_bytes() == baseline
    assert not state.with_suffix(".json.lock").exists()
