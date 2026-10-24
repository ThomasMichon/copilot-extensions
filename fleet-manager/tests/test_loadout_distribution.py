"""Installed command proves template delivery without source or optional extras."""

from __future__ import annotations

import json
import os
import shutil
import sys

import pytest

from test_client_distribution import run
from test_loadouts import write_inputs


def test_installed_designer_actualizes_bundled_arc_and_contributed_support(
    tmp_path, request: pytest.FixtureRequest,
):
    artifact, _ = request.getfixturevalue("wheel")
    uv = shutil.which("uv")
    assert uv, "native loadout proof requires the runner's package manager"
    installed = tmp_path / "installed"
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"
    result = run([
        uv, "pip", "install", "--python", sys.executable, "--target", str(installed),
        "--no-index", "--no-deps", "--link-mode", "copy", str(artifact),
    ], env=environment)
    assert result.returncode == 0, result.stderr
    caller = tmp_path / "caller"
    caller.mkdir()
    args = write_inputs(caller)
    before = {path.name: path.read_bytes() for path in caller.iterdir()}
    script = (
        "import importlib.util,runpy,socket,subprocess,sys\n"
        "sys.path.insert(0,sys.argv.pop(1))\n"
        "assert all(importlib.util.find_spec(name) is None "
        "for name in ('cryptography','aiohttp','joserfc','pytest'))\n"
        "def blocked(*args,**kwargs): raise AssertionError('unexpected network or subprocess')\n"
        "socket.socket=blocked\n"
        "subprocess.Popen=blocked\n"
        "sys.argv=['fleet-manager','design',*sys.argv[1:]]\n"
        "runpy.run_module('fleet_manager',run_name='__main__')\n"
    )
    installed_before = sorted(str(path.relative_to(installed)) for path in installed.rglob("*"))
    result = run([sys.executable, "-I", "-S", "-B", "-c", script, str(installed), *args],
                 cwd=caller, env=environment)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual["status"] == "review-required"
    assert actual["composition"]["allocations"]["runners"]["configuration"]["adapter"] == "helm.arc"
    assert actual["composition"]["dependency_order"] == ["support", "runners"]
    assert actual["grid"]["worker-a"]["remaining"] == {
        "cpu_millis": 0, "memory_bytes": 0, "disk_bytes": 0,
    }
    assert not actual["activation_authorized"] and not actual["live_readiness_verified"]
    assert before == {path.name: path.read_bytes() for path in caller.iterdir()}
    assert sorted(str(path.relative_to(installed)) for path in installed.rglob("*")) == installed_before
