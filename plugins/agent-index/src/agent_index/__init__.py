"""agent-index runtime package."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the installed package metadata (pyproject version),
    # so `status` / `--version` never drift from the real version. Mirrors the
    # ecosystem convention (agent-mcp, agent-bridge, ...) and removes the
    # hardcoded-version foot-gun that left __version__ lagging the triplet.
    __version__ = _pkg_version("agent-index")
except PackageNotFoundError:  # running from source without an install
    __version__ = "0.1.0-dev136"
