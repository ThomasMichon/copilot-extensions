"""Execution observations retain identity, never claim native prompt acceptance."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import launch_attempt, launch_execution as audit, launch_seed_exec as launcher
from agent_worktrees import launch_seed_state as seeds, output, tracking, tracking_write
from agent_worktrees.sessions import LiveVerdict

pytestmark = pytest.mark.guard


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
    monkeypatch.setattr(launcher.sessions, "verify_worktree_active", lambda *a: LiveVerdict())
    return path


def _correlation():
    return audit.Correlation(uuid4().hex, uuid4().hex)


def _history(record, correlation):
    files = list((record.parent / "launch-attempts" / correlation.attempt_id).glob("*.execution.json"))
    return [audit.read(record.parent, correlation.attempt_id, p.name.split(".")[0]) for p in files]


def _backend(monkeypatch, spawn):
    popen = launcher.subprocess.Popen
    monkeypatch.setattr(
        launcher.subprocess, "Popen",
        lambda argv, **kw: spawn(argv, **kw)
        if argv[0] in {"copilot", "custom-setup"} else popen(argv, **kw),
    )


@pytest.mark.parametrize("kind", ["new", "resume"])
def test_backend_observations_are_prompt_free_and_not_acceptance(record, monkeypatch, kind):
    seed = seeds.stage(record, kind=kind, text="private fixture\n'quoted' Unicode \u03a9")
    calls = []
    def spawn(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(pid=os.getpid(), wait=lambda: 7)
    _backend(monkeypatch, spawn)
    correlation = _correlation()
    assert launcher.launch(
        record, ["copilot"], invoke=True, seed_id=seed.seed_id, correlation=correlation,
    ) == 7
    saved, = _history(record, correlation)
    assert saved["resolution_id"] == correlation.resolution_id
    assert saved["worktree_id"] == "wt-a"
    assert saved["creation_nonce"] == tracking.load_record(record).creation_nonce
    assert [e["phase"] for e in saved["events"]] == [
        "execution-requested", "seed-claimed", "process-spawned", "execution-returned",
    ]
    claim, process = saved["events"][1:3]
    assert claim["details"]["seed_id"] == seed.seed_id
    assert claim["details"]["handoff_id"]
    assert process["details"]["boundary"] == "backend-command"
    assert process["details"]["pid"] == os.getpid()
    assert process["details"]["process_start_time"]
    assert saved["events"][-1]["details"] == {
        "exit_code": 7, "process_exit_code": 7, "receipt_uncertain": False,
    }
    assert seeds.peek(record) is None
    assert calls == [["copilot", "--interactive", seed.text]]
    serialized = json.dumps(saved)
    assert seed.text not in serialized and "private fixture" not in serialized
    assert "accepted" not in serialized and "conversation" not in serialized


def test_initial_failure_prevents_seed_claim_and_spawn(record, monkeypatch, capsys):
    seed = seeds.stage(record, kind="new", text="must remain staged")
    monkeypatch.setattr(audit.Execution, "append", lambda *a, **k: (_ for _ in ()).throw(OSError("secret")))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    assert launcher.launch(
        record, ["copilot"], invoke=True, seed_id=seed.seed_id, correlation=_correlation(),
    ) == 3
    assert seeds.peek(record) == seed
    assert "no command started" in capsys.readouterr().err


@pytest.mark.parametrize("failed_phase", ["seed-claimed", "process-spawned", "execution-returned"])
@pytest.mark.parametrize("interrupted", [False, True])
def test_lost_observation_does_not_repeat_spawn_or_skip_seed_settlement(
    record, monkeypatch, failed_phase, interrupted, capsys,
):
    seed = seeds.stage(record, kind="resume", text="exactly one argv handoff")
    append = audit.Execution.append
    calls = []
    waits = []
    def observe(self, phase, **details):
        if phase == failed_phase:
            if interrupted:
                raise KeyboardInterrupt()
            raise tracking_write.AmbiguousWriteOutcome("private diagnostic")
        return append(self, phase, **details)
    monkeypatch.setattr(audit.Execution, "append", observe)
    _backend(monkeypatch, lambda argv, **k: calls.append(argv) or SimpleNamespace(
        pid=os.getpid(), wait=lambda: waits.append(True) or 0,
    ))
    assert launcher.launch(
        record, ["copilot"], invoke=True, seed_id=seed.seed_id, correlation=_correlation(),
    ) == 3
    assert len(calls) == len(waits) == 1 and seeds.peek(record) is None
    error = capsys.readouterr().err
    assert "unconfirmed" in error and "private diagnostic" not in error


def test_interrupted_process_identity_probe_retains_settlement_and_wait(record, monkeypatch):
    seed = seeds.stage(record, kind="new", text="synthetic handoff")
    waits = []
    _backend(monkeypatch, lambda *a, **k: SimpleNamespace(
        pid=os.getpid(), wait=lambda: waits.append(True) or 0,
    ))
    monkeypatch.setattr(
        audit.locks, "process_start_time", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    correlation = _correlation()
    assert launcher.launch(
        record, ["copilot"], invoke=True, seed_id=seed.seed_id, correlation=correlation,
    ) == 3
    assert waits == [True] and seeds.peek(record) is None
    saved, = _history(record, correlation)
    assert saved["events"][-1]["details"]["process_exit_code"] == 0
    assert saved["events"][2]["details"]["process_start_time"] is None


def test_failed_spawn_retains_seed_and_records_no_process(record, monkeypatch):
    seed = seeds.stage(record, kind="resume", text="retry remains owned")
    _backend(monkeypatch, lambda *a, **k: (_ for _ in ()).throw(OSError("missing")))
    correlation = _correlation()
    assert launcher.launch(
        record, ["copilot"], invoke=True, seed_id=seed.seed_id, correlation=correlation,
    ) == 3
    assert seeds.peek(record) == seed
    saved, = _history(record, correlation)
    assert [e["phase"] for e in saved["events"]] == [
        "execution-requested", "seed-claimed", "execution-returned",
    ]


def test_wrapper_launch_is_not_a_backend_start(record, monkeypatch):
    _backend(monkeypatch, lambda *a, **k: SimpleNamespace(pid=os.getpid(), wait=lambda: 0))
    correlation = _correlation()
    assert launcher.launch(record, ["custom-setup"], admission_only=True, correlation=correlation) == 0
    saved, = _history(record, correlation)
    assert saved["events"][1]["details"]["boundary"] == "launcher-command"


def test_child_and_concurrent_invocations_never_share_execution_ids(record):
    correlation = _correlation()
    first, second = audit.Execution(record, correlation), audit.Execution(record, correlation)
    first.append("execution-requested")
    second.append("execution-requested")
    child = audit.Execution(record, first.child())
    child.append("execution-requested")
    histories = _history(record, correlation)
    assert len({h["operation_id"] for h in histories}) == 3
    assert {h["resolution_id"] for h in histories} == {correlation.resolution_id}
    assert child.subject["parent_id"] == first.subject["operation_id"]


@pytest.mark.parametrize("exception", [ValueError("private"), KeyboardInterrupt()])
def test_exception_retains_unknown_without_replay(record, exception):
    correlation = _correlation()
    calls = []
    def work(execution):
        calls.append(execution.subject["operation_id"])
        raise exception
    with pytest.raises(type(exception)):
        audit.run(record, correlation, work)
    saved, = _history(record, correlation)
    assert saved["events"][-1]["phase"] == "unknown"
    assert len(calls) == 1 and "private" not in json.dumps(saved)


@pytest.mark.parametrize("damage", [
    {"schema": "foreign"}, {"version": True}, {"events": []}, {"parent_id": "invalid"},
    {"creation_nonce": ""}, {"unexpected": "field"},
])
def test_corrupt_history_is_not_repaired(record, damage):
    correlation = _correlation()
    execution = audit.Execution(record, correlation)
    execution.append("execution-requested")
    path = launch_attempt._path(record.parent, correlation.attempt_id, execution.subject["operation_id"], execution=True)
    path.write_text(json.dumps({**json.loads(path.read_text(encoding="utf-8")), **damage}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        execution.append("execution-returned", exit_code=0)
    assert path.read_bytes() == before


def test_terminal_and_out_of_order_events_are_refused(record):
    execution = audit.Execution(record, _correlation())
    execution.append("execution-requested")
    execution.append("process-spawned", pid=1, process_start_time=None, boundary="backend-command")
    with pytest.raises(ValueError):
        execution.append("seed-claimed", seed_id="fixture", handoff_id=uuid4().hex)
    execution.append("execution-returned", exit_code=0)
    with pytest.raises(ValueError):
        execution.append("unknown")


@pytest.mark.parametrize("monitoring", [False, True])
def test_old_resident_is_excluded_before_sending_an_execution_write(record, monkeypatch, monitoring):
    from agent_worktrees import locks, status_monitor_runtime

    old = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": ["launch_attempt_resolution"],
        "tracking_write_verb_versions": {"launch_attempt_resolution": 1},
        "pid": os.getpid(), "start_time": locks.process_start_time(os.getpid()),
    }
    monkeypatch.setattr(locks, "read_lock", lambda *a: old)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: monitoring)
    monkeypatch.setattr(
        status_monitor_runtime, "_ensure_status_monitor",
        lambda *a: pytest.fail("boot requested for known incompatible live owner"),
    )
    monkeypatch.setattr(tracking_write, "time", SimpleNamespace(
        time=lambda: 0, sleep=lambda *a: pytest.fail("waited for incompatible live owner"),
    ))
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *a, **kw: pytest.fail("sent new verb to old owner"),
    )
    execution = audit.Execution(record, _correlation())
    execution.append("execution-requested")
    assert audit.read(record.parent, execution.subject["attempt_id"], execution.subject["operation_id"])


def test_hardlinked_receipt_is_not_read_or_replaced(record):
    execution = audit.Execution(record, _correlation())
    execution.append("execution-requested")
    path = launch_attempt._path(record.parent, execution.subject["attempt_id"], execution.subject["operation_id"], execution=True)
    alias = path.with_suffix(".other")
    os.link(path, alias)
    before = alias.read_bytes()
    with pytest.raises(ValueError):
        execution.append("execution-returned", exit_code=0)
    assert alias.read_bytes() == before


@pytest.mark.parametrize("script_name", ["default-setup.ps1", "default-setup.sh"])
def test_setup_propagates_explicit_child_identity_only_with_declared_boundary(record, script_name):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    script = scripts / script_name
    command = ["pwsh", "-File", str(script)] if script.suffix == ".ps1" else ["bash", str(script)]
    correlation = audit.Execution(record, _correlation()).child()
    result = launcher._setup_command(record, "seed-a", command, correlation)
    assert result is not None
    assert all(v in result for v in (correlation.attempt_id, correlation.resolution_id, correlation.parent_id))
    content = script.read_text(encoding="utf-8-sig")
    for flag in ("--launch-attempt-id", "--launch-resolution-id", "--launch-parent-id"):
        assert flag in content


def test_legacy_custom_setup_is_not_given_new_flags_or_allowed_to_drop_correlation(record, tmp_path):
    script = tmp_path / "custom.sh"
    script.write_text(launcher.BOUNDARY_MARKER + "\n", encoding="utf-8")
    command = ["bash", str(script)]
    assert launcher._setup_command(record, "seed-a", command) is not None
    assert launcher._setup_command(record, "seed-a", command, _correlation()) is None


def test_resolution_binds_actual_deferred_plan_before_publication(record, monkeypatch):
    monkeypatch.setattr(launch_attempt.cfg, "tracking_dir", lambda: record.parent)
    dispatch = seeds._dispatch
    monkeypatch.setattr(seeds, "_dispatch", lambda verb, args, **kw:
                        launch_attempt._apply(args) if verb == "launch_attempt_resolution"
                        else dispatch(verb, args, **kw))
    correlation = _correlation()
    command = launcher.deferred_command(["copilot"], record, seed_id="seed-a")
    def work():
        output._json_output({"launch": {"action": "exec", "cmd": command, "worktree_id": "wt-a"}})
        return 0
    with output.capture_json_output() as captured:
        assert launch_attempt.resolve(argparse.Namespace(launch_attempt_id=correlation.attempt_id), work) == 0
    launch = json.loads(captured.getvalue())["launch"]
    prefix, backend = launch["cmd"][:launch["cmd"].index("--")], launch["cmd"][launch["cmd"].index("--") + 1:]
    assert prefix[:3] == [sys.executable, "-m", "agent_worktrees.launch_seed_exec"]
    assert prefix[prefix.index("--launch-attempt-id") + 1] == correlation.attempt_id
    assert prefix[prefix.index("--launch-resolution-id") + 1] == launch["launch_attempt_receipt"]["operation_id"]
    assert backend == ["copilot"]
