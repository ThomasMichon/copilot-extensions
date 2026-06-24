"""agent-mcp -- a swiss-army MCP bridge.

Wraps an upstream MCP server (HTTP/SSE or a stdio child process) as a local
stdio MCP server, injecting host credentials (Entra/az, gh, git-credential, or a
static/env token) and applying an optional **decorator stack** (filter, rename,
defer, code-mode, storage). One config file describes one bridge: an upstream
``server`` launch spec (same shape as a ``.mcp.json`` entry) plus bridge
``auth``, ``decorators``, and other overrides.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the installed package metadata (pyproject version),
    # so `status` / `--version` never drift from the real version.
    __version__ = _pkg_version("agent-mcp")
except PackageNotFoundError:  # running from source without an install
    __version__ = "0.0.0+unknown"
