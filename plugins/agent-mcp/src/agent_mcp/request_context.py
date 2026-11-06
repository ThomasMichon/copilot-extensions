"""Versioned configuration selection for the authenticated resident protocol.

The root is a caller selection, not an owner report. The native configuration
consumer still resolves and validates its configuration owner independently.
"""

from __future__ import annotations

from pathlib import Path

CONTEXT_VERSION = 1
BRIDGE_OPS = frozenset({"call", "list", "attach"})


def capture_context(bridge: str) -> dict:
    return {"version": CONTEXT_VERSION, "root": str(Path.cwd()), "bridge": bridge}


def supports_context(reply: object) -> bool:
    return (
        isinstance(reply, dict) and reply.get("ok") is True
        and reply.get("pong") is True
        and type(reply.get("configuration_context")) is int
        and reply["configuration_context"] == CONTEXT_VERSION
    )


def request_root(request: dict) -> Path:
    context = request.get("configuration_context")
    if not isinstance(context, dict) or set(context) != {"version", "root", "bridge"}:
        raise ValueError("resident configuration context is required")
    if type(context["version"]) is not int or context["version"] != CONTEXT_VERSION:
        raise ValueError("unsupported resident configuration context")
    if context["bridge"] != request.get("bridge"):
        raise ValueError("resident configuration context belongs to another bridge")
    raw = context["root"]
    if not isinstance(raw, str) or not raw or not Path(raw).is_absolute():
        raise ValueError("resident configuration root must be absolute")
    root = Path(raw).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("resident configuration root must be a directory")
    return root
