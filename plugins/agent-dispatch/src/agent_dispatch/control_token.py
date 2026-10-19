"""Server-independent derivation shared by HTTP clients and the coordinator."""
from __future__ import annotations

import hashlib
import hmac


def scoped_control_token(control_token: str, scope: str) -> str:
    """Derive a bearer scoped to one producer-owned operation."""
    return hmac.new(
        control_token.encode("utf-8"),
        scope.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
