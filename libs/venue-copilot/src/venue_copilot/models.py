"""Resolve the caller's Copilot model settings for a venue session.

Shared by every venue plugin's launch paths (agent-codespaces' ACP launcher
and ``copilot --detach``, agent-containers' and agent-ssh's ``copilot
--detach``): a session a caller starts on a venue runs on the caller's own
model, reasoning effort, and context tier instead of the venue's CLI defaults.
It reads the caller's current ``~/.copilot/settings.json`` at launch time and
never writes venue settings.

Precedence: an explicit override, then ``AGENT_CODESPACES_ACP_MODEL`` /
``_EFFORT`` / ``_CONTEXT`` (historical names, honored by every venue), then the
host settings (``model``, ``effortLevel``, ``contextTier``). Set
``AGENT_CODESPACES_MODEL_PROPAGATE`` to ``0``/``false``/``no`` to opt out.

Model availability is organization/account dependent, so the flags are
best-effort: ``copilot`` may still reject an unavailable model at launch.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("venue-copilot")

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
#: ``(config key, copilot flag)`` in emission order.
MODEL_FLAGS = (
    ("model", "--model"),
    ("effort", "--reasoning-effort"),
    ("context", "--context"),
)


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


def normalized_config(values: dict[str, Any] | None) -> dict[str, str]:
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
    """Resolve the caller's model configuration (see the module docstring for
    precedence). Returns any of ``model``, ``effort``, ``context``. Never
    raises."""
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
        cfg.update(normalized_config(override))
        return cfg
    except Exception:
        return {}


def model_copilot_args(existing: list[str] | None = None) -> list[str]:
    """Copilot args that start a detached (interactive) venue session on the
    caller's own model, reasoning effort, and context tier, as single
    ``--flag=value`` tokens for the launch's ``--copilot-arg`` list. A flag the
    caller already passed in ``existing`` wins and is not duplicated.
    Degrade-safe: never raises."""
    try:
        resolved = resolve_model_config()
    except Exception:
        return []
    given = {str(arg).split("=", 1)[0] for arg in existing or []}
    out: list[str] = []
    for key, flag in MODEL_FLAGS:
        value = resolved.get(key)
        if value and flag not in given:
            out.append(f"{flag}={value}")
    if out:
        log.info("Propagating model flags to detached session: %s", " ".join(out))
    return out
