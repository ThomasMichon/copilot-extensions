"""Process-log snapshot configuration, separate from the core config map."""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any

from agent_logger.sync.log_archives import LogArchiveSettings, resolve_log_archive_settings


class ProcessLogArchiveConfig:
    _data: dict[str, Any]

    @property
    @abstractmethod
    def sync_source(self) -> Path:
        """The containing configuration supplies its resolved local source."""
        raise NotImplementedError

    @property
    def process_logs_enabled(self) -> bool:
        """Opt-in publication; repo-scoped passes remain excluded by the engine."""
        return bool(self._data.get("sync", {}).get("process_logs", {}).get("enabled", False))

    @property
    def process_logs_source(self) -> Path:
        """Configured machine's process-log directory."""
        return self.sync_source / "logs"

    @property
    def process_log_archives(self) -> LogArchiveSettings:
        """Validated additive ZIP snapshot policy for local process logs."""
        raw = self._data.get("sync", {}).get("process_logs", {}).get("zip_snapshots")
        return resolve_log_archive_settings(raw)
