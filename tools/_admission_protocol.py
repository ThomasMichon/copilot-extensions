"""Shared host-wide admission-lease protocol.

Both ``tools/run-plugin-tests.py`` and
``tools/_devcontainer_host_admission.py`` must agree on the SAME lock
directory and service name to actually coordinate against one shared
lease -- extracted here, into one importable module, so neither
duplicates (and risks silently drifting from) the other's copy of this
contract. ``run-plugin-tests.py``'s hyphenated filename means it can't be
imported as a module itself, which is why this split exists one level up
rather than just importing that script directly.
"""

from __future__ import annotations

import os
from pathlib import Path

ADMISSION_SERVICE = "copilot-extensions-test-runner"


def admission_dir() -> Path:
    """Per-user, host-wide lock directory shared by all worktrees."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "copilot-extensions" / "test-runner"
