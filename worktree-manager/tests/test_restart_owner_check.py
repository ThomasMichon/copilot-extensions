"""Frozen attempt inspection crosses the real owner API without granting launch."""

from __future__ import annotations

import json
import importlib
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import engine_client, restart_observer, self_install
from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal
from worktree_manager.restart_attempts import RestartAttempts
from worktree_manager.__main__ import main


ATTEMPT = "00000000-0000-4000-8000-000000000005"
NATIVE = "00000000-0000-4000-8000-000000000001"
EPOCH = restart_observer.HostEpoch("linux", "b" * 32, "current-boot")


@pytest.fixture
def owner_check(tmp_path, monkeypatch):
    importlib.import_module("worktree_manager.restart_owner_check")
    from worktree_manager.restart_attempts import RestartAttempts

    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: EPOCH)
    row = {
        "attempt_id": ATTEMPT, "state": "frozen",
        "target_epoch": vars(EPOCH), "automatic_eligible": False,
        "last_positive": {
            "project": "example", "worktree_id": "exact-worktree",
            "worktree_path": "/example/checkout", "native_session_id": NATIVE,
            "worktree_incarnation": "0123456789abcdef", "head_revision": 5,
            "owner_revision": 8, "activation_ordinal": 2,
            "source_identity": "native-source-v1:" + "a" * 64,
        },
    }
    monkeypatch.setattr(RestartAttempts, "snapshot", lambda self: [row])
    calls = []

    def run(project, args, **kwargs):
        calls.append((project, args, kwargs))
        return {
            "schema": "native-restart-worktree-check", "version": 1,
            "expected": json.loads(args[2]), "worktree_qualified": True,
            "launch_authorized": False, "automatic_eligible": False,
            "reason": "current-worktree-matches",
        }

    monkeypatch.setattr(engine_client, "run_json", run)
    return tmp_path, row, calls


def test_explicit_attempt_check_uses_owner_process_and_remains_blocked(owner_check, capsys):
    root, row, calls = owner_check
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["owner_check"]["worktree_qualified"] is True
    assert result["owner_check"]["launch_authorized"] is False
    assert "task policy" in result["blocker"]
    assert result["automatic_eligible"] is False
    project, args, kwargs = calls[0]
    assert project == "example"
    assert args[:2] == ["session-role", "--restart-check"]
    assert json.loads(args[2])["native_session_id"] == NATIVE
    assert kwargs["environment"] == {
        "WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1",
    }
    assert kwargs["timeout"] == 4
    assert list(root.iterdir()) == []


def test_ordinary_inspection_never_calls_owner(owner_check, capsys):
    root, row, calls = owner_check
    assert main(["mux-daemon", "recovery-evidence"]) == 0
    assert calls == []


@pytest.mark.parametrize("mutation", [
    {"schema": "legacy"}, {"version": True}, {"version": 2}, {"expected": {}},
    {"launch_authorized": True}, {"automatic_eligible": True},
    {"worktree_qualified": "yes"}, {"reason": ""},
    {"reason": "paused"},
])
def test_malformed_or_different_owner_proof_blocks(owner_check, monkeypatch, capsys, mutation):
    root, row, calls = owner_check
    original = engine_client.run_json
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: {
        **original(*a, **k), **mutation,
    })
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "unconfirmed owner response" in result["blocker"]
    assert "owner_check" not in result


def test_foreign_target_epoch_never_calls_owner(owner_check, capsys):
    root, row, calls = owner_check
    row["target_epoch"] = {**vars(EPOCH), "boot_id": "old-target"}
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "target epoch" in result["blocker"]
    assert calls == []


def test_absent_attempt_never_calls_owner(owner_check, capsys):
    root, row, calls = owner_check
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", NATIVE]) == 1
    assert calls == []
    assert "not found" in json.loads(capsys.readouterr().out)["blocker"]


def test_owner_unavailable_preserves_visible_blocker(owner_check, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise engine_client.EngineError("owner timed out")

    monkeypatch.setattr(engine_client, "run_json", failed)
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", ATTEMPT]) == 1
    assert "owner timed out" in json.loads(capsys.readouterr().out)["blocker"]


def test_response_binding_preserves_json_types(owner_check, monkeypatch, capsys):
    root, row, calls = owner_check
    row["last_positive"]["head_revision"] = 1
    original = engine_client.run_json

    def wrong_type(*args, **kwargs):
        response = original(*args, **kwargs)
        response["expected"]["head_revision"] = True
        return response

    monkeypatch.setattr(engine_client, "run_json", wrong_type)
    assert main(["mux-daemon", "recovery-evidence", "--check-attempt", ATTEMPT]) == 1
    assert "unconfirmed owner response" in json.loads(capsys.readouterr().out)["blocker"]


def test_real_frozen_storage_and_engine_process_remain_read_only(tmp_path):
    evidence = PositiveEvidence(
        project="example", worktree_id="exact-worktree", worktree_path="/example/checkout",
        native_session_id=NATIVE, native_incarnation="101:100", binding_proof="inuse-hold-v1",
        owner_revision=8, session_incarnation="mux-one", mapping_revision=1, platform="linux",
        environment_id=EPOCH.environment_id, boot_id="previous-boot",
        observed_at=100.0, observation_sequence=1, activation_ordinal=2,
        source_identity="native-source-v1:" + "a" * 64, lifecycle_qualified=True,
        worktree_incarnation="0123456789abcdef", head_revision=5,
    )
    journal = RecoveryJournal(tmp_path)
    journal.observe(evidence)
    attempts = RestartAttempts(tmp_path)
    frozen = attempts.freeze(journal.snapshot(), EPOCH)
    before = attempts.path.read_bytes()
    code = """
import json, sys
from dataclasses import asdict
from pathlib import Path
from worktree_manager import engine_client
from worktree_manager.restart_attempts import RestartAttempts
from worktree_manager.restart_observer import HostEpoch
from worktree_manager.restart_owner_check import inspect_attempt
engine = '''
import json, os, sys
assert os.environ["WORKTREE_NO_UPDATE"] == os.environ["WORKTREE_NO_RECONCILE"] == "1"
expected = json.loads(sys.argv[sys.argv.index("--restart-check") + 1])
print(json.dumps({
    "schema": "native-restart-worktree-check", "version": 1, "expected": expected,
    "worktree_qualified": True, "launch_authorized": False, "automatic_eligible": False,
    "reason": "current-worktree-matches",
}))
'''
engine_client.set_engine_command([sys.executable, "-c", engine])
epoch = HostEpoch(**json.loads(sys.argv[3]))
result = inspect_attempt(sys.argv[2], RestartAttempts(Path(sys.argv[1])).snapshot(), epoch)
print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), frozen[0]["attempt_id"], json.dumps(vars(EPOCH))],
        capture_output=True, text=True, timeout=30, check=False, **no_window_kwargs(),
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["worktree_qualified"] is True
    assert attempts.path.read_bytes() == before
    assert attempts.snapshot() == frozen
