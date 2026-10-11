"""Routing configuration composition over the existing single-server parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .config import BridgeConfig


@dataclass
class RoutingSpec:
    """A trusted discovery contract and explicit request-selected targets."""

    parameter: str
    catalog: str
    targets: dict[str, BridgeConfig]


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


def _parse_routing(data: dict[str, Any], *, name: str | None,
                   source_path: Path | None) -> BridgeConfig:
    from .config import ConfigError, parse_config

    raw = data["routing"]
    if not isinstance(raw, dict) or set(raw) - {"parameter", "catalog", "targets"}:
        raise ConfigError("routing must contain only parameter, catalog, targets")
    parameter, catalog, targets = (raw.get(k) for k in ("parameter", "catalog", "targets"))
    if not isinstance(parameter, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parameter):
        raise ConfigError("routing.parameter must be a nonempty argument identifier")
    if not isinstance(targets, dict) or not targets or any(
        not isinstance(k, str) or not k for k in targets
    ):
        raise ConfigError("routing.targets must be a nonempty mapping with string names")
    if not isinstance(catalog, str) or catalog not in targets:
        raise ConfigError("routing.catalog must explicitly name a configured target")
    if "server" in data or "auth" in data:
        raise ConfigError("routed configs put server and auth inside each target")
    shared = {k: v for k, v in data.items()
              if k not in {"routing", "decorators", "tools"}}
    parsed = {}
    for key, target in targets.items():
        if not isinstance(target, dict) or "routing" in target:
            raise ConfigError(f"routing target {key}: expected a non-routed mapping")
        if set(target) - {"server", "auth", "headers", "tools", "decorators",
                          "timeout", "retries", "idle_timeout"}:
            raise ConfigError(f"routing target {key}: unsupported target settings")
        policy = target.get("tools", {})
        if not isinstance(policy, dict) or set(policy) - {"allow", "deny"} or any(
            not isinstance(v, list) or any(not isinstance(p, str) for p in v)
            for v in policy.values()
        ):
            raise ConfigError(f"routing target {key}: tools allow/deny must be string lists")
        cfg = parse_config(_deep_merge(shared, target), name=name, source_path=source_path)
        if any(d.type != "input_gate" for d in cfg.decorators):
            raise ConfigError(f"routing target {key}: only input_gate decorators are supported")
        parsed[key] = cfg
    outer = parse_config(
        {**shared, "server": targets[catalog]["server"],
         "tools": data.get("tools"), "decorators": data.get("decorators")},
        name=name, source_path=source_path,
    )
    for decorator in outer.decorators:
        if decorator.type == "gate":
            mapping = decorator.options["preflight"].get("args_from") or {}
            if mapping.get(parameter) != f"$args.{parameter}":
                raise ConfigError("routed gate preflight must map the same selector "
                                  f"with args_from: {{{parameter}: '$args.{parameter}'}}")
    outer.routing = RoutingSpec(parameter, catalog, parsed)
    return outer
