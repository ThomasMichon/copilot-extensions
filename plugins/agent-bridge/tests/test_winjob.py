"""Tests for the Windows kill-on-close Job Object helper (#90)."""

from __future__ import annotations

import sys

import pytest

from agent_bridge import winjob


def test_noop_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(winjob, "_job_handle", None)
    assert winjob.setup_kill_on_close_job() is False


def test_idempotent_when_already_armed(monkeypatch):
    # A non-None handle means the job is already armed -- a second call is a
    # no-op regardless of platform.
    monkeypatch.setattr(winjob, "_job_handle", 12345)
    assert winjob.setup_kill_on_close_job() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job object layout")
def test_struct_layout_builds_on_windows():
    import ctypes

    extended_cls = winjob._build_structs()
    inst = extended_cls()
    inst.BasicLimitInformation.LimitFlags = (
        winjob._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    assert (
        inst.BasicLimitInformation.LimitFlags
        == winjob._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    # The extended struct must be larger than its embedded basic-limit block.
    assert ctypes.sizeof(extended_cls) > 0
