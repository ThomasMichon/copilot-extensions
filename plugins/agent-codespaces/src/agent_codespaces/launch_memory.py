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
import uuid
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
    session id -- only when exactly one selector is given and it names one."""
    own: list[str] = []
    selectors: list[str] = []
    ids: list[str] = []
    count = 0
    i = 0
    while i < len(args):
        arg = str(args[i])
        flag, eq, value = arg.partition("=")
        if flag not in SESSION_SELECTORS:
            own.append(arg)
            i += 1
            continue
        selectors.append(arg)
        count += 1
        if not eq and flag not in _NO_ID and i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
            value = str(args[i + 1])
            selectors.append(value)
            i += 1
        if value and flag not in _NO_ID:
            ids.append(value)
        i += 1
    # More than one selector (``--continue --resume=x``, two ``--resume``s) is
    # ambiguous: it names no one session to match a record to.
    session_id = ids[0] if count == 1 and len(ids) == 1 else None
    return own, selectors, session_id


def _path(codespace: str, tenant: str) -> Path | None:
    """One file per CodeSpace and tenant: a readable prefix plus a digest of the tenant."""
    if not _CODESPACE.match(codespace or "") or not tenant:
        return None
    digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:16]
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", tenant)[:48]
    return LAUNCHES_DIR / codespace / f"{label}-{digest}.json"


def _load(path: Path | None) -> dict:
    """The record, only if its whole payload is well-formed (else ``{}``)."""
    if path is None:
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    args, driver = data.get("copilot_args"), data.get("driver")
    if (
        not isinstance(data.get("tenant"), str) or not isinstance(data.get("session_id"), str)
        or not isinstance(args, list) or not all(isinstance(a, str) and a for a in args)
        or not isinstance(driver, str) or not driver
    ):
        return {}
    return data


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
    if record["copilot_args"]:
        own = split_selectors(record["copilot_args"])[0]
        recalled.append("copilot_args")
    if record["driver"] != DEFAULT_DRIVER:
        driver = record["driver"]
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
    # Owner-only (a record another local user could edit would inject flags,
    # permission flags included, into a later resume), atomic, like lease.py.
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(json.dumps(data, indent=2).encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        pass  # best-effort: a launch never fails over its own memory
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
