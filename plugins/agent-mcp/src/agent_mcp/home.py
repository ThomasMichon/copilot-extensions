"""Dependency-free legacy state-home selection shared by all MCP consumers."""

from __future__ import annotations

import os
from pathlib import Path

_LEGACY_ROOT = ".agent-mcp"  # marketplace-isolation: allow legacy compatibility root


def default_home_dir() -> Path:
    """Return the absolute legacy home without creating it.

    An unset or empty ``AGENT_MCP_HOME`` uses ``~/.agent-mcp``. Explicit
    relative paths retain their launch-directory meaning. Make them absolute
    without collapsing parent components, dereferencing symlinks or expanding
    literal path text.
    A process changing cwd must bind this selection before doing so.
    """
    raw = os.environ.get("AGENT_MCP_HOME")
    path = raw if raw else Path.home() / _LEGACY_ROOT
    return Path(path).absolute()
