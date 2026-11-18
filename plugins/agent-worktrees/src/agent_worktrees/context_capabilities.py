"""Selected-runtime capability values for the read-only context CLI."""

from __future__ import annotations

import json


def emit(key: str) -> bool:
    if key == "finalize-retention":
        print(json.dumps({
            "schema": "agent-worktrees.finalize-retention",
            "version": 1,
            "retain_checkout": True,
            "invocation": ["finalize", "--retain-checkout"],
        }))
        return True
    elif key == "finalize-recovery":
        from .retired_owner_finalization import capabilities
    elif key == "checkout-relocation":
        from .checkout_relocation import capabilities
    else:
        return False
    print(json.dumps(capabilities()))
    return True
