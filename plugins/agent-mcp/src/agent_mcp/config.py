"""Per-MCP bridge configuration: load + validate a JSON/YAML config file.

A bridge config has two parts:

* ``server`` -- the *original upstream MCP launch info*, intentionally the same
  shape as a Copilot CLI ``.mcp.json`` / VS Code ``mcpServers`` entry, so an
  existing server definition can be pasted in unchanged. ``server.type``
  (``http`` | ``stdio``) selects the transport.
* overrides -- everything the bridge layers on top: ``auth``, extra ``headers``,
  ``tools`` filtering, ``timeout``, ``retries``.

Named bridges resolve to ``~/.agent-mcp/bridges/<name>.{yaml,yml,json}``; an
explicit path (``--config <file>``) is loaded directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Transport kinds (mirror MCP server-launch ``type`` values).
TRANSPORTS = ("http", "stdio")

# Auth injector kinds. ``az`` is an alias for ``entra``; ``static`` for ``env``.
AUTH_KINDS = ("entra", "az", "gh", "git-credential", "env", "static", "none")

# Where token credentials are injected.
INJECT_MODES = ("header", "env")

BRIDGES_DIR = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp")) / "bridges"


class ConfigError(ValueError):
    """Raised when a bridge config is missing, unparsable, or invalid."""


@dataclass
class ServerSpec:
    """Upstream MCP launch info (the ``server`` block)."""

    type: str = "http"
    # http
    url: str | None = None
    # stdio
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class AuthSpec:
    """How to acquire and inject credentials (the ``auth`` block)."""

    kind: str = "none"
    # entra/az
    resource: str | None = None
    scope: str | None = None
    tenant: str | None = None
    # env/static
    source_env: str | None = None
    value: str | None = None
    # injection
    inject: str | None = None  # defaults per transport in resolve_inject()
    header: str = "Authorization"
    format: str = "Bearer {token}"
    target_env: str | None = None

    @property
    def normalized_kind(self) -> str:
        """Collapse aliases (``az`` -> ``entra``, ``static`` -> ``env``)."""
        if self.kind == "az":
            return "entra"
        if self.kind == "static":
            return "env"
        return self.kind

    def resolve_inject(self, transport_type: str) -> str:
        """Injection mode, defaulting to ``header`` for http and ``env`` for stdio."""
        if self.inject:
            return self.inject
        return "header" if transport_type == "http" else "env"


@dataclass
class ToolFilter:
    """Optional allow/deny filtering applied to the upstream ``tools/list``."""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.allow or self.deny)


@dataclass
class BridgeConfig:
    """A fully-resolved bridge definition."""

    server: ServerSpec
    auth: AuthSpec
    headers: dict[str, str] = field(default_factory=dict)
    tools: ToolFilter = field(default_factory=ToolFilter)
    timeout: float = 30.0
    retries: int = 1
    name: str | None = None
    source_path: Path | None = None


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top-level config must be a mapping")
    return data


def resolve_config_path(name_or_path: str) -> Path:
    """Resolve a ``--config`` value or a bare bridge name to a file path.

    A value containing a path separator or an explicit extension is treated as a
    path; otherwise it is looked up under ``~/.agent-mcp/bridges/<name>.{yaml,yml,json}``.
    """
    candidate = Path(name_or_path).expanduser()
    if candidate.suffix or os.sep in name_or_path or "/" in name_or_path:
        return candidate
    for ext in (".yaml", ".yml", ".json"):
        p = BRIDGES_DIR / f"{name_or_path}{ext}"
        if p.exists():
            return p
    raise ConfigError(
        f"no bridge named '{name_or_path}' under {BRIDGES_DIR} "
        f"(looked for {name_or_path}.yaml/.yml/.json)"
    )


def _as_command(value: Any) -> list[str]:
    """Accept either a string command or an argv list for ``server.command``."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value]
    raise ConfigError("server.command must be a string or a list")


def parse_config(data: dict[str, Any], *, name: str | None = None,
                 source_path: Path | None = None) -> BridgeConfig:
    """Build a :class:`BridgeConfig` from a parsed mapping (no I/O)."""
    raw_server = data.get("server")
    if not isinstance(raw_server, dict):
        raise ConfigError("config must have a 'server' mapping")

    server = ServerSpec(
        type=str(raw_server.get("type", "http")),
        url=raw_server.get("url"),
        command=_as_command(raw_server.get("command")) + [
            str(a) for a in raw_server.get("args", [])
        ],
        env={str(k): str(v) for k, v in (raw_server.get("env") or {}).items()},
    )

    raw_auth = data.get("auth") or {"kind": "none"}
    if not isinstance(raw_auth, dict):
        raise ConfigError("'auth' must be a mapping")
    auth = AuthSpec(
        kind=str(raw_auth.get("kind", "none")),
        resource=raw_auth.get("resource"),
        scope=raw_auth.get("scope"),
        tenant=raw_auth.get("tenant"),
        source_env=raw_auth.get("source_env"),
        value=raw_auth.get("value"),
        inject=raw_auth.get("inject"),
        header=str(raw_auth.get("header", "Authorization")),
        format=str(raw_auth.get("format", "Bearer {token}")),
        target_env=raw_auth.get("target_env"),
    )

    raw_tools = data.get("tools") or {}
    tools = ToolFilter(
        allow=[str(t) for t in raw_tools.get("allow", [])],
        deny=[str(t) for t in raw_tools.get("deny", [])],
    )

    cfg = BridgeConfig(
        server=server,
        auth=auth,
        headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        tools=tools,
        timeout=float(data.get("timeout", 30.0)),
        retries=int(data.get("retries", 1)),
        name=name,
        source_path=source_path,
    )
    errors = validate_config(cfg)
    if errors:
        bullet = "\n  - ".join(errors)
        raise ConfigError(f"invalid bridge config:\n  - {bullet}")
    return cfg


def load_config(name_or_path: str) -> BridgeConfig:
    """Resolve, read, parse, and validate a bridge config."""
    path = resolve_config_path(name_or_path)
    data = _read_file(path)
    name = path.stem
    return parse_config(data, name=name, source_path=path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config(cfg: BridgeConfig) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid)."""
    errors: list[str] = []
    s = cfg.server

    if s.type not in TRANSPORTS:
        errors.append(f"server.type '{s.type}' must be one of {TRANSPORTS}")
    if s.type == "http" and not s.url:
        errors.append("server.url is required for transport 'http'")
    if s.type == "stdio" and not s.command:
        errors.append("server.command is required for transport 'stdio'")

    a = cfg.auth
    if a.kind not in AUTH_KINDS:
        errors.append(f"auth.kind '{a.kind}' must be one of {AUTH_KINDS}")
    if a.inject and a.inject not in INJECT_MODES:
        errors.append(f"auth.inject '{a.inject}' must be one of {INJECT_MODES}")

    kind = a.normalized_kind
    if kind == "entra" and not (a.resource or a.scope):
        errors.append("auth: entra/az requires 'resource' or 'scope'")
    if kind == "env" and not (a.source_env or a.value):
        errors.append("auth: env/static requires 'source_env' or 'value'")

    if cfg.tools.allow and cfg.tools.deny:
        errors.append("tools: set either 'allow' or 'deny', not both")
    if cfg.retries < 0:
        errors.append("retries must be >= 0")
    if cfg.timeout <= 0:
        errors.append("timeout must be > 0")
    return errors
