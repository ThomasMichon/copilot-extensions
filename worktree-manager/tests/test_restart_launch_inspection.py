"""Manager composes immutable frozen attempts with read-only owner outcomes."""

from __future__ import annotations

import json

import pytest

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
        return {
            "schema": "native-restart-launch", "version": 1,
            "request": json.loads(args[4]), "state": "mux-created",
            "seed_acknowledged": False, "restored": False, "automatic_eligible": False,
        }

    monkeypatch.setattr(engine_client, "run_json", run)
    return tmp_path, row, calls


@pytest.mark.parametrize("state", ["not-reserved", "launch-uncertain", "mux-created"])
def test_explicit_inspection_retains_launch_vs_recovery_distinction(subject, monkeypatch, capsys, state):
    root, row, calls = subject
    original = engine_client.run_json
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: {
        **original(*a, **k), "state": state,
    })
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["launch_receipt"]["state"] == state
    assert result["automatic_eligible"] is False
    assert "not acknowledged" in result["blocker"]
    assert row["state"] == "frozen"
    assert list(root.iterdir()) == []
    project, args, kwargs = calls[0]
    assert project == "example"
    assert args[:4] == ["embody", "--worktree-id", "exact-worktree", "--restart-request"]
    assert args[5:] == ["--restart-receipt", "--json"]
    request = json.loads(args[4])
    assert request["request_id"] == ATTEMPT
    assert request["expected"]["native_session_id"] == NATIVE
    assert request["source_epoch"]["boot_id"] == "previous-boot"
    assert request["native_incarnation"] == "42:100"
    assert kwargs == {
        "timeout": 4,
        "environment": {"WORKTREE_NO_UPDATE": "1", "WORKTREE_NO_RECONCILE": "1"},
    }


@pytest.mark.parametrize("mutation", [
    {"schema": "legacy"}, {"version": True}, {"state": "acknowledged"},
    {"state": []}, {"seed_acknowledged": True}, {"restored": True},
    {"automatic_eligible": True}, {"request": {}}, {"unexpected": "legacy"},
])
def test_malformed_or_authority_shaped_response_never_becomes_recovery(subject, monkeypatch, capsys, mutation):
    original = engine_client.run_json
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: {
        **original(*a, **k), **mutation,
    })
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "unconfirmed launch receipt" in result["blocker"]
    assert "launch_receipt" not in result


def test_foreign_attempt_target_never_contacts_owner(subject, capsys):
    _, row, calls = subject
    row["target_epoch"] = {**vars(EPOCH), "boot_id": "different"}
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    assert calls == []
    assert "target epoch" in json.loads(capsys.readouterr().out)["blocker"]


def test_owner_unavailable_is_not_an_absent_receipt(subject, monkeypatch, capsys):
    def failed(*args, **kwargs):
        raise engine_client.EngineError("owner unavailable")

    monkeypatch.setattr(engine_client, "run_json", failed)
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "owner unavailable" in result["blocker"]
    assert "launch_receipt" not in result
