"""Opt-in real governed/offline wheel fixture, never a production installation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_index_service.staging import NativeBuildConfig, inspect_candidates, stage_candidate


def _snapshot(root):
    hashes = {}
    for path in root.rglob("*"):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            hashes[path.relative_to(root)] = digest.hexdigest()
    return hashes


def test_real_uv_first_touch_and_replay_preserve_host_and_activation_markers(tmp_path):
    manifest = os.environ.get("STANDALONE_STAGING_TEST_FIXTURE")
    if not manifest:
        pytest.skip("real uv fixture must be prepared through an approved package source")
    fixture = json.loads(Path(manifest).read_text(encoding="utf-8"))
    config = tmp_path / "host.yaml"
    host = tmp_path / "host-state"
    config.write_text(json.dumps({
        "schema_version": 1, "home": str(host),
        "sources": [{"name": "git:fixture", "path": str(tmp_path / "documents")}],
    }), encoding="utf-8")
    root = tmp_path / "runtime"
    build = NativeBuildConfig(
        python=Path(fixture["python"]), uv=Path(fixture["uv"]),
        third_party_lock=Path(fixture["third_party_lock"]), uv_config=Path(fixture["uv_config"]),
    )
    arguments = {
        "expected_source_commit": fixture["source_commit"],
        "install_root": root, "host_config_path": config, "build": build,
    }
    first = stage_candidate(Path(fixture["descriptor"]), **arguments)
    slot = Path(first["slot"])
    before = _snapshot(slot)
    second = stage_candidate(Path(fixture["descriptor"]), **arguments)
    assert first["state"] == "staged"
    assert second["state"] == "already_staged"
    assert first["identity"] == second["identity"]
    assert _snapshot(slot) == before
    observed = inspect_candidates(root, host_config_path=config)
    assert observed["candidates"][0]["state"] == "staged"
    assert not host.exists()
    assert not (root / "current-version").exists()
    assert not (root / "last-known-good").exists()
    assert not (root / "bin").exists()
    # The interpreter is consumed at its permanent location after the staging
    # management process has returned, not via a relocatable temporary venv.
    result = subprocess.run(
        [first["python"], "-I", "-B", "-c",
         "import json,sys,agent_index_service; "
         "print(json.dumps({'python':sys.executable,'version':agent_index_service.__version__}))"],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, "staged interpreter was not usable after staging returned"
    assert Path(json.loads(result.stdout)["python"]).resolve() == Path(first["python"]).resolve()
