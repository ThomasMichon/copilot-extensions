"""Discovery of launcher-owned cleanup Jobs, never inherited Job handles."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any
from . import (
    _JOB_SCOPES_ENV as ENVIRONMENT_KEY,
    _job_child_environment as child_environment,
    _new_job_name as new_name,
    _job_scope_names as scope_names,
    _publish_job_scope as publish_current,
)

__all__ = [
    "ENVIRONMENT_KEY", "new_name", "scope_names", "child_environment",
    "publish_current", "query_scopes",
]


def _api() -> Any:
    import ctypes
    from ctypes import wintypes
    from . import _kernel32

    kernel = _kernel32()
    kernel.OpenJobObjectW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenJobObjectW.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


@contextmanager
def query_scopes(environment: Mapping[str, str]) -> Iterator[Callable[[int], None]]:
    """Pin exact scope objects only during suspended-child admission."""
    names = scope_names(environment)
    if not names:
        yield lambda process_handle: None
        return
    import ctypes
    from ctypes import wintypes

    windows: Any = ctypes
    kernel = _api()
    handles: list[int] = []
    try:
        for name in names:
            handle = kernel.OpenJobObjectW(4, False, name)  # JOB_OBJECT_QUERY
            if not handle:
                error = windows.get_last_error()
                if error == 2:  # ERROR_FILE_NOT_FOUND: a destroyed Job cannot retain a child.
                    continue
                raise windows.WinError(error)
            handles.append(handle)

        def verify(process_handle: int) -> None:
            for handle in handles:
                inside = wintypes.BOOL()
                if not kernel.IsProcessInJob(process_handle, handle, ctypes.byref(inside)):
                    raise windows.WinError(windows.get_last_error())
                if inside.value:
                    raise OSError("daemon retained a launcher-owned cleanup Job")

        yield verify
    finally:
        failure = None
        for handle in reversed(handles):
            if not kernel.CloseHandle(handle):
                failure = windows.WinError(windows.get_last_error())
        if failure is not None:
            raise failure
