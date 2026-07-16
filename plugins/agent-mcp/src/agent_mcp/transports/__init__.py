"""Upstream transports: HTTP (Streamable HTTP + SSE), stdio (child process),
and cli (local CLI->MCP responder -- no upstream)."""

from __future__ import annotations

from ..auth.base import AuthInjector
from ..config import BridgeConfig
from .base import Transport
from .cli import CliTransport
from .http import HttpTransport
from .stdio import StdioTransport

__all__ = ["CliTransport", "HttpTransport", "StdioTransport", "Transport", "build_transport"]


def build_transport(cfg: BridgeConfig, injector: AuthInjector) -> Transport:
    """Construct the transport for a bridge config (selected by ``server.type``)."""
    if cfg.server.type == "http":
        return HttpTransport(cfg, injector)
    if cfg.server.type == "stdio":
        return StdioTransport(cfg, injector)
    if cfg.server.type == "cli":
        return CliTransport(cfg, injector)
    raise ValueError(f"unknown transport: {cfg.server.type}")
