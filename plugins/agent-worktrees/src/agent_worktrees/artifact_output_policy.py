"""Inert user-output policy over roots selected by the storage owner."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import re
from string import Formatter
from typing import Any

from configuration_layers import ConfigurationError

TARGETS = frozenset({"onedrive", "downloads"})
DEFAULT_TEMPLATES = {
    "onedrive": "Apps/Copilot/Artifacts/{date} {topic}",
    "downloads": "Copilot/Artifacts/{date} {topic}",
}
_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|CLOCK\$|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])(?:\.|$)",
    re.IGNORECASE,
)


def member_name(value: object) -> str:
    """Require one portable, bounded path component without stream syntax."""
    if (
        not isinstance(value, str) or not value or len(value) > 120
        or value in {".", ".."} or value.endswith((" ", "."))
        or any(
            ord(char) < 32 or ord(char) == 127 or 0xD800 <= ord(char) <= 0xDFFF
            or char in '<>:"/\\|?*' for char in value
        )
        or len(value.encode("utf-8")) > 255
        or _RESERVED.match(value)
    ):
        raise ConfigurationError("user-output member must be a portable plain name")
    return value


def _template(value: object) -> str:
    if not isinstance(value, str) or len(value) > 1024:
        raise ConfigurationError("user-output template must be bounded relative text")
    try:
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (name not in {"date", "topic"} or spec or conversion):
                raise ValueError("unsupported template field")
        rendered = value.format(date="2000-01-01", topic="Example")
        parts = rendered.split("/")
        if not 1 <= len(parts) <= 16 or any("{" in part or "}" in part for part in parts):
            raise ValueError("invalid template structure")
        for part in parts:
            member_name(part)
    except (ValueError, KeyError, IndexError) as exc:
        raise ConfigurationError("invalid portable user-output template") from exc
    return value


def validate_policy(value: object, *, machine_paths: bool, native_paths: bool) -> dict[str, Any]:
    """Validate partial contributing layers before configuration composition."""
    if not isinstance(value, dict) or set(value) - {"target", "templates", "roots"}:
        raise ConfigurationError("unknown user-output policy field")
    if "target" in value and (
        not isinstance(value["target"], str) or value["target"] not in TARGETS | {"auto"}
    ):
        raise ConfigurationError("unsupported user-output target")
    if "templates" in value:
        templates = value["templates"]
        if not isinstance(templates, dict) or set(templates) - TARGETS:
            raise ConfigurationError("unknown user-output template target")
        for template in templates.values():
            _template(template)
    if "roots" in value:
        if not machine_paths:
            raise ConfigurationError("physical user-output roots require a machine-scoped source")
        roots = value["roots"]
        if not isinstance(roots, dict) or set(roots) - TARGETS:
            raise ConfigurationError("unknown user-output root target")
        for root in roots.values():
            if (
                not isinstance(root, str) or not root or "\x00" in root
                or native_paths and not Path(root).is_absolute()
            ):
                raise ConfigurationError("user-output root must name an absolute native directory")
    return copy.deepcopy(value)


def resolve_user_output(
    storage: dict[str, Any], *, topic: str, owner_roots: dict[str, str],
    destination: str | None = None, target: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Select a destination without discovery, filesystem reads or allocation.

    The storage provider must supply only roots for the intended account.
    A missing required target is an error, never a fallback to another class.
    """
    policy = validate_policy(storage.get("user_output", {}), machine_paths=True, native_paths=True)
    if destination is not None:
        path = Path(destination)
        if not path.is_absolute() or ".." in path.parts or "\x00" in destination:
            raise ConfigurationError("explicit user-output destination must be an absolute native path")
        member_name(path.name)
        return {
            "schema": "agent-worktrees.user-output-destination/v1", "target": "explicit",
            "root": str(path.parent), "members": [path.name], "destination": str(path),
            "origin": "operation", "root_admitted": False,
        }
    roots = validate_policy(
        {"roots": owner_roots}, machine_paths=True, native_paths=True,
    )["roots"]
    roots.update(policy.get("roots", {}))
    selected = target if target is not None else policy.get("target", "auto")
    if not isinstance(selected, str) or selected not in TARGETS | {"auto"}:
        raise ConfigurationError("unsupported user-output target")
    if selected == "auto":
        selected = next((name for name in ("onedrive", "downloads") if name in roots), "")
    if selected not in roots:
        raise ConfigurationError("required user-output target is unavailable from its storage owner")
    when = timestamp if timestamp is not None else datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ConfigurationError("user-output timestamp must be timezone qualified")
    template = policy.get("templates", {}).get(selected, DEFAULT_TEMPLATES[selected])
    members = template.format(date=when.date().isoformat(), topic=member_name(topic)).split("/")
    for member in members:
        member_name(member)
    root = Path(roots[selected])
    path = root.joinpath(*members)
    return {
        "schema": "agent-worktrees.user-output-destination/v1", "target": selected,
        "root": str(root), "members": members, "destination": str(path),
        "origin": "machine" if selected in policy.get("roots", {}) else "storage-owner",
        "root_admitted": False,
    }
