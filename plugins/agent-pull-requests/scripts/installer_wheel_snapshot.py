"""Exact-set byte custody under the installer's existing temporary-input owner."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import BinaryIO, Mapping

from installer_wheel_inputs import (
    WheelError, _check_deadline, _identity, _ordinary, _unsafe_windows_component,
)


@contextmanager
def retain_wheel_inputs(archives, expected, **limits):
    """Own exact byte snapshots, not profile approval or an installation."""
    import installer_dependency_policy as policy

    with policy._temporary_inputs(None) as temporary:
        root = policy._private_retained_root(Path(temporary)).resolve()
        with open_wheel_snapshot(root, archives, expected, **limits) as snapshot:
            yield snapshot


def _unchanged(path, before, stream, *, opened=None):
    after = os.fstat(stream.fileno())
    changed = []
    if _identity(after) != _identity(before):
        changed.append("handle identity")
    if os.name != "nt" and opened is not None and after.st_ctime_ns != opened.st_ctime_ns:
        changed.append("handle change time")
    if _identity(_ordinary(path)) != _identity(before):
        changed.append("path identity")
    if changed:
        raise WheelError("wheel snapshot input changed: " + ", ".join(changed))
    return after


def _digest(stream, limit, deadline, destination=None):
    stream.seek(0)
    digest = hashlib.sha256()
    size = 0
    while True:
        _check_deadline(deadline)
        chunk = stream.read(min(1 << 20, limit - size + 1))
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise WheelError("wheel snapshot byte budget exceeded")
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
        _check_deadline(deadline)
    _check_deadline(deadline)
    stream.seek(0)
    return "sha256:" + digest.hexdigest(), size


def _open(path):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        import msvcrt
        from installer_dependency_policy import _native_retained_path

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        # Deny writers and deletion for the entire held-input lifetime. Windows
        # change time can move on reads when NTFS last-access updates are enabled.
        handle = create(_native_retained_path(path), 0x80000000, 1, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
        except BaseException:
            close(handle)
            raise
        try:
            return os.fdopen(descriptor, "rb")
        except BaseException:
            os.close(descriptor)
            raise
    flags = os.O_RDONLY
    for name in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, name, 0)
    return os.fdopen(os.open(path, flags), "rb")


@dataclass(frozen=True)
class WheelSnapshot:
    """Read-only exact copies and streams; no profile or admission authority."""

    paths: Mapping[str, Path]
    archives: Mapping[str, BinaryIO]
    _records: Mapping[str, tuple]
    _deadline: float

    @property
    def digests(self) -> Mapping[str, str]:
        """Asserted archive identities, not approval or semantic admission."""
        return MappingProxyType({name: record[2] for name, record in self._records.items()})

    def verify(self):
        """Recheck custody before a caller's construction/publication boundary."""
        for name, path in self.paths.items():
            _check_deadline(self._deadline)
            before, opened, expected, size = self._records[name]
            stream = self.archives[name]
            _unchanged(path, before, stream, opened=opened)
            if _digest(stream, size, self._deadline) != (expected, size):
                raise WheelError("retained wheel snapshot digest changed")
            _unchanged(path, before, stream, opened=opened)
        _check_deadline(self._deadline)


@dataclass(frozen=True)
class WheelInstallInputs:
    """Inspected construction data; never profile/feed approval or a trust token.

    The semantic profile owner supplies its full canonical binding and the actual
    inspected distribution versions. The caller retains snapshot custody through
    installed health; the installer binds this data into its existing identity.
    """

    snapshot: WheelSnapshot
    binding_json: str
    distributions: tuple[tuple[str, str, str, str], ...]

    def receipt(self) -> dict:
        return {
            "construction": "closed-wheels",
            "lock_id": "sha256:" + hashlib.sha256(self.binding_json.encode("ascii")).hexdigest(),
            "binding": json.loads(self.binding_json),
            "distributions": {
                name: {"version": version, "filename": filename, "sha256": digest}
                for name, version, filename, digest in self.distributions
            },
        }

    def verify(self) -> None:
        if not isinstance(self.snapshot, WheelSnapshot):
            raise WheelError("wheel construction requires retained input custody")
        _check_deadline(self.snapshot._deadline)
        binding = json.loads(self.binding_json)
        if not isinstance(binding, dict) or not binding or self.binding_json != json.dumps(
            binding, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ):
            raise WheelError("wheel construction binding must be canonical JSON")
        if (
            not isinstance(self.distributions, tuple) or not self.distributions
            or len(self.distributions) != len(self.snapshot.paths)
        ):
            raise WheelError("wheel construction needs exact immutable distribution facts")
        names, expected = set(), {}
        for item in self.distributions:
            _check_deadline(self.snapshot._deadline)
            if not isinstance(item, tuple) or len(item) != 4 or any(
                not isinstance(value, str) or not value for value in item
            ):
                raise WheelError("invalid wheel construction distribution facts")
            name, _version, filename, digest = item
            if (
                re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
                or name in names or filename in expected
            ):
                raise WheelError("ambiguous wheel construction distribution set")
            names.add(name)
            expected[filename] = digest
        if expected != self.snapshot.digests:
            raise WheelError("wheel construction facts differ from retained inputs")
        self.snapshot.verify()


@contextmanager
def open_wheel_snapshot(
    root: Path, archives: Mapping[str, Path], expected: Mapping[str, str], *,
    max_wheels: int, max_archive_bytes: int, max_total_bytes: int,
    max_snapshot_bytes: int, deadline: float,
):
    """Copy beneath a private invocation-owned root; its caller owns cleanup.

    Budgets are inclusive. Storage counts only the copied archive bytes: no
    extraction, cache population, resolution or backend execution occurs here.
    All limits and the one deadline come from the caller, not runtime defaults.
    """
    _check_deadline(deadline)
    if any(type(value) is not int or value <= 0 for value in (
        max_wheels, max_archive_bytes, max_total_bytes, max_snapshot_bytes,
    )):
        raise WheelError("wheel snapshot limits must be explicit positive integers")
    if not archives or len(archives) > max_wheels or len(expected) > max_wheels:
        raise WheelError("wheel snapshot count limit exceeded or empty set")
    if set(archives) != set(expected):
        raise WheelError("wheel snapshot filename set differs from expected")
    paths, hashes = dict(archives), dict(expected)
    names = set()
    originals = {}
    total = 0
    for name, source in paths.items():
        _check_deadline(deadline)
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl", name) is None
            or _unsafe_windows_component(name) or name.casefold() in names
        ):
            raise WheelError("unsafe or ambiguous wheel snapshot filename")
        names.add(name.casefold())
        if not isinstance(hashes[name], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", hashes[name]):
            raise WheelError("invalid wheel snapshot digest")
        source = Path(os.path.abspath(source))
        before = _ordinary(source)
        if before.st_size > max_archive_bytes:
            raise WheelError("wheel snapshot archive byte budget exceeded")
        total += before.st_size
        if total > max_total_bytes:
            raise WheelError("wheel snapshot total byte budget exceeded")
        if total > max_snapshot_bytes:
            raise WheelError("wheel snapshot storage byte budget exceeded")
        originals[name] = source, before
    _check_deadline(deadline)
    directory = root / "wheels"
    directory.mkdir(mode=0o700)
    retained, streams, records = {}, {}, {}
    with ExitStack() as opened:
        with ExitStack() as sources:
            actual = 0
            source_streams = {}
            for name, (source, before) in originals.items():
                _check_deadline(deadline)
                stream = sources.enter_context(_open(source))
                opened_info = _unchanged(source, before, stream)
                source_streams[name] = stream, opened_info
                destination = directory / name
                limit = min(max_archive_bytes, max_total_bytes - actual, max_snapshot_bytes - actual)
                with destination.open("xb") as output:
                    digest, size = _digest(stream, limit, deadline, output)
                _unchanged(source, before, stream, opened=opened_info)
                if digest != hashes[name] or size != before.st_size:
                    raise WheelError("wheel snapshot source digest or size mismatch")
                actual += size
                destination.chmod(0o400)
                retained[name] = destination
            # Original handles remain open until the complete set has been copied.
            for name, (source, before) in originals.items():
                _check_deadline(deadline)
                stream, opened_info = source_streams[name]
                _unchanged(source, before, stream, opened=opened_info)
        for name, path in retained.items():
            _check_deadline(deadline)
            before = _ordinary(path)
            stream = opened.enter_context(_open(path))
            opened_info = _unchanged(path, before, stream)
            streams[name] = stream
            records[name] = before, opened_info, hashes[name], before.st_size
        snapshot = WheelSnapshot(
            MappingProxyType(retained), MappingProxyType(streams),
            MappingProxyType(records), deadline,
        )
        snapshot.verify()
        yield snapshot
        snapshot.verify()
