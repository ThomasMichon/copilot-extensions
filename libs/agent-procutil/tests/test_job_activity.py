"""Held-Job accounting controls without native actors."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_procutil as pu
from agent_procutil import job_activity as activity


@pytest.fixture
def accounting(monkeypatch):
    values = iter([100, 100, 120, 120, 130])

    def query(handle, kind, pointer, size, returned):
        assert handle == 0x123456789ABC and kind == 8
        record = ctypes.cast(pointer, ctypes.POINTER(activity._accounting_type())).contents
        record.IoInfo.WriteTransferCount = next(values)
        record.IoInfo.ReadTransferCount += 1000
        record.BasicInfo.TotalUserTime += 1000
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return 1

    kernel = SimpleNamespace(QueryInformationJobObject=Mock(side_effect=query))
    monkeypatch.setattr(pu, "_kernel32", lambda: kernel)
    monkeypatch.setattr(pu, "_configure_job_teardown_api", lambda kernel: None)
    job = pu.JobHandle(0x123456789ABC)
    yield job, kernel
    job._handle = None


def test_only_new_completed_write_bytes_count(accounting):
    job, _ = accounting
    observer = activity.JobWriteActivity(job)
    assert [observer() for _ in range(4)] == [False, True, False, True]


@pytest.mark.parametrize("failure", ["closed", "size", "native", "decrease"])
def test_accounting_failure_never_attests_activity(accounting, monkeypatch, failure):
    job, kernel = accounting
    observer = activity.JobWriteActivity(job)
    if failure == "closed":
        job._handle = None
    elif failure == "size":
        kernel.QueryInformationJobObject.side_effect = lambda *args: 1
    elif failure == "native":
        kernel.QueryInformationJobObject.side_effect = lambda *args: 0
        monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
        monkeypatch.setattr(
            ctypes, "WinError", lambda code: OSError(code, "accounting refused"), raising=False
        )
    else:
        monkeypatch.setattr(activity, "_write_count", lambda job: 1)
    with pytest.raises(activity.JobActivityError) as raised:
        observer()
    if failure == "native":
        assert isinstance(raised.value.__cause__, OSError)
        assert raised.value.__cause__.errno == 5


def test_missing_job_refuses_observation():
    with pytest.raises(activity.JobActivityError, match="owned"):
        activity.JobWriteActivity(None)
