"""Private, data-only equivalence checks before sharing backend execution."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path


class SharingUnavailable(OSError):
    """No application work was accepted; current direct execution is permitted."""


_KEY = secrets.token_hex(32)
_CONTRACT = "agent-mcp.execution-context/2"
_INSTALL_BOOKKEEPING = (
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_NATIVE_INSTALLER",
    "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "COPILOT_PLUGIN_INSTALL_STAGED",
    "COPILOT_PLUGIN_STAGED_FROM", "COPILOT_PROJECT_DIR",
    "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN",
    "UPDATE_DRIVER_WITNESS",
)


def descriptor() -> dict:
    return {"contract": _CONTRACT, "key": _KEY}


def _references(cfg, key):
    # Explicit configuration use makes even a reserved key semantically relevant.
    encoded = json.dumps(asdict(cfg), sort_keys=True, default=str)
    if os.name == "nt":
        return key.casefold() in encoded.casefold()
    return key in encoded


def inherited_environment(cfg):
    environment = dict(os.environ)
    for key in ("AGENT_MCP_CONTROL_TOKEN", *_INSTALL_BOOKKEEPING):
        if not _references(cfg, key):
            environment.pop(key, None)
    return environment


def semantic_environment(cfg):
    from .home import default_home_dir

    from agent_procutil.daemon_instances import INSTANCE_ENV, REGISTRY_ENV
    from agent_procutil._job_scopes import ENVIRONMENT_KEY

    environment = inherited_environment(cfg)
    if not environment.get("AGENT_MCP_HOME") and not _references(cfg, "AGENT_MCP_HOME"):
        environment["AGENT_MCP_HOME"] = str(default_home_dir().resolve())
    # These owner-declared fields carry process custody, not caller credentials.
    # They remain in the physical child environment where lifetime owners need them.
    for key in (
        INSTANCE_ENV, REGISTRY_ENV, ENVIRONMENT_KEY, "AGENT_MCP_SERVE_SOCKET",
        "AGENT_MCP_INSTALL_DIR",
        # Exact scratch variables declared by the shipped CMD wrapper.
        "_ROOT", "_PY", "_VER", "_SNAP", "_INST", "_PSX",
    ):
        if not _references(cfg, key):
            environment.pop(key, None)
    return environment


def _identity(cfg, key: str) -> str:
    # Dynamic acquisition and routed/CLI execution need stronger per-target
    # custody than this first-slice equivalence check provides.
    if cfg.routing is not None or cfg.server.type != "stdio" or any(
        auth.normalized_kind not in {"none", "env"} for auth in cfg.auths
    ):
        raise SharingUnavailable("execution context requires current direct execution")
    # No raw context crosses IPC or enters a checkpoint, argv, or log.
    data = json.dumps(
        {"config": asdict(cfg), "environment": semantic_environment(cfg), "cwd": os.getcwd()},
        sort_keys=True, separators=(",", ":"), default=str,
    ).encode()
    if len(data) > 1024 * 1024:
        raise SharingUnavailable("execution context exceeds its admission budget")
    return hmac.new(bytes.fromhex(key), data, hashlib.sha256).hexdigest()


def request_fields(handle, bridge) -> dict:
    from . import incoming
    from .config import load_config

    if incoming.launch_generation() is None:
        return {}  # unmanaged development retains its existing peer contract
    return fields_for_peer(
        load_config(bridge, repository_root=Path.cwd()), incoming._ping(handle),
    )


def fields_for_peer(cfg, reply) -> dict:
    """Bind this caller's complete context to an already authenticated peer."""
    peer = reply.get("execution_context") if isinstance(reply, dict) else None
    if not isinstance(peer, dict) or peer.get("contract") != _CONTRACT:
        raise SharingUnavailable("serving peer cannot qualify caller execution context")
    key = peer.get("key")
    if not isinstance(key, str) or len(key) != 64:
        raise SharingUnavailable("serving peer has an invalid execution context")
    try:
        if len(bytes.fromhex(key)) != 32:
            raise ValueError("invalid context key length")
        digest = _identity(cfg, key)
    except (ValueError, TypeError) as exc:
        raise SharingUnavailable("caller execution context cannot be qualified") from exc
    return {"execution_context": {"contract": _CONTRACT, "digest": digest}}


def load_config(bridge, request, loader, *, repository_root):
    from . import incoming

    cfg = loader(bridge, repository_root=repository_root)
    supplied = request.get("execution_context")
    if supplied is None and incoming.launch_generation() is None:
        return cfg
    if not isinstance(supplied, dict) or supplied.get("contract") != _CONTRACT:
        raise SharingUnavailable("caller execution context is required")
    expected = _identity(cfg, _KEY)
    actual = supplied.get("digest")
    if not isinstance(actual, str) or not hmac.compare_digest(actual, expected):
        raise SharingUnavailable("caller execution context differs; no work accepted")
    return cfg


def config_failure(exc) -> dict:
    if isinstance(exc, SharingUnavailable):
        return {"ok": False, "shared_execution": False, "error": str(exc)}
    return {"ok": False, "error": f"config: {exc}"}
