"""Directory scanning and atomic entry writes."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TypeVar

from .model import EntryDecision, Finding, ScanAuthority, ScanSnapshot

T = TypeVar("T")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _registry_finding(
    registry: str,
    directory: Path,
    reason: str,
    detail: str,
) -> Finding:
    return Finding(
        registry=registry,
        entry=str(directory),
        status="indeterminate",
        reason=reason,
        detail=detail,
    )


def scan_directory(
    directory: str | os.PathLike[str],
    classify: Callable[[Path], EntryDecision[T]],
    *,
    registry: str,
    suffixes: Iterable[str] | None = None,
) -> ScanSnapshot[T]:
    """Classify a registry directory without conflating absence and unreadability.

    ``classify`` owns the registry-specific codec and validity rules. An
    :class:`OSError` reading one entry becomes an entry-indeterminate decision;
    other exceptions are intentionally not swallowed because a consumer adapter
    must convert definitive parse/schema failures into an explicit inactive
    decision with the right reason.
    """
    root = Path(directory)
    try:
        mode = root.stat().st_mode
    except FileNotFoundError:
        return ScanSnapshot(registry=registry, authority=ScanAuthority.ABSENT)
    except OSError as exc:
        finding = _registry_finding(
            registry, root, "registry-indeterminate", str(exc)
        )
        return ScanSnapshot(
            registry=registry,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )

    if not stat.S_ISDIR(mode):
        finding = _registry_finding(
            registry, root, "registry-indeterminate", "registry path is not a directory"
        )
        return ScanSnapshot(
            registry=registry,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )

    accepted = frozenset(suffixes) if suffixes is not None else None
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        finding = _registry_finding(
            registry, root, "registry-indeterminate", str(exc)
        )
        return ScanSnapshot(
            registry=registry,
            authority=ScanAuthority.INDETERMINATE,
            findings=(finding,),
        )

    decisions: dict[str, EntryDecision[T]] = {}
    findings: list[Finding] = []
    for path in entries:
        if accepted is not None and path.suffix not in accepted:
            continue
        key = str(path)
        try:
            info = path.lstat()
        except OSError as exc:
            decision = EntryDecision.indeterminate(
                Finding(
                    registry=registry,
                    entry=key,
                    status="indeterminate",
                    reason="entry-indeterminate",
                    detail=str(exc),
                )
            )
            decisions[key] = decision
            findings.extend(decision.findings)
            continue
        is_reparse = bool(
            getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        )
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse:
            decision = EntryDecision.inactive(
                Finding(
                    registry=registry,
                    entry=key,
                    status="inactive",
                    reason="invalid-entry",
                    detail="registry entry must be a regular non-reparse file",
                )
            )
            decisions[key] = decision
            findings.extend(decision.findings)
            continue
        try:
            decision = classify(path)
        except OSError as exc:
            decision = EntryDecision.indeterminate(
                Finding(
                    registry=registry,
                    entry=key,
                    status="indeterminate",
                    reason="entry-indeterminate",
                    detail=str(exc),
                )
            )
        decisions[key] = decision
        findings.extend(decision.findings)

    return ScanSnapshot(
        registry=registry,
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
        findings=tuple(findings),
    )


def atomic_write_text(
    path: str | os.PathLike[str],
    content: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Atomically replace one registry entry in its own directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=str(target.parent),
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return target
