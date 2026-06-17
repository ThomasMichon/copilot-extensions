"""agent-mcp -- a reusable MCP bridge.

Wraps an upstream MCP server (HTTP/SSE or a stdio child process) as a local
stdio MCP server, injecting host credentials (Entra/az, gh, git-credential, or a
static/env token). One config file describes one bridge: an upstream ``server``
launch spec (same shape as a ``.mcp.json`` entry) plus bridge ``auth`` and other
overrides.
"""

from __future__ import annotations

__version__ = "0.1.0-dev1"
