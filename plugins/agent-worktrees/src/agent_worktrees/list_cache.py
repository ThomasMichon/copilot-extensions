"""Short-TTL, coalescing result cache for the ``list --json`` path.

The Worktree Picker (and other consumers) poll ``agent-worktrees list --json
--classify --mux-details`` on a refresh cadence, and **every** call re-runs the
expensive enrichment -- ``scan_sessions_fast`` (events.jsonl reads), git
classification, mux queries, and the machine-wide bare-orphan scan. Concurrent
polls each pay the full cost independently, which spikes CPU (the observed "list
spam churns CPU").

This cache coalesces those: the computed ``{"worktrees": [...]}`` payload is
written to a per-project, args-keyed sidecar with a timestamp; a call within the
TTL window reuses it instead of re-scanning. Staleness is bounded by the TTL
(status/list rows are display hints, not authoritative), and a caller that needs
exactness passes ``--fresh`` to bypass the read.

The resident status monitor proactively refreshes recently requested shapes on
each sweep, once per active project. Demand registration preserves the exact
flags used by each Picker/bridge caller instead of guessing one canonical shape,
and avoids running expensive scans on projects nobody is polling. Resident
writes carry a bounded extended freshness lease so they remain available until
the next refresh; ordinary on-demand writes keep the short TTL.

Best-effort + fail-open: any cache error falls back to a live scan, so the cache
can never wedge or corrupt a listing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from . import config as cfg

#: Env override for the TTL (seconds). ``0`` / ``off`` / ``false`` disables the
#: cache entirely (every call scans live).
_TTL_ENV = "AGENT_WORKTREES_LIST_CACHE_TTL"
_DEFAULT_TTL = 4.0
_DEMAND_MAX_AGE = 120.0
_RESIDENT_FRESH_FLOOR = 45.0

_OFF = frozenset({"0", "off", "false", "no"})

#: The ``cmd_list`` args that change the JSON payload -- the cache key axes.
_KEY_ARGS = (
    "classify", "mux_details", "all", "include_other_platforms",
)


def ttl_seconds(env=None) -> float:
    """Resolve the cache TTL. ``<=0`` / off-token disables (returns 0.0)."""
    env = env if env is not None else os.environ
    raw = str(env.get(_TTL_ENV, "")).strip().lower()
    if raw in _OFF:
        return 0.0
    if not raw:
        return _DEFAULT_TTL
    try:
        v = float(raw)
        return v if v > 0 else 0.0
    except ValueError:
        return _DEFAULT_TTL


def _cache_dir() -> Path:
    return cfg.install_dir() / "list-cache"


def _demand_dir() -> Path:
    return _cache_dir() / "demand"


def cache_key(args, *, project: str, tracking_status: str) -> str:
    """A stable key over the output-affecting args + project + status filter.

    Two calls with the same key produce the same payload, so they may share a
    cache entry; differing flags (``--classify`` etc.) get distinct entries.
    """
    parts = [project or "", tracking_status or "all"]
    for name in _KEY_ARGS:
        parts.append(f"{name}={int(bool(getattr(args, name, False)))}")
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return digest


def read_fresh(key: str, *, ttl: float | None = None, now: float | None = None) -> Any | None:
    """Return the cached payload if present and younger than the TTL, else None.

    Never raises. A ``ttl`` of 0 (cache disabled) always misses.
    """
    ttl = ttl_seconds() if ttl is None else ttl
    if ttl <= 0:
        return None
    now = time.time() if now is None else now
    path = _cache_dir() / f"{key}.json"
    try:
        if not path.is_file():
            return None
        env = json.loads(path.read_text("utf-8"))
        if not isinstance(env, dict):
            return None
        fresh_until = env.get("fresh_until")
        if fresh_until is not None:
            try:
                if now > float(fresh_until):
                    return None
            except (ValueError, TypeError):
                return None
        else:
            stamped = float(env.get("stamped_at", 0))
            if now - stamped > ttl:
                return None
        payload = env.get("payload")
        # Fail-open shape guard: only serve a well-formed listing payload; a
        # corrupted cache (payload not a dict / missing worktrees) is a miss, so
        # the caller re-scans rather than emitting garbage or crashing.
        if not isinstance(payload, dict) or "worktrees" not in payload:
            return None
        return payload
    except (OSError, ValueError, TypeError):
        return None


def write(
    key: str,
    payload: Any,
    *,
    now: float | None = None,
    fresh_for: float | None = None,
) -> None:
    """Persist ``payload`` under ``key`` with a timestamp (atomic). Best-effort.

    A no-op when the cache is disabled (TTL 0), so a disabled cache never writes.
    """
    if ttl_seconds() <= 0:
        return
    now = time.time() if now is None else now
    tmp: str | None = None
    try:
        d = _cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        env = {"stamped_at": now, "payload": payload}
        if fresh_for is not None and fresh_for > 0:
            env["fresh_until"] = now + fresh_for
        fd, tmp = tempfile.mkstemp(prefix=key + ".", suffix=".tmp", dir=str(d))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(env, fh, default=str)
        os.replace(tmp, str(d / f"{key}.json"))
    except Exception:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def note_demand(
    key: str,
    args,
    *,
    project: str,
    tracking_status: str,
    now: float | None = None,
) -> None:
    """Register an exact list shape for demand-aware resident refresh."""
    if ttl_seconds() <= 0:
        return
    now = time.time() if now is None else now
    path = _demand_dir() / f"{key}.json"
    try:
        if path.is_file():
            os.utime(path, (now, now))
            return
        d = _demand_dir()
        d.mkdir(parents=True, exist_ok=True)
        data = {
            "key": key,
            "project": project,
            "tracking_status": tracking_status,
            "args": {
                name: bool(getattr(args, name, False))
                for name in _KEY_ARGS
            },
        }
        fd, tmp = tempfile.mkstemp(prefix=key + ".", suffix=".tmp", dir=str(d))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, path)
            os.utime(path, (now, now))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except (OSError, TypeError, ValueError):
        pass


def recent_demands(
    project: str,
    *,
    now: float | None = None,
    max_age: float = _DEMAND_MAX_AGE,
) -> list[dict]:
    """Return valid, recently touched demand records for ``project``."""
    now = time.time() if now is None else now
    out: list[dict] = []
    try:
        files = list(_demand_dir().glob("*.json"))
    except OSError:
        return out
    for path in files:
        try:
            if now - path.stat().st_mtime > max_age:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            data = json.loads(path.read_text("utf-8"))
            if not isinstance(data, dict) or data.get("project") != project:
                continue
            key = data.get("key")
            shape = data.get("args")
            if not isinstance(key, str) or not isinstance(shape, dict):
                raise ValueError("invalid demand shape")
            if any(name not in shape or not isinstance(shape[name], bool)
                   for name in _KEY_ARGS):
                raise ValueError("incomplete demand shape")
            out.append(data)
        except (OSError, ValueError, TypeError):
            try:
                path.unlink()
            except OSError:
                pass
    return out


def resident_fresh_for(interval: float) -> float:
    """Freshness lease for daemon writes, spanning slow resident sweeps."""
    return max(_RESIDENT_FRESH_FLOOR, interval * 3, ttl_seconds() * 2)
