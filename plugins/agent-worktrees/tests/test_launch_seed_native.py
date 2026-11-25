"""Finite native handoffs retain existing seed and execution authorities."""

from __future__ import annotations

from typing import Any, cast

import json
import builtins
import os
from pathlib import Path
import shutil
import subprocess
import sys
from machine_transport import detect_platform
from types import SimpleNamespace
from uuid import uuid4

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import config as cfg
from agent_worktrees import launch_admission, launch_execution as audit, launch_seed_exec as legacy
from agent_worktrees import launch_seed_native as native, launch_seed_state as seeds, locks, tracking
from agent_worktrees.sessions import LiveVerdict
from agent_worktrees import sessions
from agent_worktrees.tracking_lifecycle import create_new_record

pytestmark = pytest.mark.guard


def _peek(record: Path) -> seeds.LaunchSeed:
    seed = seeds.peek(record)
    assert seed is not None
    return seed


@pytest.mark.parametrize("setup", [False, True])
def test_empty_executable_refuses_before_claim_or_process_query(record: Path, monkeypatch: pytest.MonkeyPatch, setup: Any) -> None:
    seed = seeds.stage(record, kind="new", text="must remain staged")
    assert seed is not None
    monkeypatch.setattr(
        native, "_require_process",
        lambda *args: pytest.fail("Invalid command must be rejected before process queries"),
    )
    with pytest.raises(ValueError, match="literal backend command"):
        native.prepare(record, [""], seed_id=seed.seed_id, setup=setup)
    assert seeds.peek(record) == seed
    assert not (record.parent / "launch-attempts").exists()
    with pytest.raises(ValueError, match="literal fields"):
        native.nul_reply({
            "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
            "operation_id": "c" * 32, "parent_id": None, "environment": None, "argv": [""],
        })


def test_finite_callback_import_does_not_load_preparation_or_seed_daemon() -> None:
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-c",
         "import sys;import agent_worktrees.launch_seed_native;"
         "assert 'agent_worktrees.launch_seed_exec' not in sys.modules;"
         "assert 'agent_worktrees.launch_seed_state' not in sys.modules;"
         "assert 'agent_worktrees.launch_seed_posix' not in sys.modules;"
         "assert 'agent_worktrees.worktree_status_daemon' not in sys.modules"],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_finite_monitor_paths_do_not_load_command_surface() -> None:
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-c",
         "import sys;from agent_worktrees import status_monitor_runtime as monitor,config;"
         "root=config.install_dir();"
         "assert monitor._monitor_lock_path()==root/'status-monitor.lock';"
         "assert monitor._monitor_registry_dir()==root/'status-monitor.d';"
         "assert monitor._monitor_handoff_claim_root()==root/'status-monitor-handoffs.d';"
         "assert 'agent_worktrees.__main__' not in sys.modules"],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_finite_monitor_paths_preserve_loaded_cli_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agent_worktrees import __main__ as core, status_monitor_runtime as monitor

    monkeypatch.setattr(core, "_aw_runtime_home", lambda: tmp_path)
    assert monitor._monitor_lock_path() == tmp_path / "status-monitor.lock"
    monkeypatch.setattr(core, "_monitor_lock_path", lambda: tmp_path / "selected.lock")
    assert monitor._monitor_lock_path() == tmp_path / "selected.lock"


@pytest.fixture
def record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project = cfg.project_dir("demo")
    (project / "worktrees").mkdir(parents=True, exist_ok=True)
    (project / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "worktree_root": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    checkout = tmp_path / "wt-a"
    checkout.mkdir()
    path = create_new_record(
        "wt-a", "worktree/wt-a", str(checkout), "demo", "test",
        detect_platform(), project / "worktrees",
    ).yaml_path
    monkeypatch.setattr(sessions, "verify_worktree_active", lambda *a: LiveVerdict())
    return path


def _identity(reply: Any) -> Any:
    return audit.Correlation(reply["attempt_id"], reply["resolution_id"], reply["parent_id"])


def _saved(record: Path, reply: Any) -> Any:
    return audit.read(record.parent, reply["attempt_id"], reply["operation_id"])


def _spawn(record: Path, reply: Any, **kwargs: Any) -> Any:
    birth = locks.process_start_time(os.getpid())
    assert birth is not None
    native.spawned(
        record, _identity(reply), reply["operation_id"],
        pid=os.getpid(), process_start_time=birth, **kwargs,
    )


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_finite_prepare_claims_without_spawning_or_finishing(record: Path, monkeypatch: pytest.MonkeyPatch, kind: Any) -> None:
    seed = seeds.stage(record, kind=kind, text="private fixture\nquotes ' \" \u03a9")
    assert seed is not None
    popen = subprocess.Popen
    def forbid_backend(argv: Any, **kwargs: Any) -> Any:
        if argv[0] == "copilot":
            pytest.fail("The finite provider must not spawn a backend")
        return popen(argv, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", forbid_backend)
    monkeypatch.setenv(audit.NATIVE_HINT_ENV, "stale")
    reply = native.prepare(record, ["copilot", "", "--", "literal"], seed_id=seed.seed_id)
    assert reply["version"] == 1
    assert reply["argv"] == ["copilot", "", "--interactive", seed.text, "--", "literal"]
    saved = _saved(record, reply)
    assert [event["phase"] for event in saved["events"]] == ["execution-requested", "seed-claimed"]
    assert seed.text not in json.dumps(saved)
    assert json.loads(reply["environment"][audit.NATIVE_HINT_ENV])["operation_id"] == reply["operation_id"]
    claimed = seeds.peek(record)
    assert claimed is not None
    assert claimed.text == seed.text and claimed.handoff_id
    with pytest.raises(ValueError, match="refused"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    assert seeds.peek(record) == claimed


@pytest.mark.parametrize(("failure", "fail_import"), [
    (RuntimeError("private diagnostic and prompt must not be persisted"), False),
    (ModuleNotFoundError("private diagnostic and prompt must not be persisted"), False),
    (subprocess.TimeoutExpired(["private-command", "private-prompt"], 15), False),
    (ModuleNotFoundError("private import diagnostic must not be persisted"), True),
])
@pytest.mark.parametrize("owner", ["native", "python"])
def test_headed_preparation_failure_retains_correlated_unknown_without_replay(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, failure: Exception,
    fail_import: bool, owner: str,
) -> None:
    from agent_remote_driver.headed import caller, launcher
    from agent_worktrees import activity

    seed = seeds.stage(record, kind="new", text="private-prompt")
    assert seed is not None
    log = record.parent / "activity.jsonl"
    monkeypatch.setattr(activity, "log_path", lambda: log)
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(launcher, "prepare_headed_command", fail)
    monkeypatch.setattr(caller, "prepare_headed_command", fail)
    if fail_import:
        original_import = builtins.__import__
        selected = f"agent_remote_driver.headed.{'launcher' if owner == 'native' else 'caller'}"

        def import_module(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == selected:
                raise failure
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", import_module)
    arguments = [
        "launch_seed_native", "--contract-version", "1", "--phase", "prepare",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--seed-id", seed.seed_id, "--backend-mode", "headed-live-owner-v1", "--", "copilot",
    ] if owner == "native" else [
        "launch_seed_exec", "--invoke", "--record", str(record), "--seed-id", seed.seed_id,
        "--", "copilot", "--session-id", str(uuid4()),
    ]
    monkeypatch.setattr(sys, "argv", arguments)
    assert (native.main if owner == "native" else legacy.main)() == 3
    captured = capsys.readouterr()
    assert not captured.out and "unconfirmed" in captured.err
    retained = _peek(record)
    assert retained.seed_id == seed.seed_id and retained.handoff_id
    raw = log.read_text(encoding="utf-8")
    assert "private" not in raw
    events = [json.loads(line) for line in raw.splitlines()]
    failed, = [
        event for event in events
        if event.get("reason") == (
            "headed-preparation-failed-handoff-retained" if owner == "native"
            else "headed-execution-unconfirmed-handoff-retained"
        )
    ]
    assert failed["error_type"] == type(failure).__name__
    saved = audit.read(record.parent, failed["attempt_id"], failed["operation_id"])
    assert [event["phase"] for event in saved["events"]] == [
        "execution-requested", "seed-claimed", "unknown",
    ]
    with pytest.raises(ValueError, match="refused"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    assert _peek(record) == retained


@pytest.mark.parametrize("valid_operation", [False, True])
def test_failed_native_callback_retains_only_validated_supplied_correlation(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, valid_operation: bool,
) -> None:
    from agent_worktrees import activity

    seed = seeds.stage(record, kind="new", text="private-prompt")
    assert seed is not None
    correlation = audit.Correlation(uuid4().hex, uuid4().hex, uuid4().hex)
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id, correlation=correlation)
    before = _saved(record, reply)
    files = set((record.parent / "launch-attempts").rglob("*.execution.json"))
    log = record.parent / "activity.jsonl"
    monkeypatch.setattr(activity, "log_path", lambda: log)
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    operation = reply["operation_id"] if valid_operation else "private-invalid-operation"
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1", "--phase", "spawned",
        "--root", str(cfg.install_dir()), "--record", str(record),
        *correlation.options(), "--operation-id", operation, "--pid", "0",
        "--process-start-time", "unconfirmed",
    ])
    assert native.main() == 3
    assert "unconfirmed" in capsys.readouterr().err
    raw = log.read_text(encoding="utf-8")
    assert "private" not in raw
    event = json.loads(raw)
    assert event["reason"] == "native-spawned-unconfirmed"
    assert event["attempt_id"] == correlation.attempt_id
    assert event["resolution_id"] == correlation.resolution_id
    assert event["parent_id"] == correlation.parent_id
    assert event.get("operation_id") == (reply["operation_id"] if valid_operation else None)
    assert _saved(record, reply) == before
    assert set((record.parent / "launch-attempts").rglob("*.execution.json")) == files
    assert _peek(record).handoff_id


def test_python_entry_failure_retains_supplied_attempt_without_creating_execution(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    from agent_worktrees import activity

    correlation = audit.Correlation(uuid4().hex, uuid4().hex, uuid4().hex)
    log = record.parent / "activity.jsonl"
    monkeypatch.setattr(activity, "log_path", lambda: log)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("private diagnostic must not be persisted")

    monkeypatch.setattr(legacy, "launch", fail)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_exec", "--record", str(record), *correlation.options(), "--", "copilot",
    ])
    assert legacy.main() == 3
    capsys.readouterr()
    raw = log.read_text(encoding="utf-8")
    assert "private" not in raw
    event = json.loads(raw)
    assert event["attempt_id"] == correlation.attempt_id
    assert event["resolution_id"] == correlation.resolution_id
    assert event["parent_id"] == correlation.parent_id
    assert not (record.parent / "launch-attempts").exists()


def test_missing_seed_refusal_survives_without_console_or_prompt_text(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    from agent_worktrees import activity

    log = record.parent / "activity.jsonl"
    monkeypatch.setattr(activity, "log_path", lambda: log)
    assert legacy._prepare(record, ["copilot"], invoke=True, seed_id="missing") == 3
    capsys.readouterr()
    event = json.loads(log.read_text(encoding="utf-8"))
    assert event["reason"] == "staged-seed-missing-or-superseded"
    assert event["worktree_id"] == record.stem
    assert event["exit_code"] == 3


def test_failure_log_unavailable_does_not_change_seed_refusal(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    from agent_worktrees import activity

    monkeypatch.setattr(activity, "log_path", lambda: record.parent)
    assert legacy._prepare(record, ["copilot"], invoke=True, seed_id="missing") == 3
    assert "missing or superseded" in capsys.readouterr().err
    assert seeds.peek(record) is None


@pytest.mark.parametrize("preserved", [False, True])
def test_admission_preparation_returns_its_execution_hint_without_claiming_prompt(
    record: Path, monkeypatch: pytest.MonkeyPatch, preserved: Any,
) -> None:
    seed = seeds.stage(record, kind="resume", text="retained prompt") if preserved else None
    monkeypatch.setenv(audit.NATIVE_HINT_ENV, "stale")
    reply = native.prepare(
        record, ["copilot", "--acp", "--stdio"], seed_id=None, admission_only=True,
        preserve_seed_id=seed.seed_id if seed else None,
    )
    fields = native.nul_reply(reply).decode("utf-8").split("\0")
    assert audit.NATIVE_HINT_ENV in fields
    assert json.loads(reply["environment"][audit.NATIVE_HINT_ENV]) == {
        "attempt_id": reply["attempt_id"], "resolution_id": reply["resolution_id"],
        "operation_id": reply["operation_id"],
    }
    assert reply["argv"] == ["copilot", "--acp", "--stdio"]
    assert seeds.peek(record) == seed
    assert [event["phase"] for event in _saved(record, reply)["events"]] == [
        "execution-requested",
    ]


def test_native_spawn_and_actual_return_reuse_existing_receipts(record: Path) -> None:
    seed = seeds.stage(record, kind="resume", text="one native prompt")
    assert seed is not None
    correlation = audit.Correlation(uuid4().hex, uuid4().hex, uuid4().hex)
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id, correlation=correlation)
    assert _identity(reply) == correlation
    _spawn(record, reply)
    assert seeds.peek(record) is None
    native.returned(record, correlation, reply["operation_id"], exit_code=7)
    saved = _saved(record, reply)
    assert [event["phase"] for event in saved["events"]] == [
        "execution-requested", "seed-claimed", "process-spawned", "execution-returned",
    ]
    assert saved["events"][-1]["details"] == {
        "exit_code": 7, "process_exit_code": 7, "receipt_uncertain": False,
    }
    with pytest.raises(ValueError, match="do not replay"):
        native.returned(record, correlation, reply["operation_id"], exit_code=0)


def test_headed_callback_finishes_seed_only_for_bound_native_identity(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.headed import receipt

    seed = seeds.stage(record, kind="new", text="one headed native prompt")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    coordinator = os.getpid()
    birth = locks.process_start_time(coordinator)
    child_pid = coordinator + 100000
    births = {coordinator: birth, child_pid: "123456789"}
    monkeypatch.setattr(locks, "process_start_time", births.get)
    monkeypatch.setattr(receipt, "process_start_time", births.get)
    monkeypatch.setattr(receipt, "pid_alive", lambda pid: pid in births)
    state_file = record.parent / "headed.json"
    state = {
        "version": 2, "mode": "headed", "process_receipt": receipt.PROCESS_RECEIPT,
        "state": "native_starting", "session_id": "chosen", "nonce": "b" * 64,
        "pid": coordinator, "host_pid": coordinator, "host_process_start_time": birth,
        "child_pid": child_pid, "child_process_start_time": births[child_pid],
    }
    state_file.write_text(json.dumps(state), encoding="utf-8")
    options: Any = {
        "pid": coordinator, "process_start_time": birth, "headed_state_file": state_file,
        "headed_session_id": "chosen", "headed_nonce": "a" * 64,
    }
    with pytest.raises(ValueError, match="unconfirmed"):
        native.spawned(record, _identity(reply), reply["operation_id"], **options)
    assert _peek(record).handoff_id
    assert _saved(record, reply)["events"][-1]["phase"] == "seed-claimed"
    state["nonce"] = options["headed_nonce"]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    native.spawned(record, _identity(reply), reply["operation_id"], **options)
    assert seeds.peek(record) is None
    assert _saved(record, reply)["events"][-1]["details"] == {
        "pid": child_pid, "process_start_time": "123456789", "boundary": "backend-command",
    }
    native.returned(record, _identity(reply), reply["operation_id"], exit_code=17)
    assert _saved(record, reply)["events"][-1]["details"]["process_exit_code"] == 17


def test_python_native_boundary_invokes_selected_core_and_records_actual_native_pid(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.headed import caller

    seed = seeds.stage(record, kind="new", text="one original\nseed")
    assert seed is not None
    captured: Any = {}

    def execute(argv: Any, **options: Any) -> Any:
        captured.update(options)
        assert argv == ["copilot", "--session-id", "chosen", "--interactive", seed.text]
        assert _peek(record).handoff_id
        options["admit"]()
        options["spawned"](os.getpid(), locks.process_start_time(os.getpid()))
        assert seeds.peek(record) is None
        return 17, True

    monkeypatch.setattr(caller, "execute", execute)
    assert legacy.launch(record, ["copilot", "--session-id", "chosen"], invoke=True) == 17
    attempt = captured["attempt_directory"]
    assert attempt.parent.parent == record.parent / "launch-attempts"
    saved = audit.read(record.parent, attempt.parent.name, attempt.name)
    assert saved["events"][-2]["details"]["pid"] == os.getpid()
    assert saved["events"][-1]["details"]["process_exit_code"] == 17


@pytest.mark.parametrize("preserved", [False, True])
def test_admission_only_python_owner_keeps_fence_and_does_not_claim_a_seed(record: Path, monkeypatch: pytest.MonkeyPatch, preserved: Any) -> None:
    from agent_remote_driver.headed import caller

    seed = seeds.stage(record, kind="resume", text="retained intent") if preserved else None
    captured: Any = {}
    monkeypatch.setattr(
        legacy, "_final_cold_error",
        lambda *_: pytest.fail("Ordinary unseeded direct launch does not require mux absence"),
    )

    def execute(argv: Any, **options: Any) -> Any:
        captured.update(options)
        assert argv == ["copilot", "--resume=chosen"]
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            with launch_admission.guard(record):
                pytest.fail("The original owner must retain admission")
        options["admit"]()
        options["spawned"](os.getpid(), locks.process_start_time(os.getpid()))
        assert seeds.peek(record) == seed
        return 17, True

    monkeypatch.setattr(caller, "execute", execute)
    assert legacy.launch(
        record, ["copilot", "--resume=chosen"], admission_only=True,
        preserve_seed_id=seed.seed_id if seed else None,
    ) == 17
    with launch_admission.guard(record):
        pass
    attempt = captured["attempt_directory"]
    saved = audit.read(record.parent, attempt.parent.name, attempt.name)
    assert [event["phase"] for event in saved["events"]] == [
        "execution-requested", "process-spawned", "execution-returned",
    ]
    assert saved["events"][-1]["details"]["process_exit_code"] == 17


def _set_status(record: Path, status: Any) -> Any:
    current = tracking.load_record(record)
    current.status = status
    tracking.save_record(current, record)


@pytest.mark.parametrize(("before", "during", "admitted"), [
    ("finalized", None, True),
    ("complete", None, True),
    ("active", "finalized", False),
    ("pushed", "finalized", False),
    ("finalized", "finalizing", False),
    ("finalized", "archived", False),
])
def test_native_callback_uses_prepare_time_status(record: Path, before: Any, during: Any, admitted: Any) -> None:
    # Native setup callbacks (POSIX execute_headed, Windows --phase admit) run in
    # a separate process and pass no planned_status; prepare retains it instead.
    _set_status(record, before)
    reply = native.prepare(record, ["custom-backend"], seed_id=None, admission_only=True)
    if during:
        _set_status(record, during)
    if admitted:
        native.admit(record, _identity(reply), reply["operation_id"], admission_only=True)
    else:
        with pytest.raises(ValueError, match=f"launch started as '{before}'"):
            native.admit(record, _identity(reply), reply["operation_id"], admission_only=True)


def test_native_callback_without_retained_status_refuses_terminal_status(record: Path) -> None:
    _set_status(record, "finalized")
    reply = native.prepare(record, ["custom-backend"], seed_id=None, admission_only=True)
    native._planned_status_path(record, reply["attempt_id"], reply["operation_id"]).unlink()
    with pytest.raises(ValueError, match="worktree status is 'finalized'$"):
        native.admit(record, _identity(reply), reply["operation_id"], admission_only=True)


@pytest.mark.parametrize(("before", "during", "admitted"), [
    ("finalized", None, True),
    ("active", "finalized", False),
    ("finalized", "finalizing", False),
    ("finalized", "archived", False),
])
def test_admission_only_reopens_already_finalized_worktree_but_refuses_later_finalization(
    record: Path, monkeypatch: pytest.MonkeyPatch, before: Any, during: Any, admitted: Any,
) -> None:
    from agent_remote_driver.headed import caller

    _set_status(record, before)

    def execute(argv: Any, **options: Any) -> Any:
        if during:
            _set_status(record, during)
        if not admitted:
            with pytest.raises(ValueError, match=f"worktree status is '{during}'"):
                options["admit"]()
            return 1, True
        options["admit"]()
        options["spawned"](os.getpid(), locks.process_start_time(os.getpid()))
        return 17, True

    monkeypatch.setattr(caller, "execute", execute)
    assert legacy.launch(
        record, ["copilot", "--session-id", "chosen"], admission_only=True,
    ) == (17 if admitted else 1)


def test_finalizing_worktree_is_refused_even_when_planned(record: Path) -> None:
    reply = native.prepare(record, ["custom-backend"], seed_id=None, admission_only=True)
    _set_status(record, "finalizing")
    with pytest.raises(ValueError, match="worktree status is 'finalizing'"):
        native.admit(
            record, _identity(reply), reply["operation_id"],
            admission_only=True, planned_status="finalizing",
        )
    _set_status(record, "finalized")
    with pytest.raises(ValueError, match="worktree status is 'finalized'"):
        native.admit(record, _identity(reply), reply["operation_id"], admission_only=True)
    native.admit(
        record, _identity(reply), reply["operation_id"],
        admission_only=True, planned_status="finalized",
    )


def test_admission_only_python_owner_rejects_newer_prompt_before_core_creation(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.headed import caller

    seed = seeds.stage(record, kind="new", text="newer intent")
    assert seed is not None
    monkeypatch.setattr(caller, "execute", lambda *_a, **_k: pytest.fail("Changed intent must not launch"))
    assert legacy.launch(record, ["copilot", "--session-id", "chosen"], admission_only=True) == 3
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("native_resume", [False, True])
def test_seeded_admission_cannot_disable_the_cold_stop_check(record: Path, native_resume: Any) -> None:
    correlation = audit.Correlation("a" * 32, "b" * 32)
    with pytest.raises(ValueError, match="Only ordinary admission-only"):
        with native.admission(
            record, correlation, "c" * 32, require_cold_stop=False,
            admission_only=native_resume,
            native_resume_session="chosen" if native_resume else None,
        ):
            pytest.fail("Seeded admission must retain the cold-stop requirement")


def test_unknown_coordinator_receipt_never_restores_or_completes_seed(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver.headed import caller

    seeds.stage(record, kind="new", text="retain this handoff")
    captured: Any = {}

    def execute(_argv: Any, **options: Any) -> Any:
        captured.update(options)
        options["admit"]()
        return 0, False

    monkeypatch.setattr(caller, "execute", execute)
    assert legacy.launch(record, ["copilot", "--session-id", "chosen"], invoke=True) == 3
    assert _peek(record).handoff_id
    attempt = captured["attempt_directory"]
    saved = audit.read(record.parent, attempt.parent.name, attempt.name)
    assert all(event["phase"] != "process-spawned" for event in saved["events"])
    assert saved["events"][-1]["details"] == {
        "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": True,
    }


def test_changed_owner_cannot_admit_prepared_headed_native(record: Path, monkeypatch: pytest.MonkeyPatch, overwrite_record_nonce: Any) -> None:
    from agent_remote_driver.headed import caller

    seeds.stage(record, kind="new", text="do not admit after owner replacement")

    def execute(_argv: Any, **options: Any) -> Any:
        overwrite_record_nonce(record, uuid4().hex)
        options["admit"]()
        pytest.fail("A changed original owner must not admit native creation")

    monkeypatch.setattr(caller, "execute", execute)
    with pytest.raises(ValueError, match="identity or incarnation"):
        legacy.launch(record, ["copilot", "--session-id", "chosen"], invoke=True)
    assert _peek(record).handoff_id


@pytest.mark.parametrize("seeded", [False, True])
def test_headed_prepare_retains_one_original_command_and_finite_live_recheck(record: Path, monkeypatch: pytest.MonkeyPatch, seeded: Any) -> None:
    from agent_remote_driver.headed import launcher, receipt

    seed = seeds.stage(record, kind="new", text="one literal\n' seed") if seeded else None
    captured: Any = {}

    def retain(argv: Any, **kwargs: Any) -> Any:
        captured.update(kwargs, argv=argv)
        return ["physical-python", "retained-core", *argv]

    monkeypatch.setattr(launcher, "prepare_headed_command", retain)
    reply = native.prepare(record, ["copilot", "--model", "auto"], seed_id=seed.seed_id if seed else None, headed=True)
    selected = reply["headed"]
    assert selected["argv"] == captured["argv"]
    assert selected["session_id"] == captured["session_id"]
    assert selected["argv"].count("--session-id") == 1
    assert selected["argv"][selected["argv"].index("--session-id") + 1] == selected["session_id"]
    assert selected["argv"] == [
        "copilot", "--model", "auto", *(["--interactive", seed.text] if seed else []),
        "--session-id", selected["session_id"],
    ]
    assert captured["attempt_directory"].name == reply["operation_id"]
    assert str(captured["state_file"]) == selected["state_file"]
    assert len(reply["environment"][receipt.NONCE_ENV]) == 64
    assert reply["argv"][:2] == ["physical-python", "retained-core"]
    native.admit(record, _identity(reply), reply["operation_id"])
    assert all(event["phase"] != "process-spawned" for event in _saved(record, reply)["events"])
    if seed:
        assert _peek(record).handoff_id
    else:
        seeds.stage(record, kind="new", text="newer intent")
        with pytest.raises(ValueError, match="intent changed"):
            native.admit(record, _identity(reply), reply["operation_id"])


@pytest.mark.parametrize("argv", [
    ["custom-copilot"], ["copilot", "--connect"], ["copilot", "--continue"],
    ["copilot", "--session-id", "one", "--connect=remote"],
    ["copilot", "--connect=remote", "--session-id=one"],
    ["copilot", "--ahp", "http://127.0.0.1:3210", "--resume=selected"],
    ["copilot", "--ahp=http://127.0.0.1:3210", "--resume=selected"],
    ["copilot", "--resume"], ["copilot", "-r"], ["copilot", "plugin", "list"],
    ["copilot", "--session-id=one", "--resume=two"],
    ["copilot", "--environment-host", "remote"], ["copilot", "--help"],
    ["copilot", "--stdio"], ["copilot", "-p", "one prompt"],
])
def test_headed_prepare_leaves_nonlocal_and_ambiguous_commands_on_direct_path(record: Path, argv: Any) -> None:
    reply = native.prepare(record, argv, seed_id=None, headed=True)
    assert "headed" not in reply
    assert reply["argv"] == argv


def test_headed_prepare_preserves_selected_resume_and_prompt_flag_literals() -> None:
    command = ["copilot", "--model", "auto", "--resume=chosen", "-i", "--connect"]
    assert native._headed_command(command) == (command, "chosen")


def test_uncertain_coordinator_return_records_actual_exit_without_completing_seed(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="not a confirmed native spawn")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    native.returned(record, _identity(reply), reply["operation_id"], exit_code=0, receipt_uncertain=True)
    saved = _saved(record, reply)
    assert saved["events"][-1]["details"] == {
        "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": True,
    }
    assert _peek(record).handoff_id


@pytest.mark.parametrize("extra", [
    ["--phase", "setup"], ["--phase", "prepare", "--output-format", "nul"],
    ["--phase", "prepare", "--environment-reply", "execution-hint"],
    ["--phase", "spawn-failed"],
])
def test_headed_mode_refuses_other_boundaries_before_claim(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, extra: Any) -> None:
    seed = seeds.stage(record, kind="new", text="no effects before negotiation")
    assert seed is not None
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1", "--root", str(cfg.install_dir()),
        "--record", str(record), "--backend-mode", "headed-live-owner-v1", *extra,
    ])
    assert native.main() == 3
    assert not capsys.readouterr().out
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("argv", [
    ["custom-copilot", "--session-id", "chosen"],
    ["copilot", "--acp", "--session-id", "chosen"],
    ["copilot", "--ahp", "http://127.0.0.1:3210", "--resume=selected"],
    ["copilot", "--ahp=http://127.0.0.1:3210", "--resume=selected"],
    ["copilot", "--resume"], ["copilot"],
    ["copilot", "--session-id", "one", "--resume", "two"],
    ["copilot", "-i", "--session-id", "--"], ["copilot", "--", "--session-id", "literal"],
])
def test_unsupported_or_unselected_native_commands_do_not_get_silently_wrapped(argv: Any) -> None:
    assert legacy._headed_session(argv) is None


def test_definite_spawn_failure_restores_once_without_automatic_retry(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="retained after OS refusal")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    native.spawn_failed(record, _identity(reply), reply["operation_id"])
    restored = seeds.peek(record)
    assert restored is not None
    assert restored.seed_id == seed.seed_id and restored.text == seed.text
    assert restored.handoff_id is None
    assert _saved(record, reply)["events"][-1]["phase"] == "execution-returned"
    with pytest.raises(ValueError, match="do not replay"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])


@pytest.mark.skipif(os.name != "nt" or shutil.which("pwsh") is None, reason="Native PowerShell required")
@pytest.mark.parametrize("preparation", ["headed", "substituted", "drive-relative", "root-relative"])
def test_direct_client_refusal_restores_a_real_seed(record: Path, tmp_path: Path, preparation: Any) -> None:
    seed = seeds.stage(record, kind="new", text="retry only after definite pre-creation refusal")
    assert seed is not None
    reply = native.prepare(record, [sys.executable], seed_id=seed.seed_id)
    if preparation == "headed":
        reply["headed"] = {"version": 1}
    elif preparation in {"drive-relative", "root-relative"}:
        image = Path(sys.executable)
        reply["argv"][0] = (
            image.drive + str(image)[len(image.anchor):]
            if preparation == "drive-relative" else str(image)[len(image.drive):]
        )
    assert _peek(record).handoff_id
    prepared = tmp_path / "prepared.json"
    prepared.write_text(json.dumps(reply), encoding="utf-8")
    restore = tmp_path / "restore.py"
    restore.write_text(
        "import argparse,json\nfrom pathlib import Path\n"
        "from agent_worktrees import launch_seed_native as native,launch_execution as audit\n"
        "parser=argparse.ArgumentParser()\n"
        "for name in ('record','launch-attempt-id','launch-resolution-id','operation-id',"
        "'launch-parent-id'):parser.add_argument('--'+name)\n"
        "args=parser.parse_args()\n"
        "native.spawn_failed(Path(args.record),audit.Correlation("
        "args.launch_attempt_id,args.launch_resolution_id,args.launch_parent_id),args.operation_id)\n"
        "print(json.dumps({'recorded':True}))\n",
        encoding="utf-8",
    )

    def literal(value: Any) -> Any:
        return "'" + str(value).replace("'", "''") + "'"

    boundary = Path(__file__).resolve().parents[1] / "scripts" / "native-seed-boundary.ps1"
    selected = str(tmp_path / "forwarder.exe") if preparation == "substituted" else reply["argv"][0]
    driver = tmp_path / "refused.ps1"
    driver.write_text(
        f". {literal(boundary)}\n"
        f"[Environment]::CurrentDirectory={literal(Path(sys.executable).anchor)}\n"
        "function Get-AwNativeSeedRuntime {return @{Python='fixture';Bootstrap='fixture'}}\n"
        "function Test-AwHeadedPreparation {return $false}\n"
        "function Invoke-AwNativeSeedPhase {param([string]$Root,[string[]]$Arguments)\n"
        f" if($Arguments[1] -eq 'prepare'){{return (Get-Content -Raw {literal(prepared)} | ConvertFrom-Json)}}\n"
        " if($Arguments[1] -ne 'spawn-failed'){throw 'unexpected post-preparation phase'}\n"
        f" $info=[Diagnostics.ProcessStartInfo]::new({literal(sys.executable)})\n"
        " $info.UseShellExecute=$false;$info.CreateNoWindow=$true\n"
        " $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true\n"
        f" foreach($arg in @('-I',{literal(restore)})+@($Arguments|Select-Object -Skip 2))"
        "{$info.ArgumentList.Add($arg)}\n"
        " $process=[Diagnostics.Process]::Start($info)\n"
        " try{\n"
        "  $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()\n"
        "  if(-not $process.WaitForExit(15000)){throw 'restoration did not settle'}\n"
        "  if($process.ExitCode -ne 0){throw $err.GetAwaiter().GetResult()}\n"
        "  return (ConvertFrom-Json -InputObject $out.GetAwaiter().GetResult())\n"
        " }finally{$process.Dispose()}\n"
        "}\n"
        f"exit (Invoke-AwNativeSeedBackend -Root {literal(tmp_path)} -Record {literal(record)} "
        f"-SeedId {literal(seed.seed_id)} -Command @({literal(selected)}) "
        "-RequireDirectClient -BeforeResume {throw 'refused target must never be published'})\n",
        encoding="utf-8",
    )
    result = run_caller_owned(
        [str(shutil.which("pwsh")), "-NoLogo", "-NoProfile", "-File", str(driver)],
        cwd=tmp_path, timeout=30, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert not result.stdout
    assert "Direct native client preparation is unconfirmed" in result.stderr
    restored = seeds.peek(record)
    assert restored is not None
    assert (restored.seed_id, restored.text, restored.handoff_id) == (seed.seed_id, seed.text, None)
    assert _saved(record, reply)["events"][-1]["phase"] == "execution-returned"
    retry = native.prepare(record, [sys.executable], seed_id=seed.seed_id)
    assert _peek(record).handoff_id
    native.spawn_failed(record, _identity(retry), retry["operation_id"])


def test_unconfirmed_child_identity_never_finishes_or_restores(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="unknown remains held")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    claimed = seeds.peek(record)
    assert claimed is not None
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.spawned(
            record, _identity(reply), reply["operation_id"],
            pid=os.getpid(), process_start_time="0",
        )
    assert seeds.peek(record) == claimed
    assert _saved(record, reply)["events"][-1]["phase"] == "seed-claimed"
    with pytest.raises(ValueError, match="recorded spawn"):
        native.returned(record, _identity(reply), reply["operation_id"], exit_code=0)


def test_spawned_backend_cannot_restore_or_claim_another_spawn(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="already launched")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    _spawn(record, reply)
    with pytest.raises(ValueError, match="cannot restore"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    with pytest.raises(ValueError, match="already observed"):
        _spawn(record, reply)
    assert seeds.peek(record) is None


def test_changed_incarnation_or_correlation_cannot_finish_seed(record: Path, overwrite_record_nonce: Any) -> None:
    seed = seeds.stage(record, kind="new", text="must not cross incarnations")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    wrong = audit.Correlation(reply["attempt_id"], uuid4().hex)
    with pytest.raises(ValueError, match="identity or incarnation"):
        native.spawn_failed(record, wrong, reply["operation_id"])
    overwrite_record_nonce(record, uuid4().hex)
    with pytest.raises(ValueError, match="identity or incarnation"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    assert _peek(record).handoff_id


def test_superseded_seed_is_not_finished_or_restored(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="old intent")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    replacement = seeds.stage(record, kind="resume", text="new intent")
    assert replacement is not None
    with pytest.raises(ValueError, match="missing or superseded"):
        _spawn(record, reply)
    with pytest.raises(ValueError, match="missing or superseded"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    assert seeds.peek(record) == replacement


def test_uncertain_cleanup_preserves_actual_child_outcome(record: Path) -> None:
    reply = native.prepare(record, ["copilot"], seed_id=None)
    _spawn(record, reply)
    native.returned(
        record, _identity(reply), reply["operation_id"], exit_code=0, receipt_uncertain=True,
    )
    assert _saved(record, reply)["events"][-1]["details"] == {
        "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": True,
    }


def test_missing_initial_receipt_cannot_claim_seed(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed = seeds.stage(record, kind="new", text="must remain unclaimed")
    assert seed is not None
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise OSError("fixture receipt failure")
    monkeypatch.setattr(audit.Execution, "append", fail)
    with pytest.raises(OSError):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    assert seeds.peek(record) == seed


def test_unconfirmed_claim_observation_is_terminal_not_replay_authority(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed = seeds.stage(record, kind="new", text="held after ambiguous observation")
    assert seed is not None
    append = audit.Execution.append
    correlation = audit.Correlation(uuid4().hex, uuid4().hex)
    def fail_claim(self: Any, phase: Any, **details: Any) -> Any:
        if phase == "seed-claimed":
            raise OSError("fixture observation failure")
        return append(self, phase, **details)
    monkeypatch.setattr(audit.Execution, "append", fail_claim)
    with pytest.raises(ValueError, match="observation is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, correlation=correlation)
    claimed = seeds.peek(record)
    assert claimed is not None
    assert claimed.handoff_id
    files = list((record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json"))
    operation_id = files[0].name.split(".")[0]
    assert audit.read(record.parent, correlation.attempt_id, operation_id)["events"][-1]["phase"] == "unknown"
    with pytest.raises(ValueError, match="do not replay"):
        native.spawn_failed(record, correlation, operation_id)
    assert seeds.peek(record) == claimed


def test_unconfirmed_finish_retains_claim_and_actual_spawn_observation(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed = seeds.stage(record, kind="new", text="do not resubmit on finish failure")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    monkeypatch.setattr(seeds, "finish", lambda *a: {"finished": False, "reason": "fixture"})
    with pytest.raises(ValueError, match="completion is unconfirmed"):
        _spawn(record, reply)
    assert _peek(record).handoff_id
    assert _saved(record, reply)["events"][-1]["phase"] == "process-spawned"
    native.returned(
        record, _identity(reply), reply["operation_id"], exit_code=7, receipt_uncertain=True,
    )
    assert _saved(record, reply)["events"][-1]["details"] == {
        "exit_code": 7, "process_exit_code": 7, "receipt_uncertain": True,
    }


@pytest.mark.parametrize("extra", [
    ["--phase", "prepare", "--pid", "123", "--", "copilot"],
    ["--phase", "spawn-failed", "--", "copilot"],
    ["--phase", "returned", "--exit-code", "0"],
    ["--phase", "prepare", "--headed-state-file", "never-read.json", "--", "copilot"],
    ["--phase", "spawned", "--headed-session-id", "missing-state"],
])
def test_cli_refuses_incomplete_or_cross_phase_outcomes(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, extra: Any) -> None:
    seed = seeds.stage(record, kind="new", text="no child on malformed request")
    assert seed is not None
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr("sys.argv", [
        "launch_seed_native", "--contract-version", "1", "--root", str(cfg.install_dir()),
        "--record", str(record), *extra,
    ])
    assert native.main() == 3
    captured: Any = capsys.readouterr()
    assert not captured.out and "do not automatically retry" in captured.err
    assert seed.text not in captured.err
    assert seeds.peek(record) == seed


def test_native_provider_declares_receipt_expansion_without_changing_legacy_reply(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(sys, "argv", ["launch_seed_native", "--describe-contract"])
    with pytest.raises(SystemExit) as result:
        native.main()
    assert result.value.code == 0
    declaration = json.loads(capsys.readouterr().out)
    assert declaration["backend_process_receipts"] == ["direct-v1", "headed-native-child-v1"]
    assert declaration["backend_preparation_modes"] == ["direct-v1", "headed-live-owner-v1"]
    assert declaration["version"] == 1
    assert declaration["nul_reply_schema"] == native.NUL_REPLY_SCHEMA


@pytest.mark.parametrize("fault", [
    None, "captured-old-slot", "missing-completion", "missing-bootstrap", "foreign-root",
    "separate-registry",
])
@pytest.mark.parametrize("application", [False, True])
def test_native_cli_selects_only_current_completed_same_installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Any, application: Any) -> None:
    root = tmp_path / "engine"
    prefix = root / "versions" / "1.2.3"
    (prefix / ".runtime-bootstrap").mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    complete = prefix / ".install-complete.json"
    complete.write_text("{}", encoding="utf-8")
    bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.write_text("# fixture\n", encoding="utf-8")
    environment = prefix / ".venv" if application else prefix
    environment.mkdir(exist_ok=True)
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    if fault == "captured-old-slot":
        (root / "current-version").write_text("1.2.4\n", encoding="utf-8")
    elif fault == "missing-completion":
        complete.unlink()
    elif fault == "missing-bootstrap":
        bootstrap.unlink()
    elif fault == "foreign-root":
        foreign = tmp_path / "another-engine"
        foreign.mkdir()
        root = foreign
    elif fault == "separate-registry":
        registry = tmp_path / "registry"
        registry.mkdir()
        monkeypatch.setattr(cfg, "install_dir", lambda: registry)
    if fault and fault != "separate-registry":
        with pytest.raises(ValueError):
            native.require_current_runtime(root)
    else:
        native.require_current_runtime(root)
        with pytest.raises(ValueError):
            native.require_current_runtime(Path("relative-engine"))


def _setup(record: Path, *, seed_id: Any=None, **options: Any) -> Any:
    script = record.parent / "default-setup.ps1"
    script.write_text(
        legacy.BOUNDARY_MARKER + "\n" + legacy.ATTEMPT_BOUNDARY_MARKER + "\n",
        encoding="utf-8",
    )
    identity = {
        "pid": os.getpid(), "process_start_time": locks.process_start_time(os.getpid()),
        **options,
    }
    return native.prepare(
        record, ["pwsh", "-NoProfile", "-File", str(script)], seed_id=seed_id,
        setup=True, **identity,
    )


def test_setup_preparation_records_existing_owner_without_claiming_seed(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="retained until actual backend")
    assert seed is not None
    reply = _setup(record, seed_id=seed.seed_id)
    assert reply["setup_seed_id"] == seed.seed_id
    assert seeds.peek(record) == seed
    saved = _saved(record, reply)
    assert [event["phase"] for event in saved["events"]] == ["execution-requested", "process-spawned"]
    assert saved["events"][-1]["details"] == {
        "pid": os.getpid(), "process_start_time": locks.process_start_time(os.getpid()),
        "boundary": "launcher-command",
    }
    backend = native.prepare(
        record, ["copilot"], seed_id=seed.seed_id,
        correlation=audit.Correlation(reply["attempt_id"], reply["resolution_id"], reply["operation_id"]),
    )
    assert backend["parent_id"] == reply["operation_id"]
    _spawn(record, backend)
    native.returned(record, _identity(backend), backend["operation_id"], exit_code=7)
    native.returned(record, _identity(reply), reply["operation_id"], exit_code=7)
    assert seeds.peek(record) is None
    assert _saved(record, reply)["events"][-1]["details"]["process_exit_code"] == 7


@pytest.mark.parametrize("preserve", [False, True])
def test_setup_admission_releases_before_child_lifetime_and_never_takes_seed(record: Path, preserve: Any) -> None:
    seed = seeds.stage(record, kind="resume", text="preserved bare-resume intent") if preserve else None
    reply = _setup(
        record, admission_only=True, preserve_seed_id=seed.seed_id if seed else None,
    )
    assert reply["setup_seed_id"] is None
    with launch_admission.guard(record):
        pass
    assert seeds.peek(record) == seed
    native.interrupted(record, _identity(reply), reply["operation_id"])
    assert _saved(record, reply)["events"][-1]["phase"] == "unknown"
    with pytest.raises(ValueError, match="do not replay"):
        native.returned(record, _identity(reply), reply["operation_id"], exit_code=0)


def test_setup_admission_cannot_hide_new_seed_or_bypass_busy_mutation(record: Path) -> None:
    with launch_admission.guard(record):
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            _setup(record, admission_only=True)
    seed = seeds.stage(record, kind="new", text="later intent")
    assert seed is not None
    with pytest.raises(ValueError, match="refused"):
        _setup(record, admission_only=True)
    assert seeds.peek(record) == seed


def test_setup_failure_leaves_prompt_unclaimed_and_never_records_backend(record: Path) -> None:
    seed = seeds.stage(record, kind="new", text="setup must not consume this")
    assert seed is not None
    reply = _setup(record, seed_id=seed.seed_id)
    native.interrupted(record, _identity(reply), reply["operation_id"])
    assert seeds.peek(record) == seed
    saved = _saved(record, reply)
    assert saved["events"][-1]["phase"] == "unknown"
    assert all(event.get("details", {}).get("boundary") != "backend-command" for event in saved["events"])


def test_setup_refuses_unconfirmed_owner_before_any_receipt(record: Path) -> None:
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(
            record, ["copilot"], seed_id=None, setup=True,
            pid=os.getpid(), process_start_time="0",
        )
    assert not list((record.parent / "launch-attempts").glob("*/*.execution.json"))


@pytest.mark.parametrize("admission_only", [False, True])
def test_setup_can_resolve_process_start_time_from_its_canonical_owner(record: Path, admission_only: Any) -> None:
    seed = seeds.stage(record, kind="resume", text="retained setup intent")
    assert seed is not None
    expected = locks.process_start_time(os.getpid())
    reply = _setup(
        record, seed_id=None if admission_only else seed.seed_id,
        admission_only=admission_only,
        preserve_seed_id=seed.seed_id if admission_only else None,
        process_start_time=None,
    )
    assert _saved(record, reply)["events"][-1]["details"] == {
        "pid": os.getpid(), "process_start_time": expected, "boundary": "launcher-command",
    }
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("pid", [None, 0, -1, True, "123"])
def test_setup_provider_identity_refuses_invalid_pid_before_queries_or_effects(
    record: Path, monkeypatch: pytest.MonkeyPatch, pid: Any,
) -> None:
    seed = seeds.stage(record, kind="new", text="unclaimed")
    assert seed is not None
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: pytest.fail("Invalid PID query"))
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, setup=True, pid=pid)
    assert seeds.peek(record) == seed
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("birth", [None, ""])
def test_setup_refuses_unavailable_provider_identity_before_any_effects(record: Path, monkeypatch: pytest.MonkeyPatch, birth: Any) -> None:
    seed = seeds.stage(record, kind="new", text="unclaimed")
    assert seed is not None
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: birth)
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, setup=True, pid=os.getpid())
    assert seeds.peek(record) == seed
    assert not (record.parent / "launch-attempts").exists()


def test_explicit_empty_birth_does_not_opt_into_provider_identity(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: pytest.fail("Explicit invalid birth"))
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(
            record, ["copilot"], seed_id=None, setup=True, pid=os.getpid(), process_start_time="",
        )
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("supplied", [False, True])
def test_setup_birth_change_during_preparation_is_unknown_not_a_recorded_spawn(
    record: Path, monkeypatch: pytest.MonkeyPatch, supplied: Any,
) -> None:
    seed = seeds.stage(record, kind="new", text="unclaimed")
    assert seed is not None
    prepare = legacy._prepare
    birth = locks.process_start_time(os.getpid())

    def replace_process(*args: Any, **kwargs: Any) -> Any:
        prepared = prepare(*args, **kwargs)
        monkeypatch.setattr(locks, "process_start_time", lambda *_a: "replacement")
        return prepared

    script = record.parent / "default-setup.ps1"
    script.write_text(
        legacy.BOUNDARY_MARKER + "\n" + legacy.ATTEMPT_BOUNDARY_MARKER + "\n", encoding="utf-8",
    )
    correlation = audit.Correlation(uuid4().hex, uuid4().hex)
    monkeypatch.setattr(legacy, "_prepare", replace_process)
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(
            record, ["pwsh", "-NoProfile", "-File", str(script)], seed_id=seed.seed_id,
            setup=True, pid=os.getpid(), process_start_time=birth if supplied else None,
            correlation=correlation,
        )
    assert seeds.peek(record) == seed
    saved, = (record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json")
    events = audit.read(record.parent, correlation.attempt_id, saved.name.split(".")[0])["events"]
    assert [event["phase"] for event in events] == ["execution-requested", "unknown"]


@pytest.mark.parametrize("output_format", ["json", "nul"])
def test_cli_setup_resolves_process_start_time_without_changing_reply_or_seed_custody(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: Any, output_format: Any,
) -> None:
    seed = seeds.stage(record, kind="new", text="retained private intent")
    assert seed is not None
    script = record.parent / "default-setup.sh"
    script.write_text(
        legacy.BOUNDARY_MARKER + "\n" + legacy.ATTEMPT_BOUNDARY_MARKER + "\n", encoding="utf-8",
    )
    expected = locks.process_start_time(os.getpid())
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "setup", "--output-format", output_format, "--pid", str(os.getpid()),
        "--seed-id", seed.seed_id, "--", "bash", str(script),
    ])
    assert native.main() == 0
    captured: Any = capsysbinary.readouterr()
    assert not captured.err
    assert seed.text.encode("utf-8") not in captured.out
    assert seeds.peek(record) == seed
    if output_format == "json":
        reply = json.loads(captured.out)
        attempt, operation = reply["attempt_id"], reply["operation_id"]
        assert reply["version"] == 1 and reply["setup_seed_id"] == seed.seed_id
    else:
        fields = captured.out.decode("utf-8").split("\0")
        assert fields[0] == native.NUL_REPLY_SCHEMA
        assert fields[5] == seed.seed_id and fields[-2:] == ["complete", ""]
        attempt, operation = fields[1], fields[3]
    saved = audit.read(record.parent, attempt, operation)
    assert saved["events"][-1]["details"] == {
        "pid": os.getpid(), "process_start_time": expected, "boundary": "launcher-command",
    }


def test_backend_spawn_callback_does_not_infer_missing_child_birth(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seed = seeds.stage(record, kind="new", text="held backend intent")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    monkeypatch.setattr(
        locks, "process_start_time", lambda *_a: pytest.fail("Missing child birth must not be inferred"),
    )
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.spawned(
            record, _identity(reply), reply["operation_id"], pid=os.getpid(), process_start_time=cast(Any, None),
        )
    assert _peek(record).handoff_id
    assert _saved(record, reply)["events"][-1]["phase"] == "seed-claimed"


@pytest.mark.parametrize("environment", [None, {}, {"ODD-NAME": "value=\n'\u03a9", "EMPTY": ""}])
def test_nul_reply_preserves_literal_fields_and_distinguishes_environment(environment: Any) -> None:
    argv = ["copilot", "", "quotes ' \"", "$(not-a-command); *", "line\nnext\n", "\u03a9", "--"]
    reply = {
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": "d" * 32,
        "setup_seed_id": "e" * 32, "environment": environment, "argv": argv,
    }
    encoded = native.nul_reply(reply)
    fields = encoded.decode("utf-8").split("\0")
    assert fields[:7] == [
        "agent-worktrees.native-seed-reply.v1", "a" * 32, "b" * 32, "c" * 32,
        "d" * 32, "e" * 32, str(len(environment)) if environment is not None else "-1",
    ]
    offset = 7
    decoded_environment: Any = None if fields[6] == "-1" else {}
    if decoded_environment is not None:
        for _ in range(int(fields[6])):
            decoded_environment[fields[offset]] = fields[offset + 1]
            offset += 2
    assert decoded_environment == environment
    assert int(fields[offset]) == len(argv)
    assert fields[offset + 1:] == [*argv, "complete", ""]


@pytest.mark.parametrize("field,value", [
    ("argv", ["copilot", "invalid\0field"]),
    ("argv", ["copilot", "\udcff"]),
    ("environment", {"BAD=NAME": "value"}),
    ("environment", {"": "value"}),
    ("environment", {"NAME": "invalid\0value"}),
])
def test_nul_reply_refuses_unrepresentable_fields_without_partial_output(field: Any, value: Any) -> None:
    reply = {
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": None, "environment": None, "argv": ["copilot"],
        field: value,
    }
    with pytest.raises(ValueError):
        native.nul_reply(reply)


@pytest.mark.parametrize("output_format", ["json", "nul"])
def test_cli_preparation_output_formats_keep_actual_seed_custody(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: Any, output_format: Any,
) -> None:
    seed = seeds.stage(record, kind="new", text="private fixture\nquotes ' \" \u03a9")
    assert seed is not None
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", output_format,
        "--seed-id", seed.seed_id, "--", "copilot", "", "--", "literal",
    ])
    assert native.main() == 0
    captured: Any = capsysbinary.readouterr()
    assert not captured.err
    held = seeds.peek(record)
    assert held is not None
    assert held.seed_id == seed.seed_id and held.handoff_id
    if output_format == "json":
        reply = json.loads(captured.out)
        assert reply["version"] == 1
        assert reply["argv"] == ["copilot", "", "--interactive", seed.text, "--", "literal"]
        operation_id = reply["operation_id"]
        attempt_id = reply["attempt_id"]
    else:
        fields = captured.out.decode("utf-8").split("\0")
        assert fields[0] == "agent-worktrees.native-seed-reply.v1"
        assert fields[4:6] == ["", ""]
        assert fields[-2:] == ["complete", ""]
        offset = 7 + max(0, int(fields[6])) * 2
        assert fields[offset:] == [
            "6", "copilot", "", "--interactive", seed.text, "--", "literal", "complete", "",
        ]
        attempt_id, operation_id = fields[1], fields[3]
    saved = audit.read(record.parent, attempt_id, operation_id)
    assert [event["phase"] for event in saved["events"]] == ["execution-requested", "seed-claimed"]
    assert seed.text not in json.dumps(saved)


@pytest.mark.parametrize("option", [
    ["--output-format", "nul"], ["--environment-reply", "execution-hint"],
])
def test_nul_callback_request_is_rejected_before_runtime_or_receipt_mutation(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, option: Any,
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("A callback output-format error must precede runtime selection and mutation")
    monkeypatch.setattr(native, "require_current_runtime", forbidden)
    monkeypatch.setattr(native, "returned", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "returned", *option, "--exit-code", "0",
    ])
    assert native.main() == 3
    captured: Any = capsys.readouterr()
    assert not captured.out and "do not automatically retry" in captured.err
    assert not list((record.parent / "launch-attempts").glob("*/*.execution.json"))


def test_hint_reply_does_not_encode_unused_non_utf8_environment(record: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: Any) -> None:
    seed = seeds.stage(record, kind="new", text="literal prompt")
    assert seed is not None
    monkeypatch.setenv("FIXTURE_NON_UTF8", "value\udcff")
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", "nul",
        "--environment-reply", "execution-hint", "--seed-id", seed.seed_id, "--", "copilot",
    ])
    assert native.main() == 0
    captured: Any = capsysbinary.readouterr()
    fields = captured.out.decode("utf-8").split("\0")
    assert fields[6:8] == ["1", audit.NATIVE_HINT_ENV]
    assert os.environ["FIXTURE_NON_UTF8"] == "value\udcff"
    assert not captured.err
    held = seeds.peek(record)
    assert held is not None
    assert held.seed_id == seed.seed_id and held.handoff_id


def test_non_utf8_nul_command_refuses_before_runtime_or_seed_claim(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    seed = seeds.stage(record, kind="new", text="must remain staged")
    assert seed is not None

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Unrepresentable argv must refuse before runtime selection or seed effects")

    monkeypatch.setattr(native, "require_current_runtime", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", "nul",
        "--environment-reply", "execution-hint", "--seed-id", seed.seed_id,
        "--", "copilot", "--add-dir", "path\udcff",
    ])
    assert native.main() == 3
    captured: Any = capsys.readouterr()
    assert not captured.out and "do not automatically retry" in captured.err
    assert seeds.peek(record) == seed
    assert not list((record.parent / "launch-attempts").glob("*/*.execution.json"))


def test_unrepresentable_prepared_prompt_remains_held_with_unknown_outcome(record: Path, monkeypatch: pytest.MonkeyPatch, capsysbinary: Any) -> None:
    seed = seeds.stage(record, kind="new", text="invalid\0prompt")
    assert seed is not None
    correlation = audit.Correlation(uuid4().hex, uuid4().hex)
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", "nul", "--seed-id", seed.seed_id,
        *correlation.options(), "--", "copilot",
    ])
    assert native.main() == 3
    captured: Any = capsysbinary.readouterr()
    assert not captured.out and b"do not automatically retry" in captured.err
    assert b"invalid" not in captured.err
    held = seeds.peek(record)
    assert held is not None
    assert held.seed_id == seed.seed_id and held.handoff_id
    saved, = (record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json")
    assert audit.read(record.parent, correlation.attempt_id, saved.name.split(".")[0])[
        "events"
    ][-1]["phase"] == "unknown"


def test_bash_reads_counted_nul_reply_without_evaluating_literal_arguments(tmp_path: Path) -> None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else (
        shutil.which("bash") if os.name != "nt" else None
    )
    if not bash:
        pytest.skip("Bash is unavailable")
    unexpected = tmp_path / "must-not-execute"
    argv = ["copilot", "", f"$(touch '{unexpected.as_posix()}')", "' \" ; *", "\u03a9\n"]
    environment = {"LITERAL": "quotes ' \"\n\u03a9", "EMPTY": ""}
    reply = {
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": None, "environment": environment, "argv": argv,
    }
    script = r"""
set -euo pipefail
for ((index=0; index<7; index++)); do IFS= read -r -d '' value; header[index]=$value; done
[[ ${header[0]} == agent-worktrees.native-seed-reply.v1 ]]
for ((index=0; index<header[6]; index++)); do
    IFS= read -r -d '' name
    IFS= read -r -d '' value
    export "$name=$value"
done
IFS= read -r -d '' count
argv=()
for ((index=0; index<count; index++)); do
    IFS= read -r -d '' value
    argv+=("$value")
done
IFS= read -r -d '' complete
[[ $complete == complete ]]
if IFS= read -r -d '' unexpected; then exit 3; fi
printf '%s\0' "$LITERAL" "$EMPTY" "${argv[@]}"
"""
    result = run_caller_owned(
        [bash, "--noprofile", "--norc", "-c", script],
        input_text=native.nul_reply(reply).decode("utf-8"),
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\0") == [*environment.values(), *argv, ""]
    assert not unexpected.exists()


def test_selected_runtime_describes_literal_output_support_without_preparation() -> None:
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-m", "agent_worktrees.launch_seed_native", "--describe-contract"],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "contract": "agent-worktrees.native-seed", "version": 1,
        "preparation_output_formats": ["json", "nul"],
        "preparation_environment_modes": ["full", "execution-hint"],
        "nul_reply_schema": "agent-worktrees.native-seed-reply.v1",
        "setup_process_start_time_sources": ["caller", "provider"],
        "backend_execution_modes": [
            "posix-exec-v1", "posix-exec-env-v1", "posix-headed-env-v1",
        ] if os.name == "posix" else [],
        "backend_process_receipts": ["direct-v1", "headed-native-child-v1"],
        "backend_preparation_modes": ["direct-v1", "headed-live-owner-v1"],
        "setup_admission_modes": ["posix-live-owner-v1"] if os.name == "posix" else [],
    }
    assert not result.stderr


@pytest.mark.parametrize("certain", [False, True])
def test_posix_headed_owner_records_native_identity_and_keeps_actual_exit(
    record: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, certain: Any,
) -> None:
    from agent_remote_driver.headed import caller
    from agent_worktrees import launch_seed_posix as posix

    seed = seeds.stage(record, kind="new", text="one literal\nprompt")
    assert seed is not None
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    correlation = _identity(reply)
    complete = tmp_path / "completion"
    monkeypatch.setattr(posix, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(posix, "_completion_file", lambda *a: os.open(
        complete, os.O_CREAT | os.O_WRONLY, 0o600,
    ))
    monkeypatch.setattr(posix, "_backend_environment", lambda fd: {"LITERAL": "one\nvalue"})
    original_birth = locks.process_start_time
    monkeypatch.setattr(
        locks, "process_start_time", lambda pid: "202001" if pid == 202 else original_birth(pid),
    )

    def execute(argv: Any, **kwargs: Any) -> Any:
        assert argv[0] == "copilot" and "--session-id" in argv
        assert kwargs["interactive"] is True
        assert kwargs["environment"] == {"LITERAL": "one\nvalue"}
        assert kwargs["attempt_directory"].parts[-2:] == (
            reply["attempt_id"], reply["operation_id"],
        )
        kwargs["admit"]()
        if certain:
            kwargs["spawned"](202, "202001")
        return 17, certain

    monkeypatch.setattr(caller, "execute", execute)
    monkeypatch.setattr(posix, "execute", lambda *a, **k: pytest.fail("No direct replay"))
    assert posix.execute_headed(
        record, correlation, reply["operation_id"], reply["argv"], complete,
        backend_environment_fd=3,
    ) == 17
    assert complete.read_text() == ("0\n" if certain else "3\n")
    saved = _saved(record, reply)
    if certain:
        assert saved["events"][-1]["phase"] == "process-spawned"
        assert saved["events"][-1]["details"]["pid"] == 202
        assert seeds.peek(record) is None
    else:
        assert saved["events"][-1]["details"] == {
            "exit_code": 17, "process_exit_code": 17, "receipt_uncertain": True,
        }
        assert _peek(record).handoff_id


def test_posix_headed_owner_keeps_unsupported_command_on_original_direct_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agent_remote_driver.headed import caller
    from agent_worktrees import launch_seed_posix as posix

    monkeypatch.setattr(posix, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(caller, "execute", lambda *a, **k: pytest.fail("Unsupported native mode"))
    command = ["copilot", "--ahp", "http://127.0.0.1:3210", "--resume=chosen"]
    calls = []
    def execute(*args: Any, **kwargs: Any) -> int:
        calls.append((args, kwargs))
        return 7
    monkeypatch.setattr(posix, "execute", execute)
    correlation = audit.Correlation("a" * 32, "b" * 32)
    assert posix.execute_headed(
        tmp_path, correlation, "c" * 32, command, tmp_path, backend_environment_fd=3,
    ) == 7
    assert calls == [((tmp_path, correlation, "c" * 32, command, tmp_path), {
        "backend_environment_fd": 3, "admission_only": False,
        "preserve_seed_id": None, "native_resume_session": None,
    })]


@pytest.mark.parametrize("preserved", [False, True])
def test_posix_admission_owner_keeps_fence_through_core_wait_without_claiming_seed(
    record: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, preserved: Any,
) -> None:
    from agent_remote_driver.headed import caller
    from agent_worktrees import launch_seed_posix as posix

    seed = seeds.stage(record, kind="new", text="preserved, never claimed") if preserved else None
    options: Any = {"admission_only": True, "preserve_seed_id": seed.seed_id if seed else None}
    reply = native.prepare(record, ["copilot"], seed_id=None, **options)
    complete = tmp_path / "completion"
    monkeypatch.setattr(posix, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    monkeypatch.setattr(posix, "_completion_file", lambda *a: os.open(
        complete, os.O_CREAT | os.O_WRONLY, 0o600,
    ))

    def execute(argv: Any, **kwargs: Any) -> Any:
        assert "--interactive" not in argv
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            with launch_admission.guard(record):
                pytest.fail("Creation fence was released before the core wait")
        kwargs["admit"]()
        kwargs["spawned"](os.getpid(), locks.process_start_time(os.getpid()))
        assert seeds.peek(record) == seed
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            with launch_admission.guard(record):
                pytest.fail("Native receipt cannot release the retained owner's fence")
        return 17, True

    monkeypatch.setattr(caller, "execute", execute)
    assert posix.execute_headed(
        record, _identity(reply), reply["operation_id"], reply["argv"], complete, **options,
    ) == 17
    assert complete.read_text() == "0\n"
    with launch_admission.guard(record):
        pass
    native.returned(record, _identity(reply), reply["operation_id"], exit_code=17)
    assert [event["phase"] for event in _saved(record, reply)["events"]] == [
        "execution-requested", "process-spawned", "execution-returned",
    ]
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("changed", [False, True])
def test_direct_posix_admission_acquires_after_fd_scrub_and_fences_exec(
    record: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, changed: Any,
) -> None:
    from agent_worktrees import launch_seed_posix as posix

    reply = native.prepare(record, ["custom-backend"], seed_id=None, admission_only=True)
    if changed:
        seeds.stage(record, kind="new", text="newer intent must never be claimed")
    complete = tmp_path / "completion"
    events = []

    def scrub(_writer: Any) -> Any:
        events.append("scrub")
        with launch_admission.guard(record):
            pass

    def execute(*_args: Any) -> Any:
        assert not changed, "Changed intent must refuse before exec"
        assert events == ["scrub"]
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            with launch_admission.guard(record):
                pytest.fail("Final admission was not retained across exec")
        events.append("exec")
        raise RuntimeError("fixture exec boundary")

    monkeypatch.setattr(posix, "os", SimpleNamespace(**{
        **vars(os), "name": "posix", "fork": lambda: 123,
        "waitpid": lambda *a: (123, 0), "execvpe": execute,
        "write": lambda _descriptor, value: len(value),
    }))
    monkeypatch.setattr(posix, "_completion_file", lambda *a: os.open(
        complete, os.O_CREAT | os.O_WRONLY, 0o600,
    ))
    monkeypatch.setattr(posix, "_close_inherited_descriptors", scrub)
    with pytest.raises(ValueError if changed else RuntimeError, match="intent changed|fixture exec boundary"):
        posix.execute(
            record, _identity(reply), reply["operation_id"], reply["argv"], complete,
            admission_only=True,
        )
    assert events == (["scrub"] if changed else ["scrub", "exec"])
    with launch_admission.guard(record):
        pass
    assert [event["phase"] for event in _saved(record, reply)["events"]] == ["execution-requested"]
    if changed:
        assert _peek(record).handoff_id is None


def test_admission_backend_rechecks_preserved_seed_and_native_resume_before_creation(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import session_liveness

    seed = seeds.stage(record, kind="new", text="preserved")
    assert seed is not None
    monkeypatch.setattr(sessions, "has_mux_session", lambda *_: False)
    monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "dead")
    current = tracking.load_record(record)
    current.head_session = "native-original"
    current.sessions = [tracking.SessionEntry("native-original", current.started_at)]
    tracking.save_record(current, record)
    options: Any = {
        "admission_only": True, "preserve_seed_id": seed.seed_id,
        "native_resume_session": "native-original",
    }
    reply = native.prepare(record, ["copilot", "--resume=native-original"], seed_id=None, **options)
    with native.admission(record, _identity(reply), reply["operation_id"], **options) as check:
        check()
        assert seeds.peek(record) == seed
        monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "alive")
        with pytest.raises(ValueError, match="intent changed"):
            check()
    monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "dead")
    replacement = seeds.stage(record, kind="new", text="superseded")
    assert replacement is not None
    with pytest.raises(ValueError, match="intent changed"):
        with native.admission(record, _identity(reply), reply["operation_id"], **options):
            pytest.fail("Superseded preserved intent was admitted")
    assert seeds.peek(record) == replacement


def test_finite_phase_checker_reads_preserved_seed_under_parent_record_fence(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import session_liveness

    seed = seeds.stage(record, kind="new", text="preserved native intent")
    assert seed is not None
    current = tracking.load_record(record)
    current.head_session = "native-original"
    current.sessions = [tracking.SessionEntry("native-original", current.started_at)]
    tracking.save_record(current, record)
    monkeypatch.setattr(sessions, "has_mux_session", lambda *_: False)
    monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "dead")
    options: Any = {
        "admission_only": True, "preserve_seed_id": seed.seed_id,
        "native_resume_session": "native-original",
    }
    reply = native.prepare(record, ["copilot", "--acp"], seed_id=None, **options)
    code = (
        "import json,sys\nfrom pathlib import Path\n"
        "from agent_worktrees import launch_seed_native as native, sessions, session_liveness\n"
        "from agent_worktrees.launch_execution import Correlation\n"
        "from agent_worktrees.sessions import LiveVerdict\n"
        "sessions.has_mux_session=lambda *_:False\n"
        "sessions.verify_worktree_active=lambda *_:LiveVerdict()\n"
        "session_liveness.session_liveness=lambda *_:'dead'\n"
        "reply=json.loads(sys.argv[2])\n"
        "native.admit(Path(sys.argv[1]),Correlation(reply['attempt_id'],reply['resolution_id'],reply['parent_id']),"
        "reply['operation_id'],**json.loads(sys.argv[3]))\n"
        "print('admitted')\n"
    )
    with tracking._RecordLock(record, require_sidecar=True):
        result = run_caller_owned(
            [sys.executable, "-I", "-B", "-c", code, str(record), json.dumps(reply), json.dumps(options)],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "admitted"
    assert seeds.peek(record) == seed


def test_headed_native_receipt_releases_record_fence_before_child_exit(record: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from agent_procutil import no_window_kwargs
    from agent_remote_driver.headed import caller
    from agent_worktrees import launch_seed_posix, resource_relationship, session_liveness
    from agent_worktrees.retained_native_resume import ENVIRONMENT_KEY

    current = tracking.load_record(record)
    current.head_session = "native-original"
    current.head_revision = 1
    current.sessions = [tracking.SessionEntry("native-original", current.started_at)]
    tracking.save_record(current, record)
    identity = {"installation_root": "retained-owner", "runtime_generation": "a" * 32}
    monkeypatch.setattr(resource_relationship, "writer_identity", lambda: identity)
    monkeypatch.setenv(ENVIRONMENT_KEY, json.dumps({
        "version": 1, "operation_id": "original-recovery",
        "writer": {
            "version": 1, "identity": identity, "creation_nonce": current.creation_nonce,
            "worktree_id": current.worktree_id, "worktree_path": current.worktree_path,
            "project": current.repo,
        },
        "head": {"session_id": "native-original", "revision": 1},
    }))
    monkeypatch.setattr(sessions, "has_mux_session", lambda *_: False)
    monkeypatch.setattr(session_liveness, "session_liveness", lambda *_: "dead")
    command = ["copilot", "--resume=native-original"]
    options: Any = {"admission_only": True, "native_resume_session": "native-original"}
    reply = native.prepare(record, command, seed_id=None, **options)
    assets = record.parent / "launch-attempts" / reply["attempt_id"] / "headed.shell"
    assets.mkdir()
    completion = assets / ".native-seed-exec.complete"
    completion.touch(mode=0o600)
    if os.name != "posix":
        monkeypatch.setattr(launch_seed_posix, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
        monkeypatch.setattr(launch_seed_posix, "_completion_file", lambda _record, path: os.open(path, os.O_WRONLY))
    phases = []
    probe = (
        "import sys;from pathlib import Path;from agent_worktrees import tracking\n"
        "try:\n"
        " with tracking._RecordLock(Path(sys.argv[1]),timeout=0,require_sidecar=True): pass\n"
        "except TimeoutError: sys.exit(7)\n"
    )

    def writer(expected: Any) -> Any:
        result = run_caller_owned(
            [sys.executable, "-I", "-B", "-c", probe, str(record)],
            timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        phases.append(expected)

    def execute(_argv: Any, *, admit: Any, spawned: Any, **kwargs: Any) -> Any:
        admit()
        writer(7)
        assert ENVIRONMENT_KEY not in kwargs["environment"]
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c",
             f"import os,sys;assert {ENVIRONMENT_KEY!r} not in os.environ;sys.stdin.buffer.read(1)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=kwargs["environment"],
            **no_window_kwargs(),
        )
        try:
            spawned(child.pid, locks.process_start_time(child.pid))
            assert child.poll() is None
            writer(0)
            assert child.poll() is None
            child.communicate(input=b"x", timeout=5)
            return child.returncode, True
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)

    monkeypatch.setattr(caller, "execute", execute)
    assert launch_seed_posix.execute_headed(
        record, _identity(reply), reply["operation_id"], command, completion, **options,
    ) == 0
    assert phases == [7, 0]
    assert completion.read_text() == "0\n"


def test_unknown_output_format_refuses_before_preparation(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    seed = seeds.stage(record, kind="new", text="must not be claimed")
    assert seed is not None
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("Unknown output syntax must not enter runtime selection")
    monkeypatch.setattr(native, "require_current_runtime", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", "unknown", "--", "copilot",
    ])
    with pytest.raises(SystemExit) as stopped:
        native.main()
    assert stopped.value.code == 2
    captured: Any = capsys.readouterr()
    assert not captured.out and "invalid choice" in captured.err
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("extra", [
    [], ["--operation-id", "c" * 32],
    ["--completion-file", "missing"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--pid", "123"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--receipt-uncertain"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--seed-id", "seed"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--preserve-seed-id", "seed"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--native-resume-session", "original"],
])
def test_exec_cli_refuses_incomplete_or_mixed_outcome_before_exec(
    record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, extra: Any,
) -> None:
    from agent_worktrees import launch_seed_posix

    seed = seeds.stage(record, kind="new", text="must not change")
    assert seed is not None
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(
        launch_seed_posix, "execute",
        lambda *args: pytest.fail("Invalid exec syntax must refuse before exec"),
    )
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record), "--phase", "exec",
        "--launch-attempt-id", "a" * 32, "--launch-resolution-id", "b" * 32,
        *extra, "--", "copilot",
    ])
    assert native.main() == 3
    captured: Any = capsys.readouterr()
    assert not captured.out and "unconfirmed" in captured.err
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("headed", [False, True])
def test_exec_cli_routes_negotiated_admission_to_the_original_owner(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, headed: Any) -> None:
    from agent_worktrees import launch_seed_posix

    calls = []
    selected = "execute_headed" if headed else "execute"
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    def execute(*args: Any, **kwargs: Any) -> int:
        calls.append((args, kwargs))
        return 17
    monkeypatch.setattr(launch_seed_posix, selected, execute)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record), "--phase", "exec",
        "--launch-attempt-id", "a" * 32, "--launch-resolution-id", "b" * 32,
        "--operation-id", "c" * 32, "--completion-file", "completion",
        "--admission-only", "--preserve-seed-id", "preserved", "--native-resume-session", "original",
        *(["--backend-mode", "headed-live-owner-v1"] if headed else []),
        "--", "copilot", "--resume=original",
    ])
    assert native.main() == 17
    assert calls == [((record, audit.Correlation("a" * 32, "b" * 32), "c" * 32,
                      ["copilot", "--resume=original"], Path("completion")), {
        "admission_only": True, "preserve_seed_id": "preserved", "native_resume_session": "original",
    })]
    assert not capsys.readouterr().out


@pytest.mark.parametrize("option", [
    ["--completion-file", "not-a-preparation-option"], ["--backend-environment-fd", "3"],
])
def test_exec_only_inputs_are_not_accepted_by_existing_preparation(record: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, option: Any) -> None:
    monkeypatch.setattr(
        native, "require_current_runtime",
        lambda root: pytest.fail("Invalid completion use must refuse before runtime selection"),
    )
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record), "--phase", "prepare",
        *option, "--", "copilot",
    ])
    assert native.main() == 3
    assert "unconfirmed" in capsys.readouterr().err
