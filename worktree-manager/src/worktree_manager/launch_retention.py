"""Local private-input maintenance; never inspects or removes execution queues."""

from __future__ import annotations

from pathlib import Path

from .launch_attempts import LaunchAttemptError, LaunchAttempts
from .seed_history import SeedHistory, SeedHistoryError


def maintain(root: Path | None = None) -> str | None:
    """Attempt both stores independently and report only sanitized failures."""
    failures = []
    for label, store in (
        ("launch journal", LaunchAttempts(root)),
        ("prompt history", SeedHistory("", root)),
    ):
        try:
            store.expire()
        except (LaunchAttemptError, SeedHistoryError):
            failures.append(label)
    if failures:
        return "Private-input retention maintenance failed for " + ", ".join(failures) + "."
    return None
