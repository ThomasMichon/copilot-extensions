"""Portable transport dependencies survive staging and a fresh runtime install."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import _trusted_pointer_materializer as materializer

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


@pytest.mark.parametrize("consumer", ["agent-machines", "agent-worktrees", "worktree-manager"])
def test_staged_identity_dependency_installs_without_checkout_paths(tmp_path, consumer):
    import yaml
    repo = Path(__file__).resolve().parents[2]
    source = repo / "worktree-manager" if consumer == "worktree-manager" else repo / "plugins" / consumer
    payload = tmp_path / "payload"
    external_libs = {
        lib for _name, _path, lib, _editable in materializer.find_uv_editable_refs(source)
    }
    ignore_cache = shutil.ignore_patterns(
        ".venv", ".test-venvs", "__pycache__", "*.pyc", "*.egg-info", ".pytest_cache",
    )

    def ignore(directory, names):
        excluded = set(ignore_cache(directory, names))
        if Path(directory) == source / "libs":
            excluded.update(external_libs)
        return excluded

    shutil.copytree(
        source, payload,
        ignore=ignore,
    )
    result = materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=source, dest_consumer_dir=payload,
        canonical_root=repo, dest_root=payload,
    )
    unexpected_skips = [
        line
        for line in result
        if line.startswith("SKIP ")
        and "nested in" not in line
        and ".venv" not in line
    ]
    assert not unexpected_skips, result
    manifest = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))
    assert "agent-machine-transport" in manifest["project"]["dependencies"]
    assert manifest["tool"]["uv"]["sources"]["agent-machine-transport"]["path"].endswith(
        "libs/machine-transport"
    )
    dependency = payload / "libs" / "machine-transport"
    remote_login_shell = payload / "libs" / "remote-login-shell"
    assert remote_login_shell.is_dir(), "nested remote-login-shell dependency must be staged"
    uv = shutil.which("uv")
    assert uv, "the managed test runner requires uv"
    wheels = tmp_path / "wheels"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["UV_OFFLINE"] = "1"
    environment["UV_CACHE_DIR"] = str(tmp_path / "empty-cache")

    def run(command):
        completed = subprocess.run(
            command, cwd=tmp_path, env=environment, capture_output=True, text=True,
            timeout=60, **no_window_kwargs(),
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed.stdout

    run([
        uv, "build", "--wheel", "--no-build-isolation", "--python", sys.executable,
        "--out-dir", str(wheels), str(dependency),
    ])
    run([
        uv, "build", "--wheel", "--no-build-isolation", "--python", sys.executable,
        "--out-dir", str(wheels), str(remote_login_shell),
    ])
    wheel, = wheels.glob("agent_machine_transport-*.whl")
    remote_wheel, = wheels.glob("agent_remote_login_shell-*.whl")
    runtime = tmp_path / "runtime"
    run([uv, "venv", "--python", sys.executable, str(runtime)])
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    run([uv, "pip", "install", "--python", str(python), "--no-index", "--no-deps", str(remote_wheel)])
    run([uv, "pip", "install", "--python", str(python), "--no-index", "--no-deps", str(wheel)])
    script = """
import importlib.util, json, pathlib, sys
# Use the runner's prepared third-party YAML, never an index or shared-code path.
yaml_file = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location(
    "yaml", yaml_file, submodule_search_locations=[str(yaml_file.parent)],
)
module = importlib.util.module_from_spec(spec)
sys.modules["yaml"] = module
spec.loader.exec_module(module)
import machine_transport, remote_login_shell
print(json.dumps({
    "origin": machine_transport.__file__,
    "remote_origin": remote_login_shell.__file__,
    "guest": machine_transport.resolve_identity("example-host", guest=True).canonical,
}))
"""
    result = json.loads(run([str(python), "-I", "-c", script, str(Path(yaml.__file__))]))
    assert Path(result["origin"]).resolve().is_relative_to(runtime.resolve())
    assert Path(result["remote_origin"]).resolve().is_relative_to(runtime.resolve())
    assert result["guest"] == "example-host-wsl"
