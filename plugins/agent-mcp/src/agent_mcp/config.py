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
AUTH_KINDS = (
    "entra", "az", "gh", "git-credential", "command", "env", "static", "none",
)

# Where token credentials are injected.
INJECT_MODES = ("header", "env")

# How a ``command`` source's stdout is interpreted.
#   ``keyvalue`` -- git-credential ``key=value`` text; extract ``field``.
#   ``raw``      -- the whole trimmed stdout is the secret verbatim.
PARSE_MODES = ("keyvalue", "raw")

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
    # command (run an external git-credential-fill-shaped command)
    command: list[str] = field(default_factory=list)
    request: dict[str, str] = field(default_factory=dict)
    parse: str = "keyvalue"
    field_name: str | None = None  # which output key to extract (keyvalue mode)
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
    # Additional auth injectors beyond ``auth`` (the first). Populated when the
    # config's ``auth`` is a *list* -- e.g. a bridge that must inject two
    # vault-sourced secrets into two env vars. Empty for the single-auth form.
    extra_auths: list[AuthSpec] = field(default_factory=list)

    @property
    def auths(self) -> list[AuthSpec]:
        """All auth injectors for this bridge, in order (``auth`` first)."""
        return [self.auth, *self.extra_auths]


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


def _parse_auth_spec(raw_auth: dict[str, Any]) -> AuthSpec:
    """Build one :class:`AuthSpec` from a parsed ``auth`` mapping."""
    if not isinstance(raw_auth, dict):
        raise ConfigError("each 'auth' entry must be a mapping")
    return AuthSpec(
        kind=str(raw_auth.get("kind", "none")),
        resource=raw_auth.get("resource"),
        scope=raw_auth.get("scope"),
        tenant=raw_auth.get("tenant"),
        source_env=raw_auth.get("source_env"),
        value=raw_auth.get("value"),
        command=_as_command(raw_auth.get("command")) + [
            str(a) for a in raw_auth.get("args", [])
        ],
        request={str(k): str(v) for k, v in (raw_auth.get("request") or {}).items()},
        parse=str(raw_auth.get("parse", "keyvalue")),
        field_name=raw_auth.get("field"),
        inject=raw_auth.get("inject"),
        header=str(raw_auth.get("header", "Authorization")),
        format=str(raw_auth.get("format", "Bearer {token}")),
        target_env=raw_auth.get("target_env"),
    )


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

    # ``auth`` may be a single mapping (one injector) or a list of mappings
    # (several secrets injected into the same bridge child, e.g. a password and
    # an API key into two env vars). An absent ``auth`` means no injection.
    raw_auth = data.get("auth")
    if raw_auth is None:
        auth_specs = [AuthSpec(kind="none")]
    elif isinstance(raw_auth, list):
        if not raw_auth:
            auth_specs = [AuthSpec(kind="none")]
        else:
            auth_specs = [_parse_auth_spec(a) for a in raw_auth]
    elif isinstance(raw_auth, dict):
        auth_specs = [_parse_auth_spec(raw_auth)]
    else:
        raise ConfigError("'auth' must be a mapping or a list of mappings")
    auth = auth_specs[0]
    extra_auths = auth_specs[1:]

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
        extra_auths=extra_auths,
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

    # The bridge injects via the transport's native mechanism: header for http,
    # env for stdio. ``inject`` is parsed but the transport ultimately decides, so
    # reject an explicit value that contradicts the transport rather than silently
    # ignoring it.
    native_inject = "header" if cfg.server.type == "http" else "env"

    for idx, a in enumerate(cfg.auths):
        label = "auth" if len(cfg.auths) == 1 else f"auth[{idx}]"
        if a.kind not in AUTH_KINDS:
            errors.append(f"{label}.kind '{a.kind}' must be one of {AUTH_KINDS}")
        if a.inject and a.inject not in INJECT_MODES:
            errors.append(f"{label}.inject '{a.inject}' must be one of {INJECT_MODES}")
        elif a.inject and a.inject != native_inject:
            errors.append(
                f"{label}.inject '{a.inject}' is not supported for "
                f"'{cfg.server.type}' transport (it injects via '{native_inject}')"
            )

        kind = a.normalized_kind
        if kind == "entra" and not (a.resource or a.scope):
            errors.append(f"{label}: entra/az requires 'resource' or 'scope'")
        if kind == "env" and not (a.source_env or a.value):
            errors.append(f"{label}: env/static requires 'source_env' or 'value'")
        if kind == "command":
            if not a.command:
                errors.append(f"{label}: command requires 'command'")
            if a.parse not in PARSE_MODES:
                errors.append(f"{label}.parse '{a.parse}' must be one of {PARSE_MODES}")

    # Multiple auths compose only cleanly over stdio, where each targets a
    # distinct env var. Over http they would all write the same header (default
    # Authorization) and silently clobber, so restrict the list form to stdio and
    # require a distinct target_env per injector.
    if len(cfg.auths) > 1:
        if cfg.server.type != "stdio":
            errors.append(
                "auth: a list of injectors is supported for 'stdio' transport "
                "only (each must inject a distinct env var); use a single auth "
                f"for '{cfg.server.type}'"
            )
        targets: list[str] = []
        for idx, a in enumerate(cfg.auths):
            if a.normalized_kind == "none":
                continue
            if not a.target_env:
                errors.append(
                    f"auth[{idx}]: 'target_env' is required when 'auth' is a list "
                    "(multiple injectors must each target a distinct env var)"
                )
            else:
                targets.append(a.target_env)
        dupes = sorted({t for t in targets if targets.count(t) > 1})
        if dupes:
            errors.append(f"auth: duplicate target_env across injectors: {dupes}")

    if cfg.tools.allow and cfg.tools.deny:
        errors.append("tools: set either 'allow' or 'deny', not both")
    if cfg.retries < 0:
        errors.append("retries must be >= 0")
    if cfg.timeout <= 0:
        errors.append("timeout must be > 0")
    return errors
