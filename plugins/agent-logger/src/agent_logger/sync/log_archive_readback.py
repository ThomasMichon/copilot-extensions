"""Pass-local, exact-revision ZIP readback; never source-retirement authority."""

from __future__ import annotations

import hashlib
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LogArchiveRevision:
    logical_name: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"process-[0-9]{1,20}-[0-9]{1,10}\.log", self.logical_name):
            raise ValueError("process-log revision requires a raw producer filename")
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("process-log revision requires a SHA-256 digest")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise ValueError("process-log revision size must be a nonnegative integer")

    @property
    def archive_name(self) -> str:
        key = hashlib.sha256(
            self.logical_name.encode("utf-8") + b"\0" + self.sha256.encode("ascii"),
        ).hexdigest()
        return f"process-log-{key}.zip"


@dataclass(frozen=True)
class LogArchiveReadbackResult:
    target_name: str
    revision: LogArchiveRevision
    supported: bool
    verified: bool
    detail: str


@dataclass(frozen=True)
class ProcessLogReadback:
    """A destination resolved once for a pass, independent of mutable options."""

    target_name: str
    directory: Path | None = None

    def verify(self, revision: LogArchiveRevision) -> LogArchiveReadbackResult:
        if self.directory is None:
            return LogArchiveReadbackResult(
                self.target_name, revision, False, False,
                "target does not support process-log ZIP readback",
            )
        # Deferred to avoid the target registry's import cycle with log_archives.
        from agent_logger.sync.log_archives import _verify_archive
        from agent_logger.sync.publication_directory import publication_directory

        try:
            with publication_directory(self.directory, create=False) as root:
                _verify_archive(
                    root, revision.archive_name, revision.logical_name,
                    revision.sha256, revision.size,
                )
        except (OSError, ValueError, EOFError, zipfile.BadZipFile, zlib.error, RuntimeError) as exc:
            # Do not expose configured paths or arbitrary exception text in reports.
            return LogArchiveReadbackResult(
                self.target_name, revision, True, False,
                f"{revision.archive_name}: readback failed ({type(exc).__name__})",
            )
        return LogArchiveReadbackResult(
            self.target_name, revision, True, True, revision.archive_name,
        )


def filesystem_log_readback(name: str, root: Path, machine: str) -> ProcessLogReadback:
    """Resolve local stored bytes, not a cloud client's upload acknowledgement."""
    if (
        not machine or machine in {".", ".."}
        or any(char in machine for char in "/\\:\0")
    ):
        raise ValueError("process-log readback requires a local machine, not a namespace")
    return ProcessLogReadback(name, root.expanduser().absolute() / machine / "logs")


@dataclass(frozen=True)
class RequiredLogReadbackResult:
    results: tuple[LogArchiveReadbackResult, ...]

    @property
    def verified(self) -> bool:
        """Empty, partial or unsupported verification is never a quorum."""
        return bool(self.results) and all(
            result.supported and result.verified for result in self.results
        )


def verify_required_log_archives(
    targets: tuple[ProcessLogReadback, ...], revision: LogArchiveRevision,
) -> RequiredLogReadbackResult:
    """Read every resolved target afresh; no transfer or prior receipt is consulted."""
    return RequiredLogReadbackResult(tuple(target.verify(revision) for target in targets))
