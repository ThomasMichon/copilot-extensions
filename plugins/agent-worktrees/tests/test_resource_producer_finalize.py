"""Actual finalize and cross-process producer/finalizing lock regression."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from agent_worktrees import config as cfg, finalize, tracking
from test_resource_producer import absence, call
from test_transitive_finalize_integration import _env, _save_worktree


@pytest.fixture
def final_owner(tmp_path, monkeypatch):
    root, records, config = _env.__wrapped__(tmp_path, monkeypatch)
    _save_worktree(records, "owner")
    path = records / "owner.yaml"
    record = tracking.load_record(path)
    record.repo = config.repo_name
    tracking.save_record(record, path)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    return root, path, config


def _reserve(path, config):
    return call(path, "reserve", {"kind": "container", "namespace": "raw-resource", "recovery": {}},
                ref=tracking.format_claim_ref(config.machine, config.repo_name, "owner"))


@pytest.mark.parametrize("mode", ["off", "warn", "block"])
@pytest.mark.parametrize("abandon", [False, True])
@pytest.mark.parametrize("allocation_started", [False, True])
def test_real_finalize_blocks_initially_empty_owner_after_reserve(final_owner, monkeypatch, mode, abandon, allocation_started):
    _, path, config = final_owner
    assert tracking.load_record(path).resources == []
    monkeypatch.setenv("AGENT_WORKTREES_OBLIGATION_GATE", mode)
    result = _reserve(path, config)
    assert result["phase"] == "pending"
    if allocation_started:
        armed = call(path, "begin", {"token": result["token"]}, ref=result["owner"])
        assert armed["phase"] == "allocating"
    assert finalize.validate_and_finalize("owner", config, abandon=abandon, handoff_to="recipient" if abandon else None) is False
    current = tracking.load_record(path)
    assert current.status == "active" and current.resources[0].is_unsettled


def test_actual_final_locked_gate_rejects_late_producer(final_owner, monkeypatch):
    _, path, config = final_owner
    check = finalize._assert_obligations_settled
    def initial_check(record, *args, **kwargs):
        assert check(record, *args, **kwargs) is True
        assert _reserve(path, config)["ok"]
        return True
    monkeypatch.setattr(finalize, "_assert_obligations_settled", initial_check)
    assert finalize.validate_and_finalize("owner", config, abandon=True, handoff_to="recipient") is False
    assert tracking.load_record(path).status == "active"
    assert tracking.load_record(path).resources[0].is_unsettled


def test_confirmed_close_then_actual_finalize(final_owner, monkeypatch):
    from agent_worktrees import claim_providers

    _, path, config = final_owner
    result = _reserve(path, config)
    assert finalize.validate_and_finalize("owner", config) is False
    monkeypatch.setattr(claim_providers, "resolve_claim_status", lambda ref: absence(ref))
    closed = call(path, "close", {"token": result["token"]}, ref=result["owner"])
    assert closed["phase"] == "closed"
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    assert finalize.validate_and_finalize("owner", config) is True
    current = tracking.load_record(path)
    assert current.status == "finalized" and current.resources[0].producer["phase"] == "closed"


def test_separate_process_producer_waits_for_lock_then_refuses_finalizing(final_owner):
    root, path, config = final_owner
    signal = root / "producer-entered"
    code = """
import json, sys
from pathlib import Path
from agent_worktrees import config as cfg, tracking
from agent_worktrees.tracking_producer_write import apply_resource_producer
path, signal, machine, project = sys.argv[1:]
config = cfg.Config(srcroot=str(Path(path).parent.parent.parent), machine=machine,
 platform="linux", repo_name=project, repos={project: cfg.RepoConfig(
 anchor=str(Path(path).parent), worktree_root=str(Path(path).parent))})
cfg.load_project_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: Path(path).parent.parent
Path(signal).write_text("entered")
print(json.dumps(apply_resource_producer({
 "schema":"copilot-extensions.resource-producer", "version":1,
 "operation":"reserve", "input":{"kind":"container","namespace":"raw-resource","recovery":{}},
 "owner_ref":tracking.format_claim_ref(machine,project,"owner"), "yaml_path":path})))
"""
    # A real separate interpreter competes for the OS sidecar, not a mock or
    # just a thread sharing the in-process RLock.
    with tracking._RecordLock(path, require_sidecar=True):
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(path), str(signal), config.machine, config.repo_name],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
        )
        deadline = time.monotonic() + 10
        while not signal.exists() and time.monotonic() < deadline:
            if child.poll() is not None:
                break
            time.sleep(0.02)
        if not signal.exists() or child.poll() is not None:
            stdout, stderr = child.communicate(timeout=15)
            pytest.fail(f"producer subprocess did not reach locked operation: {stdout}\n{stderr}")
        record = tracking.load_record(path)
        record.status = "finalizing"
        tracking.save_record(record, path)
    stdout, stderr = child.communicate(timeout=15)
    assert child.returncode == 0, stderr
    assert json.loads(stdout)["error"] == "rejected"
    assert tracking.load_record(path).resources == []
