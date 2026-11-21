"""Receive a physically sealed legacy authority namespace, never infer a drain.

The boot provider supplies observations, not permission to seal or reboot.
Unsealed installations retain ordinary publication but cannot retire an
ambiguous legacy attempt. This receiver does not create a transition.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from . import config as cfg

CONTRACT = "agent-worktrees.pr-legacy-admission.v1"
AUTHORITY_CONTRACT = "agent-worktrees.pr-authority-file.v1"
_AUTHORITY = "pr-rewrite-authority.lock"
_TRANSITION = "pr-legacy-admission"
_MAX_BYTES = 8192
Identity = tuple[int, int]


class LegacyAdmissionError(ValueError):
    """No qualified exclusion of old publication admission and in-flight work."""


@dataclass(frozen=True)
class Seal:
    """One exact physical transition, not an all-publisher inventory."""

    root: Identity
    directory: Identity
    original: Identity
    barrier: Identity
    domain: str
    boot: str
    journal: bytes


def _identity(path: Path, *, directory: bool = False) -> Identity:
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
        or (not directory and info.st_nlink != 1)
        or not all(type(value) is int and 0 <= value < 1 << 128 for value in (info.st_dev, info.st_ino))
        or not info.st_ino
    ):
        raise LegacyAdmissionError("Legacy admission requires plain, unaliased native paths.")
    return info.st_dev, info.st_ino


def _native_volume_type(root: Path) -> int:
    if os.name != "nt" or sys.platform != "win32":
        raise LegacyAdmissionError("Legacy authority sealing has no qualified native volume provider.")
    import ctypes

    try:
        query = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True).GetDriveTypeW
        query.argtypes = [ctypes.c_wchar_p]
        query.restype = ctypes.c_uint
        return int(query(root.anchor))
    except (OSError, AttributeError) as error:
        raise LegacyAdmissionError("Native legacy authority volume evidence is unavailable.") from error


def _root() -> Path:
    root = cfg.install_dir()
    if (
        not root.is_absolute() or root.resolve(strict=True) != root
        or str(root).startswith(("\\\\", "//"))
    ):
        raise LegacyAdmissionError("Legacy admission requires its canonical local installation.")
    if _native_volume_type(root) != 3:
        raise LegacyAdmissionError("Legacy admission requires a qualified fixed local volume.")
    for path in (root, *root.parents):
        _identity(path, directory=True)
    return root


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyAdmissionError("Duplicate legacy admission field.")
        result[key] = value
    return result


def _record_identity(value: object) -> Identity:
    if (
        not isinstance(value, list) or len(value) != 2
        or any(type(item) is not int or not 0 <= item < 1 << 128 for item in value)
        or not value[1]
    ):
        raise LegacyAdmissionError("Invalid legacy admission file identity.")
    return value[0], value[1]


def _bytes(path: Path) -> bytes:
    identity = _identity(path)
    if path.lstat().st_size > _MAX_BYTES:
        raise LegacyAdmissionError("Legacy admission journal exceeds its bound.")
    with path.open("rb") as stream:
        if (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) != identity:
            raise LegacyAdmissionError("Legacy admission journal changed while opening.")
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES or _identity(path) != identity:
        raise LegacyAdmissionError("Legacy admission journal changed while reading.")
    return raw


def read_seal() -> Seal:
    """Validate physical closure of the one supported historical lock entry."""
    try:
        from update_driver.boot import parse_execution_domain_boot_evidence

        root = _root()
        directory = root / _TRANSITION
        _identity(directory, directory=True)
        raw = _bytes(directory / "transition.json")
        value = json.loads(raw, object_pairs_hook=_unique)
        if (
            not isinstance(value, dict)
            or set(value) != {
                "contract", "phase", "authority_contract", "root", "directory",
                "original", "barrier", "sealed_boot",
            }
            or value["contract"] != CONTRACT or value["phase"] != "sealed"
            or value["authority_contract"] != AUTHORITY_CONTRACT
        ):
            raise LegacyAdmissionError("Unsupported or incomplete legacy admission transition.")
        evidence = parse_execution_domain_boot_evidence(value["sealed_boot"])
        seal = Seal(
            _record_identity(value["root"]), _record_identity(value["directory"]),
            _record_identity(value["original"]), _record_identity(value["barrier"]),
            evidence.domain, evidence.boot, raw,
        )
        if (
            _identity(root, directory=True) != seal.root
            or _identity(directory, directory=True) != seal.directory
            or _identity(directory / "retained.lock") != seal.original
            or _identity(root / _AUTHORITY, directory=True) != seal.barrier
            or seal.original == seal.barrier
            or set(entry.name for entry in directory.iterdir()) != {"transition.json", "retained.lock"}
        ):
            raise LegacyAdmissionError("Legacy admission physical seal differs from its record.")
        entries = list((root / _AUTHORITY).iterdir())
        if entries and entries != [root / _AUTHORITY / "writer.lock"]:
            raise LegacyAdmissionError("Legacy admission barrier has unexpected entries.")
        if entries:
            _identity(entries[0])
        return seal
    except LegacyAdmissionError:
        raise
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError, ImportError, RuntimeError) as error:
        raise LegacyAdmissionError(
            "Legacy publication admission is not physically qualified; retain the attempt.",
        ) from error


def require_guarded_quiescence(*, expected: Seal | None = None) -> Seal:
    """Qualify the guarded historical entry, not unguarded publisher families."""
    try:
        from update_driver.boot import (
            native_execution_domain_boot_evidence,
        )

        before = read_seal()
        current = native_execution_domain_boot_evidence()
        if current.domain != before.domain or current.boot == before.boot:
            raise LegacyAdmissionError(
                "Legacy publication quiescence needs a later boot in the same execution domain.",
            )
        if (expected is not None and before != expected) or read_seal() != before:
            raise LegacyAdmissionError("Legacy admission changed during qualification.")
        return before
    except (ImportError, OSError, RuntimeError) as error:
        raise LegacyAdmissionError("Native legacy publication boot evidence is unavailable.") from error


def _require_complete_coverage(_seal: Seal) -> None:
    raise LegacyAdmissionError(
        "Complete legacy publication coverage is unsupported: older foreign-provider "
        "creation does not enter the authority file. Retain the attempt; a boot "
        "and authority barrier alone do not qualify recovery."
    )


def require_quiescence(*, expected: Seal | None = None) -> Seal:
    """Refuse retirement until every relevant historical entry is covered."""
    seal = require_guarded_quiescence(expected=expected)
    _require_complete_coverage(seal)
    return seal


def authority_path() -> tuple[Path, Seal | None]:
    """Preserve old-syntax publication; never create a second authority in a gap."""
    root = cfg.install_dir()
    historical = root / _AUTHORITY
    if not os.path.lexists(root / _TRANSITION) and not historical.is_dir():
        return historical, None
    seal = read_seal()
    return historical / "writer.lock", seal


def validate_authority(path: Path, stream: BinaryIO, seal: Seal | None) -> None:
    """Recheck the opened writer against the exact admitted physical namespace."""
    if seal is None:
        if authority_path() != (path, None):
            raise LegacyAdmissionError("Legacy authority transitioned during admission.")
        return
    if read_seal() != seal:
        raise LegacyAdmissionError("Legacy authority seal changed during admission.")
    opened = os.fstat(stream.fileno())
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != _identity(path):
        raise LegacyAdmissionError("Legacy authority writer changed during admission.")
