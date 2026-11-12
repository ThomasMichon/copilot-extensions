"""Contained process-boundary CAS controls, not native recovery qualification."""

from dataclasses import replace
import json
import os
import subprocess
import sys
import time

import pytest

from agent_remote_driver.headed.recovery import RecoveryDescriptor, RecoveryRecord, RecoveryStep
from agent_remote_driver.headed.recovery_codec import encode_recovery_record
from agent_worktrees import config as cfg, launch_recovery_store as state, tracking, tracking_write


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
