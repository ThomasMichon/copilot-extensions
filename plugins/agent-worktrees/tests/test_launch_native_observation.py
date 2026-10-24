"""Exact, original-source evidence stays distinct from arbitrary prompt acceptance."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_worktrees import config as cfg, config_authority
from agent_worktrees import launch_attempt, launch_execution as execution
from agent_worktrees import launch_native_observation as native, launch_seed_exec as launcher
from agent_worktrees import native_lifecycle_source as source, native_source_context as context
from agent_worktrees import tracking, tracking_write

pytestmark = pytest.mark.guard
SID = "00000000-0000-4000-8000-000000000001"
EPOCH = {"platform": "linux", "environment_id": "e" * 32, "boot_id": SID}


@pytest.fixture
def case(tmp_path, monkeypatch, monkeypatch_config):
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "demo")
    authority = replace(cfg.load_project_config("demo"), platform=cfg.detect_platform())
    monkeypatch.setattr(config_authority, "load_project_authority_config", lambda *a, **kw: authority)
    project = cfg.project_dir("demo")
    project.mkdir(parents=True, exist_ok=True)
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    path = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(checkout), "demo", "test",
        cfg.detect_platform(), cfg.tracking_dir(),
    ).yaml_path
    monkeypatch.setattr(context, "epoch", lambda: dict(EPOCH))
    monkeypatch.setattr(source, "session_process_owns_hold", lambda sid, pid, birth:
                        sid == SID and pid == 102 and birth == "200")
    witness = {
        "version": 1, "qualified": True, **EPOCH, "ancestors": [
            {"pid": 102, "start_time": "200"}, {"pid": 101, "start_time": "100"},
        ],
    }
    token = source.start_identity(SID, witness)
    assert token
    tracking.register_session("wt-a", SID, source_identity=token)
    assert tracking.load_record(path).session_entry(SID)
    receipt = execution.Execution(path, execution.Correlation(uuid4().hex, uuid4().hex))
    receipt.append("execution-requested")
    hint = receipt.environment(invoke=True)[execution.NATIVE_HINT_ENV]
    payload = {"_agentWorktrees": {"environment": {execution.NATIVE_HINT_ENV: hint}, "nativeSource": witness}}
    return SimpleNamespace(
        path=path, receipt=receipt, witness=witness, token=token, payload=payload,
        attempt=receipt.subject["attempt_id"], operation=receipt.subject["operation_id"],
    )


def _observe(case):
    native.observe_registration(
        case.path, SID, case.token, payload=case.payload, witness=case.witness, resident=True,
    )


def _join(case):
    return native.joined(case.path.parent, case.attempt, case.operation)


def _spawn(case, **changes):
    case.receipt.append("process-spawned", **{
        "pid": 101, "process_start_time": "100", "boundary": "backend-command", **changes,
    })


def test_observation_before_spawn_receipt_joins_later_without_replay(case):
    _observe(case)
    assert _join(case) == {"status": "unknown", "reason": "backend-process-unconfirmed"}
    before = native.read(case.path.parent, case.attempt, case.operation)
    _spawn(case)
    assert _join(case) == {
        "status": "native-session-observed", "native_session_id": SID,
        "source_identity": case.token, "prompt_acceptance": "unknown",
    }
    assert native.read(case.path.parent, case.attempt, case.operation) == before
    serialized = json.dumps(before)
    assert "environment" not in before and "prompt" not in serialized


def test_exact_backend_can_itself_own_the_native_hold(case):
    _spawn(case, pid=102, process_start_time="200")
    _observe(case)
    assert _join(case)["status"] == "native-session-observed"


@pytest.mark.parametrize("changes", [
    {"pid": 999}, {"process_start_time": "101"}, {"process_start_time": None},
    {"boundary": "launcher-command"},
])
def test_pid_birth_and_backend_boundary_are_not_optimistic_matches(case, changes):
    _spawn(case, **changes)
    _observe(case)
    assert _join(case)["status"] == "unknown"


def test_native_source_cannot_be_above_the_launched_backend(case):
    case.witness["ancestors"].reverse()
    case.payload["_agentWorktrees"]["nativeSource"] = case.witness
    _spawn(case)
    _observe(case)
    assert _join(case)["reason"] == "backend-ancestry-unconfirmed"


def test_historical_join_does_not_recapture_a_new_boot_or_native_hold(case, monkeypatch):
    _spawn(case)
    _observe(case)
    monkeypatch.setattr(context, "epoch", lambda: pytest.fail("historical epoch recaptured"))
    monkeypatch.setattr(source, "start_identity", lambda *a: pytest.fail("historical session acquired"))
    assert _join(case)["status"] == "native-session-observed"


@pytest.mark.parametrize("field,value", [
    ("native_epoch", None),
    ("native_epoch", {**EPOCH, "boot_id": "00000000-0000-4000-8000-000000000002"}),
    ("native_epoch", {**EPOCH, "environment_id": "f" * 32}),
    ("creation_nonce", "replacement"),
    ("resolution_id", "f" * 32),
])
def test_same_pid_and_birth_cannot_join_foreign_execution_identity(case, field, value):
    _spawn(case)
    _observe(case)
    path = launch_attempt._path(case.path.parent, case.attempt, case.operation, execution=True)
    saved = execution.read(case.path.parent, case.attempt, case.operation)
    saved[field] = value
    launch_attempt._write(path, saved)
    assert _join(case)["reason"] == "native-execution-identity-unconfirmed"


def test_legacy_execution_remains_readable_but_never_epoch_qualified(case):
    _spawn(case)
    _observe(case)
    path = launch_attempt._path(case.path.parent, case.attempt, case.operation, execution=True)
    saved = execution.read(case.path.parent, case.attempt, case.operation)
    del saved["native_epoch"]
    saved["version"] = 1
    launch_attempt._write(path, saved)
    assert execution.read(case.path.parent, case.attempt, case.operation)["version"] == 1
    assert _join(case)["status"] == "unknown"


def test_missing_observation_does_not_create_or_repair_state(case):
    before = sorted(p.name for p in case.path.parent.rglob("*"))
    assert _join(case)["reason"] == "native-observation-missing"
    assert sorted(p.name for p in case.path.parent.rglob("*")) == before


def test_missing_execution_keeps_existing_observation_unknown_without_writes(case):
    _observe(case)
    observation = native.read(case.path.parent, case.attempt, case.operation)
    launch_attempt._path(case.path.parent, case.attempt, case.operation, execution=True).unlink()
    before = sorted(p.name for p in case.path.parent.rglob("*"))
    assert _join(case) == {"status": "unknown", "reason": "execution-receipt-missing"}
    assert native.read(case.path.parent, case.attempt, case.operation) == observation
    assert sorted(p.name for p in case.path.parent.rglob("*")) == before


def test_entirely_missing_attempt_is_unknown_and_never_created(tmp_path):
    directory = tmp_path / "absent"
    assert native.joined(directory, uuid4().hex, uuid4().hex) == {
        "status": "unknown", "reason": "execution-receipt-missing",
    }
    assert not directory.exists()


def test_corrupt_execution_still_raises_instead_of_becoming_unknown(case):
    path = launch_attempt._path(case.path.parent, case.attempt, case.operation, execution=True)
    path.write_text('{"schema":"foreign"}', encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        _join(case)
    assert path.read_bytes() == before


def test_resident_never_uses_ambient_execution_hint(case, monkeypatch):
    monkeypatch.setenv(execution.NATIVE_HINT_ENV, case.receipt.environment(invoke=True)[execution.NATIVE_HINT_ENV])
    case.payload = {"_agentWorktrees": {"nativeSource": case.witness}}
    _observe(case)
    assert _join(case)["reason"] == "native-observation-missing"


@pytest.mark.parametrize("damage", ["hint", "source", "registration", "nonce", "hold", "epoch"])
def test_hint_alone_is_not_native_ownership_or_canonical_registration(case, monkeypatch, capsys, damage):
    if damage == "hint":
        case.payload["_agentWorktrees"]["environment"][execution.NATIVE_HINT_ENV] = '{"prompt":"private fixture"}'
    elif damage == "source":
        case.token = None
    elif damage == "registration":
        record = tracking.load_record(case.path)
        record.sessions = []
        tracking.save_record(record, case.path)
    elif damage == "nonce":
        record = tracking.load_record(case.path)
        record.creation_nonce = "replacement"
        tracking.save_record(record, case.path)
    elif damage == "hold":
        monkeypatch.setattr(source, "session_process_owns_hold", lambda *a: False)
    else:
        monkeypatch.setattr(context, "epoch", lambda: {**EPOCH, "environment_id": "f" * 32})
    _observe(case)
    assert _join(case)["reason"] == "native-observation-missing"
    captured = capsys.readouterr()
    error = captured.out + captured.err
    assert "unconfirmed" in error and "private fixture" not in error


def test_duplicate_is_idempotent_but_changed_observation_never_overwrites(case, capsys):
    _observe(case)
    before = native.read(case.path.parent, case.attempt, case.operation)
    _observe(case)
    assert native.read(case.path.parent, case.attempt, case.operation) == before
    case.witness["ancestors"].append({"pid": 1, "start_time": "0"})
    _observe(case)
    assert "unconfirmed" in capsys.readouterr().err
    assert native.read(case.path.parent, case.attempt, case.operation) == before


def test_persistence_uncertainty_does_not_fail_registration_or_replay(case, monkeypatch, capsys):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        raise tracking_write.AmbiguousWriteOutcome("private fixture")
    monkeypatch.setattr(native, "_dispatch", fail)
    _observe(case)
    assert len(calls) == 1 and tracking.load_record(case.path).session_entry(SID)
    assert "private fixture" not in capsys.readouterr().err


@pytest.mark.parametrize("damage", [
    {"native_session_id": None}, {"native_birth": "invalid"}, {"native_pid": True},
    {"native_epoch": None}, {"worktree_id": "..\\outside"}, {"ancestors": []},
    {"unexpected": "field"}, {"version": True},
])
def test_corrupt_observation_is_a_visible_error_not_unknown_success(case, damage):
    _observe(case)
    value = native.read(case.path.parent, case.attempt, case.operation)
    path = launch_attempt._path(case.path.parent, case.attempt, case.operation, native=True)
    path.write_text(json.dumps({**value, **damage}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        _join(case)
    assert path.read_bytes() == before


def test_linked_native_observation_is_never_read_or_replaced(case, capsys):
    _observe(case)
    path = launch_attempt._path(case.path.parent, case.attempt, case.operation, native=True)
    other = path.with_suffix(".alias")
    os.link(path, other)
    before = other.read_bytes()
    with pytest.raises(ValueError):
        _join(case)
    _observe(case)
    assert "unconfirmed" in capsys.readouterr().err and other.read_bytes() == before


def test_only_backend_child_gets_exact_hint_and_parent_environment_is_unchanged(case, monkeypatch):
    monkeypatch.setenv(execution.NATIVE_HINT_ENV, "inherited-stale-hint")
    expected = {name: case.receipt.subject[name] for name in ("attempt_id", "resolution_id", "operation_id")}
    assert json.loads(case.receipt.environment(invoke=True)[execution.NATIVE_HINT_ENV]) == expected
    assert execution.NATIVE_HINT_ENV not in case.receipt.environment(invoke=False)
    assert os.environ[execution.NATIVE_HINT_ENV] == "inherited-stale-hint"


def test_actual_popen_boundary_gets_hint_only_for_explicit_execution(case, monkeypatch):
    from agent_worktrees.sessions import LiveVerdict

    monkeypatch.setattr(launcher.sessions, "verify_worktree_active", lambda *a: LiveVerdict())
    monkeypatch.setenv(execution.NATIVE_HINT_ENV, "inherited-stale-hint")
    environments = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw:
                        environments.append(kw["env"]) or SimpleNamespace(pid=os.getpid(), wait=lambda: 0))
    assert launcher.launch(
        case.path, ["copilot"], invoke=True,
        correlation=execution.Correlation(case.attempt, case.receipt.subject["resolution_id"]),
    ) == 0
    assert json.loads(environments[-1][execution.NATIVE_HINT_ENV])["attempt_id"] == case.attempt
    assert launcher.launch(case.path, ["copilot"], invoke=True) == 0
    assert execution.NATIVE_HINT_ENV not in environments[-1]
    assert os.environ[execution.NATIVE_HINT_ENV] == "inherited-stale-hint"


def test_source_registration_supplies_original_witness_only_after_success(case, monkeypatch):
    from agent_worktrees import __main__ as main, session_binding_cli

    seen = []
    monkeypatch.setattr(session_binding_cli, "_spawn_status_updater", lambda *a: None)
    monkeypatch.setattr(native, "observe_registration", lambda *a, **kw: seen.append((a, kw)))
    args = SimpleNamespace(
        session_id=SID, worktree_id="wt-a", cwd=str(case.path.parent),
        hook_payload=case.payload, resident_environment=True,
    )
    assert main.cmd_register_session(args) == 0
    assert len(seen) == 1 and seen[0][0][1:3] == (SID, case.token)
    assert seen[0][1]["witness"] is case.witness and seen[0][1]["resident"] is True
    monkeypatch.setattr(tracking, "register_session", lambda *a, **kw:
                        (_ for _ in ()).throw(ValueError("refused")))
    assert main.cmd_register_session(args) == 1
    assert len(seen) == 1


def test_epoch_schema_is_strict_and_old_writer_is_not_used(case, monkeypatch):
    assert execution._epoch() == EPOCH
    for invalid in ({**EPOCH, "boot_id": "wrong"}, {**EPOCH, "platform": "win32"}, {"platform": "linux"}):
        with pytest.raises(ValueError):
            execution.validate_epoch(invalid)
    execution.validate_epoch({**EPOCH, "platform": "wsl", "boot_id": SID + ":0"})
    monkeypatch.setattr(context, "epoch", lambda: (_ for _ in ()).throw(ValueError("unqualified")))
    assert execution._epoch() is None
    assert execution.Execution(case.path, execution.Correlation(uuid4().hex, uuid4().hex)).subject["native_epoch"] is None


def test_native_reference_refuses_duplicate_keys(case, capsys):
    raw = case.receipt.environment(invoke=True)[execution.NATIVE_HINT_ENV]
    case.payload["_agentWorktrees"]["environment"][execution.NATIVE_HINT_ENV] = raw[:-1] + ',"attempt_id":"' + case.attempt + '"}'
    _observe(case)
    assert "unconfirmed" in capsys.readouterr().err
    assert _join(case)["status"] == "unknown"


def test_observation_never_boots_or_waits_for_a_cold_resident(case, monkeypatch):
    seen = []
    def dispatch(verb, args, **kwargs):
        seen.append(kwargs)
        return native._apply(args)
    monkeypatch.setattr(tracking_write, "dispatch", dispatch)
    _observe(case)
    assert len(seen) == 1
    assert seen[0]["ensure_monitor"] is None and seen[0]["boot_wait_s"] == 0.0
    assert seen[0]["request_deadline_s"] == 1.5 and seen[0]["min_version"] == 1
    assert native.read(case.path.parent, case.attempt, case.operation)


def test_contended_observation_is_nonblocking_and_preserves_missing_state(case, monkeypatch, capsys):
    from contextlib import contextmanager

    @contextmanager
    def contended(path, **kwargs):
        assert kwargs == {"blocking": False, "require_sidecar": True}
        yield SimpleNamespace(acquired=False)
    monkeypatch.setattr(tracking, "_RecordLock", contended)
    _observe(case)
    assert "unconfirmed" in capsys.readouterr().err
    assert _join(case)["reason"] == "native-observation-missing"


def test_v2_execution_append_requires_a_v2_writer(case, monkeypatch):
    from agent_worktrees import launch_seed_state

    seen = []
    monkeypatch.setattr(
        launch_seed_state, "_dispatch",
        lambda verb, args, **kw: seen.append((verb, args, kw)) or {"recorded": True},
    )
    case.receipt.append("execution-returned", exit_code=0)
    assert len(seen) == 1 and seen[0][0] == "launch_attempt_execution"
    assert seen[0][1]["subject"]["version"] == 2 and seen[0][2]["min_version"] == 2
