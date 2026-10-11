"""The shared opt-out for automatic and manually invoked sync operations."""

from __future__ import annotations

import os


def automation_disabled() -> bool:
    return os.environ.get("AGENT_LOGGER_SYNC_DISABLED") == "1"
