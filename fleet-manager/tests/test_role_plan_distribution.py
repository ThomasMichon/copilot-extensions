"""Actual dependency-free installed role compiler, not an ambient SDK import."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from test_client_distribution import run, wheel as shared_wheel  # noqa: F401 - pytest fixture
from test_role_deployment import write_inputs


def test_role_plan_distribution_runs_built_wheel_without_source_or_host_dependencies(
    tmp_path: Path, request: pytest.FixtureRequest,
) -> None:
    artifact, _ = request.getfixturevalue("shared_wheel")
    uv = shutil.which("uv")
    assert uv, "native role planning proof requires the runner's package manager"
    installed = tmp_path / "installed"
    clean = dict(os.environ)
    clean.pop("PYTHONPATH", None)
    clean["UV_OFFLINE"] = "1"
    result = run([
        uv, "pip", "install", "--python", sys.executable, "--target", str(installed),
        "--no-index", "--no-deps", "--link-mode", "copy", str(artifact),
    ], env=clean)
    assert result.returncode == 0, result.stderr
    work = tmp_path / "caller"
    work.mkdir()
    args, before = write_inputs(work)
    script = (
        "import importlib.util,runpy,socket,subprocess,sys\n"
        "sys.path.insert(0,sys.argv.pop(1))\n"
        "assert all(importlib.util.find_spec(name) is None "
        "for name in ('cryptography','aiohttp','joserfc','pytest'))\n"
        "def blocked(*args,**kwargs): raise AssertionError('unexpected process or network')\n"
        "socket.socket=blocked\n"
        "subprocess.Popen=blocked\n"
        "sys.argv=['fleet_manager.role_plan',*sys.argv[1:]]\n"
        "runpy.run_module('fleet_manager.role_plan',run_name='__main__')\n"
    )
    installed_before = sorted(str(path.relative_to(installed)) for path in installed.rglob("*"))
    result = run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(installed), *args],
        cwd=work, env=clean,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["schema"] == "fleet-manager.role-plan"
    assert output["status"] == "review-required"
    assert output["roles"][0]["projection"]["resource_type"] == "Stack"
    assert not output["activation_authorized"] and not output["live_readiness_verified"]
    assert {path.name: path.read_bytes() for path in work.iterdir()} == before
    assert sorted(str(path.relative_to(installed)) for path in installed.rglob("*")) == installed_before
