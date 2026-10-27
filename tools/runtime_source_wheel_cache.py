"""Installation-owned storage for wheels built from already admitted inputs.

The caller supplies the complete construction binding and validates fresh build
outputs. A cache receipt is byte custody, never source, backend or feed approval.
No UV cache files or completed runtime generations are modified.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from installer_dependency_policy import _native_retained_path, _private_retained_root
from installer_wheel_inputs import WheelError, _check_deadline, _ordinary
from installer_wheel_snapshot import _digest, _open, _unchanged
import versioned_runtime as runtime


_RECEIPT = "wheel.json"
_SCHEMA = "copilot-extensions.cached-source-wheel"
_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,250}\.whl")


def _directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or (
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise WheelError("source wheel cache directory must be ordinary")


def _read_receipt(entry: Path, key: str, max_archive_bytes: int) -> tuple[Path, str, int]:
    _directory(entry)
    receipt = entry / _RECEIPT
    before = _ordinary(receipt)
    if before.st_size > 4096:
        raise WheelError("source wheel cache receipt exceeds its byte budget")
    with _open(receipt) as stream:
        opened = _unchanged(receipt, before, stream)
        content = stream.read(4097)
        _unchanged(receipt, before, stream, opened=opened)
    try:
        record = json.loads(content)
    except (ValueError, UnicodeDecodeError) as error:
        raise WheelError("source wheel cache receipt is invalid") from error
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "version", "key", "filename", "sha256", "size"}
        or record["schema"] != _SCHEMA or type(record["version"]) is not int
        or record["version"] != 1 or record["key"] != key
        or not isinstance(record["filename"], str)
        or _FILENAME.fullmatch(record["filename"]) is None
        or not isinstance(record["sha256"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", record["sha256"]) is None
        or type(record["size"]) is not int or not 0 < record["size"] <= max_archive_bytes
    ):
        raise WheelError("source wheel cache receipt differs from the selected construction")
    if {path.name for path in entry.iterdir()} != {_RECEIPT, record["filename"]}:
        raise WheelError("source wheel cache entry is not one complete wheel")
    return entry / record["filename"], record["sha256"], record["size"]


def _copy_wheel(
    source: Path, output: Path, expected: str, size: int, deadline: float,
) -> Path:
    before = _ordinary(source)
    if before.st_size != size:
        raise WheelError("cached source wheel size differs from its receipt")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / source.name
    created = False
    try:
        with _open(source) as incoming:
            opened = _unchanged(source, before, incoming)
            with Path(_native_retained_path(destination)).open("xb") as target:
                created = True
                observed = _digest(incoming, size, deadline, destination=target)
            _unchanged(source, before, incoming, opened=opened)
            if observed != (expected, size):
                raise WheelError("cached source wheel digest differs from its receipt")
    except BaseException:
        # A colliding existing output belongs to the caller, not this attempt.
        if created:
            destination.unlink()
        raise
    return destination


def cached_source_wheel(
    owner_root: Path, binding: bytes, output: Path, *, build, validate,
    max_archive_bytes: int, deadline: float,
) -> Path:
    """Build once, publish atomically, and copy a held exact wheel to the caller.

    Only offline callers with exact admitted build artifacts may enter here.
    ``build`` writes one wheel in its supplied private directory; ``validate``
    checks that fresh artifact against the caller's source and build selection.
    Corrupt/foreign entries are explicit errors, never silently rebuilt in place.
    """
    _check_deadline(deadline)
    if (
        not isinstance(binding, bytes) or not binding
        or type(max_archive_bytes) is not int or max_archive_bytes <= 0
        or not owner_root.is_absolute() or not output.is_absolute()
    ):
        raise WheelError("source wheel caching requires bounded, owner-attributed inputs")
    key = hashlib.sha256(binding).hexdigest()
    # The installation owner has already admitted this root. Reject linked
    # cache components before acquiring its canonical per-key kernel lease.
    _directory(owner_root)
    cache = owner_root / ".source-wheel-cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    _directory(cache)
    with runtime.construction_lease(cache, "cache-root", timeout=deadline - time.monotonic()):
        root = cache / "inputs"
        if not os.path.lexists(root):
            _private_retained_root(cache)
        _directory(root)
    with runtime.construction_lease(cache, key, timeout=deadline - time.monotonic()):
        entry = root / key
        if os.path.lexists(entry):
            wheel, expected, size = _read_receipt(entry, key, max_archive_bytes)
            return _copy_wheel(wheel, output, expected, size, deadline)
        with tempfile.TemporaryDirectory(prefix=".build-", dir=root) as temporary:
            candidate = Path(temporary) / "entry"
            candidate.mkdir()
            wheel = build(candidate)
            _check_deadline(deadline)
            if (
                not isinstance(wheel, Path) or wheel.parent != candidate
                or _FILENAME.fullmatch(wheel.name) is None
                or {path.name for path in candidate.iterdir()} != {wheel.name}
            ):
                raise WheelError("source build did not produce one ordinary cache-local wheel")
            validate(wheel)
            before = _ordinary(wheel)
            with _open(wheel) as stream:
                opened = _unchanged(wheel, before, stream)
                expected, size = _digest(stream, max_archive_bytes, deadline)
                _unchanged(wheel, before, stream, opened=opened)
            if not size:
                raise WheelError("source build produced an empty wheel")
            (candidate / _RECEIPT).write_text(json.dumps({
                "schema": _SCHEMA, "version": 1, "key": key, "filename": wheel.name,
                "sha256": expected, "size": size,
            }, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")
            _check_deadline(deadline)
            candidate.rename(entry)
        wheel, expected, size = _read_receipt(entry, key, max_archive_bytes)
        return _copy_wheel(wheel, output, expected, size, deadline)
