"""Pure, shared validation for runtime and payload identity-cache pins."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def parse_pinned_context(raw: str, installation_id: str | None) -> dict:
    def unique_fields(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate pinned field")
            result[name] = value
        return result

    try:
        pinned = json.loads(raw, object_pairs_hook=unique_fields)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Pinned vault context must be valid JSON") from exc
    if (
        not isinstance(pinned, dict)
        or set(pinned) != {"version", "kpdb", "group", "vault_name", "port"}
        or type(pinned.get("version")) is not int or pinned["version"] != 1
        or any(not isinstance(pinned.get(name), str) for name in ("kpdb", "group", "vault_name"))
        or not pinned["kpdb"] or not Path(pinned["kpdb"]).is_absolute()
        or type(pinned.get("port")) is not int
        or any(ord(char) < 32 for name in ("kpdb", "group", "vault_name") for char in pinned[name])
    ):
        raise RuntimeError("Pinned vault context has invalid fields, types or version")
    if not (1 <= pinned["port"] <= 65535 or (pinned["port"] == 0 and installation_id)):
        raise RuntimeError("Pinned vault context has an invalid port")
    if pinned["group"] != pinned["group"].strip("/ "):
        raise RuntimeError("Pinned vault group must be a normalized group or empty")
    return pinned


def identity_cache_digest(database: str, home: str, installation_id: str) -> str:
    identity = json.dumps({
        "version": 1,
        "database": os.path.normcase(str(Path(database).resolve())),
        "home": os.path.normcase(str(Path(home).resolve())),
        "installation_id": installation_id,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def validate_payload_cache(
    raw: str, cache_enabled: str, cache_dir: str,
    source_home: str, source_id: str, runtime_root: str, installation_id: str,
) -> str:
    pinned = parse_pinned_context(raw, installation_id or None)
    root = Path(runtime_root).resolve()
    if (
        cache_enabled != "1" or not cache_dir or not Path(cache_dir).is_absolute()
        or not source_home or not Path(source_home).is_absolute()
        or os.path.normcase(str(Path(source_home).resolve())) != os.path.normcase(str(root))
        or source_id != installation_id
    ):
        raise RuntimeError("Identity cache pin does not belong to the selected payload cell")
    digest = identity_cache_digest(pinned["kpdb"], str(root), installation_id)
    expected = (root / "cache" / "identity" / digest).resolve()
    selected = Path(cache_dir).resolve()
    if os.path.normcase(str(selected)) != os.path.normcase(str(expected)):
        raise RuntimeError("Identity cache pin is outside the selected database/cell scope")
    return str(selected)
