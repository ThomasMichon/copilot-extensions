"""Finite native handoffs retain existing seed and execution authorities."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees import config as cfg
from agent_worktrees import launch_admission, launch_execution as audit, launch_seed_exec as legacy
from agent_worktrees import launch_seed_native as native, launch_seed_state as seeds, locks, tracking
from agent_worktrees.sessions import LiveVerdict

pytestmark = pytest.mark.guard


@pytest.mark.parametrize("setup", [False, True])
def test_empty_executable_refuses_before_claim_or_process_query(record, monkeypatch, setup):
    seed = seeds.stage(record, kind="new", text="must remain staged")
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


def test_finite_callback_import_does_not_load_preparation_or_seed_daemon():
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


def test_finite_monitor_paths_do_not_load_command_surface():
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


def test_finite_monitor_paths_preserve_loaded_cli_overrides(monkeypatch, tmp_path):
    from agent_worktrees import __main__ as core, status_monitor_runtime as monitor

    monkeypatch.setattr(core, "_aw_runtime_home", lambda: tmp_path)
    assert monitor._monitor_lock_path() == tmp_path / "status-monitor.lock"
    monkeypatch.setattr(core, "_monitor_lock_path", lambda: tmp_path / "selected.lock")
    assert monitor._monitor_lock_path() == tmp_path / "selected.lock"


@pytest.fixture
def record(tmp_path, monkeypatch):
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
        cfg.detect_platform(), tmp_path,
    ).yaml_path
    monkeypatch.setattr(legacy.sessions, "verify_worktree_active", lambda *a: LiveVerdict())
    return path


def _identity(reply):
    return audit.Correlation(reply["attempt_id"], reply["resolution_id"], reply["parent_id"])


def _saved(record, reply):
    return audit.read(record.parent, reply["attempt_id"], reply["operation_id"])


def _spawn(record, reply, **kwargs):
    native.spawned(
        record, _identity(reply), reply["operation_id"],
        pid=os.getpid(), process_start_time=locks.process_start_time(os.getpid()), **kwargs,
    )


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_finite_prepare_claims_without_spawning_or_finishing(record, monkeypatch, kind):
    seed = seeds.stage(record, kind=kind, text="private fixture\nquotes ' \" \u03a9")
    popen = legacy.subprocess.Popen
    def forbid_backend(argv, **kwargs):
        if argv[0] == "copilot":
            pytest.fail("The finite provider must not spawn a backend")
        return popen(argv, **kwargs)
    monkeypatch.setattr(legacy.subprocess, "Popen", forbid_backend)
    monkeypatch.setenv(audit.NATIVE_HINT_ENV, "stale")
    reply = native.prepare(record, ["copilot", "", "--", "literal"], seed_id=seed.seed_id)
    assert reply["version"] == 1
    assert reply["argv"] == ["copilot", "", "--interactive", seed.text, "--", "literal"]
    saved = _saved(record, reply)
    assert [event["phase"] for event in saved["events"]] == ["execution-requested", "seed-claimed"]
    assert seed.text not in json.dumps(saved)
    assert json.loads(reply["environment"][audit.NATIVE_HINT_ENV])["operation_id"] == reply["operation_id"]
    claimed = seeds.peek(record)
    assert claimed.text == seed.text and claimed.handoff_id
    with pytest.raises(ValueError, match="refused"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    assert seeds.peek(record) == claimed


def test_native_spawn_and_actual_return_reuse_existing_receipts(record):
    seed = seeds.stage(record, kind="resume", text="one native prompt")
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


def test_definite_spawn_failure_restores_once_without_automatic_retry(record):
    seed = seeds.stage(record, kind="new", text="retained after OS refusal")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    native.spawn_failed(record, _identity(reply), reply["operation_id"])
    restored = seeds.peek(record)
    assert restored.seed_id == seed.seed_id and restored.text == seed.text
    assert restored.handoff_id is None
    assert _saved(record, reply)["events"][-1]["phase"] == "execution-returned"
    with pytest.raises(ValueError, match="do not replay"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])


def test_unconfirmed_child_identity_never_finishes_or_restores(record):
    seed = seeds.stage(record, kind="new", text="unknown remains held")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    claimed = seeds.peek(record)
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.spawned(
            record, _identity(reply), reply["operation_id"],
            pid=os.getpid(), process_start_time="0",
        )
    assert seeds.peek(record) == claimed
    assert _saved(record, reply)["events"][-1]["phase"] == "seed-claimed"
    with pytest.raises(ValueError, match="recorded spawn"):
        native.returned(record, _identity(reply), reply["operation_id"], exit_code=0)


def test_spawned_backend_cannot_restore_or_claim_another_spawn(record):
    seed = seeds.stage(record, kind="new", text="already launched")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    _spawn(record, reply)
    with pytest.raises(ValueError, match="cannot restore"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    with pytest.raises(ValueError, match="already observed"):
        _spawn(record, reply)
    assert seeds.peek(record) is None


def test_changed_incarnation_or_correlation_cannot_finish_seed(record):
    seed = seeds.stage(record, kind="new", text="must not cross incarnations")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    wrong = audit.Correlation(reply["attempt_id"], uuid4().hex)
    with pytest.raises(ValueError, match="identity or incarnation"):
        native.spawn_failed(record, wrong, reply["operation_id"])
    current = tracking.load_record(record)
    current.creation_nonce = uuid4().hex
    tracking.save_record(current, record)
    with pytest.raises(ValueError, match="identity or incarnation"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    assert seeds.peek(record).handoff_id


def test_superseded_seed_is_not_finished_or_restored(record):
    seed = seeds.stage(record, kind="new", text="old intent")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    replacement = seeds.stage(record, kind="resume", text="new intent")
    with pytest.raises(ValueError, match="missing or superseded"):
        _spawn(record, reply)
    with pytest.raises(ValueError, match="missing or superseded"):
        native.spawn_failed(record, _identity(reply), reply["operation_id"])
    assert seeds.peek(record) == replacement


def test_uncertain_cleanup_preserves_actual_child_outcome(record):
    reply = native.prepare(record, ["copilot"], seed_id=None)
    _spawn(record, reply)
    native.returned(
        record, _identity(reply), reply["operation_id"], exit_code=0, receipt_uncertain=True,
    )
    assert _saved(record, reply)["events"][-1]["details"] == {
        "exit_code": 3, "process_exit_code": 0, "receipt_uncertain": True,
    }


def test_missing_initial_receipt_cannot_claim_seed(record, monkeypatch):
    seed = seeds.stage(record, kind="new", text="must remain unclaimed")
    def fail(*args, **kwargs):
        raise OSError("fixture receipt failure")
    monkeypatch.setattr(audit.Execution, "append", fail)
    with pytest.raises(OSError):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    assert seeds.peek(record) == seed


def test_unconfirmed_claim_observation_is_terminal_not_replay_authority(record, monkeypatch):
    seed = seeds.stage(record, kind="new", text="held after ambiguous observation")
    append = audit.Execution.append
    correlation = audit.Correlation(uuid4().hex, uuid4().hex)
    def fail_claim(self, phase, **details):
        if phase == "seed-claimed":
            raise OSError("fixture observation failure")
        return append(self, phase, **details)
    monkeypatch.setattr(audit.Execution, "append", fail_claim)
    with pytest.raises(ValueError, match="observation is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, correlation=correlation)
    claimed = seeds.peek(record)
    assert claimed.handoff_id
    files = list((record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json"))
    operation_id = files[0].name.split(".")[0]
    assert audit.read(record.parent, correlation.attempt_id, operation_id)["events"][-1]["phase"] == "unknown"
    with pytest.raises(ValueError, match="do not replay"):
        native.spawn_failed(record, correlation, operation_id)
    assert seeds.peek(record) == claimed


def test_unconfirmed_finish_retains_claim_and_actual_spawn_observation(record, monkeypatch):
    seed = seeds.stage(record, kind="new", text="do not resubmit on finish failure")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    monkeypatch.setattr(seeds, "finish", lambda *a: {"finished": False, "reason": "fixture"})
    with pytest.raises(ValueError, match="completion is unconfirmed"):
        _spawn(record, reply)
    assert seeds.peek(record).handoff_id
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
])
def test_cli_refuses_incomplete_or_cross_phase_outcomes(record, monkeypatch, capsys, extra):
    seed = seeds.stage(record, kind="new", text="no child on malformed request")
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr("sys.argv", [
        "launch_seed_native", "--contract-version", "1", "--root", str(cfg.install_dir()),
        "--record", str(record), *extra,
    ])
    assert native.main() == 3
    captured = capsys.readouterr()
    assert not captured.out and "do not automatically retry" in captured.err
    assert seed.text not in captured.err
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("fault", [None, "captured-old-slot", "missing-completion", "missing-bootstrap", "foreign-root"])
def test_native_cli_selects_only_current_completed_same_installation(tmp_path, monkeypatch, fault):
    root = tmp_path / "engine"
    prefix = root / "versions" / "1.2.3"
    (prefix / ".runtime-bootstrap").mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    complete = prefix / ".install-complete.json"
    complete.write_text("{}", encoding="utf-8")
    bootstrap = prefix / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.write_text("# fixture\n", encoding="utf-8")
    monkeypatch.setattr(native.sys, "prefix", str(prefix))
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
        monkeypatch.setattr(cfg, "install_dir", lambda: foreign)
    if fault:
        with pytest.raises(ValueError):
            native.require_current_runtime(root)
    else:
        native.require_current_runtime(root)
        with pytest.raises(ValueError):
            native.require_current_runtime(Path("relative-engine"))


def _setup(record, *, seed_id=None, **options):
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


def test_setup_preparation_records_existing_owner_without_claiming_seed(record):
    seed = seeds.stage(record, kind="new", text="retained until actual backend")
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
def test_setup_admission_releases_before_child_lifetime_and_never_takes_seed(record, preserve):
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


def test_setup_admission_cannot_hide_new_seed_or_bypass_busy_mutation(record):
    with launch_admission.guard(record):
        with pytest.raises(launch_admission.LaunchAdmissionBusy):
            _setup(record, admission_only=True)
    seed = seeds.stage(record, kind="new", text="later intent")
    with pytest.raises(ValueError, match="refused"):
        _setup(record, admission_only=True)
    assert seeds.peek(record) == seed


def test_setup_failure_leaves_prompt_unclaimed_and_never_records_backend(record):
    seed = seeds.stage(record, kind="new", text="setup must not consume this")
    reply = _setup(record, seed_id=seed.seed_id)
    native.interrupted(record, _identity(reply), reply["operation_id"])
    assert seeds.peek(record) == seed
    saved = _saved(record, reply)
    assert saved["events"][-1]["phase"] == "unknown"
    assert all(event.get("details", {}).get("boundary") != "backend-command" for event in saved["events"])


def test_setup_refuses_unconfirmed_owner_before_any_receipt(record):
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(
            record, ["copilot"], seed_id=None, setup=True,
            pid=os.getpid(), process_start_time="0",
        )
    assert not list((record.parent / "launch-attempts").glob("*/*.execution.json"))


@pytest.mark.parametrize("admission_only", [False, True])
def test_setup_can_resolve_process_start_time_from_its_canonical_owner(record, admission_only):
    seed = seeds.stage(record, kind="resume", text="retained setup intent")
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
    record, monkeypatch, pid,
):
    seed = seeds.stage(record, kind="new", text="unclaimed")
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: pytest.fail("Invalid PID query"))
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, setup=True, pid=pid)
    assert seeds.peek(record) == seed
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("birth", [None, ""])
def test_setup_refuses_unavailable_provider_identity_before_any_effects(record, monkeypatch, birth):
    seed = seeds.stage(record, kind="new", text="unclaimed")
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: birth)
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(record, ["copilot"], seed_id=seed.seed_id, setup=True, pid=os.getpid())
    assert seeds.peek(record) == seed
    assert not (record.parent / "launch-attempts").exists()


def test_explicit_empty_birth_does_not_opt_into_provider_identity(record, monkeypatch):
    monkeypatch.setattr(locks, "process_start_time", lambda *_a: pytest.fail("Explicit invalid birth"))
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.prepare(
            record, ["copilot"], seed_id=None, setup=True, pid=os.getpid(), process_start_time="",
        )
    assert not (record.parent / "launch-attempts").exists()


@pytest.mark.parametrize("supplied", [False, True])
def test_setup_birth_change_during_preparation_is_unknown_not_a_recorded_spawn(
    record, monkeypatch, supplied,
):
    seed = seeds.stage(record, kind="new", text="unclaimed")
    prepare = legacy._prepare
    birth = locks.process_start_time(os.getpid())

    def replace_process(*args, **kwargs):
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
    record, monkeypatch, capsysbinary, output_format,
):
    seed = seeds.stage(record, kind="new", text="retained private intent")
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
    captured = capsysbinary.readouterr()
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


def test_backend_spawn_callback_does_not_infer_missing_child_birth(record, monkeypatch):
    seed = seeds.stage(record, kind="new", text="held backend intent")
    reply = native.prepare(record, ["copilot"], seed_id=seed.seed_id)
    monkeypatch.setattr(
        locks, "process_start_time", lambda *_a: pytest.fail("Missing child birth must not be inferred"),
    )
    with pytest.raises(ValueError, match="identity is unconfirmed"):
        native.spawned(
            record, _identity(reply), reply["operation_id"], pid=os.getpid(), process_start_time=None,
        )
    assert seeds.peek(record).handoff_id
    assert _saved(record, reply)["events"][-1]["phase"] == "seed-claimed"


@pytest.mark.parametrize("environment", [None, {}, {"ODD-NAME": "value=\n'\u03a9", "EMPTY": ""}])
def test_nul_reply_preserves_literal_fields_and_distinguishes_environment(environment):
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
    decoded_environment = None if fields[6] == "-1" else {}
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
def test_nul_reply_refuses_unrepresentable_fields_without_partial_output(field, value):
    reply = {
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": None, "environment": None, "argv": ["copilot"],
        field: value,
    }
    with pytest.raises(ValueError):
        native.nul_reply(reply)


@pytest.mark.parametrize("output_format", ["json", "nul"])
def test_cli_preparation_output_formats_keep_actual_seed_custody(
    record, monkeypatch, capsysbinary, output_format,
):
    seed = seeds.stage(record, kind="new", text="private fixture\nquotes ' \" \u03a9")
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", output_format,
        "--seed-id", seed.seed_id, "--", "copilot", "", "--", "literal",
    ])
    assert native.main() == 0
    captured = capsysbinary.readouterr()
    assert not captured.err
    held = seeds.peek(record)
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


def test_nul_callback_request_is_rejected_before_runtime_or_receipt_mutation(record, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("A callback output-format error must precede runtime selection and mutation")
    monkeypatch.setattr(native, "require_current_runtime", forbidden)
    monkeypatch.setattr(native, "returned", forbidden)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "returned", "--output-format", "nul", "--exit-code", "0",
    ])
    assert native.main() == 3
    captured = capsys.readouterr()
    assert not captured.out and "do not automatically retry" in captured.err
    assert not list((record.parent / "launch-attempts").glob("*/*.execution.json"))


def test_unrepresentable_prepared_prompt_remains_held_with_unknown_outcome(record, monkeypatch, capsysbinary):
    seed = seeds.stage(record, kind="new", text="invalid\0prompt")
    correlation = audit.Correlation(uuid4().hex, uuid4().hex)
    monkeypatch.setattr(native, "require_current_runtime", lambda root: None)
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record),
        "--phase", "prepare", "--output-format", "nul", "--seed-id", seed.seed_id,
        *correlation.options(), "--", "copilot",
    ])
    assert native.main() == 3
    captured = capsysbinary.readouterr()
    assert not captured.out and b"do not automatically retry" in captured.err
    assert b"invalid" not in captured.err
    held = seeds.peek(record)
    assert held.seed_id == seed.seed_id and held.handoff_id
    saved, = (record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json")
    assert audit.read(record.parent, correlation.attempt_id, saved.name.split(".")[0])[
        "events"
    ][-1]["phase"] == "unknown"


def test_bash_reads_counted_nul_reply_without_evaluating_literal_arguments(tmp_path):
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


def test_selected_runtime_describes_literal_output_support_without_preparation():
    result = run_caller_owned(
        [sys.executable, "-I", "-B", "-m", "agent_worktrees.launch_seed_native", "--describe-contract"],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "contract": "agent-worktrees.native-seed", "version": 1,
        "preparation_output_formats": ["json", "nul"],
        "nul_reply_schema": "agent-worktrees.native-seed-reply.v1",
        "setup_process_start_time_sources": ["caller", "provider"],
        "backend_execution_modes": ["posix-exec-v1"] if os.name == "posix" else [],
    }
    assert not result.stderr


def test_unknown_output_format_refuses_before_preparation(record, monkeypatch, capsys):
    seed = seeds.stage(record, kind="new", text="must not be claimed")
    def forbidden(*args, **kwargs):
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
    captured = capsys.readouterr()
    assert not captured.out and "invalid choice" in captured.err
    assert seeds.peek(record) == seed


@pytest.mark.parametrize("extra", [
    [], ["--operation-id", "c" * 32],
    ["--completion-file", "missing"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--pid", "123"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--receipt-uncertain"],
    ["--operation-id", "c" * 32, "--completion-file", "missing", "--seed-id", "seed"],
])
def test_exec_cli_refuses_incomplete_or_mixed_outcome_before_exec(
    record, monkeypatch, capsys, extra,
):
    from agent_worktrees import launch_seed_posix

    seed = seeds.stage(record, kind="new", text="must not change")
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
    captured = capsys.readouterr()
    assert not captured.out and "unconfirmed" in captured.err
    assert seeds.peek(record) == seed


def test_completion_file_is_not_accepted_by_existing_preparation(record, monkeypatch, capsys):
    monkeypatch.setattr(
        native, "require_current_runtime",
        lambda root: pytest.fail("Invalid completion use must refuse before runtime selection"),
    )
    monkeypatch.setattr(sys, "argv", [
        "launch_seed_native", "--contract-version", "1",
        "--root", str(cfg.install_dir()), "--record", str(record), "--phase", "prepare",
        "--completion-file", "not-a-preparation-option", "--", "copilot",
    ])
    assert native.main() == 3
    assert "unconfirmed" in capsys.readouterr().err
