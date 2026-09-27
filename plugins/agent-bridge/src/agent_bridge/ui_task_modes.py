"""Repository-declared ways to start a task from the ``/ui`` New task dialog.

A repository may describe the kinds of task a person starts in it -- for
example a harness that runs "one task with one worker" or "a campaign across
several workers" -- in ``.copilot-extensions/agent-bridge/task-modes.yaml`` at
the root of its checkout::

    modes:
      - id: single
        label: One task
        description: One worker in its own venue.
        prompt: "{prompt}"
      - id: campaign
        label: Campaign
        description: Several workers split by area, sharing a board.
        prompt: >
          Run this as a campaign ... {prompt}

``prompt`` is a template for the session's first message; ``{prompt}`` is
replaced by what the person typed (appended when the template omits it). The
template is folded onto one line because the seed is typed into the session.
The bridge only ever uses a template the repository declares; the page names a
mode by id and never sends a template of its own. A repository with no file
offers a single plain mode.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("agent-bridge")

MODES_FILE = Path(".copilot-extensions") / "agent-bridge" / "task-modes.yaml"
MAX_MODES = 8
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_LIMITS = {"label": 40, "description": 300, "prompt": 4000}

_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}


def checkout_path(repo_row: dict[str, Any]) -> Path | None:
    """The repository's own checkout on this machine, from its registry row."""
    paths = repo_row.get("paths") or {}
    if isinstance(paths, str):
        paths = {"default": paths}
    if not isinstance(paths, dict):
        return None
    prefer = ["windows"] if os.name == "nt" else ["linux", "wsl", "mac", "darwin", "posix"]
    ordered = [paths[k] for k in prefer if k in paths] + [v for k, v in paths.items() if k not in prefer]
    for raw in ordered:
        if isinstance(raw, str) and raw and Path(raw).is_dir():
            return Path(raw)
    return None


def _clean(raw: Any) -> list[dict[str, str]]:
    items = raw.get("modes") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise ValueError("expected a top-level 'modes' list")
    modes: list[dict[str, str]] = []
    for item in items[:MAX_MODES]:
        if not isinstance(item, dict):
            raise ValueError("each mode must be a mapping")
        mode = {k: " ".join(str(item.get(k) or "").split()) for k in ("id", "label", "description", "prompt")}
        if not _ID.match(mode["id"]):
            raise ValueError(f"mode id {mode['id']!r} must be lowercase letters, digits and dashes")
        if not mode["label"] or not mode["prompt"]:
            raise ValueError(f"mode {mode['id']!r} needs a label and a prompt")
        for key, limit in _LIMITS.items():
            if len(mode[key]) > limit:
                raise ValueError(f"mode {mode['id']!r}: {key} is longer than {limit} characters")
        if any(m["id"] == mode["id"] for m in modes):
            raise ValueError(f"mode id {mode['id']!r} is declared twice")
        modes.append(mode)
    return modes


def load_modes(checkout: Path | None) -> list[dict[str, str]]:
    """The declared modes, or ``[]`` when the repository declares none (or the
    file is invalid, which is logged)."""
    if checkout is None:
        return []
    path = checkout / MODES_FILE
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        modes = _clean(yaml.safe_load(path.read_text("utf-8")))
    except (OSError, ValueError, yaml.YAMLError) as exc:
        log.warning("ignoring %s: %s", path, exc)
        modes = []
    _cache[str(path)] = (mtime, modes)
    return modes


def public(modes: list[dict[str, str]]) -> list[dict[str, str]]:
    """What the page needs to offer the modes (never the templates)."""
    return [{k: m[k] for k in ("id", "label", "description")} for m in modes]


def seed_for(modes: list[dict[str, str]], mode_id: str | None, prompt: str) -> str:
    """The first message for *prompt* in the chosen mode.

    Raises ``KeyError`` for a mode the repository does not declare. With no
    declared modes only the plain prompt (no mode, or ``""``) is accepted.
    """
    if not modes:
        if mode_id:
            raise KeyError(mode_id)
        return prompt
    mode = next((m for m in modes if m["id"] == (mode_id or modes[0]["id"])), None)
    if mode is None:
        raise KeyError(mode_id)
    template = mode["prompt"]
    if "{prompt}" in template:
        return template.replace("{prompt}", prompt)
    return f"{template} {prompt}"
