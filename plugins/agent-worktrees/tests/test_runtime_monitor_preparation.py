import json
import os
import time

import pytest
from zdd import breadcrumb, diagnostics, routing

from agent_worktrees import runtime_monitor_preparation as preparation


@pytest.fixture
def preparing(tmp_path, monkeypatch):
    directory = tmp_path / "status-monitor-routing"
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr("zdd.process_exit.observe_original_exit", lambda pid, start: False)
    witness = routing.publication_witness(directory, deadline=time.monotonic() + 1)
    record = breadcrumb.write_breadcrumb(
        directory, state="started", new_port=41002, publication_witness=witness, self_retirement=True,
    )
    env = {}
    preparation.launch_environment(directory, env)
    monkeypatch.setenv(preparation.ENV, env[preparation.ENV])
    return tmp_path, directory, record


def test_child_registers_native_identity_before_listening(preparing):
    root, directory, record = preparing
    assert preparation.register(root, 41002)
    registered = breadcrumb.read_breadcrumb(directory)
    assert registered["new_pid"] == os.getpid()
    assert registered["new_process_start_time"] == "1001"
    assert all(registered[key] == record[key] for key in preparation._ATTEMPT)
    payload = {
        "pid": registered["new_pid"], "start": registered["new_process_start_time"],
        "port": registered["new_port"], "started_at": record["started_at"],
        "publication_witness": record["publication_witness"],
    }
    assert preparation.cancel(payload, root=root, port=41002, passive=True)
    assert not preparation.cancel({**payload, "start": "2002"}, root=root, port=41002, passive=True)
    assert not preparation.cancel(payload, root=root, port=41002, passive=False)


@pytest.mark.parametrize("change", ["absent", "cancelled", "attempt", "owner-exit", "owner-unknown"])
def test_late_child_cannot_outlive_cancelled_or_unattributable_preparation(preparing, monkeypatch, change):
    root, directory, record = preparing
    if change == "absent":
        breadcrumb.clear_breadcrumb(directory)
    elif change == "cancelled":
        breadcrumb.write_breadcrumb(
            directory, state="started", new_port=41002, publication_witness=record["publication_witness"],
            started_at=record["started_at"], self_retirement=True, error="Passive startup cancelled",
        )
    elif change == "attempt":
        expected = json.loads(os.environ[preparation.ENV])
        expected["started_at"] = "different-attempt"
        monkeypatch.setenv(preparation.ENV, json.dumps(expected))
    else:
        monkeypatch.setattr(breadcrumb, "owner_exited", lambda value: change == "owner-exit" or None)
    assert not preparation.register(root, 41002)
    remaining = breadcrumb.read_breadcrumb(directory)
    assert remaining is None or remaining["new_pid"] is None


def test_registered_child_cannot_cancel_after_publication(preparing):
    root, directory, _record = preparing
    assert preparation.register(root, 41002)
    record = breadcrumb.read_breadcrumb(directory)
    routing.publish_active(
        directory, bind="127.0.0.1", port=41002, pid=os.getpid(), process_start_time="1001",
    )
    assert not preparation.cancel({
        "pid": os.getpid(), "start": "1001", "port": 41002,
        "started_at": record["started_at"], "publication_witness": record["publication_witness"],
    }, root=root, port=41002, passive=True)
