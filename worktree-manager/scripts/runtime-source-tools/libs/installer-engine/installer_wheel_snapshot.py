"""Exact-set byte custody under the installer's existing temporary-input owner."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import BinaryIO, Mapping
import zipfile
import zlib

from installer_wheel_inputs import (
    WheelError, WheelLimits, _check_deadline, _identity, _member_chunks, _ordinary,
    _unsafe_windows_component, preflight_archive,
)


@contextmanager
def retain_wheel_inputs(archives, expected, **limits):
    """Own exact byte snapshots, not profile approval or an installation."""
    import installer_dependency_policy as policy

    with policy._temporary_inputs(None) as temporary:
        root = policy._private_retained_root(Path(temporary)).resolve()
        with open_wheel_snapshot(root, archives, expected, **limits) as snapshot:
            yield snapshot


@contextmanager
def retain_wheel_bundle(bundle, expected, **limits):
    """Unpack an already selected transport into the existing snapshot owner."""
    import installer_dependency_policy as policy

    with policy._temporary_inputs(None) as temporary:
        root = policy._private_retained_root(Path(temporary)).resolve()
        with open_wheel_bundle_snapshot(root, bundle, expected, **limits) as snapshot:
            yield snapshot


def _selected_hashes(expected, max_wheels, deadline):
    hashes = dict(expected)
    if not hashes or len(hashes) > max_wheels:
        raise WheelError("wheel snapshot count limit exceeded or empty set")
    names = set()
    for name, digest in hashes.items():
        _check_deadline(deadline)
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl", name) is None
            or _unsafe_windows_component(name) or name.casefold() in names
        ):
            raise WheelError("unsafe or ambiguous wheel snapshot filename")
        names.add(name.casefold())
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise WheelError("invalid wheel snapshot digest")
    return hashes


@contextmanager
def open_wheel_bundle_snapshot(
    root: Path, bundle: Path, expected: Mapping[str, str], *,
    bundle_sha256: str, max_bundle_bytes: int, max_wheels: int,
    max_archive_bytes: int, max_total_bytes: int, max_snapshot_bytes: int,
    deadline: float,
):
    """Retain exact ``wheels/<filename>`` members, not release/feed authority.

    The caller owns a private root and cleanup, as with open_wheel_snapshot.
    Storage includes both unpacked inputs and retained copies. All byte/count
    bounds and the single deadline are inclusive and supplied by the caller.
    """
    from installer_dependency_config import native_retained_path

    _check_deadline(deadline)
    if any(type(value) is not int or value <= 0 for value in (
        max_bundle_bytes, max_wheels, max_archive_bytes, max_total_bytes, max_snapshot_bytes,
    )):
        raise WheelError("wheel bundle limits must be explicit positive integers")
    if not isinstance(bundle_sha256, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", bundle_sha256):
        raise WheelError("invalid wheel bundle digest")
    hashes = _selected_hashes(expected, max_wheels, deadline)
    root = Path(native_retained_path(root))
    bundle = Path(os.path.abspath(bundle))
    before = _ordinary(bundle)
    if before.st_size > max_bundle_bytes:
        raise WheelError("wheel bundle archive byte budget exceeded")
    paths = {}
    total = 0
    try:
        with _open(bundle) as stream:
            opened = _unchanged(bundle, before, stream)
            if _digest(stream, max_bundle_bytes, deadline) != (bundle_sha256, before.st_size):
                raise WheelError("wheel bundle digest or size mismatch")
            _unchanged(bundle, before, stream, opened=opened)
            preflight_archive(
                stream, limits=WheelLimits(
                    max_archive_bytes=max_bundle_bytes, max_expanded_bytes=max_total_bytes,
                    max_member_bytes=max_archive_bytes, max_metadata_bytes=max_bundle_bytes,
                    max_members=max_wheels, max_tags=1, max_metadata_member_bytes=max_bundle_bytes,
                ), deadline=deadline,
            )
            with zipfile.ZipFile(stream) as archive:
                members = archive.infolist()
                if (
                    len(members) != len(hashes)
                    or {member.filename for member in members} != {"wheels/" + name for name in hashes}
                ):
                    raise WheelError("wheel bundle member set differs from selected wheels")
                for member in members:
                    _check_deadline(deadline)
                    if (
                        member.is_dir() or member.orig_filename != member.filename or member.flag_bits & 1
                        or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or stat.S_IFMT(member.external_attr >> 16) not in (0, stat.S_IFREG)
                        or member.external_attr & 0x10
                        or member.file_size <= 0 or member.file_size > max_archive_bytes
                    ):
                        raise WheelError("wheel bundle member is unsafe or exceeds its byte budget")
                    total += member.file_size
                    if total > max_total_bytes or 2 * total > max_snapshot_bytes:
                        raise WheelError("wheel bundle total or storage byte budget exceeded")
                directory = root / "bundle"
                directory.mkdir(mode=0o700)
                for member in members:
                    _check_deadline(deadline)
                    name = member.filename.removeprefix("wheels/")
                    destination = directory / name
                    digest, size = hashlib.sha256(), 0
                    with destination.open("xb") as output:
                        for chunk in _member_chunks(
                            stream, archive, member, limit=member.file_size, deadline=deadline,
                        ):
                            digest.update(chunk)
                            size += len(chunk)
                            output.write(chunk)
                    if ("sha256:" + digest.hexdigest(), size) != (hashes[name], member.file_size):
                        raise WheelError("wheel bundle member digest or size mismatch")
                    paths[name] = destination
            _unchanged(bundle, before, stream, opened=opened)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, UnicodeError) as error:
        raise WheelError("wheel bundle archive is invalid") from error
    with open_wheel_snapshot(
        root, paths, hashes, max_wheels=max_wheels, max_archive_bytes=max_archive_bytes,
        max_total_bytes=max_total_bytes, max_snapshot_bytes=max_snapshot_bytes - total,
        deadline=deadline,
    ) as snapshot:
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

    def subset(self, filenames: tuple[str, ...]) -> WheelSnapshot:
        """Borrow an exact nonempty subset; the original owner keeps custody."""
        self.verify()
        if (
            not isinstance(filenames, tuple) or not filenames
            or any(not isinstance(name, str) for name in filenames)
            or len(set(filenames)) != len(filenames) or not set(filenames) <= self.paths.keys()
        ):
            raise WheelError("wheel snapshot subset must select unique retained filenames")
        return WheelSnapshot(
            MappingProxyType({name: self.paths[name] for name in filenames}),
            MappingProxyType({name: self.archives[name] for name in filenames}),
            MappingProxyType({name: self._records[name] for name in filenames}),
            self._deadline,
        )

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
class WheelReuseInputs:
    """Existing constructor evidence; cannot supply bytes for construction.

    The current installation owner must independently admit its source, profile
    and policy before use. This type verifies only the bounded receipt shape.
    """

    generation: str
    canonical_json: str
    deadline: float

    def receipt(self) -> dict:
        self.verify()
        return json.loads(self.canonical_json)

    @property
    def distributions(self) -> tuple[tuple[str, str, str, str], ...]:
        return tuple(
            (name, wheel["version"], wheel["filename"], wheel["sha256"])
            for name, wheel in sorted(self.receipt()["distributions"].items())
        )

    def verify(self) -> None:
        _check_deadline(self.deadline)
        try:
            record = json.loads(self.canonical_json)
            binding = json.dumps(record["binding"], sort_keys=True, separators=(",", ":"), allow_nan=False)
            if (
                not isinstance(self.generation, str) or not self.generation or self.generation == "dev"
                or not isinstance(record, dict) or set(record) - {"acquisition"} != {
                    "construction", "lock_id", "binding", "distributions",
                }
                or record["construction"] != "closed-wheels"
                or not isinstance(record["binding"], dict) or not record["binding"]
                or record["lock_id"] != "sha256:" + hashlib.sha256(binding.encode("ascii")).hexdigest()
                or self.canonical_json != json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
                or not isinstance(record["distributions"], dict) or not record["distributions"]
            ):
                raise ValueError
            filenames = set()
            for name, wheel in record["distributions"].items():
                _check_deadline(self.deadline)
                if (
                    re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) is None
                    or not isinstance(wheel, dict) or set(wheel) != {"version", "filename", "sha256"}
                    or not isinstance(wheel["version"], str) or not wheel["version"]
                    or not isinstance(wheel["filename"], str)
                    or re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*\.whl", wheel["filename"]) is None
                    or _unsafe_windows_component(wheel["filename"])
                    or wheel["filename"].casefold() in filenames
                    or not isinstance(wheel["sha256"], str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", wheel["sha256"]) is None
                ):
                    raise ValueError
                filenames.add(wheel["filename"].casefold())
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise WheelError("existing wheel evidence has an invalid constructor receipt") from None
        _check_deadline(self.deadline)


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
    acquisition_json: str | None = None

    @property
    def deadline(self) -> float:
        return self.snapshot._deadline

    def receipt(self) -> dict:
        result = {
            "construction": "closed-wheels",
            "lock_id": "sha256:" + hashlib.sha256(self.binding_json.encode("ascii")).hexdigest(),
            "binding": json.loads(self.binding_json),
            "distributions": {
                name: {"version": version, "filename": filename, "sha256": digest}
                for name, version, filename, digest in self.distributions
            },
        }
        if self.acquisition_json is not None:
            acquisition = self._acquisition()
            result["acquisition"] = {
                key: acquisition[key] for key in ("schema", "version", "policy_id", "files")
            }
        return result

    def _acquisition(self) -> dict:
        try:
            record = json.loads(self.acquisition_json)
            counters = {
                "metadata_bytes", "wheel_bytes", "request_count", "redirect_count",
                "control_bytes", "temporary_highwater_bytes",
            }
            if (
                not isinstance(record, dict)
                or set(record) != counters | {"schema", "version", "policy_id", "files"}
                or record["schema"] != "copilot-extensions.wheel-acquisition"
                or type(record["version"]) is not int or record["version"] != 1
                or self.acquisition_json != json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", record["policy_id"]) is None
                or any(type(record[key]) is not int or record[key] < 0 for key in counters)
                or not isinstance(record["files"], list) or len(record["files"]) != len(self.snapshot.paths)
            ):
                raise ValueError
            seen, total = set(), 0
            for item in record["files"]:
                _check_deadline(self.deadline)
                if not isinstance(item, dict) or set(item) - {"requires_python"} != {
                    "filename", "sha256", "size", "feed", "uploaded", "source_origins", "yanked", "withdrawn",
                }:
                    raise ValueError
                name = item["filename"]
                if (
                    name in seen or name not in self.snapshot.paths
                    or item["sha256"] != self.snapshot.digests[name]
                    or type(item["size"]) is not int or item["size"] != self.snapshot._records[name][3]
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", item["feed"]) is None
                    or not isinstance(item["uploaded"], str) or len(item["uploaded"]) > 64
                    or datetime.fromisoformat(item["uploaded"]).tzinfo is None
                    or not isinstance(item["source_origins"], list) or not item["source_origins"]
                    or any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in item["source_origins"])
                    or item["yanked"] is not False or item["withdrawn"] is not False
                    or ("requires_python" in item and (
                        not isinstance(item["requires_python"], str) or not item["requires_python"]
                        or item["requires_python"] != item["requires_python"].strip()
                        or len(item["requires_python"]) > 4096
                    ))
                ):
                    raise ValueError
                seen.add(name)
                total += item["size"]
            if (
                total != record["wheel_bytes"] or seen != set(self.snapshot.paths)
                or record["temporary_highwater_bytes"] != max(record["control_bytes"] + total, 2 * total)
                or record["request_count"] < sum(len(item["source_origins"]) for item in record["files"])
            ):
                raise ValueError
            return record
        except WheelError:
            raise
        except (ValueError, TypeError, KeyError):
            raise WheelError("wheel acquisition facts differ from retained construction inputs") from None

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
        if self.acquisition_json is not None:
            self._acquisition()
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
    from installer_dependency_config import native_retained_path

    root = Path(native_retained_path(root))
    _check_deadline(deadline)
    if any(type(value) is not int or value <= 0 for value in (
        max_wheels, max_archive_bytes, max_total_bytes, max_snapshot_bytes,
    )):
        raise WheelError("wheel snapshot limits must be explicit positive integers")
    if not archives or len(archives) > max_wheels or len(expected) > max_wheels:
        raise WheelError("wheel snapshot count limit exceeded or empty set")
    if set(archives) != set(expected):
        raise WheelError("wheel snapshot filename set differs from expected")
    paths, hashes = dict(archives), _selected_hashes(expected, max_wheels, deadline)
    originals = {}
    total = 0
    for name, source in paths.items():
        _check_deadline(deadline)
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
