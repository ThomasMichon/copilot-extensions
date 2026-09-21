"""Detect and (optionally) purge stale-schema entries from Copilot CLI's
persisted MCP tool-snapshot cache (``mcp-tools/``).

Copilot CLI's own runtime caches each MCP server's ``tools/list`` result on
disk so a session doesn't have to re-discover tools on every startup. The
runtime's loader only purges a cache entry once it ages out (14 days) -- it
never purges an entry whose ``schemaVersion`` no longer matches what the
running CLI writes (e.g. after a CLI upgrade bumps the schema). Those entries
are re-read, re-parsed, and re-rejected on *every* cache hydration attempt,
forever -- wasted disk I/O inside the runtime's own 2-second hydration
timeout budget. Confirmed present on multiple facility machines (aperture-labs
#7323). This module is the maintenance mitigation, exposed as
``agent-mcp clean-tool-cache``.

The authoritative "current" schema version is the *maximum* seen among cached
entries, never hardcoded and never the most common: the runtime only ever
increments the schema version on a CLI upgrade, so the highest value present
is always current, regardless of how much stale garbage has accumulated. A
machine that has gone a long time between cleanups can easily have more
stale entries than current ones -- treating "most common" as authoritative
would silently pick the stale version and delete the good entries instead.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CacheEntry:
    path: Path
    size: int
    schema_version: int | None
    parse_error: str | None
    stale: bool = False


@dataclass
class ScanResult:
    cache_dir: Path
    entries: list[CacheEntry]
    version_counts: Counter
    current_version: int | None

    @property
    def stale_entries(self) -> list[CacheEntry]:
        return [e for e in self.entries if e.stale]


def resolve_cache_dir(override: str | None = None) -> Path | None:
    """Mirror `copilot-agent-runtime`'s cache-home resolution
    (``session/mcp/tool_snapshot_cache.rs``'s ``cache_home()`` +
    ``mcp/tool_cache.rs``'s ``path()``): an explicit ``COPILOT_CACHE_HOME``
    override replaces the whole cache-home directory (the runtime then joins
    ``mcp-tools`` onto it, same as every other case); otherwise Windows uses
    ``%LOCALAPPDATA%\\copilot``, and everything else uses
    ``$XDG_CACHE_HOME/copilot`` (default ``~/.cache/copilot`` when
    ``XDG_CACHE_HOME`` is unset).
    """
    if override:
        return Path(override).expanduser()

    env_override = os.environ.get("COPILOT_CACHE_HOME")
    if env_override:
        return Path(env_override).expanduser() / "mcp-tools"

    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return None
        return Path(local_app_data) / "copilot" / "mcp-tools"

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "copilot" / "mcp-tools"

    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache_home).expanduser() if xdg_cache_home else Path.home() / ".cache"
    return base / "copilot" / "mcp-tools"


def scan(cache_dir: Path) -> ScanResult:
    entries: list[CacheEntry] = []
    for entry_path in sorted(cache_dir.glob("*.json")):
        size = entry_path.stat().st_size
        schema_version: int | None = None
        parse_error: str | None = None
        try:
            data = json.loads(entry_path.read_text(encoding="utf-8"))
            schema_version = data.get("schemaVersion")
            if schema_version is None:
                parse_error = "missing schemaVersion field"
        except (OSError, json.JSONDecodeError) as error:
            parse_error = str(error)
        entries.append(
            CacheEntry(
                path=entry_path,
                size=size,
                schema_version=schema_version,
                parse_error=parse_error,
            )
        )

    version_counts = Counter(
        e.schema_version for e in entries if e.schema_version is not None
    )
    current_version = max(version_counts) if version_counts else None

    for e in entries:
        e.stale = e.schema_version != current_version or e.parse_error is not None

    return ScanResult(
        cache_dir=cache_dir,
        entries=entries,
        version_counts=version_counts,
        current_version=current_version,
    )


def clean(cache_dir: Path, *, apply: bool = False) -> dict[str, Any]:
    """Scan ``cache_dir`` and, if ``apply``, delete every stale entry.

    Returns a JSON-serializable summary dict; the caller (CLI or a future
    programmatic consumer) decides how to present it.
    """
    scanned = scan(cache_dir)
    stale = scanned.stale_entries
    stale_bytes = sum(e.size for e in stale)
    deleted = 0
    delete_errors: list[str] = []

    if apply:
        for e in stale:
            try:
                e.path.unlink()
                deleted += 1
            except OSError as error:
                delete_errors.append(f"{e.path}: {error}")

    return {
        "cache_dir": str(cache_dir),
        "found": True,
        "total_entries": len(scanned.entries),
        "current_schema_version": scanned.current_version,
        "schema_version_counts": dict(scanned.version_counts),
        "stale_entries": len(stale),
        "stale_bytes": stale_bytes,
        "deleted": deleted,
        "delete_errors": delete_errors,
        "applied": apply,
    }
