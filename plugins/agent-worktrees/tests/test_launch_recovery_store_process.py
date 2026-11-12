"""Contained process-boundary CAS controls, not native recovery qualification."""

from dataclasses import asdict, replace
import json
import os
import subprocess
import sys
import time

import pytest

from agent_remote_driver.headed.recovery import RecoveryDescriptor, RecoveryRecord, RecoveryStep
from agent_remote_driver.headed.recovery_codec import encode_recovery_record
from agent_worktrees import config as cfg, launch_execution, launch_recovery_store as state
from agent_worktrees import launch_recovery_admission as admission, tracking, tracking_write


_CHILD = """
import json
from pathlib import Path
import sys
import time
from agent_remote_driver.headed.recovery_codec import decode_recovery_record
from agent_worktrees import launch_recovery_store as state, locks
locks.read_lock = lambda *_: None
path, raw, ready, gate = sys.argv[1:]
record = decode_recovery_record(raw.encode("utf-8"))
store = state.WorktreeRecoveryStore(Path(path))
write = state._write
def delayed_write(*args):
    time.sleep(0.25)
    return write(*args)
state._write = delayed_write
Path(ready).touch()
deadline = time.monotonic() + 15
while not Path(gate).exists():
    if time.monotonic() > deadline:
        raise TimeoutError("fixture barrier did not open")
    time.sleep(0.01)
print(json.dumps({"committed": store.compare_and_swap(record.descriptor, 0, record)}))
"""

_INSPECT_CHILD = """
import json
from pathlib import Path
import sys
from agent_remote_driver.headed.recovery_codec import decode_recovery_record
from agent_worktrees import launch_execution, launch_recovery_admission as admission, tracking_write
path, raw, launch_id, original_intent = sys.argv[1:]
record = decode_recovery_record(raw.encode("utf-8"))
binding = admission.RecoveryBinding(
    record.descriptor, launch_execution.Correlation("a" * 32, "b" * 32), launch_id,
    admission.SourceSelection("target", "account", "recipient", "route", "controller-1"),
    admission.OwnerIntent(**json.loads(original_intent)), object(),
)
class Lease:
    def remaining_seconds(self):
        return 30
def refuse(*args, **kwargs):
    raise AssertionError("passive inspector attempted a write")
tracking_write.dispatch = refuse
inspector = admission.RecoveryAdmissionInspector(Path(path), binding, record.operation_id)
observation = admission.RecoveryObservation(binding, "approved", 1, Lease())
first = inspector.inspect(record.descriptor, record.operation_id, "retire_intent", 1, observation)
second = inspector.inspect(record.descriptor, record.operation_id, "retire_intent", 1, observation)
assert first == second
print(json.dumps(first))
"""


@pytest.fixture
def journal(tmp_path):
    directory = cfg.project_dir("demo")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(json.dumps({
        "repo_name": "demo", "machine": "test",
        "repos": {"demo": {"anchor": str(tmp_path), "default_branch": "dev"}},
    }), encoding="utf-8")
    record = tracking.create_new_record(
        "wt-a", "worktree/wt-a", str(tmp_path / "checkout"), "demo", "test",
        "windows", directory / "worktrees",
    )
    descriptor = RecoveryDescriptor(
        tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        record.worktree_id, "descriptor-1", "source", "revision", "conversation",
        "environment", "boot", "scope", "incarnation", 1,
    )
    return record, descriptor


@pytest.mark.parametrize("changed_descriptor", [False, True])
def test_two_fresh_owners_reserve_one_operation_and_reopen(tmp_path, journal, changed_descriptor):
    record, descriptor = journal
    candidates = [
        RecoveryRecord(
            "one-operation",
            replace(descriptor, boot_ref="other-boot") if changed_descriptor and index else descriptor,
            (RecoveryStep("retire_intent", f"intent-{index}", ("unknown-effect",)),),
            ("retained-hold",),
        )
        for index in range(2)
    ]
    children = []
    gate = tmp_path / "go"
    try:
        for index, candidate in enumerate(candidates):
            children.append(subprocess.Popen(
                [
                    sys.executable, "-c", _CHILD, str(record.yaml_path),
                    encode_recovery_record(candidate).decode("utf-8"),
                    str(tmp_path / f"ready-{index}"), str(gate),
                ],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            ))
        deadline = time.monotonic() + 15
        while not all((tmp_path / f"ready-{index}").exists() for index in range(2)):
            assert all(child.poll() is None for child in children)
            assert time.monotonic() < deadline, "fresh owners did not reach fixture barrier"
            time.sleep(0.01)
        gate.touch()
        outcomes = []
        for child in children:
            stdout, stderr = child.communicate(timeout=20)
            assert child.returncode == 0, stderr
            outcomes.append(json.loads(stdout)["committed"])
        assert sorted(outcomes) == [False, True]
        winner = candidates[outcomes.index(True)]
        reopened = state.WorktreeRecoveryStore(record.yaml_path)
        assert reopened.load(winner.descriptor) == winner
        assert not reopened.compare_and_swap(winner.descriptor, 0, winner)
        loser = candidates[outcomes.index(False)]
        if changed_descriptor:
            assert reopened.load(loser.descriptor) is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


def test_new_caller_uses_live_resident_capability_and_complete_record(journal, monkeypatch):
    record, descriptor = journal
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        advertised = tracking_write.rendezvous_fields(server)
        assert state._VERB in advertised["tracking_write_verbs"]
        assert advertised["tracking_write_verb_versions"].get(state._VERB, 1) == 1
        monkeypatch.setattr(state.locks, "read_lock", lambda *_: advertised)
        monkeypatch.setattr(
            tracking_write, "run_direct", lambda *_a, **_k: pytest.fail("resident CAS fell back"),
        )
        store = state.WorktreeRecoveryStore(record.yaml_path)
        candidate = RecoveryRecord(
            "resident-operation", descriptor,
            (RecoveryStep("retire_intent", "resident-intent", ("unknown-effect",)),),
            ("retained-hold",),
        )
        assert store.compare_and_swap(descriptor, 0, candidate)
        assert store.load(descriptor) == candidate
        assert not store.compare_and_swap(descriptor, 0, candidate)
    finally:
        server.close()


@pytest.mark.parametrize("changed_intent", [False, True])
def test_fresh_inspector_reopens_real_owner_state_without_writes(journal, monkeypatch, changed_intent):
    record, descriptor = journal
    monkeypatch.setattr(state.locks, "read_lock", lambda *_: None)
    execution = launch_execution.Execution(
        record.yaml_path, launch_execution.Correlation("a" * 32, "b" * 32),
    )
    execution.append("execution-requested")
    original_intent = admission.OwnerIntent.capture(record.yaml_path)
    retained = RecoveryRecord(
        "same-operation", descriptor,
        (RecoveryStep("retire_intent", "retained-intent", ("external-work",)),),
    )
    store = state.WorktreeRecoveryStore(record.yaml_path)
    assert store.compare_and_swap(descriptor, 0, retained)
    if changed_intent:
        record.yaml_path.write_text(
            record.yaml_path.read_text(encoding="utf-8") + "\nhead_revision: 10\n",
            encoding="utf-8",
        )
    directory = record.yaml_path.parent
    before = {item: item.read_bytes() for item in directory.rglob("*") if item.is_file()}
    completed = subprocess.run(
        [
            sys.executable, "-c", _INSPECT_CHILD, str(record.yaml_path),
            encode_recovery_record(retained).decode("utf-8"), execution.subject["operation_id"],
            json.dumps(asdict(original_intent)),
        ],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if changed_intent:
        assert completed.returncode != 0
        assert "Original owner intent changed" in completed.stderr
        assert not completed.stdout
    else:
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["classification"] == "candidate"
        assert result["effects_wired"] is False
        assert result["native_recovery"] == "unqualified"
        assert result["unknown_effects"] == ["external-work"]
    assert {item: item.read_bytes() for item in directory.rglob("*") if item.is_file()} == before
    assert state.WorktreeRecoveryStore(record.yaml_path).load(descriptor) == retained
