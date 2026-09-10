from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import finalize, tracking

WORKTREE_ID = "host-win-20260909-abcd"


def _record(tmp_path) -> tracking.WorktreeRecord:
    return tracking.WorktreeRecord(
        worktree_id=WORKTREE_ID,
        branch=f"worktree/{WORKTREE_ID}",
        worktree_path=str(tmp_path / "worktree"),
        repo="example",
        machine="host",
        platform="windows",
        started_at="2026-09-09T00:00:00+00:00",
        last_resumed_at="2026-09-09T00:00:00+00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
    )


def _configure(monkeypatch, tmp_path, outputs) -> None:
    repo = SimpleNamespace(worktree_root=str(tmp_path))
    monkeypatch.setattr(
        cli.cfg,
        "load_config",
        lambda: SimpleNamespace(default_repo=repo, repos={"example": repo}),
    )
    monkeypatch.setattr(cli.cfg, "tracking_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_resolve_worktree_id", lambda value: value)
    monkeypatch.setattr(cli, "_json_output", outputs.append)


def _args(action: str, **overrides):
    values = {
        "action": action,
        "worktree_id": WORKTREE_ID,
        "provider": None,
        "state": "active",
        "binding_revision": None,
        "blob_file": None,
        "if_match_revision": None,
        "operation": None,
        "reservation_token": None,
        "reservation_owner": "manager:test",
        "reservation_owner_pid": None,
        "reservation_owner_start_time": None,
        "lease_seconds": 300,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_execution_leg_set_get_clear_with_fencing(monkeypatch, tmp_path):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    blob_path = tmp_path / "blob.json"
    blob = {"session_id": "11111111-1111-1111-1111-111111111111"}
    blob_path.write_text(json.dumps(blob), encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "set",
        provider="ahp",
        binding_revision=1,
        blob_file=str(blob_path),
        if_match_revision=0,
    )) == 0
    assert outputs[-1]["execution_leg"]["blob"] == blob
    assert finalize._has_live_session(tracking.load_record(yaml_path))

    assert cli.cmd_execution_leg(_args("get")) == 0
    assert outputs[-1]["legacy"] is False
    assert outputs[-1]["execution_leg"]["provider"] == "ahp"

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]

    assert cli.cmd_execution_leg(_args(
        "clear",
        provider="ahp",
        if_match_revision=reserved_revision,
        reservation_token=token,
    )) == 0
    assert outputs[-1]["execution_leg"] is None
    assert tracking.derive_execution_leg(tracking.load_record(yaml_path)) is None


def test_execution_leg_rejects_stale_revision(monkeypatch, tmp_path):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        binding_revision=3,
        blob={"session_id": "existing"},
    )
    tracking.save_record(record, yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text("{}", encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    rc = cli.cmd_execution_leg(_args(
        "set",
        provider="ahp",
        binding_revision=4,
        blob_file=str(blob_path),
        if_match_revision=2,
    ))

    assert rc == 3
    assert "expected 2, found 3" in outputs[-1]["error"]
    assert tracking.load_record(yaml_path).execution_leg.binding_revision == 3


def test_execution_leg_get_translates_and_clear_removes_legacy(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.session_backend = tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="example-user",
        created_at="2026-09-09T00:00:01+00:00",
        last_seen_at="2026-09-09T00:00:02+00:00",
        binding_revision=2,
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args("get")) == 0
    assert outputs[-1]["legacy"] is True
    assert outputs[-1]["execution_leg"]["binding_revision"] == 2

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]

    assert cli.cmd_execution_leg(_args(
        "clear",
        provider="ahp",
        if_match_revision=reserved_revision,
        reservation_token=token,
    )) == 0
    loaded = tracking.load_record(yaml_path)
    assert loaded.session_backend is None
    assert tracking.derive_execution_leg(loaded) is None


def test_execution_leg_reservation_serializes_lifecycle_and_blocks_finalize(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
    )) == 0
    token = outputs[-1]["reservation_token"]
    assert outputs[-1]["reservation_owner"] == "manager:test"
    assert outputs[-1]["created_at"]
    assert outputs[-1]["expires_at"]
    reservation = json.loads(
        cli._execution_leg_reservation_path(yaml_path).read_text(encoding="utf-8")
    )
    assert reservation["owner"] == "manager:test"
    assert reservation["operation"] == "dispose"
    assert reservation["previous_execution_leg"]["state"] == "active"
    reserved = tracking.load_record(yaml_path).execution_leg
    assert reserved.state == "unknown"
    assert reserved.binding_revision == 4
    assert finalize._has_live_session(tracking.load_record(yaml_path))

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
    )) == 3
    assert "already reserved" in outputs[-1]["error"]

    assert cli.cmd_execution_leg(_args(
        "release",
        reservation_token=token,
    )) == 0
    restored = tracking.load_record(yaml_path).execution_leg
    assert restored.state == "active"
    assert restored.binding_revision == 5


def test_execution_leg_expired_reservation_can_be_taken_over_and_released(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
        reservation_owner="manager:crashed",
        lease_seconds=1,
    )) == 0
    stale_token = outputs[-1]["reservation_token"]
    reservation_path = cli._execution_leg_reservation_path(yaml_path)
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    reservation["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    reservation_path.write_text(json.dumps(reservation), encoding="utf-8")

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
        reservation_owner="manager:replacement",
    )) == 0
    replacement_token = outputs[-1]["reservation_token"]
    assert replacement_token != stale_token
    assert outputs[-1]["previous_execution_leg"]["state"] == "active"

    assert cli.cmd_execution_leg(_args(
        "release",
        reservation_token=stale_token,
    )) == 3
    assert "token mismatch" in outputs[-1]["error"]

    assert cli.cmd_execution_leg(_args(
        "release",
        reservation_token=replacement_token,
    )) == 0
    restored = tracking.load_record(yaml_path).execution_leg
    assert restored.state == "active"
    assert restored.blob == {"session_id": "session-1"}


def test_execution_leg_live_owner_cannot_be_preempted_after_expiry(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)
    owner_start = cli.locks.process_start_time(os.getpid())

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
        reservation_owner_pid=os.getpid(),
        reservation_owner_start_time=owner_start,
        lease_seconds=1,
    )) == 0
    reservation_path = cli._execution_leg_reservation_path(yaml_path)
    reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    reservation["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat(timespec="seconds")
    reservation_path.write_text(json.dumps(reservation), encoding="utf-8")

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
        reservation_owner="manager:replacement",
    )) == 3
    assert "already reserved" in outputs[-1]["error"]

    monkeypatch.setattr(cli.locks, "pid_alive", lambda _pid: False)
    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
        reservation_owner="manager:replacement",
    )) == 0


def test_execution_leg_renew_extends_lease_and_fences_stale_token(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
        lease_seconds=10,
    )) == 0
    token = outputs[-1]["reservation_token"]
    old_expiry = outputs[-1]["expires_at"]

    assert cli.cmd_execution_leg(_args(
        "renew",
        reservation_token=token,
        lease_seconds=300,
    )) == 0
    assert outputs[-1]["expires_at"] > old_expiry

    assert cli.cmd_execution_leg(_args(
        "renew",
        reservation_token="stale",
    )) == 3
    assert "token mismatch" in outputs[-1]["error"]


def test_execution_leg_reserve_sidecar_failure_never_publishes_unknown(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)
    monkeypatch.setattr(
        cli,
        "_write_execution_leg_reservation",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("sidecar publication failed")
        ),
    )

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
    )) == 3
    loaded = tracking.load_record(yaml_path).execution_leg
    assert loaded.state == "active"
    assert loaded.binding_revision == 3


def test_execution_leg_prepared_sidecar_recovers_unknown_reservation(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)
    real_write = cli._write_execution_leg_reservation
    writes = 0

    def fail_second_write(path, value):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("reserved publication failed")
        real_write(path, value)

    monkeypatch.setattr(cli, "_write_execution_leg_reservation", fail_second_write)
    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
        reservation_owner_pid=987654,
        reservation_owner_start_time="dead",
    )) == 3
    reserved = tracking.load_record(yaml_path).execution_leg
    assert reserved.state == "unknown"
    reservation_path = cli._execution_leg_reservation_path(yaml_path)
    assert json.loads(reservation_path.read_text(encoding="utf-8"))["phase"] == "prepared"

    monkeypatch.setattr(cli, "_write_execution_leg_reservation", real_write)
    monkeypatch.setattr(cli.locks, "pid_alive", lambda _pid: False)
    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
        reservation_owner="manager:replacement",
    )) == 0
    assert outputs[-1]["previous_execution_leg"]["state"] == "active"


def test_execution_leg_prepared_sidecar_recovers_before_record_publication(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)
    real_save = tracking._save_record_unlocked
    saves = 0

    def fail_first_save(*args, **kwargs):
        nonlocal saves
        saves += 1
        if saves == 1:
            raise OSError("record publication failed")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(tracking, "_save_record_unlocked", fail_first_save)
    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose",
        reservation_owner_pid=987654,
        reservation_owner_start_time="dead",
    )) == 3
    current = tracking.load_record(yaml_path).execution_leg
    assert current.state == "active"
    assert current.binding_revision == 3
    reservation_path = cli._execution_leg_reservation_path(yaml_path)
    assert json.loads(reservation_path.read_text(encoding="utf-8"))["phase"] == "prepared"

    monkeypatch.setattr(tracking, "_save_record_unlocked", real_save)
    monkeypatch.setattr(cli.locks, "pid_alive", lambda _pid: False)
    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
        reservation_owner="manager:replacement",
    )) == 0
    assert outputs[-1]["previous_execution_leg"]["state"] == "active"
    assert outputs[-1]["execution_leg"]["binding_revision"] == 4


@pytest.mark.parametrize("action", ["set", "release"])
def test_execution_leg_unlink_failure_is_idempotently_retried(
    monkeypatch,
    tmp_path,
    action,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"session-1"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]
    reservation_path = cli._execution_leg_reservation_path(yaml_path)
    real_unlink = Path.unlink
    failed = False

    def fail_once(path, *args, **kwargs):
        nonlocal failed
        if path == reservation_path and not failed:
            failed = True
            raise OSError("unlink failed")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    if action == "set":
        args = _args(
            "set",
            provider="ahp",
            binding_revision=reserved_revision + 1,
            blob_file=str(blob_path),
            if_match_revision=reserved_revision,
            reservation_token=token,
        )
    else:
        args = _args("release", reservation_token=token)

    assert cli.cmd_execution_leg(args) == 3
    assert reservation_path.exists()
    assert cli.cmd_execution_leg(args) == 0
    assert not reservation_path.exists()


def test_execution_leg_reserved_commit_requires_owning_token(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"session-1"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]

    assert cli.cmd_execution_leg(_args(
        "set",
        provider="ahp",
        binding_revision=reserved_revision + 1,
        blob_file=str(blob_path),
        if_match_revision=reserved_revision,
        reservation_token="wrong",
    )) == 3
    assert "token mismatch" in outputs[-1]["error"]

    assert cli.cmd_execution_leg(_args(
        "set",
        provider="ahp",
        binding_revision=reserved_revision + 1,
        blob_file=str(blob_path),
        if_match_revision=reserved_revision,
        reservation_token=token,
    )) == 0
    committed = tracking.load_record(yaml_path).execution_leg
    assert committed.state == "active"
    assert committed.binding_revision == reserved_revision + 1


@pytest.mark.parametrize("action", ["set", "clear"])
@pytest.mark.parametrize("current_state", ["active", "unknown"])
def test_execution_leg_rejects_unreserved_cross_provider_live_mutation(
    monkeypatch,
    tmp_path,
    action,
    current_state,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state=current_state,
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"replacement"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    args = _args(
        action,
        provider="other",
        binding_revision=4 if action == "set" else None,
        blob_file=str(blob_path) if action == "set" else None,
        if_match_revision=3,
    )
    assert cli.cmd_execution_leg(args) == 3
    assert "provider-owning reservation" in outputs[-1]["error"]
    persisted = tracking.load_record(yaml_path).execution_leg
    assert persisted.provider == "ahp"
    assert persisted.binding_revision == 3


def test_execution_leg_same_provider_live_mutation_uses_reservation(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"session-2"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="ensure",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]
    assert cli.cmd_execution_leg(_args(
        "set",
        provider="ahp",
        binding_revision=reserved_revision + 1,
        blob_file=str(blob_path),
        if_match_revision=reserved_revision,
        reservation_token=token,
    )) == 0
    persisted = tracking.load_record(yaml_path).execution_leg
    assert persisted.provider == "ahp"
    assert persisted.blob == {"session_id": "session-2"}


@pytest.mark.parametrize("action", ["set", "clear"])
def test_execution_leg_rejects_cross_provider_use_of_owning_reservation(
    monkeypatch,
    tmp_path,
    action,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="active",
        binding_revision=3,
        blob={"session_id": "session-1"},
    )
    tracking.save_record(record, yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"replacement"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="ahp",
        operation="dispose" if action == "clear" else "ensure",
    )) == 0
    token = outputs[-1]["reservation_token"]
    reserved_revision = outputs[-1]["execution_leg"]["binding_revision"]
    args = _args(
        action,
        provider="other",
        binding_revision=reserved_revision + 1 if action == "set" else None,
        blob_file=str(blob_path) if action == "set" else None,
        if_match_revision=reserved_revision,
        reservation_token=token,
    )

    assert cli.cmd_execution_leg(args) == 3
    assert "must match the owning reservation provider ahp" in (
        outputs[-1]["error"]
    )
    persisted = tracking.load_record(yaml_path).execution_leg
    assert persisted.provider == "ahp"
    assert persisted.state == "unknown"
    assert persisted.binding_revision == reserved_revision


@pytest.mark.parametrize("existing_state", [None, "disposed"])
def test_execution_leg_absent_or_disposed_allows_unreserved_set(
    monkeypatch,
    tmp_path,
    existing_state,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    current_revision = 0
    if existing_state is not None:
        current_revision = 3
        record.execution_leg = tracking.ExecutionLegBinding(
            provider="ahp",
            state=existing_state,
            binding_revision=current_revision,
            blob={"session_id": "old"},
        )
    tracking.save_record(record, yaml_path)
    blob_path = tmp_path / "blob.json"
    blob_path.write_text('{"session_id":"new"}', encoding="utf-8")
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "set",
        provider="other",
        binding_revision=current_revision + 1,
        blob_file=str(blob_path),
        if_match_revision=current_revision,
    )) == 0
    persisted = tracking.load_record(yaml_path).execution_leg
    assert persisted.provider == "other"
    assert persisted.state == "active"


@pytest.mark.parametrize("existing_state", [None, "disposed"])
def test_execution_leg_absent_or_disposed_allows_unreserved_clear(
    monkeypatch,
    tmp_path,
    existing_state,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    current_revision = 0
    if existing_state is not None:
        current_revision = 3
        record.execution_leg = tracking.ExecutionLegBinding(
            provider="ahp",
            state=existing_state,
            binding_revision=current_revision,
            blob={"session_id": "old"},
        )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "clear",
        provider="other",
        if_match_revision=current_revision,
    )) == 0
    assert tracking.derive_execution_leg(tracking.load_record(yaml_path)) is None


def test_execution_leg_disposed_allows_new_provider_reservation(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    record = _record(tmp_path)
    record.execution_leg = tracking.ExecutionLegBinding(
        provider="ahp",
        state="disposed",
        binding_revision=3,
        blob={"session_id": "old"},
    )
    tracking.save_record(record, yaml_path)
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args(
        "reserve",
        provider="other",
        operation="ensure",
    )) == 0
    assert outputs[-1]["previous_execution_leg"]["state"] == "disposed"
    persisted = tracking.load_record(yaml_path).execution_leg
    assert persisted.provider == "other"
    assert persisted.state == "unknown"
    assert persisted.binding_revision == 4


def test_worktree_json_projects_legacy_backend_as_execution_leg(tmp_path):
    record = _record(tmp_path)
    record.session_backend = tracking.SessionBackendBinding(
        kind="ahp",
        endpoint_url="ws://127.0.0.1:8765",
        session_id="11111111-1111-1111-1111-111111111111",
        protocol_version="0.7.0",
        auth_account="example-user",
        created_at="2026-09-09T00:00:01+00:00",
        last_seen_at="2026-09-09T00:00:02+00:00",
        state="active",
        binding_revision=2,
    )

    payload = cli._worktree_to_dict(record)

    assert payload["session_backend"] == record.session_backend.to_dict()
    assert payload["session_ahp_live"] is True
    assert payload["execution_leg"] == (
        tracking.derive_execution_leg(record).to_dict()
    )
    assert payload["execution_leg_live"] is True


def test_execution_leg_get_rejects_opaque_legacy_backend(
    monkeypatch,
    tmp_path,
):
    yaml_path = tmp_path / f"{WORKTREE_ID}.yaml"
    tracking.save_record(_record(tmp_path), yaml_path)
    with yaml_path.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n".join([
                "",
            "session_backend:",
            "  version: 99",
            "  kind: future",
            "",
            ])
        )
    outputs = []
    _configure(monkeypatch, tmp_path, outputs)

    assert cli.cmd_execution_leg(_args("get")) == 3
    assert "unsupported hosted-session schema" in outputs[-1]["error"]
