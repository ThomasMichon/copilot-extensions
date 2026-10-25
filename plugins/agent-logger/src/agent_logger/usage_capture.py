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
from agent_logger.usage_progress import TranscriptBudget, capture_order, read_transcript_progress
from agent_logger.usage_sqlite import (
    UsageReadError, UsageRowLimitError, UsageStoreMissingError, UsageTimeLimitError,
    read_sqlite_usage,
)
from agent_logger.usage_transcript import read_transcript_usage

MAX_SESSIONS = 10000
CAPTURE_BATCH_SIZE = 32
MAX_CAPTURE_BYTES = 256 * 1024 * 1024
if TYPE_CHECKING:
    from agent_logger.config import Config


@dataclass(frozen=True)
class CaptureResult:
    updated: int = 0
    failures: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    rebuilt: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.failures and not self.gaps and not self.deferred


def capture_for_sync(
    cfg: Config, include: set[str] | None, *, verbose: bool = False,
) -> tuple[bool, bool]:
    """Report producer coverage and return compaction/failure decisions."""
    enabled = cfg.as_dict().get("sync", {}).get("usage_capture", True)
    if type(enabled) is not bool:
        raise ValueError("sync.usage_capture must be a boolean")
    if not enabled:
        return True, False
    accounting = capture_usage(cfg.sync_source, include, cache_root=cfg.home / "transcript-progress")
    if accounting.updated or verbose:
        print(f"session-sync: preserved usage evidence for {accounting.updated} session(s)")
    for diagnostic in (*accounting.gaps, *accounting.failures):
        print(f"session-sync: accounting incomplete: {diagnostic}", file=sys.stderr)
    for diagnostic in accounting.deferred:
        print(f"session-sync: accounting deferred: {diagnostic}", file=sys.stderr)
    for session_id in accounting.rebuilt:
        print(f"session-sync: accounting cache rebuilt: {session_id}", file=sys.stderr)
    if not accounting.complete and cfg.sync_compact["enabled"]:
        print(
            "session-sync: cold compaction skipped; accounting preservation is incomplete",
            file=sys.stderr,
        )
    return accounting.complete, bool(accounting.failures)


def capture_usage(
    source: Path, include: set[str] | None, *, cache_root: Path | None = None,
) -> CaptureResult:
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
    admitted = sorted(sessions)
    budget = TranscriptBudget(MAX_CAPTURE_BYTES)
    try:
        with capture_order(cache_root, source, admitted, budget) as (ordered, rebuilt):
            result = _combine_results([
                _capture_batch(source, ordered[offset : offset + CAPTURE_BATCH_SIZE], cache_root, budget)
                for offset in range(0, len(ordered), CAPTURE_BATCH_SIZE)
            ])
            if rebuilt:
                result = _combine_results([result, CaptureResult(rebuilt=("collection order",))])
            return result
    except (OSError, ValueError) as exc:
        return CaptureResult(failures=(f"accounting collection progress: {exc}",))


def _combine_results(results: list[CaptureResult]) -> CaptureResult:
    return CaptureResult(
        sum(result.updated for result in results),
        tuple(failure for result in results for failure in result.failures),
        tuple(dict.fromkeys(gap for result in results for gap in result.gaps)),
        tuple(item for result in results for item in result.deferred),
        tuple(item for result in results for item in result.rebuilt),
    )


def _capture_batch(
    source: Path, sessions: list[str], cache_root: Path | None, budget: TranscriptBudget,
) -> CaptureResult:
    gaps: list[str] = []
    split = False
    try:
        stored = read_sqlite_usage(source / "session-store.db", set(sessions))
        store_coverage = "captured"
    except (UsageRowLimitError, UsageTimeLimitError) as exc:
        if len(sessions) == 1:
            return CaptureResult(failures=(f"{sessions[0]}: session-store capture: {exc}",))
        split = True
    except UsageStoreMissingError:
        stored = {}
        store_coverage = "missing"
        gaps.append("session-store accounting source is missing")
    except (UsageReadError, OSError) as exc:
        return CaptureResult(failures=(f"session-store capture: {exc}",))
    if split:
        middle = len(sessions) // 2
        return _combine_results([
            _capture_batch(source, sessions[:middle], cache_root, budget),
            _capture_batch(source, sessions[middle:], cache_root, budget),
        ])
    failures: list[str] = []
    deferred: list[str] = []
    rebuilt: list[str] = []
    updated = 0
    for session_id in sessions:
        session = source / "session-state" / session_id
        try:
            try:
                if cache_root is None:
                    transcript = read_transcript_usage(session, session_id)
                else:
                    progress = read_transcript_progress(
                        session, session_id, cache_root, total_budget=budget,
                    )
                    if progress.rebuilt:
                        rebuilt.append(session_id)
                    if not progress.complete:
                        deferred.append(
                            f"{session_id}: transcript {progress.reason} "
                            f"({progress.offset}/{progress.size} bytes)"
                        )
                        continue
                    transcript = progress.records
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
    return CaptureResult(updated, tuple(failures), tuple(gaps), tuple(deferred), tuple(rebuilt))
