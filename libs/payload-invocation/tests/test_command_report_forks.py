"""Concurrent native forks preserve one report owner and explicit interval loss."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from test_command_evidence import _recent
from test_command_evidence import payload as command_payload
from test_command_reports import _owner_script

payload = command_payload


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Native POSIX fork")
def test_overlapping_native_forks_leave_one_settled_replacement(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "parent=os.getpid()\n"
        "original=entry._ReportOwner\n"
        "started=[]\n"
        "constructing=threading.Event()\n"
        "release=threading.Event()\n"
        "returned=threading.Event()\n"
        "native_returns=threading.Barrier(2,timeout=4)\n"
        "class Tracked(original):\n"
        "    def __init__(self,recorder):\n"
        "        super().__init__(recorder)\n"
        "        if os.getpid()==parent:\n"
        "            started.append(self)\n"
        "            constructing.set()\n"
        "            assert release.wait(5)\n"
        "entry._ReportOwner=Tracked\n"
        "os.register_at_fork(after_in_parent=lambda:native_returns.wait())\n"
        "children=[]\n"
        "failures=[]\n"
        "def launch():\n"
        "    try:\n"
        "        child=os.fork()\n"
        "        if child==0: os._exit(0)\n"
        "        children.append(child)\n"
        "        returned.set()\n"
        "    except BaseException as error: failures.append(type(error).__name__)\n"
        "threads=[threading.Thread(target=launch) for _ in range(2)]\n"
        "for thread in threads: thread.start()\n"
        "try:\n"
        "    assert constructing.wait(5)\n"
        "    assert returned.wait(2),'both forks blocked on replacement construction'\n"
        "    assert len(started)==1,len(started)\n"
        "finally:\n"
        "    release.set()\n"
        "    for thread in threads: thread.join(5)\n"
        "    for child in children: assert os.waitpid(child,0)[1]==0\n"
        "assert not failures,failures\n"
        "assert all(not thread.is_alive() for thread in threads)\n"
        "assert len(children)==2\n"
        "assert len(started)==1 and entry._REPORT_OWNER is started[0]\n"
        "entry._REPORT_OWNER.close()\n"
        "assert owner.writer.join(.25)\n"
        "assert all(item.writer.join(.25) for item in started)\n"
        "assert all(not item.writer._thread.is_alive() for item in [owner,*started])\n",
    )
    assert result.returncode == 0, result.stderr
    assert _recent(payload)["exists"] is False


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Native POSIX fork")
def test_fork_callback_reports_keep_loss_visible_across_owner_replacement(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "os.register_at_fork(\n"
        "    before=lambda:logging.error('private-before-fork'),\n"
        "    after_in_parent=lambda:logging.error('private-parent-fork'),\n"
        ")\n"
        "state=getattr(entry,'_FORK_STATE',None)\n"
        "child=os.fork()\n"
        "if child==0:\n"
        "    if state is not None:\n"
        "        assert entry._FORK_STATE is not state\n"
        "        assert entry._FORK_STATE.lock is not state.lock\n"
        "        assert entry._FORK_STATE.active==0\n"
        "    os._exit(0)\n"
        "assert os.waitpid(child,0)[1]==0\n"
        "assert entry._REPORT_OWNER is not owner\n"
        "logging.error('private-after-fork')\n"
        "entry._REPORT_OWNER.close()\n",
    )
    assert result.returncode == 0, result.stderr
    for text in (b"private-before-fork", b"private-parent-fork", b"private-after-fork"):
        assert text in result.stderr
    assert b"Automatic command evidence incomplete" in result.stderr
    records = _recent(payload)["records"]
    record, = records
    assert record["code"] == "error-log-record"
    assert "private-" not in json.dumps(records)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Native POSIX fork")
def test_shutdown_during_fork_does_not_start_a_replacement_writer(
    payload: tuple[Path, dict[str, str]], tmp_path: Path,
) -> None:
    result = _owner_script(
        payload, tmp_path,
        "os.register_at_fork(after_in_parent=entry._close_reports)\n"
        "child=os.fork()\n"
        "if child==0: os._exit(0)\n"
        "assert os.waitpid(child,0)[1]==0\n"
        "assert entry._REPORT_OWNER is owner\n"
        "assert owner.writer.closed and owner.writer.join(.25)\n",
    )
    assert result.returncode == 0, result.stderr
    assert _recent(payload)["exists"] is False
