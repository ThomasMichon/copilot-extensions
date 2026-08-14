"""Optional shared, on-disk token cache for auth injectors.

Generalizes what per-plugin auth wrappers used to do by hand -- mint once, cache
to disk, reuse until the token's own expiry -- into the agent-mcp auth layer, so
any :class:`~agent_mcp.auth.base.TokenInjector` gets cross-process / cross-session
caching by declaring an ``auth.cache`` policy, and no provider re-implements it.
Stdlib-only.

Opt-in via ``auth.cache.scope``:

* ``memory`` (default) -- historical in-process-only caching (no disk).
* ``shared``           -- persist under ``<AGENT_MCP_HOME|~/.agent-mcp>/token-cache/``
                          keyed by a stable identity, reused across processes.
* ``none``             -- no caching at all (re-acquire every call).

Expiry is authoritative: a cached token is served only while unexpired (minus a
refresh ``skew``). ``ttl: auto`` derives the expiry from the token's own JWT
``exp``; a fixed ``ttl: <seconds>`` sets one explicitly. When neither is
available (``auto`` on a non-JWT secret) the entry is **not** persisted -- caching
without a known expiry risks serving a stale token -- so it stays in-memory only.

NOTE (v1): tokens are written as plaintext with best-effort ``0600`` perms, the
same baseline as the wrappers this replaces. Sealing at rest (agent-vault KEK) is
a planned follow-up, gated behind an ``auth.cache.seal`` flag.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from pathlib import Path

log = logging.getLogger("agent-mcp.auth.cache")


def _root() -> Path:
    base = os.environ.get("AGENT_MCP_HOME")
    root = Path(base) if base else Path.home() / ".agent-mcp"
    return root / "token-cache"


def default_key(
    *,
    kind: str,
    command: list[str] | None = None,
    resource: str | None = None,
    scope: str | None = None,
    tenant: str | None = None,
    header: str | None = None,
) -> str:
    """A stable cache key for one auth identity.

    Derived from the fields that determine *which* token is minted, so distinct
    bridges that authorize the same way share one cached token, and differing
    resource/tenant/command never collide.
    """
    parts = [
        kind or "",
        json.dumps(command or [], separators=(",", ":")),
        resource or "",
        scope or "",
        tenant or "",
        header or "",
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _safe_key(key: str) -> str:
    """A filesystem-safe cache-file stem for ``key``.

    Derived keys are already a bare 32-hex hash and pass through unchanged; a
    caller-supplied ``auth.cache.key`` that is not a plain ``[A-Za-z0-9_-]`` token
    is hashed instead, so a value containing ``..`` or a path separator can never
    escape the cache directory or name an arbitrary file.
    """
    if key and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
        return key
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:32]


def _path(key: str) -> Path:
    return _root() / f"{_safe_key(key)}.json"


def jwt_exp(token: str) -> int | None:
    """Best-effort ``exp`` (unix seconds) from a JWT access token, else ``None``."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = data.get("exp")
        return int(exp) if exp is not None else None
    except Exception:
        return None


def read(key: str, *, skew: int = 60) -> str | None:
    """Return a cached token if present and unexpired (minus ``skew``), else None."""
    try:
        rec = json.loads(_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token = rec.get("token")
    exp = rec.get("exp")
    if not token or not isinstance(exp, (int, float)):
        return None
    if time.time() >= float(exp) - max(0, skew):
        return None
    return str(token)


def write(key: str, token: str, *, ttl: str = "auto") -> bool:
    """Persist ``token`` with an expiry. Returns ``False`` (nothing written) when
    the expiry can't be determined (``auto`` on a non-JWT), keeping such secrets
    in-memory only rather than risking a stale-token serve."""
    ttl = (ttl or "auto").strip().lower()
    exp: float | None
    if ttl == "auto":
        e = jwt_exp(token)
        exp = float(e) if e is not None else None
    else:
        try:
            secs = float(ttl)
        except ValueError:
            secs = None
        if secs is None or not math.isfinite(secs) or secs <= 0:
            return False
        exp = time.time() + secs
    if exp is None:
        return False
    if exp <= time.time():
        return False  # already expired -- don't leave a useless cache file
    root = _root()
    try:
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(root), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"token": token, "exp": exp, "acquired_at": time.time()}, f)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, _path(key))
            tmp = None  # consumed by replace
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
        return True
    except OSError as exc:
        log.debug("token cache write failed for %s: %s", key, exc)
        return False


def invalidate(key: str) -> None:
    """Drop the persisted entry for ``key`` (no-op if absent)."""
    try:
        _path(key).unlink()
    except OSError:
        pass
