"""Copy request handling for the vault service."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import service_cache
from .cache import PersistentCache
from .config import normalize_entry

if TYPE_CHECKING:
    from .service import VaultService

log = logging.getLogger("agent-vault.service")  # marketplace-isolation: allow logger-namespace


def resolve_destination(
    svc: VaultService, kpdb: str, source: str, dest: str, group: str | None,
) -> str:
    """Return the full destination entry path for a copy.

    A destination ending in ``/`` or naming an existing group keeps the
    source title inside that group. Anything else is a full entry path
    (rename); a bare name lands in the configured group like any other
    bare entry name.
    """
    dest = dest.strip()
    title = source.rstrip("/").rsplit("/", 1)[-1]
    if dest.endswith("/") or svc.cli.group_exists(kpdb, dest):
        return dest.rstrip("/") + "/" + title
    return normalize_entry(dest, group)


def handle(
    svc: VaultService, kpdb: str, group: str | None, vault_name: str, reason: str,
    request: dict[str, Any], get_cache: Callable[[], PersistentCache],
) -> dict[str, Any]:
    """Copy one entry to a destination group or full entry path.

    Explicit paths may address any group in the database. An existing
    destination entry is refused; the copy is staged before taking its title.
    ``get_cache`` returns the service's selected persistent cache.
    """
    entry = normalize_entry(request.get("entry", ""), group)
    dest = request.get("dest") or ""
    if not entry:
        return {"ok": False, "error": "No entry path provided"}
    if not dest.strip():
        return {"ok": False, "error": "No destination group or entry path provided"}
    if not svc.ensure_unlocked(kpdb, vault_name, reason):
        error_msg = svc._last_error(kpdb) or svc._last_error("") or "Vault locked"
        return {"ok": False, "error": error_msg, "needs_unlock": True}
    dest_path = resolve_destination(svc, kpdb, entry, dest, group)
    if dest_path.lstrip("/") == entry.lstrip("/"):
        return {"ok": False, "error": f"Source and destination are the same entry: {entry}"}
    if not svc.cli.has_entry(kpdb, entry):
        return {"ok": False, "error": f"Entry not found: {entry}"}
    if svc.cli.has_entry(kpdb, dest_path):
        return {"ok": False, "error": f"Destination entry '{dest_path}' already exists"}
    # Revoke cached destination values before the backend changes so a
    # concurrent read cannot repopulate them with the old value.
    if not service_cache.invalidate_entry(get_cache(), kpdb, dest_path, group):
        return {"ok": False, "error": service_cache.UNAVAILABLE}
    ok, msg = svc.cli.copy_entry(
        kpdb, entry, dest_path,
        skip_notes=request.get("skip_notes") is True,
        skip_url=request.get("skip_url") is True,
    )
    service_cache.clear_memory(svc, kpdb, dest_path)
    if not ok:
        return {"ok": False, "error": msg, "dest": dest_path}
    log.info("Copied entry %s -> %s", entry, dest_path)
    return {"ok": True, "message": msg, "source": entry, "dest": dest_path}
