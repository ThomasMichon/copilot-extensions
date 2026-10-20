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


@pytest.mark.parametrize("change", [
    {"environment_id": "d" * 32}, {"platform": "wsl"},
])
def test_foreign_attempt_target_never_contacts_owner(subject, capsys, change):
    _, row, calls = subject
    row["target_epoch"] = {**vars(EPOCH), **change}
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


def acknowledged(original):
    return {
        **original, "version": 2, "state": "seed-acknowledged", "seed_acknowledged": True,
        "acknowledgement": {
            "native_session_id": NATIVE,
            "target_epoch": original["request"]["expected"]["target_epoch"],
            "source_identity": "native-source-v1:" + "c" * 64,
            "native_incarnation": "99:456", "session_incarnation": "$1:100",
            "head_revision": 5, "owner_revision": 9, "activation_ordinal": 3,
            "status_reconciled": True,
        },
    }


def test_manager_composes_ack_without_claiming_automatic_or_completed_recovery(subject, monkeypatch, capsys):
    original = engine_client.run_json
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: acknowledged(original(*a, **k)))
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["launch_receipt"]["seed_acknowledged"] is True
    assert result["launch_receipt"]["acknowledgement"]["session_incarnation"] == "$1:100"
    assert result["launch_receipt"]["restored"] is False
    assert result["automatic_eligible"] is False
    assert "qualification" in result["blocker"]


def test_historical_launch_inspection_keeps_frozen_target_not_current_boot(subject, monkeypatch, capsys):
    root, row, calls = subject
    original = engine_client.run_json
    monkeypatch.setattr(
        restart_observer, "native_epoch",
        lambda: restart_observer.HostEpoch("linux", EPOCH.environment_id, "later-boot"),
    )
    monkeypatch.setattr(engine_client, "run_json", lambda *a, **k: acknowledged(original(*a, **k)))
    before = json.dumps(row, sort_keys=True)
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["launch_receipt"]["seed_acknowledged"] is True
    request = json.loads(calls[0][1][4])
    assert request["expected"]["target_epoch"] == vars(EPOCH)
    assert result["launch_receipt"]["acknowledgement"]["target_epoch"] == vars(EPOCH)
    assert result["automatic_eligible"] is False
    assert "qualification" in result["blocker"]
    assert json.dumps(row, sort_keys=True) == before
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("mutation", [
    {"native_session_id": ATTEMPT}, {"target_epoch": {}}, {"status_reconciled": False},
    {"owner_revision": True}, {"owner_revision": 8}, {"head_revision": 6},
    {"activation_ordinal": 2}, {"source_identity": "unknown"},
    {"native_incarnation": "unknown"}, {"session_incarnation": ""},
    {"session_incarnation": "$1:100\n"}, {"session_incarnation": "$1:100;extra"}, {"extra": True},
])
def test_manager_refuses_ack_not_bound_to_exact_frozen_identity(subject, monkeypatch, capsys, mutation):
    original = engine_client.run_json

    def run(*args, **kwargs):
        result = acknowledged(original(*args, **kwargs))
        result["acknowledgement"].update(mutation)
        return result

    monkeypatch.setattr(engine_client, "run_json", run)
    assert main(["mux-daemon", "recovery-evidence", "--check-launch", ATTEMPT]) == 1
    result = json.loads(capsys.readouterr().out)
    assert "unconfirmed launch receipt" in result["blocker"]
    assert "launch_receipt" not in result
