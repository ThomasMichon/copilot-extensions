"""Retained claims participate in the actual initial and final cleanup gates."""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from agent_worktrees import claim_providers, finalize, tracking
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner
from test_resource_relationship import call, retired


def reserve(path, config):
    return call(
        path, "reserve",
        {"kind": "session", "namespace": "bridge-remote", "request_id": "finalize-one", "recovery": {}},
        owner_ref=tracking.format_claim_ref(config.machine, config.repo_name, "owner"),
    )


@pytest.mark.parametrize("abandon", [False, True])
def test_actual_finalize_cannot_release_retained_session_kind(final_owner, abandon):
    _, path, config = final_owner
    assert reserve(path, config)["ok"]
    assert finalize.validate_and_finalize(
        "owner", config, abandon=abandon, handoff_to="recipient" if abandon else None,
    ) is False
    current = tracking.load_record(path)
    assert current.status == "active" and current.resources[0].is_unsettled


def test_final_locked_gate_rejects_relationship_reserved_after_initial_check(final_owner, monkeypatch):
    _, path, config = final_owner
    check = finalize._assert_obligations_settled

    def initial_check(record, *args, **kwargs):
        assert check(record, *args, **kwargs) is True
        assert reserve(path, config)["ok"]
        return True

    monkeypatch.setattr(finalize, "_assert_obligations_settled", initial_check)
    assert finalize.validate_and_finalize("owner", config, abandon=True, handoff_to="recipient") is False
    assert tracking.load_record(path).status == "active"
    assert tracking.load_record(path).resources[0].is_unsettled


def test_actual_finalize_after_verified_local_retirement(final_owner, monkeypatch):
    _, path, config = final_owner
    pending = reserve(path, config)
    monkeypatch.setattr(claim_providers, "resolve_claim_status", retired)
    assert call(path, "close", {"token": pending["token"]}, owner_ref=pending["owner"])["ok"]
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
    assert finalize.validate_and_finalize("owner", config) is True
    assert tracking.load_record(path).resources[0].producer["phase"] == "closed"


def test_separate_process_reservation_waits_for_authority_and_refuses_frozen_owner(final_owner):
    root, path, config = final_owner
    signal = root / "relationship-entered"
    code = """
import json, sys
from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees.tracking_relationship_write import apply_resource_relationship
path, signal, machine, project = sys.argv[1:]
config = cfg.Config(srcroot=str(Path(path).parent.parent.parent), machine=machine,
 platform="linux", repo_name=project, repos={project: cfg.RepoConfig(
 anchor=str(Path(path).parent), worktree_root=str(Path(path).parent))})
cfg.load_project_config = lambda *a, **k: config
cfg.project_dir = lambda *a, **k: Path(path).parent.parent
Path(signal).write_text("entered")
result = apply_resource_relationship({
 "schema":"copilot-extensions.resource-relationship", "version":1,
 "operation":"reserve", "input":{"kind":"bridge","namespace":"bridge-remote","request_id":"race","recovery":{}},
 "owner_ref":machine+"/"+project+"/owner", "yaml_path":path})
print(json.dumps(result))
sys.exit(0 if result.get("error") == "rejected" and "frozen" in result.get("message", "") else 1)
"""
    child = None
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            child = subprocess.Popen(
                [sys.executable, "-c", code, str(path), str(signal), config.machine, config.repo_name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            deadline = time.monotonic() + 10
            while not signal.exists() and time.monotonic() < deadline and child.poll() is None:
                time.sleep(0.02)
            assert signal.exists() and child.poll() is None
            record = tracking.load_record(path)
            record.status = "finalizing"
            tracking.save_record(record, path)
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, stdout + stderr
        assert tracking.load_record(path).resources == []
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
