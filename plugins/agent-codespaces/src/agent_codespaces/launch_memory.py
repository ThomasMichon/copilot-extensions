"""Keep a detached session's launch flags for a later launch that omits them.

A rejoin keeps a session's forwards (the Connection Owner holds them). Its
Copilot flags (``--model``, ``--reasoning-effort``, ``--no-ask-user``, ...) and
``--driver`` were not kept anywhere: a resume that passed only ``--resume`` --
Harness Board's wake after a CodeSpace stop, or a hand recovery -- came back on
this host's default model and without them. And the Owner releases a stopped
CodeSpace's session tenants, so its hold can't carry them across a stop.

So each successful launch records what it was asked for, one file per
CodeSpace and session tenant under ``~/.agent-codespaces/launches/`` (so two
tenants' launches never overwrite each other's record); a later launch that
resumes the session and asks for nothing else -- a session selector
(``--resume``/``--session-id``/``--continue``), no other ``--copilot-arg``, the
default ``--driver`` -- reuses it. A new session starts from the current
defaults, and anything passed explicitly replaces the record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from venue_copilot import SESSION_SELECTORS

from .config import RUNTIME_DIR

LAUNCHES_DIR = RUNTIME_DIR / "launches"
#: ``copilot --driver``'s default: a launch still at it named no driver.
DEFAULT_DRIVER = "cli-mode"

_CODESPACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")


def _is_selector(arg: str) -> bool:
    return str(arg).split("=", 1)[0] in SESSION_SELECTORS


def _path(codespace: str, tenant: str) -> Path | None:
    """One file per CodeSpace and tenant: a readable prefix plus a digest of the tenant."""
    if not _CODESPACE.match(codespace or "") or not tenant:
        return None
    digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:16]
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", tenant)[:48]
    return LAUNCHES_DIR / codespace / f"{label}-{digest}.json"


def _load(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def apply(
    codespace: str, tenant: str, requested: list[str], driver: str,
) -> tuple[list[str], str, list[str]]:
    """``(copilot_args, driver, recalled)`` to launch with; ``recalled`` names
    what came from the record (``copilot_args``, ``driver``)."""
    own = [a for a in requested if not _is_selector(a)]
    selectors = [a for a in requested if _is_selector(a)]
    # Only a resume that names nothing but the session: a new session (no
    # selector) starts from the current defaults, and explicit flags win.
    if not selectors or own:
        return own + selectors, driver, []
    record = _load(_path(codespace, tenant))
    if record.get("tenant") != tenant:
        record = {}
    recalled: list[str] = []
    saved = record.get("copilot_args")
    if isinstance(saved, list) and saved:
        own = [str(a) for a in saved if not _is_selector(str(a))]
        recalled.append("copilot_args")
    saved_driver = record.get("driver")
    if driver == DEFAULT_DRIVER and isinstance(saved_driver, str) and saved_driver != DEFAULT_DRIVER:
        driver = saved_driver
        recalled.append("driver")
    return own + selectors, driver, recalled


def remember(codespace: str, tenant: str, copilot_args: list[str], driver: str) -> None:
    """Record a launch's flags (never its session selector) for the next launch."""
    path = _path(codespace, tenant)
    if path is None:
        return
    data = {"tenant": tenant, "copilot_args": [a for a in copilot_args if not _is_selector(a)],
            "driver": driver}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # best-effort: a launch never fails over its own memory
