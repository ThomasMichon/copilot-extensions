"""Keep a detached session's launch flags for a later launch that omits them.

A rejoin keeps a session's forwards (the Connection Owner holds them). Its
Copilot flags (``--model``, ``--reasoning-effort``, ``--no-ask-user``, ...) and
``--driver`` were not kept anywhere: a resume that passed only ``--resume`` --
a supervisor's wake after a CodeSpace stop, or a hand recovery -- came back on
this host's default model and without them. And the Owner releases a stopped
CodeSpace's session tenants, so its hold can't carry them across a stop.

So a launch that actually starts a session records what it was started with,
and which session that is, one file per CodeSpace and session tenant under
``~/.agent-codespaces/launches/``. A later launch that resumes *that* session
by id and asks for nothing else -- a session selector naming it, no other
``--copilot-arg``, the default ``--driver`` -- reuses the record. Anything else
starts from what it was given: a new session, a resume of a different session
(it never borrows another session's flags), ``--continue`` (no id to match),
or explicit flags. A rejoin of a session that's already running changes
nothing (its flags weren't applied), so it doesn't touch the record either.
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
#: Selectors that name no session (so nothing can be matched to a record).
_NO_ID = ("--continue",)


def split_selectors(args: list[str]) -> tuple[list[str], list[str], str | None]:
    """``(own, selectors, session_id)``: the flags that aren't a session
    selector, the selector tokens (a split ``-r <id>`` stays together), and the
    session id a selector names, if any."""
    own: list[str] = []
    selectors: list[str] = []
    session_id: str | None = None
    i = 0
    while i < len(args):
        arg = str(args[i])
        flag, eq, value = arg.partition("=")
        if flag not in SESSION_SELECTORS:
            own.append(arg)
            i += 1
            continue
        selectors.append(arg)
        if not eq and flag not in _NO_ID and i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
            value = str(args[i + 1])
            selectors.append(value)
            i += 1
        if value and flag not in _NO_ID:
            session_id = value
        i += 1
    return own, selectors, session_id


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
    own, selectors, session_id = split_selectors(requested)
    # Only a resume of the recorded session that names nothing else.
    if not session_id or own or driver != DEFAULT_DRIVER:
        return own + selectors, driver, []
    record = _load(_path(codespace, tenant))
    if record.get("tenant") != tenant or record.get("session_id") != session_id:
        return own + selectors, driver, []
    recalled: list[str] = []
    saved = record.get("copilot_args")
    if isinstance(saved, list) and saved:
        own = split_selectors([str(a) for a in saved])[0]
        recalled.append("copilot_args")
    saved_driver = record.get("driver")
    if isinstance(saved_driver, str) and saved_driver != DEFAULT_DRIVER:
        driver = saved_driver
        recalled.append("driver")
    return own + selectors, driver, recalled


def remember(
    codespace: str, tenant: str, copilot_args: list[str], driver: str, session_id: str,
) -> None:
    """Record the flags (never a session selector) a session was started with."""
    path = _path(codespace, tenant)
    if path is None or not session_id:
        return
    data = {"tenant": tenant, "session_id": session_id,
            "copilot_args": split_selectors(copilot_args)[0], "driver": driver}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # best-effort: a launch never fails over its own memory
