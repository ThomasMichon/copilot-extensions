"""agent-mcp -- a swiss-army MCP bridge.

Wraps an upstream MCP server (HTTP/SSE or a stdio child process) as a local
stdio MCP server, injecting host credentials (Entra/az, gh, git-credential, or a
static/env token) and applying an optional **decorator stack** (filter, rename,
defer, code-mode, storage). One config file describes one bridge: an upstream
``server`` launch spec (same shape as a ``.mcp.json`` entry) plus bridge
``auth``, ``decorators``, and other overrides.
"""

from __future__ import annotations

# Fallback only for running from a source tree with no installed distribution.
_FALLBACK_VERSION = "0.2.0-dev94"


def _resolve_version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        # Single source of truth: the installed package metadata (pyproject
        # version), so `status` / `--version` never drift from the real version.
        return _pkg_version("agent-mcp")
    except PackageNotFoundError:  # running from source without an install
        return _FALLBACK_VERSION


def __getattr__(name: str) -> str:
    # ``__version__`` is resolved lazily: ``importlib.metadata.version`` costs
    # ~8 MiB of RSS, which the thin per-session ``forward`` child (spawned once
    # per MCP session, the multiplexer's whole point) must not pay just to import
    # the package. It is computed only when actually read -- ``status`` /
    # ``--version`` / the upstream handshake's client-info.
    if name == "__version__":
        return _resolve_version()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
