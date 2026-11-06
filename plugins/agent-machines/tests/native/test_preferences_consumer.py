"""Opt-in normal CLI/file flow in contained, fresh local state; no cloud writes."""

from __future__ import annotations

import json
import subprocess
import sys

from agent_procutil import no_window_kwargs


def test_fresh_module_cli_projects_saved_mapping_and_local_override_without_writes(tmp_path):
    candidate = tmp_path / "received.json"
    candidate.write_text(json.dumps({
        "schema": "agent-machines.preferences", "version": 1,
        "publisher": "example-publisher", "revision": 1, "parent": None,
        "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-harness", "knowledge_repository_id": "repo-default"},
        ]}},
    }), encoding="utf-8")
    local = tmp_path / "local.json"
    local.write_text(json.dumps({
        "schema": "agent-machines.preferences.local", "version": 1,
        "machine": "example-worker", "domains": {"repositories": {"knowledge": [
            {"harness_repository_id": "repo-harness", "knowledge_repository_id": "repo-override"},
        ]}},
    }), encoding="utf-8")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    child = subprocess.run([
        sys.executable, "-m", "agent_machines", "preferences", "preview",
        "--input", str(candidate), "--local-overrides", str(local),
        "--publisher", "example-publisher", "--machine", "example-worker",
        "--harness", "repo-harness",
    ], capture_output=True, text=True, timeout=20, **no_window_kwargs())
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result["knowledge_preference"] == {
        "status": "suggested", "knowledge_repository_id": "repo-override",
    }
    assert result["scope"]["identity_resolution"] == "not-performed"
    assert result["configuration_applied"] is False
    assert result["binding_changed"] is False
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
