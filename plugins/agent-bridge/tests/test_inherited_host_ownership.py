"""Inherited recovery must act on the observed Host, not just its session name."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from zdd import process_exit

from agent_bridge.session_host.host_index import ClaimConflict, HostIndex, HostRecord
from agent_bridge.session_host.host_inventory import HostInventoryError


def host() -> HostRecord:
    return HostRecord("worker", 9001, 21, 22, extra={"host_process_start": "100", "child_process_start": "102"})


def test_conditional_claim_tracks_only_new_acquisition_and_preserves_replacement(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    idx = HostIndex(path)
    record = host()
    idx.register(record)
    sibling = replace(record, session_id="other", protocol_version=99)
    idx.register(sibling)
    acquired: list[HostRecord] = []

    def claim(expected: HostRecord) -> HostRecord:
        return idx.claim("worker", generation="next", owner_pid=os.getpid(), pid_alive=lambda _: True,
                         expected=expected, on_acquired=acquired.append)

    owned = claim(record)
    with patch.object(idx, "_flush", side_effect=AssertionError("unchanged claim was written")):
        assert claim(owned) == owned
    assert acquired == [owned]
    successor = replace(owned, port=9002)
    HostIndex(path).register(successor)
    with patch.object(idx, "_flush", side_effect=AssertionError("replacement was written")):
        assert not idx.release("worker", "next", expected=owned)
        with pytest.raises(HostInventoryError):
            claim(owned)
    assert HostIndex(path).get("worker") == successor and HostIndex(path).get("other") == sibling


def test_inherited_retirement_retries_without_changing_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "index.json"
    idx, record = HostIndex(path), host()
    idx.register(record)
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, birth: (pid, birth) in {(21, "100"), (22, "102")})
    receipts: list[HostRecord] = []

    def settled(locked: HostRecord) -> bool:
        receipts.append(locked)
        return True

    with patch.object(idx, "_flush", side_effect=OSError("interrupted write")), pytest.raises(OSError):
        idx.release_exited_local(record, settle=settled, inherited_by="previous")
    assert HostIndex(path).get("worker") == record
    assert HostIndex(path).release_exited_local(record, settle=settled, inherited_by="next")
    assert receipts == [record, record]
    assert HostIndex(path).get("worker") is None


def test_inherited_retirement_respects_live_frontend_even_after_child_exit(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    idx = HostIndex(path)
    record = replace(host(), owner_generation="previous", owner_pid=os.getpid())
    idx.register(record)
    with patch.object(process_exit, "observe_original_exit", return_value=True), \
            patch.object(idx, "_flush", side_effect=AssertionError("live owner was written")), \
            pytest.raises(ClaimConflict):
        idx.release_exited_local(record, inherited_by="next", settle=lambda _: pytest.fail("live owner was retired"))
    assert HostIndex(path).get("worker") == record


def test_inherited_claim_refuses_incomplete_storage_and_force(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    idx, record = HostIndex(path), host()
    idx.register(record)
    with pytest.raises(HostInventoryError):
        idx.claim("worker", generation="next", owner_pid=os.getpid(), pid_alive=lambda _: False,
                  expected=record, force=True)
    data = json.loads(path.read_bytes())
    del data["hosts"]["worker"]["owner_pid"]
    path.write_text(json.dumps(data))
    with pytest.raises(HostInventoryError):
        idx.claim("worker", generation="next", owner_pid=os.getpid(), pid_alive=lambda _: False, expected=record)
    assert json.loads(path.read_bytes()) == data
