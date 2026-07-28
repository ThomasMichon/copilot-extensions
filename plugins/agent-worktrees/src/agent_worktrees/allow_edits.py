"""Break-glass edit grants -- deliberate, time-boxed, per-repo bypasses.

Some repos in the registry are *agent-guarded*: a delegation policy (e.g. the
``cross-repo-guard`` hook in a harness) blocks direct edits to their checkouts
so work is routed to the repo's own in-repo agent.  Occasionally a direct edit
is genuinely unavoidable -- maintaining the target agent's OWN instructions or
skills, or a direct action to unblock -- and the guard must not *wedge* the
agent.

``repos allow-edits <repo>`` records a grant here that such a guard can read to
temporarily allow direct edits.  The store lives at
``~/.agent-worktrees/allow-edits.json`` so it is:

- **persisted** (survives across processes and hook invocations), and
- **cross-language readable** -- timestamps are epoch **milliseconds**
  (``expires_at_ms``) so a JS/TS hook can compare against ``Date.now()`` and a
  shell hook can compare against ``date +%s%3N`` without unit ambiguity.

Every grant is a deliberate, logged, time-boxed last resort -- not the default
path.  Prefer delegation (``agent-worktrees related resolve <repo>``).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_MINUTES = 10
MAX_MINUTES = 60
MIN_REASON_LEN = 8


def _now_ms() -> int:
    """Current time in epoch milliseconds (seam for tests)."""
    return int(time.time() * 1000)


def _grants_path() -> Path:
    """Path to the break-glass grant store."""
    return Path.home() / ".agent-worktrees" / "allow-edits.json"


def clamp_minutes(minutes: float | int | None) -> int:
    """Clamp a requested duration to [1, MAX_MINUTES]; default when unset/invalid."""
    try:
        n = round(float(minutes))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    if n <= 0:
        return DEFAULT_MINUTES
    return min(max(n, 1), MAX_MINUTES)


@dataclass
class Grant:
    """A single active break-glass grant."""

    repo: str
    expires_at_ms: int
    reason: str
    minutes: int
    granted_at_ms: int
    session: str | None = None

    @property
    def remaining_seconds(self) -> int:
        return max(0, (self.expires_at_ms - _now_ms()) // 1000)


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def _read_raw() -> dict:
    path = _grants_path()
    if not path.exists():
        return {"version": SCHEMA_VERSION, "grants": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": SCHEMA_VERSION, "grants": {}}
    if not isinstance(data, dict):
        return {"version": SCHEMA_VERSION, "grants": {}}
    grants = data.get("grants")
    if not isinstance(grants, dict):
        data["grants"] = {}
    data.setdefault("version", SCHEMA_VERSION)
    return data


def _write_raw(data: dict) -> None:
    path = _grants_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same filesystem


def _prune(data: dict, now_ms: int) -> bool:
    """Drop expired grants in place. Returns True if anything was removed."""
    grants = data.get("grants", {})
    expired = [name for name, g in grants.items()
               if not isinstance(g, dict) or int(g.get("expires_at_ms", 0)) <= now_ms]
    for name in expired:
        grants.pop(name, None)
    return bool(expired)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def grant(repo: str, reason: str, minutes: float | int | None = None,
          session: str | None = None) -> Grant:
    """Open (or extend) a break-glass grant for ``repo``.

    Prunes expired grants as a side effect so the store stays small.
    """
    mins = clamp_minutes(minutes)
    now = _now_ms()
    data = _read_raw()
    _prune(data, now)
    rec = {
        "expires_at_ms": now + mins * 60_000,
        "expires_at_iso": datetime.fromtimestamp((now + mins * 60_000) / 1000, timezone.utc)
            .isoformat().replace("+00:00", "Z"),
        "reason": reason,
        "minutes": mins,
        "granted_at_ms": now,
        "session": session or os.environ.get("COPILOT_AGENT_SESSION_ID"),
    }
    data["grants"][repo] = rec
    _write_raw(data)
    return Grant(repo=repo, expires_at_ms=rec["expires_at_ms"], reason=reason,
                 minutes=mins, granted_at_ms=now, session=rec["session"])


def is_active(repo: str) -> bool:
    """True iff an unexpired grant exists for ``repo``."""
    data = _read_raw()
    g = data.get("grants", {}).get(repo)
    if not isinstance(g, dict):
        return False
    return int(g.get("expires_at_ms", 0)) > _now_ms()


def list_active() -> list[Grant]:
    """Return active grants (prunes + persists if any expired)."""
    now = _now_ms()
    data = _read_raw()
    if _prune(data, now):
        _write_raw(data)
    out: list[Grant] = []
    for name, g in data.get("grants", {}).items():
        if not isinstance(g, dict):
            continue
        out.append(Grant(
            repo=name,
            expires_at_ms=int(g.get("expires_at_ms", 0)),
            reason=str(g.get("reason", "")),
            minutes=int(g.get("minutes", 0)),
            granted_at_ms=int(g.get("granted_at_ms", 0)),
            session=g.get("session"),
        ))
    out.sort(key=lambda x: x.expires_at_ms)
    return out


def revoke(repo: str) -> bool:
    """Remove any grant for ``repo``. Returns True if one existed."""
    data = _read_raw()
    if repo in data.get("grants", {}):
        data["grants"].pop(repo, None)
        _write_raw(data)
        return True
    return False
