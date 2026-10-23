"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

from agent_vault.cache import PersistentCache
from credential_record import generation_for_expiry, store_credential, valid_credential


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except (ValueError, RecursionError):
        payload = None
    if (
        not isinstance(payload, dict)
        or not valid_credential(payload.get("token"), payload.get("expires_epoch"))
    ):
        print(json.dumps({"ok": False, "error": "invalid credential payload"}))
        return 1
    token, expires_epoch = payload["token"], payload["expires_epoch"]
    generation = generation_for_expiry(expires_epoch)
    cache = PersistentCache()
    if not cache.enabled:
        print(json.dumps({"ok": False, "error": "cache disabled (AGENT_VAULT_CACHE not set?)"}))
        return 1
    ok = store_credential(cache, "mesh/gateway-token", token, expires_epoch)
    print(json.dumps({"ok": bool(ok), "generation": generation}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
