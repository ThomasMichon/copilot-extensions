"""Completed write activity from a caller's already-held Windows Job."""

from __future__ import annotations

from functools import cache

import agent_procutil as _owner


class JobActivityError(RuntimeError):
    """The owned Job can no longer attest monotonic write activity."""


@cache
def _accounting_type() -> type:
    import ctypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicAndIoAccounting(ctypes.Structure):
        _fields_ = [
            ("BasicInfo", _owner._JobBasicAccountingInformation),
            ("IoInfo", _IoCounters),
        ]

    return _BasicAndIoAccounting


def _write_count(job: _owner.JobHandle) -> int:
    handle = job.handle
    if not handle:
        raise JobActivityError("owned Job handle is closed")
    import ctypes

    kernel = _owner._kernel32()
    _owner._configure_job_teardown_api(kernel)
    accounting, returned = _accounting_type()(), ctypes.c_uint32()
    if not kernel.QueryInformationJobObject(
        handle,
        8,
        ctypes.byref(accounting),
        ctypes.sizeof(accounting),
        ctypes.byref(returned),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    if returned.value != ctypes.sizeof(accounting):
        raise JobActivityError("Job I/O accounting returned an unexpected structure size")
    return accounting.IoInfo.WriteTransferCount


class JobWriteActivity:
    """Sample aggregate completed writes, including exited members, without new custody.

    Reads, CPU time and unchanged counters are not progress. Native failures
    retain the original cause; the caller must settle its Job before reporting.
    """

    def __init__(self, job: _owner.JobHandle) -> None:
        if not isinstance(job, _owner.JobHandle):
            raise JobActivityError("write activity requires an owned Windows Job")
        self._job = job
        self._previous = self._sample()

    def _sample(self) -> int:
        try:
            return _write_count(self._job)
        except OSError as error:
            raise JobActivityError(f"owned Job write accounting failed: {error}") from error

    def __call__(self) -> bool:
        current = self._sample()
        if current < self._previous:
            raise JobActivityError("owned Job write accounting decreased")
        advanced = current > self._previous
        self._previous = current
        return advanced
