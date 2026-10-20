"""Engine wiring for opt-in session-sync protection.

``sync.redact_secrets`` / ``sync.session_files`` (see
:mod:`agent_logger.sync.protection`) change what a sync pass publishes and
when it counts as complete. :mod:`agent_logger.sync.engine` calls these
helpers at fixed points; each is inert while protection is off, so a default
pass behaves exactly as before.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from agent_logger.sync.meta import (
    MAX_DEFERRED_FILE_SAMPLES,
    MAX_DEFERRED_PATH_CHARS,
)
from agent_logger.sync.meta import (
    status_text as _status_text,
)
from agent_logger.sync.protection import MAX_SUMMARY_ENTRIES, PROTECTED_TARGETS
from agent_logger.sync.targets.base import DoctorResult

PROCESS_LOGS_REDACTION_NOTE = (
    "session-sync: process-log sync skipped (process logs are not redacted; "
    "sync.redact_secrets is on)"
)
_UNSUPPORTED = "not supported with sync.redact_secrets / sync.session_files"


def apply_protection(cfg, target) -> str | None:
    """Hand ``sync.redact_secrets`` / ``sync.session_files`` to *target*.

    Returns a refusal message when either is enabled for a target that does
    not implement them: publishing unprotected copies would silently ignore
    the operator's setting.
    """
    protection = cfg.sync_protection
    if not protection.enabled:
        return None
    if target.name not in PROTECTED_TARGETS:
        supported = " and ".join(sorted(PROTECTED_TARGETS))
        return (
            "sync.redact_secrets / sync.session_files are supported only by the "
            f"{supported} targets (configured: {target.name})"
        )
    target.protection = protection
    return None


def refuse(cfg, target) -> bool:
    """Apply protection to *target*, reporting a refusal on stderr."""
    refusal = apply_protection(cfg, target)
    if refusal:
        print(f"session-sync: {refusal}", file=sys.stderr)
    return bool(refusal)


def doctor(cfg, target, machine) -> DoctorResult:
    """*target*'s doctor under the configured protection.

    A refusal (see :func:`apply_protection`) is a failed check and the target
    is not probed. A protected target also learns the machine
    (``machine(cfg)``) whose publication receipts its doctor inspects.
    """
    refusal = apply_protection(cfg, target)
    if refusal:
        return DoctorResult(ok=False, checks=[("session protection", False, refusal)])
    if cfg.sync_protection.enabled:
        target.doctor_machine = machine(cfg)
    return target.doctor()


def print_scope(cfg, label: str = "protection:") -> None:
    """Show the effective protection scope when protection is on."""
    protection = cfg.sync_protection
    if protection.enabled:
        print(f"{label} {protection.describe()}")


def scope_suffix(cfg) -> str:
    """Dry-run summary suffix naming the effective protection scope."""
    protection = cfg.sync_protection
    return f", protection: {protection.describe()}" if protection.enabled else ""


def reclamation_allowed(cfg) -> bool:
    """Process logs are never redacted, so redaction keeps them -- and their
    reclamation, which needs a completed publication -- off the target."""
    return not cfg.sync_protection.redact_secrets


def process_logs_skipped(cfg) -> bool:
    """Whether redaction keeps enabled process logs off the target (noted)."""
    skipped = bool(cfg.sync_protection.redact_secrets and cfg.process_logs_enabled)
    if skipped:
        print(PROCESS_LOGS_REDACTION_NOTE)
    return skipped


def compaction_enabled(cfg):
    """Configured compaction, kept off (and noted) while protection is on.

    Compaction publishes whole-session archives and rewrites hub copies,
    which the protected projection does not cover.
    """
    enabled = cfg.sync_compact["enabled"]
    if enabled and cfg.sync_protection.enabled:
        print(f"session-sync: compaction skipped ({_UNSUPPORTED})")
        return False
    return enabled


def refuse_compact_hub(cfg) -> bool:
    """Refuse a hub compaction pass while protection is on (see above)."""
    if not cfg.sync_protection.enabled:
        return False
    print(f"session-sync compact-hub: {_UNSUPPORTED}", file=sys.stderr)
    return True


def merge_protection(total: dict | None, part: dict | None) -> dict | None:
    """Fold one batch's protection summary into the pass total."""
    if part is None:
        return total
    if total is None:
        return {
            **part,
            "deferred": list(part.get("deferred", [])),
            "withheld": list(part.get("withheld", [])),
        }
    for key in (
        "published_files",
        "unselected_files",
        "redacted_strings",
        "deferred_count",
        "withheld_count",
    ):
        total[key] = total.get(key, 0) + part.get(key, 0)
    for key in ("deferred", "withheld"):
        room = MAX_SUMMARY_ENTRIES - len(total[key])
        if room > 0:
            total[key].extend(part.get(key, [])[:room])
    return total


def print_protection(result) -> None:
    """Report the files a protected publication withheld or deferred."""
    summary = result.protection_summary
    if not summary:
        return
    for label, key, count_key, suffix in (
        ("withheld", "withheld", "withheld_count", ""),
        ("deferred", "deferred", "deferred_count", ", will retry"),
    ):
        count = summary.get(count_key, 0)
        if not count:
            continue
        examples = ", ".join(
            f"{_status_text(item.get('path'))} [{_status_text(item.get('reason'))}]"
            for item in summary.get(key, [])[:3]
        )
        print(f"session-sync: {label} {count} protected file(s){suffix}: {examples}")


def print_protection_status(summary) -> None:
    """Show the last publication's protection summary (metadata only)."""
    if summary is None:
        return
    if not isinstance(summary, dict):
        print("protection_last: (invalid session_protection metadata)")
        return
    print(f"protection_last: {_status_text(summary.get('settings'))}")
    for label, key in (
        ("published", "published_files"),
        ("unselected", "unselected_files"),
        ("redacted_strings", "redacted_strings"),
        ("deferred", "deferred_count"),
        ("withheld", "withheld_count"),
    ):
        print(f"  {label}: {_status_text(summary.get(key, 0))}")
    for label, key in (("deferred", "deferred"), ("withheld", "withheld")):
        items = summary.get(key)
        if not isinstance(items, list):
            continue
        for item in items[:MAX_DEFERRED_FILE_SAMPLES]:
            if not isinstance(item, dict):
                print(f"  - {label}: (invalid)")
                continue
            path = _status_text(item.get("path"), "(invalid)", MAX_DEFERRED_PATH_CHARS)
            reason = _status_text(item.get("reason"))
            print(f"  - {label}: {path} [{reason}]")


def protected_deferrals(result) -> int:
    """Files a protected publication held back for an unreadable record."""
    return int((result.protection_summary or {}).get("deferred_count", 0))


def path_session_id(rel: str) -> str | None:
    """The session a pushed relative path belongs to, if any."""
    parts = Path(rel).parts
    if len(parts) >= 2 and parts[0] == "session-state":
        return parts[1]
    if len(parts) == 2 and parts[0] == "provenance":
        return Path(parts[1]).stem
    return None


class ProtectedPass:
    """Protection bookkeeping for one change-tracked sync pass.

    Inert unless *target* carries enabled protection, so an unprotected pass
    keeps its original change tracking, completion marker and result.
    """

    def __init__(self, target) -> None:
        protection = getattr(target, "protection", None)
        enabled = protection is not None and protection.enabled
        self.protection = protection if enabled else None
        self.target = target
        self.tracker = None
        self.total: dict | None = None
        self.deferred_paths: list[str] = []
        self.deferred_sids: set[str] = set()
        self.last_deferred_sids: set[str] = set()
        self.withheld_paths: dict[str, None] = {}
        self.deferral = False
        self.extra_files = 0

    def change_tracker(self, db_path):
        """The pass's change tracker.

        In essential-only mode the change check considers exactly the files
        ``session_files`` publishes, so an unlisted file never triggers a push.
        """
        from agent_logger.sync.change_tracker import ChangeTracker

        protection = self.protection
        if protection is not None and protection.essential_only:
            self.tracker = ChangeTracker(
                db_path, member_filter=protection.member_included, include_provenance=False,
            )
        else:
            self.tracker = ChangeTracker(db_path)
        return self.tracker

    def identity_suffix(self) -> str:
        """A changed protection setting changes what the destination should
        hold, exactly like a changed destination: reconcile from scratch."""
        if self.protection is None:
            return ""
        return f"|protect={self.protection.fingerprint()}"

    def tracks_index(self, unfiltered: bool) -> bool:
        """A protected publication never sends the session index (withheld or
        unselected), so an index change alone must not trigger a push."""
        return unfiltered and self.protection is None

    def add(self, result) -> None:
        """Fold one full-reconciliation batch result into the pass totals."""
        if self.protection is None:
            return
        self.total = merge_protection(self.total, result.protection_summary)
        self.deferral = self.deferral or protected_deferrals(result) > 0
        self.deferred_paths.extend(result.deferred_files)
        self.deferred_sids.update(result.deferred_sessions)
        self.last_deferred_sids = set(result.deferred_sessions)
        self.withheld_paths.update(dict.fromkeys(result.withheld_files))

    def repush(self, source, machine, unfiltered: bool, verbose: bool, record):
        """Re-publish sessions deferred by an earlier batch; a failure result
        when that push fails, else ``None``.

        Each batch rewrites the destination's sync status, so a deferral in
        an earlier batch would read as fully synced. Publishing the deferred
        sessions once more (their current files are skipped) makes the
        recorded status list every file still held back. *record* is the
        engine's push-result recorder.
        """
        if not (self.deferral and self.deferred_sids - self.last_deferred_sids):
            return None
        repush_ids = sorted(self.deferred_sids)
        if verbose:
            print(f"session-sync: re-checking {len(repush_ids)} deferred session(s)")
        snapshot = self.tracker.snapshot(source, repush_ids)
        result = self.target.push(source, machine, set(repush_ids), batch_mode=unfiltered)
        if not result.ok:
            return result
        self.extra_files += result.file_count
        record(self.tracker, snapshot, result, False, None)
        self.deferred_paths = [
            path for path in self.deferred_paths
            if path_session_id(path) not in self.deferred_sids
        ] + list(result.deferred_files)
        self.deferred_sids = set(result.deferred_sessions)
        self.withheld_paths.update(dict.fromkeys(result.withheld_files))
        self.deferral = protected_deferrals(result) > 0
        if self.total is not None and result.protection_summary is not None:
            self.total["deferred_count"] = result.protection_summary.get(
                "deferred_count", 0,
            )
            self.total["deferred"] = list(
                result.protection_summary.get("deferred", []),
            )
        return None

    def mark_full_sync(self) -> None:
        """Record full-sync completion unless a protected file was held back:
        that keeps the reconciliation due, so the ordinary schedule retries it."""
        if not self.deferral:
            self.tracker.mark_full_sync()

    def finish(self, result):
        """Attach the pass's protection totals to a full-reconciliation result."""
        if self.protection is None:
            return result
        total = self.total
        if total is not None:
            # The session index rides along with every batch: count each
            # withheld file once.
            samples: dict[str, dict] = {}
            for item in total.get("withheld", []):
                samples.setdefault(str(item.get("path")), item)
            total["withheld"] = list(samples.values())[:MAX_SUMMARY_ENTRIES]
            total["withheld_count"] = len(self.withheld_paths)
        return dataclasses.replace(
            result,
            file_count=result.file_count + self.extra_files,
            deferred_sessions=tuple(sorted(self.deferred_sids)),
            deferred_files=tuple(self.deferred_paths),
            withheld_files=tuple(self.withheld_paths),
            protection_summary=total,
        )
