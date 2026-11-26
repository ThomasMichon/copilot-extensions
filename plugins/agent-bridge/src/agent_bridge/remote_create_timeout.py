"""Far-side session-start deadline derived from a caller's remote-create timeout."""

from __future__ import annotations

from typing import Any

_CREATE_TIMEOUT_MARGIN = 5.0
_CREATE_TIMEOUT_MAX = 600.0


def create_start_timeout(value: Any) -> float | None:
    """Bound the far-side session start by the caller's remote-create deadline.

    The far side finishes slightly before the caller's deadline so its result,
    or its real error, reaches the caller instead of a generic 504. A missing
    or invalid value keeps the hosting client's own default. Older carriers
    ignore the optional ``timeout`` field and keep their own client default.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value <= 0:
        return None
    bounded = min(float(value), _CREATE_TIMEOUT_MAX)
    return max(1.0, bounded - _CREATE_TIMEOUT_MARGIN)


def create_start_kwargs(value: Any) -> dict[str, Any]:
    """Return the ``start_session`` keyword for a remote-create deadline."""
    request_timeout = create_start_timeout(value)
    return {} if request_timeout is None else {"request_timeout": request_timeout}
