"""Filesystem sync targets: ``local`` and ``onedrive``.

Both publish the source tree into ``<root>/<machine>/`` via an incremental
copy (size + mtime delta). They differ only in how the root is resolved:

- ``local`` -- a dotfolder under ``$HOME`` (default
  ``~/.agent-logger/sessions``), or an explicit ``path``.
- ``onedrive`` -- a ``subfolder`` under the OS-resolved OneDrive root, which
  turns a OneDrive folder into a NAS-equivalent fleet aggregation point.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from agent_logger.sync.meta import write_sync_meta
from agent_logger.sync.targets.base import DoctorResult, PushResult, Target

#: Files never copied to a destination (session lock sidecars, temp files).
_EXCLUDE_NAMES = frozenset({".lock"})


def _needs_copy(src: Path, dst: Path) -> bool:
    """Copy if the destination is missing, a different size, or older."""
    try:
        d = dst.stat()
    except OSError:
        return True
    s = src.stat()
    return s.st_size != d.st_size or s.st_mtime > d.st_mtime + 1e-6


def _count_sessions(dest: Path) -> int:
    """Count session directories under ``<dest>/session-state``."""
    base = dest / "session-state"
    if not base.is_dir():
        return 0
    return sum(1 for d in base.iterdir() if d.is_dir())


class FilesystemTarget(Target):
    """Base for targets that publish to a local-or-mounted directory root."""

    def _root(self) -> Path:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def push(self, source: Path, machine: str) -> PushResult:
        if not source.is_dir():
            return PushResult(ok=False, detail=f"source not found: {source}")
        dest = self._root() / machine
        try:
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return PushResult(ok=False, detail=f"cannot create {dest}: {exc}")

        copied = 0
        nbytes = 0
        for src_file in source.rglob("*"):
            if src_file.is_dir() or src_file.name in _EXCLUDE_NAMES:
                continue
            rel = src_file.relative_to(source)
            dst_file = dest / rel
            if _needs_copy(src_file, dst_file):
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src_file, dst_file)
                except OSError as exc:
                    return PushResult(ok=False, detail=f"copy failed: {exc}")
                copied += 1
                nbytes += src_file.stat().st_size

        session_count = _count_sessions(dest)
        write_sync_meta(dest, machine, self.name, "ok", session_count)
        return PushResult(
            ok=True,
            detail=f"-> {dest}",
            file_count=copied,
            byte_count=nbytes,
        )

    def prune(self, machine: str, retention_days: int | None) -> int:
        if not isinstance(retention_days, (int, float)) or retention_days <= 0:
            return 0
        base = self._root() / machine / "session-state"
        if not base.is_dir():
            return 0
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for d in base.iterdir():
            if not d.is_dir():
                continue
            newest = max(
                (f.stat().st_mtime for f in d.rglob("*") if f.is_file()),
                default=d.stat().st_mtime,
            )
            if newest < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
        return removed

    def doctor(self) -> DoctorResult:
        result = DoctorResult(ok=True)
        root = self._root()
        # Walk up to the nearest existing ancestor to test writability.
        probe = root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        result.add("root resolved", True, str(root))
        result.add("ancestor exists", probe.exists(), str(probe))
        result.add("ancestor writable", os.access(probe, os.W_OK), str(probe))
        return result

    def describe(self) -> str:
        return f"{self.name}: {self._root()}"


class LocalTarget(FilesystemTarget):
    """Publish to a dotfolder under ``$HOME`` (default) or an explicit path."""

    name = "local"

    def _root(self) -> Path:
        path = self.options.get("path")
        if path:
            return Path(path).expanduser()
        return Path.home() / ".agent-logger" / "sessions"


def resolve_onedrive_root() -> Path | None:
    """Resolve the OneDrive root for the current OS, or ``None``.

    Honors the Windows ``OneDrive*`` environment variables first, then falls
    back to ``~/OneDrive`` if it exists.
    """
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    fallback = Path.home() / "OneDrive"
    if fallback.is_dir():
        return fallback
    return None


class OneDriveTarget(FilesystemTarget):
    """Publish to a subfolder under the resolved OneDrive root."""

    name = "onedrive"

    def _root(self) -> Path:
        explicit = self.options.get("root")
        base = Path(explicit).expanduser() if explicit else resolve_onedrive_root()
        if base is None:
            raise FileNotFoundError(
                "OneDrive root not found; set sync.targets.onedrive.root or the "
                "OneDrive environment variable"
            )
        subfolder = self.options.get("subfolder", "Apps/agent-logger/sessions")
        return base / subfolder

    def doctor(self) -> DoctorResult:
        # Surface a clear failure if OneDrive can't be resolved at all.
        if not self.options.get("root") and resolve_onedrive_root() is None:
            result = DoctorResult(ok=True)
            result.add("OneDrive root resolved", False, "no OneDrive env var or ~/OneDrive")
            return result
        return super().doctor()
