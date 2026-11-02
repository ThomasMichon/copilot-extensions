"""First-touch construction of the real materialized Worktrees source graph."""

import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import time

import pytest

from agent_procutil.owned_process import run_caller_owned


@pytest.mark.timeout(480)
def test_materialized_worktrees_cold_construction_keeps_original_watchdog(tmp_path):
    plugin = Path(__file__).resolve().parents[2]
    root = plugin.parents[1]
    materialized = tmp_path / "source" / "agent-worktrees"
    shutil.copytree(
        plugin, materialized,
        ignore=shutil.ignore_patterns(
            "tests", "__pycache__", "*.pyc", ".pytest_cache", ".ruff_cache",
            ".venv", "build", "dist", "*.egg-info",
        ),
    )
    previous_path = sys.path[:]
    try:
        sys.path.insert(0, str(root / "tools"))
        release = runpy.run_path(str(root / "tools" / "materialize_main.py"))
        results = release["materialize_uv_editable_ref_into"](
            source_consumer_dir=plugin, dest_consumer_dir=materialized,
            canonical_root=root,
        )
    finally:
        sys.path[:] = previous_path
    assert results and not any(item.startswith("SKIP ") for item in results), results
    uv = shutil.which("uv")
    assert uv, "cold construction requires native UV"
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment["UV_CACHE_DIR"] = str(tmp_path / "empty-uv-cache")
    program = """
import json,pathlib,sys,time,tomllib
plugin,runtime,uv=map(pathlib.Path,sys.argv[1:])
sys.path.insert(0,str(plugin/"scripts"))
import installer_engine as engine
version=tomllib.loads((plugin/"pyproject.toml").read_text())["project"]["version"]
packages=engine._package_roots(plugin)
before=engine.runtime.fingerprint_source(engine._source_files(packages))
provenance={
    "version":version,"commit":"cold-candidate","branch":"candidate",
    "source":str(plugin),"deployment_source":json.dumps({"path":str(plugin)}),
}
started=time.monotonic()
with engine.numbered_candidate(
    runtime,version,plugin=plugin,uv=uv,base_python=pathlib.Path(sys.executable),
    health_module="agent_worktrees.runtime_candidate",
    health_callable="verify_runtime_candidate",
    construction_callable="agent_worktrees.runtime_candidate:prepare_runtime_candidate",
    construction_data=provenance,
) as candidate:
    assert not candidate.reused
    assert engine.runtime.is_complete(runtime,version,expect_hash=candidate.payload_hash)
    assert (candidate.slot/".runtime-bootstrap"/"installer_retained_content.py").is_file()
    elapsed=time.monotonic()-started
    assert elapsed<480
    assert not (runtime/"current-version").exists()
assert not candidate.frozen_plugin.exists()
assert engine.runtime.fingerprint_source(engine._source_files(packages))==before
print(json.dumps({"packages":len(packages),"ready_seconds":elapsed,"settled":True}))
"""
    started = time.monotonic()
    result = run_caller_owned(
        [
            sys.executable, "-I", "-B", "-c", program,
            str(materialized), str(tmp_path / "runtime"), uv,
        ],
        cwd=tmp_path, env=environment, timeout=480,
        max_stdout_bytes=1024 * 1024, max_stderr_bytes=4 * 1024 * 1024,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert time.monotonic() - started < 480
    evidence = json.loads(result.stdout)
    assert evidence["packages"] > 1 and evidence["settled"]
    print(json.dumps(evidence, sort_keys=True))
