"""Scheduled-producer accounting capture, before signatures or cold archival."""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_logger.sync.publication_directory import publication_directory
from agent_logger.usage_ledger import UsageEvidenceError, merge_session_evidence
from agent_logger.usage_sqlite import UsageReadError, UsageStoreMissingError, read_sqlite_usage
from agent_logger.usage_transcript import read_transcript_usage

MAX_SESSIONS = 10000
if TYPE_CHECKING:
    from agent_logger.config import Config


@dataclass(frozen=True)
class CaptureResult:
    updated: int = 0
    failures: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.failures and not self.gaps


def capture_for_sync(
    cfg: Config, include: set[str] | None, *, verbose: bool = False,
) -> tuple[bool, bool]:
    """Report producer coverage and return compaction/failure decisions."""
    enabled = cfg.as_dict().get("sync", {}).get("usage_capture", True)
    if type(enabled) is not bool:
        raise ValueError("sync.usage_capture must be a boolean")
    if not enabled:
        return True, False
    accounting = capture_usage(cfg.sync_source, include)
    if accounting.updated or verbose:
        print(f"session-sync: preserved usage evidence for {accounting.updated} session(s)")
    for diagnostic in (*accounting.gaps, *accounting.failures):
        print(f"session-sync: accounting incomplete: {diagnostic}", file=sys.stderr)
    if not accounting.complete and cfg.sync_compact["enabled"]:
        print(
            "session-sync: cold compaction skipped; accounting preservation is incomplete",
            file=sys.stderr,
        )
    return accounting.complete, bool(accounting.failures)


def capture_usage(source: Path, include: set[str] | None) -> CaptureResult:
    """Capture only already-admitted live sessions; never mutate rescue inputs."""
    sessions: list[str] = []
    try:
        with publication_directory(source / "session-state", create=False) as root:
            with os.scandir(root.path if root.fd is None else root.fd) as entries:
                scanned = 0
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_SESSIONS:
                        raise UsageEvidenceError("session enumeration limit exceeded")
                    if include is not None and entry.name not in include:
                        continue
                    # Dot-prefixed siblings hold internal locks, not sessions.
                    if entry.name.startswith("."):
                        continue
                    if not stat.S_ISDIR(entry.stat(follow_symlinks=False).st_mode):
                        if entry.is_symlink():
                            raise UsageEvidenceError("redirected session directory refused")
                        continue
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", entry.name):
                        raise UsageEvidenceError("invalid session directory identity")
                    sessions.append(entry.name)
    except FileNotFoundError:
        return CaptureResult(gaps=("session-state source is missing",))
    except (OSError, ValueError) as exc:
        return CaptureResult(failures=(f"session enumeration: {exc}",))
    if not sessions:
        return CaptureResult()
    gaps: list[str] = []
    try:
        stored = read_sqlite_usage(source / "session-store.db", set(sessions))
        store_coverage = "captured"
    except UsageStoreMissingError:
        stored = {}
        store_coverage = "missing"
        gaps.append("session-store accounting source is missing")
    except (UsageReadError, OSError) as exc:
        return CaptureResult(failures=(f"session-store capture: {exc}",))
    failures: list[str] = []
    updated = 0
    for session_id in sorted(sessions):
        session = source / "session-state" / session_id
        try:
            try:
                transcript = read_transcript_usage(session, session_id)
                transcript_coverage = "captured"
            except FileNotFoundError:
                transcript = []
                transcript_coverage = "missing"
                gaps.append(f"{session_id}: transcript accounting source is missing")
            records = stored.get(session_id, []) + transcript
            updated += merge_session_evidence(
                session, session_id, records,
                coverage={"session-store": store_coverage, "transcript": transcript_coverage},
            )
        except (OSError, ValueError) as exc:
            failures.append(f"{session_id}: accounting capture failed: {exc}")
    return CaptureResult(updated, tuple(failures), tuple(gaps))
