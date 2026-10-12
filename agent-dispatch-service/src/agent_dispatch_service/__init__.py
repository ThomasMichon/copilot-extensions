"""agent-dispatch-service: a marker package for the standalone, Copilot-
independent distribution of the agent-dispatch coordinator (see bootstrap.sh
/ bootstrap.ps1 beside this package, and this repository's own
`tools/standalone_consumers.py`).

All coordinator functionality (CLI, `serve` command, installer, MCP tools)
lives in the `agent_dispatch` package itself; nothing is reimplemented here.
"""

from __future__ import annotations

__version__ = "0.1.1-dev1"
