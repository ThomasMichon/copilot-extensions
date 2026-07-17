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

# Transport kinds (mirror MCP server-launch ``type`` values, plus ``cli`` --
# the local CLI->MCP responder that has no upstream MCP at all).
TRANSPORTS = ("http", "stdio", "cli")

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

# Decorator types in the ``decorators:`` stack. Kept in sync with the registry in
# ``agent_mcp.decorators`` (a test asserts they match) to avoid a circular import.
DECORATOR_TYPES = ("filter", "rename", "defer", "code-mode", "storage", "transform", "gate")

BRIDGES_DIR = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp")) / "bridges"

# Machine-local config overlays. A bridge config keyed ``id`` (or, absent that,
# its filename stem with a trailing ``.mcp`` stripped) may be overridden per-host
# by a file ``~/.agent-mcp/overrides/<id>.{yaml,yml,json}`` that is deep-merged
# over the committed config at load time. This is the *by-convention*,
# **env-free** way to vary any field (a local endpoint URL, a token/vault-entry
# name, headers) on one machine without editing the shared config or exporting
# an environment variable. Mappings merge recursively; scalars and lists in the
# overlay replace the base.
OVERRIDES_DIR = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp")) / "overrides"


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
    # stdio via an npm package: name the package and let agent-mcp pick the
    # fastest available runner (bunx -> npx) at spawn time. ``npm_args`` holds
    # any extra args to pass after the package. ``command`` takes precedence if
    # both are set. See :mod:`agent_mcp.runner`.
    npm: str | None = None
    npm_args: list[str] = field(default_factory=list)
    # cli (CLI->MCP responder): a set of tool sidecar files to expose as MCP
    # tools, and an optional list of execution scopes this host is allowed to
    # run. A sidecar whose ``mcp.scope`` is set and not in ``scopes`` is neither
    # advertised nor runnable (the generic form of the facility execution
    # policy). ``scopes`` empty => no scope gating.
    tools_from: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)

    @property
    def launch_desc(self) -> str:
        """A short human description of the upstream launch (for logs/status)."""
        if self.type == "cli":
            return f"cli:{len(self.tools_from)} sidecar(s)"
        if self.url:
            return self.url
        if self.command:
            return " ".join(self.command)
        if self.npm:
            return " ".join(["npm:" + self.npm, *self.npm_args])
        return "(unconfigured)"


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
class DecoratorSpec:
    """One entry in the ``decorators:`` stack: a ``type`` plus free-form options."""

    type: str
    options: dict[str, Any] = field(default_factory=dict)


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
    # Decorator stack (client->upstream order). See ``agent_mcp.decorators``.
    decorators: list[DecoratorSpec] = field(default_factory=list)
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


def _parse_decorators(raw: Any) -> list[DecoratorSpec]:
    """Parse the ``decorators:`` list into :class:`DecoratorSpec` entries."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("'decorators' must be a list of mappings")
    specs: list[DecoratorSpec] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(f"decorators[{i}] must be a mapping")
        dtype = entry.get("type")
        if not dtype:
            raise ConfigError(f"decorators[{i}] requires a 'type'")
        options = {k: v for k, v in entry.items() if k != "type"}
        specs.append(DecoratorSpec(type=str(dtype), options=options))
    return specs


def parse_config(data: dict[str, Any], *, name: str | None = None,
                 source_path: Path | None = None) -> BridgeConfig:
    """Build a :class:`BridgeConfig` from a parsed mapping (no I/O)."""
    raw_server = data.get("server")
    if not isinstance(raw_server, dict):
        raise ConfigError("config must have a 'server' mapping")

    raw_command = _as_command(raw_server.get("command"))
    raw_args = [str(a) for a in raw_server.get("args", [])]
    raw_npm = raw_server.get("npm")
    npm = str(raw_npm) if raw_npm else None

    # An explicit ``command`` wins and folds ``args`` in (existing behavior). In
    # ``npm`` mode the command stays empty and ``args`` ride with the package,
    # resolved to a concrete runner at spawn time (see agent_mcp.runner).
    if raw_command:
        command = raw_command + raw_args
        npm_args: list[str] = []
        npm = None
    elif npm:
        command = []
        npm_args = raw_args
    else:
        command = raw_args  # empty -> stdio validation flags the missing launcher
        npm_args = []

    server = ServerSpec(
        type=str(raw_server.get("type", "http")),
        url=raw_server.get("url"),
        command=command,
        env={str(k): str(v) for k, v in (raw_server.get("env") or {}).items()},
        npm=npm,
        npm_args=npm_args,
        tools_from=[str(p) for p in (raw_server.get("tools_from") or [])],
        scopes=[str(s) for s in (raw_server.get("scopes") or [])],
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

    decorators = _parse_decorators(data.get("decorators"))

    cfg = BridgeConfig(
        server=server,
        auth=auth,
        headers={str(k): str(v) for k, v in (data.get("headers") or {}).items()},
        tools=tools,
        timeout=float(data.get("timeout", 30.0)),
        retries=int(data.get("retries", 1)),
        name=name,
        source_path=source_path,
        decorators=decorators,
        extra_auths=extra_auths,
    )
    errors = validate_config(cfg)
    if errors:
        bullet = "\n  - ".join(errors)
        raise ConfigError(f"invalid bridge config:\n  - {bullet}")
    return cfg


def _deep_merge(base: Any, overlay: Any) -> Any:
    """Merge ``overlay`` onto ``base``.

    Two mappings merge recursively (keys present only in ``base`` survive; keys
    in ``overlay`` win). Any non-mapping value in ``overlay`` -- a scalar, a
    list, or a type that differs from ``base`` -- **replaces** the base value
    wholesale (lists are replaced, not concatenated, so an override fully
    restates e.g. a ``tools.allow`` list rather than appending to it).
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, val in overlay.items():
            merged[key] = _deep_merge(merged[key], val) if key in merged else val
        return merged
    return overlay


def _overlay_id(data: dict[str, Any], path: Path) -> str:
    """The overlay key for a config: an explicit top-level ``id``, else the file
    stem with a trailing ``.mcp`` stripped (``vei.mcp.yaml`` -> ``vei``)."""
    explicit = data.get("id")
    if explicit:
        return str(explicit)
    stem = path.stem
    if stem.endswith(".mcp"):
        stem = stem[: -len(".mcp")]
    return stem


def _apply_overlay(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Deep-merge a machine-local overlay onto ``data`` if one exists.

    Looks for ``~/.agent-mcp/overrides/<id>.{yaml,yml,json}`` (see
    ``OVERRIDES_DIR``); when found, its contents are merged over the committed
    config so a single host can vary any field without editing the shared file
    or exporting an environment variable. No overlay file -> ``data`` unchanged.
    """
    oid = _overlay_id(data, path)
    if not oid:
        return data
    for ext in (".yaml", ".yml", ".json"):
        opath = OVERRIDES_DIR / f"{oid}{ext}"
        if opath.exists():
            overlay = _read_file(opath)
            return _deep_merge(data, overlay)
    return data


def load_config(name_or_path: str) -> BridgeConfig:
    """Resolve, read, apply any machine-local overlay, parse, and validate."""
    path = resolve_config_path(name_or_path)
    data = _read_file(path)
    data = _apply_overlay(data, path)
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
    if s.type == "stdio" and not s.command and not s.npm:
        errors.append("server.command or server.npm is required for transport 'stdio'")
    if s.type == "cli" and not s.tools_from:
        errors.append("server.tools_from is required for transport 'cli'")

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

    errors.extend(_validate_decorators(cfg.decorators))
    return errors


def _validate_decorators(decorators: list[DecoratorSpec]) -> list[str]:
    """Validate the decorator stack (types + a few per-type requirements)."""
    errors: list[str] = []
    for i, d in enumerate(decorators):
        label = f"decorators[{i}]"
        if d.type not in DECORATOR_TYPES:
            errors.append(f"{label}.type '{d.type}' must be one of {DECORATOR_TYPES}")
            continue
        opts = d.options
        if d.type == "filter" and opts.get("allow") and opts.get("deny"):
            errors.append(f"{label}: set either 'allow' or 'deny', not both")
        if d.type == "defer":
            mode = opts.get("mode", "lazy")
            if mode not in ("lazy", "eager", "meta_only"):
                errors.append(
                    f"{label}.mode '{mode}' must be lazy|eager|meta_only")
        if d.type == "code-mode":
            if float(opts.get("timeout", 30.0)) <= 0:
                errors.append(f"{label}.timeout must be > 0")
        if d.type == "storage":
            backend = opts.get("backend", "file")
            if backend not in ("file", "http"):
                errors.append(f"{label}.backend '{backend}' must be file|http")
            if backend == "http" and not opts.get("url"):
                errors.append(f"{label}: storage backend 'http' requires 'url'")
            if int(opts.get("threshold", 8192)) < 0:
                errors.append(f"{label}.threshold must be >= 0")
            errors.extend(_validate_storage_rules(opts.get("rules"), label))
        if d.type == "transform":
            errors.extend(_validate_transform_rules(opts, label))
        if d.type == "gate":
            errors.extend(_validate_gate(opts, label))
    return errors


def _validate_gate(opts: dict, label: str) -> list[str]:
    """Validate a gate decorator (match_tools + preflight + allow_when + actions)."""
    errors: list[str] = []
    match_tools = opts.get("match_tools")
    if not match_tools or not isinstance(match_tools, list):
        errors.append(f"{label}: gate requires a non-empty 'match_tools' list")
    preflight = opts.get("preflight")
    if not isinstance(preflight, dict) or not preflight.get("tool"):
        errors.append(f"{label}: gate requires 'preflight' with a 'tool'")
    else:
        args_from = preflight.get("args_from")
        if args_from is not None and not isinstance(args_from, dict):
            errors.append(f"{label}.preflight.args_from must be a mapping")
        cache = preflight.get("cache")
        if cache is not None and cache not in ("per-key", "none"):
            errors.append(f"{label}.preflight.cache '{cache}' must be per-key|none")
    if not isinstance(opts.get("allow_when"), dict):
        errors.append(f"{label}: gate requires an 'allow_when' predicate mapping")
    on_deny = opts.get("on_deny", "stub")
    if on_deny not in ("stub", "drop", "error"):
        errors.append(f"{label}.on_deny '{on_deny}' must be stub|drop|error")
    on_error = opts.get("on_error", "deny")
    if on_error not in ("deny", "allow"):
        errors.append(f"{label}.on_error '{on_error}' must be deny|allow")
    return errors


def _validate_transform_rules(opts: dict, label: str) -> list[str]:
    """Validate a transform decorator's rules (a ``rules`` list or inline rule)."""
    raw = opts.get("rules")
    if raw is None:
        if any(k in opts for k in ("tool", "extract", "pick", "drop", "command")):
            raw = [opts]
        else:
            return [f"{label}: transform needs 'rules' or an inline rule "
                    f"(extract/pick/drop/command)"]
    if not isinstance(raw, list):
        return [f"{label}.rules must be a list"]
    errors: list[str] = []
    for j, rule in enumerate(raw):
        rlabel = f"{label}.rules[{j}]"
        if not isinstance(rule, dict):
            errors.append(f"{rlabel} must be a mapping")
            continue
        if not any(rule.get(k) for k in ("extract", "pick", "drop", "command")):
            errors.append(f"{rlabel} needs one of extract/pick/drop/command")
        for list_field in ("pick", "drop", "command"):
            val = rule.get(list_field)
            if val is not None and not isinstance(val, list):
                errors.append(f"{rlabel}.{list_field} must be a list")
    return errors


def _validate_storage_rules(raw: Any, label: str) -> list[str]:
    """Validate a storage decorator's optional ``rules`` list."""
    if raw is None:
        return []
    errors: list[str] = []
    if not isinstance(raw, list):
        return [f"{label}.rules must be a list"]
    for j, rule in enumerate(raw):
        rlabel = f"{label}.rules[{j}]"
        if not isinstance(rule, dict):
            errors.append(f"{rlabel} must be a mapping")
            continue
        for field_name in ("outputs", "inputs"):
            entries = rule.get(field_name)
            if entries is None:
                continue
            if not isinstance(entries, list):
                errors.append(f"{rlabel}.{field_name} must be a list")
                continue
            for k, entry in enumerate(entries):
                if not isinstance(entry, dict) or not entry.get("path"):
                    errors.append(f"{rlabel}.{field_name}[{k}] requires a 'path'")
        if not rule.get("outputs") and not rule.get("inputs"):
            errors.append(f"{rlabel} needs at least one of 'outputs' or 'inputs'")
    return errors
