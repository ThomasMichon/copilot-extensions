"""Machine-only policy and pass-bound roots for scheduled process-log retirement."""

from __future__ import annotations

import errno
import os
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from agent_logger.config import Config
from agent_logger.segmenter.platform import detect_machine
from agent_logger.sync.automation import automation_disabled
from agent_logger.sync.log_archive_readback import ProcessLogReadback
from agent_logger.sync.log_archives import LogArchiveSettings
from agent_logger.sync.log_reclamation import LogReclamationResult, reclaim_process_logs
from agent_logger.sync.log_reclamation_policy import LogReclamationSettings
from agent_logger.sync.log_reclamation_targets import resolve_required_log_targets
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.sync.targets.base import Target


def validate_reclamation_context(cfg: Config, *, dry_run: bool) -> None:
    """Refuse unsupported enabled contexts before any ordinary sync writes."""
    if not cfg.process_log_reclamation.enabled:
        return
    if cfg._machine_origin is None:
        raise ValueError("process-log reclamation requires file-backed machine-only provenance")
    if cfg.sync_repo_allowlist or cfg.sync_repo_denylist or cfg.sync_require_repo_opt_in:
        raise ValueError("process-log reclamation does not support repository-filtered sync")
    if set(cfg.process_log_reclamation.required_targets) - {"local", "onedrive"}:
        raise ValueError("process-log reclamation required transport is unsupported")
    if not dry_run and os.name != "nt":
        raise OSError(errno.ENOTSUP, "process-log reclamation requires native Windows")
    with cfg._machine_origin.guard(cfg):
        pass


def _identity(path: Path) -> tuple[Path, int, int]:
    canonical = path.resolve(strict=True)
    info = canonical.stat()
    if not canonical.is_dir():
        raise ValueError("process-log reclamation source is not a directory")
    return canonical, info.st_dev, info.st_ino


@dataclass(frozen=True)
class LogReclamationPass:
    cfg: Config
    machine: str
    source: Path
    identity: tuple[Path, int, int]
    targets: tuple[ProcessLogReadback, ...]
    target_identities: tuple[tuple[Path, int, int] | None, ...]
    settings: LogReclamationSettings
    archives: LogArchiveSettings
    active_target: str

    @contextmanager
    def guard(self) -> Iterator[None]:
        origin = self.cfg._machine_origin
        if origin is None:
            raise ValueError("process-log reclamation lost machine-local policy provenance")
        with origin.guard(self.cfg):
            if automation_disabled():
                raise ValueError("process-log reclamation automation disabled")
            if (
                (self.cfg.machine_name or detect_machine()) != self.machine
                or self.cfg.process_logs_source.expanduser().absolute() != self.source
                or _identity(self.source) != self.identity
                or resolve_required_log_targets(
                    self.cfg, self.cfg.process_log_reclamation, self.machine,
                ) != self.targets
                or any(
                    observed is not None and (
                        target.directory is None or _identity(target.directory) != observed
                    )
                    for target, observed in zip(self.targets, self.target_identities)
                )
            ):
                raise ValueError("process-log reclamation source or required destinations changed")
            yield

    def run(self, *, dry_run: bool) -> LogReclamationResult:
        with self.guard():
            # Do not pin the policy for the transfer: an operator must still be
            # able to disable it while a long ZIP publication is in flight.
            pass
        with ExitStack() as evidence:
            for target, identity in zip(self.targets, self.target_identities):
                if identity is not None and target.directory is not None:
                    root = evidence.enter_context(
                        publication_directory(target.directory, create=False),
                    )
                    if _identity(root.path) != identity:
                        raise ValueError("required process-log destination root changed")
            return reclaim_process_logs(
                self.source, settings=self.settings, archives=self.archives,
                process_logs_enabled=True, active_target=self.active_target,
                targets=self.targets, policy_guard=self.guard, dry_run=dry_run,
                source_identity=self.identity,
            )


def prepare_reclamation(
    cfg: Config, machine: str, target: Target, *, dry_run: bool,
) -> LogReclamationPass | None:
    """Freeze one complete target set under the caller's existing sync lock."""
    if not cfg.process_log_reclamation.enabled:
        return None
    validate_reclamation_context(cfg, dry_run=dry_run)
    source = cfg.process_logs_source.expanduser().absolute()
    try:
        identity = _identity(source)
    except FileNotFoundError:
        # Absence at initial preparation is not a changed, already-bound source.
        # Never adopt a directory that appears later in this same pass.
        print("session-sync: process-log reclamation skipped (no process-log source)")
        return None
    targets = resolve_required_log_targets(cfg, cfg.process_log_reclamation, machine)
    active = next(item for item in targets if item.target_name == cfg.sync_target)
    if target.process_log_readback(machine) != active:
        raise ValueError("active process-log target changed before lock acquisition")
    identities = tuple(
        _identity(item.directory) if item.directory is not None and item.directory.exists() else None
        for item in targets
    )
    return LogReclamationPass(
        cfg, machine, source, identity, targets, identities,
        cfg.process_log_reclamation, cfg.process_log_archives, cfg.sync_target,
    )


def report_reclamation(run: LogReclamationPass, *, dry_run: bool) -> int:
    """Report real outcomes, including post-commit acknowledgement uncertainty."""
    result = run.run(dry_run=dry_run)
    mode = "dry-run " if dry_run else ""
    print(
        f"session-sync: process-log reclamation {mode}"
        f"inspected={result.inspected} eligible={result.eligible} retained={result.retained} "
        f"retired={result.retired} failed={len(result.failed)} "
        f"committed_unconfirmed={result.committed_unconfirmed} "
        f"reclaimed_bytes={result.reclaimed_bytes}"
    )
    if result.failed:
        print(
            f"session-sync: process-log reclamation incomplete: {'; '.join(result.failed)}",
            file=sys.stderr,
        )
    return int(bool(result.failed or result.committed_unconfirmed))
