"""Handle-owned Windows claim files with no-replace publication."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import BinaryIO

from agent_logger.sync.provenance import short_unique_id, windows_extended_path


class WindowsClaimFile:
    """Keep one original file object open until publication and verification finish.

    The caller supplies a prevalidated directory and must leave ``stream`` open
    until context exit. Cleanup changes the original handle's disposition, never
    a pathname. Unsupported filesystem operations propagate their native errors.
    """

    stream: BinaryIO

    def __init__(self, directory: Path) -> None:
        if os.name != "nt":
            raise OSError("WindowsClaimFile requires Windows")

        import ctypes
        import msvcrt
        from ctypes import wintypes

        class FileDispositionInfo(ctypes.Structure):
            _fields_ = (("DeleteFile", ctypes.c_ubyte),)

        class RenameOptions(ctypes.Union):
            _fields_ = (
                ("ReplaceIfExists", ctypes.c_ubyte),
                ("Flags", ctypes.c_uint32),
            )

        class FileRenameInfo(ctypes.Structure):
            _fields_ = (
                ("Options", RenameOptions),
                ("RootDirectory", wintypes.HANDLE),
                ("FileNameLength", ctypes.c_uint32),
                ("FileName", ctypes.c_uint16 * 1),
            )

        pointer_size = ctypes.sizeof(wintypes.HANDLE)
        if (
            ctypes.sizeof(FileDispositionInfo) != 1
            or FileRenameInfo.Options.offset != 0
            or FileRenameInfo.RootDirectory.offset != pointer_size
            or FileRenameInfo.FileNameLength.offset != 2 * pointer_size
            or FileRenameInfo.FileName.offset != 2 * pointer_size + 4
        ):
            raise RuntimeError("unsupported Windows file-information ABI")

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        set_info = kernel32.SetFileInformationByHandle
        set_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_info.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        flush_buffers = kernel32.FlushFileBuffers
        flush_buffers.argtypes = [wintypes.HANDLE]
        flush_buffers.restype = wintypes.BOOL

        generic_read_write_delete = 0x80000000 | 0x40000000 | 0x00010000
        file_share_read = 0x00000001
        create_new = 1
        normal_reparse_write_through = 0x00000080 | 0x00200000 | 0x80000000
        invalid_handle = wintypes.HANDLE(-1).value

        for _ in range(16):
            temporary = directory / f".claim-{short_unique_id()}.tmp"
            handle = create_file(
                windows_extended_path(temporary),
                generic_read_write_delete,
                file_share_read,
                None,
                create_new,
                normal_reparse_write_through,
                None,
            )
            if handle != invalid_handle:
                break
            error = ctypes.WinError(ctypes.get_last_error())
            if not isinstance(error, FileExistsError):
                raise error
        else:
            raise error

        self._published = False
        self._delete_pending = False
        self._closed = False

        def set_delete_pending(delete: bool) -> None:
            info = FileDispositionInfo(int(delete))
            if not set_info(handle, 4, ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._delete_pending = delete

        def close_native_handle() -> None:
            if not close_handle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

        def rename_by_handle(destination: Path) -> None:
            target = windows_extended_path(destination)
            if "\0" in target:
                raise ValueError("claim destination contains a NUL character")
            encoded = target.encode("utf-16-le")
            if len(encoded) > 0xFFFFFFFF:
                raise ValueError("claim destination exceeds the native byte-length limit")
            name_offset = FileRenameInfo.FileName.offset
            size = max(ctypes.sizeof(FileRenameInfo), name_offset + len(encoded) + 2)
            buffer = ctypes.create_string_buffer(size)
            info = FileRenameInfo.from_buffer(buffer)
            info.Options.ReplaceIfExists = 0
            info.RootDirectory = None
            info.FileNameLength = len(encoded)
            ctypes.memmove(ctypes.addressof(buffer) + name_offset, encoded, len(encoded))
            if not set_info(handle, 3, buffer, size):
                raise ctypes.WinError(ctypes.get_last_error())

        def flush_original_handle() -> None:
            if not flush_buffers(handle):
                raise ctypes.WinError(ctypes.get_last_error())

        self._set_delete_pending: Callable[[bool], None] = set_delete_pending
        self._rename_by_handle: Callable[[Path], None] = rename_by_handle
        self._flush_original_handle: Callable[[], None] = flush_original_handle
        self._close_resource: Callable[[], None] = close_native_handle
        try:
            # Unlike FILE_FLAG_DELETE_ON_CLOSE, this disposition is cancellable.
            self._set_delete_pending(True)
            fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
            self._close_resource = lambda: os.close(fd)
            # Retain descriptor ownership even if stream construction fails.
            self.stream = os.fdopen(fd, "w+b", closefd=False)

            def close_stream_and_descriptor() -> None:
                try:
                    self.stream.close()
                finally:
                    os.close(fd)

            self._close_resource = close_stream_and_descriptor
        except BaseException as error:
            try:
                self._dispose()
            except OSError as cleanup_error:
                raise error from cleanup_error
            raise

    def __enter__(self) -> WindowsClaimFile:
        if self._closed or self.stream.closed:
            raise ValueError("claim file is closed")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self._dispose()
        except OSError as cleanup_error:
            if exc is not None:
                raise exc.with_traceback(tb) from cleanup_error
            raise

    @property
    def file_id(self) -> tuple[int, int]:
        opened = os.fstat(self.stream.fileno())
        return opened.st_dev, opened.st_ino

    def publish(self, destination: Path) -> None:
        if self._closed or self.stream.closed:
            raise ValueError("claim file is closed")
        if self._published:
            raise RuntimeError("claim file has already been published")
        self.stream.flush()
        os.fsync(self.stream.fileno())
        try:
            self._set_delete_pending(False)
            self._rename_by_handle(destination)
        except BaseException as error:
            try:
                self._set_delete_pending(True)
            except OSError as cleanup_error:
                raise error from cleanup_error
            raise
        # A failed acknowledgement must not delete the now-published marker.
        self._published = True
        self._flush_original_handle()

    def _dispose(self) -> None:
        if self._closed:
            return
        try:
            if not self._published and not self._delete_pending:
                self._set_delete_pending(True)
        finally:
            try:
                self._close_resource()
            finally:
                self._closed = True
