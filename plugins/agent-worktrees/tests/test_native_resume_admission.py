"""Native ACP and mux launch share real owner-side process exclusion."""

from __future__ import annotations

import subprocess
import sys
import argparse
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_worktrees import launch_admission, launch_seed_exec
from agent_worktrees import session_liveness
from agent_worktrees import resolve_cli
from agent_worktrees.sessions import LiveVerdict
from agent_worktrees.retained_native_resume import ENVIRONMENT_KEY
import json

pytestmark = pytest.mark.contract("agent_worktrees.native_resume_admission")


@pytest.fixture
def owner(tmp_path, monkeypatch):
    path = tmp_path / "wt-example.yaml"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = SimpleNamespace(
        resolved_head_session="native-original", worktree_id="wt-example",
        worktree_path=str(checkout),
        pending_handoffs=[], pending_seed=None, status="active",
    )
    monkeypatch.setattr(launch_seed_exec.tracking, "load_record", lambda _: record)
    monkeypatch.setattr(launch_seed_exec.launch_seed_state, "peek", lambda _, **kwargs: None)
    monkeypatch.setattr(launch_seed_exec.sessions, "has_mux_session", lambda _: False)
    monkeypatch.setattr(
        launch_seed_exec.sessions, "verify_worktree_active", lambda _: LiveVerdict(),
    )
    monkeypatch.setattr(session_liveness, "session_liveness", lambda _: "dead")
    return path, record


@pytest.mark.parametrize("state", ["live", "unknown"])
def test_unrepresented_native_incumbent_refuses_before_spawn(owner, monkeypatch, state):
    path, _record = owner
    monkeypatch.setattr(session_liveness, "session_liveness", lambda _: state)
    spawn = Mock()
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 3
    spawn.assert_not_called()


@pytest.mark.parametrize("change", [
    {"resolved_head_session": "new-head"}, {"pending_handoffs": [object()]},
    {"status": "finalized"}, {"status": "finalizing"}, {"execution_leg_opaque": True},
    {"session_backend_opaque": True},
])
def test_changed_owner_facts_refuse_under_the_launch_fence(owner, monkeypatch, change):
    path, record = owner
    for key, value in change.items():
        setattr(record, key, value)
    spawn = Mock()
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 3
    spawn.assert_not_called()


def test_native_owner_fence_excludes_other_process_until_child_exits(owner, monkeypatch):
    path, _record = owner
    real_popen = subprocess.Popen
    code = (
        "import sys; from pathlib import Path; "
        "from agent_worktrees.launch_admission import guard, LaunchAdmissionBusy\n"
        "try:\n"
        " with guard(Path(sys.argv[1])): sys.exit(0)\n"
        "except LaunchAdmissionBusy: sys.exit(7)\n"
    )

    def excluded():
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)], capture_output=True,
            timeout=10, **no_window_kwargs(),
        )
        assert result.returncode == 7, result.stderr.decode(errors="replace")
        return 0

    def spawn(argv, **kwargs):
        if argv[0] != "copilot":
            return real_popen(argv, **kwargs)
        excluded()
        return SimpleNamespace(wait=excluded)

    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 0
    with launch_admission.guard(path):
        pass


def test_deferred_plan_carries_native_authority_at_execution_boundary(owner):
    path, _record = owner
    command = launch_seed_exec.deferred_command(
        ["copilot", "--acp"], path, admission_only=True,
        native_resume_session="native-original",
    )
    assert command[command.index("--native-resume-session") + 1] == "native-original"
    assert command.index("--native-resume-session") < command.index("--")


def test_native_record_becoming_finalizing_after_planning_refuses_execution(owner, monkeypatch):
    path, record = owner
    planned = launch_seed_exec.deferred_command(
        ["copilot", "--acp"], path, admission_only=True,
        native_resume_session="native-original",
    )
    record.status = "finalizing"
    spawn = Mock()
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, planned[planned.index("--") + 1:], admission_only=True,
        native_resume_session="native-original",
    ) == 3
    spawn.assert_not_called()


def retained_guard(record):
    return {
        "version": 1, "operation_id": "original-recovery",
        "writer": {
            "version": 1, "identity": {
                "installation_root": "retained-owner", "runtime_generation": "a" * 32,
            },
            "creation_nonce": "retained-nonce", "worktree_id": record.worktree_id,
            "worktree_path": record.worktree_path, "project": "example",
        },
        "head": {"session_id": "native-original", "revision": 7},
    }


@pytest.mark.parametrize("change", [
    {"creation_nonce": "another-nonce"}, {"head_revision": 8},
    {"repo": "another-project"}, {"worktree_id": "another-workspace"},
    {"worktree_path": "another-path"},
])
def test_retained_recovery_checks_original_pairing_at_process_creation(owner, monkeypatch, change):
    from agent_worktrees import resource_relationship

    path, record = owner
    record.repo, record.creation_nonce, record.head_revision = "example", "retained-nonce", 7
    guard = retained_guard(record)
    monkeypatch.setattr(resource_relationship, "writer_identity", lambda: guard["writer"]["identity"])
    monkeypatch.setenv(ENVIRONMENT_KEY, json.dumps(guard))
    for key, value in change.items():
        setattr(record, key, value)
    spawn = Mock()
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 3
    spawn.assert_not_called()


def test_retained_recovery_fence_preserves_original_exact_native_launch(owner, monkeypatch):
    from agent_worktrees import resource_relationship

    path, record = owner
    record.repo, record.creation_nonce, record.head_revision = "example", "retained-nonce", 7
    guard = retained_guard(record)
    monkeypatch.setattr(resource_relationship, "writer_identity", lambda: guard["writer"]["identity"])
    monkeypatch.setenv(ENVIRONMENT_KEY, json.dumps(guard))
    spawn = Mock(return_value=SimpleNamespace(wait=lambda: 0))
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 0
    spawn.assert_called_once()
    assert ENVIRONMENT_KEY not in spawn.call_args.kwargs["env"]
    assert json.loads(os.environ[ENVIRONMENT_KEY]) == guard


def test_native_head_record_writer_is_excluded_through_process_creation_not_wait(owner, monkeypatch):
    path, _record = owner
    real_popen = subprocess.Popen
    code = (
        "import sys; from pathlib import Path; from agent_worktrees import tracking\n"
        "try:\n"
        " with tracking._RecordLock(Path(sys.argv[1]), timeout=0, require_sidecar=True): pass\n"
        "except TimeoutError: sys.exit(7)\n"
    )
    phases = []

    def writer(phase):
        result = subprocess.run(
            [sys.executable, "-c", code, str(path)], capture_output=True,
            timeout=10, **no_window_kwargs(),
        )
        phases.append(phase)
        assert result.returncode == (0 if phase == "wait" else 7), result.stderr.decode(errors="replace")
        return 0

    prepare = launch_seed_exec._prepare

    def preparing(*args, **kwargs):
        result = prepare(*args, **kwargs)
        writer("validated")
        return result

    def spawn(argv, **kwargs):
        if argv[0] != "copilot":
            return real_popen(argv, **kwargs)
        writer("process-creation")
        return SimpleNamespace(wait=lambda: writer("wait"))

    monkeypatch.setattr(launch_seed_exec, "_prepare", preparing)
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 0
    assert phases == ["validated", "process-creation", "wait"]


@pytest.mark.parametrize("raw", ["{", "null", "[]", '{"version":2}'])
def test_unrecognized_retained_recovery_never_degrades_to_ordinary_launch(owner, monkeypatch, raw):
    path, _record = owner
    monkeypatch.setenv(ENVIRONMENT_KEY, raw)
    spawn = Mock()
    monkeypatch.setattr(launch_seed_exec.subprocess, "Popen", spawn)
    assert launch_seed_exec.launch(
        path, ["copilot", "--acp", "--stdio"],
        admission_only=True, native_resume_session="native-original",
    ) == 3
    spawn.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"no_resume": False}, {"worktree_id": None}, {"seed": "new task"},
    {"new_worktree": True}, {"native_resume_session": ""},
])
def test_native_resolve_rejects_fresh_or_seeded_intent_before_allocation(changes):
    values = {
        "native_resume_session": "native-original", "worktree_id": "wt-example",
        "no_resume": True,
    }
    values.update(changes)
    assert resolve_cli.cmd_resolve(argparse.Namespace(**values)) == 3
