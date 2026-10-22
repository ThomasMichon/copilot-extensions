"""Experimental reference implementation; see the parent README."""


from __future__ import annotations

import json
import sys
import time


from agent_vault.cache import PersistentCache


def main() -> int:
    payload = json.loads(sys.stdin.read())
    token = payload["token"]
    generation = int(payload.get("generation", time.time()))
    cache = PersistentCache()
    if not cache.enabled:
        print(json.dumps({"ok": False, "error": "cache disabled (AGENT_VAULT_CACHE not set?)"}))
        return 1
    ok = cache.put("mesh/gateway-token", "value", token, generation=generation)
    ok_exp = cache.put(
        "mesh/gateway-token", "expires_epoch", str(payload["expires_epoch"]), generation=generation
    )
    print(json.dumps({"ok": bool(ok and ok_exp), "generation": generation}))
    return 0 if ok and ok_exp else 1


if __name__ == "__main__":
    raise SystemExit(main())
