"""Session-sync content protection settings, separate from the core config map.

``sync.redact_secrets`` and ``sync.session_files`` are opt-in and honored by
the ``local`` and ``onedrive`` targets only; every other target refuses to
sync while either is on (see :mod:`agent_logger.sync.protection`). With both
off, published copies stay byte-identical to their sources.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_logger.sync.protection import ProtectionSettings

#: Defaults merged into ``DEFAULTS["sync"]``.
SYNC_PROTECTION_DEFAULTS: dict[str, Any] = {
    # redact_secrets: published copies replace secret-shaped strings with
    # "[REDACTED]" (JSON stays valid; a malformed JSON file is deferred;
    # databases and binaries holding a token shape are withheld).
    "redact_secrets": False,
    # session_files: "all" publishes every session file; "essential"
    # publishes only the essential set and ignores every other file --
    # including future kinds -- for change detection too.
    "session_files": "all",
}


class SyncProtectionConfig:
    _data: dict[str, Any]

    @property
    def sync_protection(self) -> ProtectionSettings:
        """Validated ``sync.redact_secrets`` / ``sync.session_files``."""
        from agent_logger.sync.protection import resolve_protection_settings

        return resolve_protection_settings(self._data.get("sync", {}))
