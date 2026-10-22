"""Implementation-only updates do not require re-baselining the golden corpus."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard
REPO = Path(__file__).resolve().parents[3]
PLUGIN = Path("plugins/agent-bridge")
CONTRACT = PLUGIN / "contract"
CONSUMERS = PLUGIN / "tests/test_http_targets_contract.py"


@pytest.mark.timeout(180)
def test_flow_update_leaves_golden_corpus_unchanged(tmp_path):
    snapshot = tmp_path / "repo"
    environment = {**os.environ, "PYTHONPATH": str(snapshot / PLUGIN / "src")}
    shutil.copytree(REPO / CONTRACT, snapshot / CONTRACT)
    shutil.copytree(
        REPO / PLUGIN / "src", snapshot / PLUGIN / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    registry_path = snapshot / CONTRACT / "registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    fixture_paths = {
        fixture["path"]
        for contract in registry["contracts"] for fixture in contract["fixtures"]
    }
    before = {path: (snapshot / path).read_bytes() for path in fixture_paths}
    for contract in registry["contracts"]:
        for record in contract["source_paths"]:
            destination = snapshot / record["path"]
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / record["path"], destination)
    for deferred in registry["deferred_contracts"]:
        if "reference_path" in deferred:
            destination = snapshot / deferred["reference_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO / deferred["reference_path"], destination)
    for name in ("check-agent-bridge-contracts.py", "agent_bridge_contract_git.py",
                 "bridge_http45_evidence.py"):
        destination = snapshot / "tools" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / "tools" / name, destination)
    shutil.copy2(REPO / PLUGIN / "plugin.json", snapshot / PLUGIN / "plugin.json")
    (snapshot / CONSUMERS).parent.mkdir(parents=True, exist_ok=True)
    for path in (CONSUMERS, PLUGIN / "tests/conftest.py"):
        shutil.copy2(REPO / path, snapshot / path)

    def run(arguments):
        result = subprocess.run(
            arguments, cwd=snapshot, env=environment, capture_output=True,
            text=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    run(["git", "init", "-q"])
    run(["git", "config", "user.name", "Fixture Test"])
    run(["git", "config", "user.email", "fixture@example.com"])
    run(["git", "config", "core.autocrlf", "false"])
    run(["git", "config", "core.longpaths", "true"])
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "contract baseline"])
    baseline = run(["git", "rev-parse", "HEAD"])
    wire = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    record = next(
        item for item in wire["source_paths"]
        if item["path"] == (PLUGIN / "src/agent_bridge/protocol.py").as_posix()
    )
    source = snapshot / record["path"]
    source.write_bytes(source.read_bytes() + b"\n# Implementation-only flow maintenance.\n")
    canonical = source.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    record.update(
        sha256=hashlib.sha256(canonical).hexdigest(), semantic=False,
        non_semantic_reason="Comment-only flow maintenance; wire behavior and floors are unchanged.",
    )
    registry_path.write_text(json.dumps(registry, indent=2) + "\n", encoding="utf-8")
    run(["git", "add", "-A"])
    run(["git", "commit", "-qm", "implementation-only update"])
    assert set(run(["git", "diff", "--name-only", baseline, "HEAD"]).splitlines()) == {
        record["path"], (CONTRACT / "registry.json").as_posix(),
    }
    run([sys.executable, "tools/check-agent-bridge-contracts.py", "--base", baseline])
    run([
        sys.executable, "-m", "pytest", str(snapshot / CONSUMERS), "-q", "--timeout=30",
        "--rootdir", str(snapshot), "--confcutdir", str(snapshot),
        "-o", "markers=guard: contract guard", "--basetemp", str(tmp_path / "consumer-tests"),
    ])
    assert {path: (snapshot / path).read_bytes() for path in fixture_paths} == before
