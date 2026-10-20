"""Explicit survivor snapshots never mutate attempts or authorize adoption."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from worktree_manager import engine_client, restart_observer, self_install
from worktree_manager.__main__ import main
from worktree_manager.restart_attempts import RestartAttempts


ATTEMPT = "00000000-0000-4000-8000-000000000005"
NATIVE = "00000000-0000-4000-8000-000000000001"
EPOCH = restart_observer.HostEpoch("linux", "b" * 32, "current-boot")


@pytest.fixture
def subject(tmp_path, monkeypatch):
    row = {
        "attempt_id": ATTEMPT, "state": "frozen", "target_epoch": vars(EPOCH),
        "last_positive": {
            "project": "example", "worktree_id": "exact-worktree",
            "worktree_path": "/example/checkout", "native_session_id": NATIVE,
            "worktree_incarnation": "0123456789abcdef", "head_revision": 5,
            "owner_revision": 8, "activation_ordinal": 2,
            "source_identity": "native-source-v1:" + "a" * 64,
            "platform": "linux", "environment_id": EPOCH.environment_id,
            "boot_id": "previous-boot", "native_incarnation": "42:100",
        },
    }
    monkeypatch.setattr(self_install, "default_root", lambda: tmp_path)
    monkeypatch.setattr(restart_observer, "native_epoch", lambda: EPOCH)
    monkeypatch.setattr(RestartAttempts, "snapshot", lambda self: [row])
    calls = []

    def run(project, args, **kwargs):
        calls.append((project, args, kwargs))
        source = json.dumps(
            ["linux", EPOCH.environment_id, EPOCH.boot_id, NATIVE, 101, "456"],
            separators=(",", ":"),
        )
        return {
            "schema": "native-restart-survivor", "version": 1, "request": json.loads(args[2]),
            "survivor_confirmed": True, "reason": "original-native-survivor-confirmed",
            "hosting": {
                "native_session_id": NATIVE, "target_epoch": vars(EPOCH),
                "head_revision": 5, "owner_revision": 10, "activation_ordinal": 3,
                "source_identity": "native-source-v1:" + hashlib.sha256(source.encode()).hexdigest(),
                "native_incarnation": "101:456", "session_incarnation": "$1:100",
            },
            "launch_authorized": False, "reuse_authorized": False,
            "seed_acknowledged": False, "restored": False, "automatic_eligible": False,
        }

    monkeypatch.setattr(engine_client, "run_json", run)
    return tmp_path, row, calls


def test_explicit_current_survivor_is_not_launch_history_or_reuse_permission(subject, capsys):
    root, row, calls = subject
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["survivor_check"]["survivor_confirmed"] is True
    assert result["survivor_check"]["reuse_authorized"] is False
    assert result["survivor_check"]["seed_acknowledged"] is False
    assert "not adoption" in result["blocker"]
    assert "launch_receipt" not in result and row["state"] == "frozen"
    assert list(root.iterdir()) == []
    project, args, kwargs = calls[0]
    assert project == "example" and args[:2] == ["session-role", "--restart-survivor"]
    assert args[-1] == "--json"
    assert json.loads(args[2])["request_id"] == ATTEMPT
    assert kwargs == {
        "timeout": 4,
        "environment": {"WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1"},
    }


@pytest.mark.parametrize("mutation", [
    {"schema": "legacy"}, {"version": True}, {"request": {}},
    {"survivor_confirmed": "yes"}, {"launch_authorized": True}, {"reuse_authorized": True},
    {"seed_acknowledged": True}, {"restored": True}, {"automatic_eligible": True},
    {"reason": "other"}, {"hosting": {}}, {"extra": True},
])
def test_wrong_typed_or_authority_shaped_response_is_visible(subject, monkeypatch, capsys, mutation):
    original = engine_client.run_json
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **kw: {
        **original(*a, **kw), **mutation,
    })
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "unconfirmed survivor" in result["blocker"] and "survivor_check" not in result


@pytest.mark.parametrize("mutation", [
    {"native_session_id": ATTEMPT}, {"target_epoch": {**vars(EPOCH), "boot_id": "stale"}},
    {"head_revision": True}, {"owner_revision": 8}, {"activation_ordinal": 2},
    {"source_identity": "native-source-v1:" + "c" * 64},
    {"native_incarnation": "101:stale"}, {"session_incarnation": "unsafe\n"},
])
def test_current_execution_must_match_exact_target_and_original(subject, monkeypatch, capsys, mutation):
    original = engine_client.run_json

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result["hosting"].update(mutation)
        return result

    monkeypatch.setattr(engine_client, "run_json", changed)
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    assert "unconfirmed survivor" in json.loads(capsys.readouterr().out)["blocker"]


def test_negative_unknown_is_not_permission_to_launch(subject, monkeypatch, capsys):
    original = engine_client.run_json

    def unknown(*args, **kwargs):
        result = original(*args, **kwargs)
        result.pop("hosting")
        return {**result, "survivor_confirmed": False, "reason": "native-head-unknown"}

    monkeypatch.setattr(engine_client, "run_json", unknown)
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["survivor_check"]["reason"] == "native-head-unknown"
    assert result["survivor_check"]["launch_authorized"] is False


def test_negative_flag_with_positive_reason_is_not_confirmed_evidence(subject, monkeypatch, capsys):
    original = engine_client.run_json

    def inconsistent(*args, **kwargs):
        result = original(*args, **kwargs)
        result.pop("hosting")
        return {**result, "survivor_confirmed": False}

    monkeypatch.setattr(engine_client, "run_json", inconsistent)
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    assert "unconfirmed survivor" in json.loads(capsys.readouterr().out)["blocker"]


def test_stale_target_never_probes_or_rebinds_survivor(subject, capsys):
    _, row, calls = subject
    row["target_epoch"] = {**vars(EPOCH), "boot_id": "old-target"}
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    assert "target epoch" in json.loads(capsys.readouterr().out)["blocker"]
    assert calls == []


def test_default_off_ordinary_read_never_probes_survivor(subject, capsys):
    assert main(["mux-daemon", "recovery-evidence"]) == 0
    assert subject[2] == []


def test_owner_failure_is_not_a_negative_survivor_snapshot(subject, monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise engine_client.EngineError("owner unavailable")

    monkeypatch.setattr(engine_client, "run_json", unavailable)
    assert main(["mux-daemon", "recovery-evidence", "--check-survivor", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "owner unavailable" in result["blocker"] and "survivor_check" not in result


def test_real_attempt_database_and_engine_child_remain_unchanged(tmp_path):
    from worktree_manager.restart_evidence import PositiveEvidence, RecoveryJournal

    evidence = PositiveEvidence(
        project="example", worktree_id="exact-worktree", worktree_path="/example/checkout",
        native_session_id=NATIVE, native_incarnation="42:100", binding_proof="inuse-hold-v1",
        owner_revision=8, session_incarnation="mux-old", mapping_revision=1, platform="linux",
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
from pathlib import Path
from worktree_manager import engine_client
from worktree_manager.restart_attempts import RestartAttempts
from worktree_manager.restart_observer import HostEpoch
from worktree_manager.restart_owner_check import inspect_survivor
engine = '''
import json, os, sys
assert os.environ["WORKTREE_NO_UPDATE"] == os.environ["WORKTREE_NO_RECONCILE"] == "1"
assert sys.argv[1:3] == ["--project", "example"]
request = json.loads(sys.argv[sys.argv.index("--restart-survivor") + 1])
print(json.dumps({
    "schema": "native-restart-survivor", "version": 1, "request": request,
    "survivor_confirmed": False, "reason": "native-head-unknown",
    "launch_authorized": False, "reuse_authorized": False,
    "seed_acknowledged": False, "restored": False, "automatic_eligible": False,
}))
'''
engine_client.set_engine_command([sys.executable, "-c", engine])
result = inspect_survivor(sys.argv[2], RestartAttempts(Path(sys.argv[1])).snapshot(),
                         HostEpoch(**json.loads(sys.argv[3])))
print(json.dumps(result))
"""
    process = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), frozen[0]["attempt_id"], json.dumps(vars(EPOCH))],
        capture_output=True, text=True, timeout=30, check=False, **no_window_kwargs(),
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["reason"] == "native-head-unknown"
    assert attempts.path.read_bytes() == before
    assert attempts.snapshot() == frozen
