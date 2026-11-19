"""Actual repository-registration expiry, not a renewed native wait deadline."""

from __future__ import annotations

import math
from typing import Any

from .queue_records import TaskError

RECEIPT_PROTOCOL = "agent-dispatch.repository-feed-receipt/v1"


def receipt_expiry(
    delegation: dict[str, Any] | None, registration_id: object, expires_at: object,
) -> int | None:
    """Keep unknown history readable; validate known expiry against original intent."""
    if expires_at is None:
        return None
    if (
        delegation is None or "feed" not in delegation
        or not isinstance(registration_id, str) or not 1 <= len(registration_id) <= 128
        or any(ord(char) < 32 for char in registration_id)
        or type(expires_at) is not int or not 0 < expires_at < 2**53
        or expires_at > math.floor(delegation["feed"]["deadline_at"])
    ):
        raise TaskError("invalid original repository-feed receipt expiry")
    return expires_at
