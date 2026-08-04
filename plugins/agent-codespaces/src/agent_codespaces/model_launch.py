"""Resolve per-session Copilot model flags for ACP launches.

This module is the public seam used by both ``agent-codespaces``' stdio
``copilot --acp`` launcher and ``agent-bridge``'s CodeSpace Session-Host
dispatch path (mirroring the ``relay_launch`` seam). It reads the caller's
current Copilot settings at dispatch/launch time and converts them into
ephemeral command-line flags; it never writes CodeSpace settings.

Model availability is organization/account dependent. The resolved flags are
therefore best-effort: an unavailable model, effort level, or context tier may
still be rejected by ``copilot`` at launch. A validation hook is a future
follow-on.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

log = logging.getLogger("agent-codespaces")

_OPT_OUT_ENV = "AGENT_CODESPACES_MODEL_PROPAGATE"
_OPT_OUT_VALUES = {"0", "false", "no"}
_ENV_KEYS = {
    "model": "AGENT_CODESPACES_ACP_MODEL",
    "effort": "AGENT_CODESPACES_ACP_EFFORT",
    "context": "AGENT_CODESPACES_ACP_CONTEXT",
}
_SETTINGS_KEYS = {
    "model": "model",
    "effortLevel": "effort",
    "contextTier": "context",
}
_ALIASES = {
    "model": "model",
    "effort": "effort",
    "effortLevel": "effort",
    "context": "context",
    "contextTier": "context",
}


def _clean(value: Any) -> str | None:
    """Return a non-empty string value, or ``None`` for unset values."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _strip_line_comments(raw: str) -> str:
    """Strip ``//`` JSON line comments while preserving ``//`` inside strings."""
    lines: list[str] = []
    for line in raw.splitlines():
        in_string = False
        escaped = False
        cut_at: int | None = None
        for index, char in enumerate(line):
            if escaped:
                escaped = False
                continue
            if in_string and char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if not in_string and char == "/" and index + 1 < len(line):
                if line[index + 1] == "/":
                    cut_at = index
                    break
        if cut_at is not None:
            line = line[:cut_at].rstrip()
        lines.append(line)
    return "\n".join(lines)


def _settings_path() -> Path:
    """Return the host Copilot settings path."""
    return Path.home() / ".copilot" / "settings.json"


def _host_settings_config() -> dict[str, str]:
    """Read model settings from host ``~/.copilot/settings.json``.

    The settings file may be missing or may include ``//`` comments. Any read or
    parse error is treated as no host configuration, preserving launch behavior.
    """
    try:
        data = json.loads(_strip_line_comments(_settings_path().read_text(encoding="utf-8")))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cfg: dict[str, str] = {}
    for settings_key, public_key in _SETTINGS_KEYS.items():
        value = _clean(data.get(settings_key))
        if value:
            cfg[public_key] = value
    return cfg


def _normalized_config(values: dict[str, Any] | None) -> dict[str, str]:
    """Normalize supported config aliases to ``model``/``effort``/``context``."""
    cfg: dict[str, str] = {}
    for key, value in (values or {}).items():
        public_key = _ALIASES.get(key)
        if not public_key:
            continue
        clean = _clean(value)
        if clean:
            cfg[public_key] = clean
    return cfg


def resolve_model_config(override: dict[str, Any] | None = None) -> dict[str, str]:
    """Resolve the caller's per-session model configuration for ACP launch.

    Returns a dictionary containing any of ``model``, ``effort``, and
    ``context``. Precedence is explicit override/environment, then the caller's
    host ``~/.copilot/settings.json`` (``model`` → ``model``, ``effortLevel`` →
    ``effort``, ``contextTier`` → ``context``), then empty. Environment
    overrides are read from ``AGENT_CODESPACES_ACP_MODEL``,
    ``AGENT_CODESPACES_ACP_EFFORT``, and ``AGENT_CODESPACES_ACP_CONTEXT``.

    Set ``AGENT_CODESPACES_MODEL_PROPAGATE`` to ``0``, ``false``, or ``no`` to
    opt out completely. This resolver is degrade-safe and never raises.
    """
    try:
        opt_out = os.environ.get(_OPT_OUT_ENV, "").strip().lower()
        if opt_out in _OPT_OUT_VALUES:
            return {}

        cfg = _host_settings_config()
        env_cfg = {
            key: value
            for key, env_name in _ENV_KEYS.items()
            if (value := _clean(os.environ.get(env_name)))
        }
        cfg.update(env_cfg)
        cfg.update(_normalized_config(override))
        return cfg
    except Exception:
        return {}


def build_model_flags(cfg: dict[str, Any] | None = None) -> str:
    """Build a shell-safe suffix for ``copilot --acp`` model flags.

    When ``cfg`` is ``None``, resolves the caller configuration with
    :func:`resolve_model_config`; otherwise the supplied mapping is normalized
    directly. Present keys are emitted as ``--model``, ``--reasoning-effort``,
    and ``--context`` with values quoted via :func:`shlex.quote`. The returned
    suffix includes a single leading space, or ``""`` when no flags apply.
    """
    resolved = resolve_model_config() if cfg is None else _normalized_config(cfg)
    parts: list[str] = []
    for key, flag in (
        ("model", "--model"),
        ("effort", "--reasoning-effort"),
        ("context", "--context"),
    ):
        value = resolved.get(key)
        if value:
            parts.extend([flag, shlex.quote(value)])
    if not parts:
        log.debug("No model flags to propagate to ACP launch")
        return ""
    suffix = " " + " ".join(parts)
    log.info("Propagating model flags to ACP launch: %s", suffix.strip())
    return suffix
