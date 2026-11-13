"""Real thread interleaving qualifies the guard outside the actor-free unit packet."""
import os
import sys
import threading
from unittest.mock import Mock

from promotion_unit_contract import UnitEvidenceError
from promotion_unit_effects import UnitRun


def test_interleaved_relative_opens_keep_thread_and_nested_path_custody(tmp_path, monkeypatch):
    temporary = tmp_path / "synthetic"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        os, "readlink",
        lambda path: str(temporary if str(path).endswith("/42") else tmp_path / "outside"),
    )
    run = UnitRun(Mock(), tmp_path, {}, temporary)
    run.active = True
    outside_ready, inside_ready, outside_done = (threading.Event() for _ in range(3))
    audit = run.audit
    executed, outcomes, settled = [], {}, {}

    def interleaved_audit(event, arguments):
        if event == "open" and arguments[0] == "same.txt":
            if threading.current_thread() is outside:
                outside_ready.set()
                assert inside_ready.wait(5)
            else:
                inside_ready.set()
                assert outside_done.wait(5)
        audit(event, arguments)

    def original(name, flags, mode, *, dir_fd):
        audit("open", (name, None, flags))
        if name == "same.txt" and dir_fd == 42:
            run.guarded_open("nested.txt", flags, dir_fd=dir_fd)
            audit("open", (name, None, flags))
        executed.append((name, dir_fd))
        return 17

    def open_path(label, directory_fd):
        try:
            if label == "inside":
                assert outside_ready.wait(5)
            outcomes[label] = run.guarded_open(
                "same.txt", os.O_WRONLY | os.O_CREAT, dir_fd=directory_fd,
            )
        except BaseException as error:
            outcomes[label] = error
        finally:
            settled[label] = run._open_paths == []
            if label == "outside":
                outside_done.set()

    run.audit = interleaved_audit
    run._os_open = original
    outside = threading.Thread(target=open_path, args=("outside", 41))
    inside = threading.Thread(target=open_path, args=("inside", 42))
    outside.start()
    inside.start()
    try:
        outside.join(10)
        inside.join(10)
    finally:
        outside_ready.set()
        inside_ready.set()
        outside_done.set()
        outside.join(5)
        inside.join(5)
    assert not outside.is_alive() and not inside.is_alive()
    assert isinstance(outcomes["outside"], UnitEvidenceError)
    assert outcomes["inside"] == 17
    assert executed == [("nested.txt", 42), ("same.txt", 42)]
    assert settled == {"outside": True, "inside": True}
    assert run.effects == ["write outside synthetic unit temporary storage"]
