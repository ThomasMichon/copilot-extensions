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

Deliberately scoped to the ``list`` path -- it does **not** touch the
status-monitor sweep. A later phase can have the resident daemon proactively warm
this cache each tick so even the first post-expiry call is free; until then the
first caller after expiry scans and refreshes it for the rest.

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


def write(key: str, payload: Any, *, now: float | None = None) -> None:
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
