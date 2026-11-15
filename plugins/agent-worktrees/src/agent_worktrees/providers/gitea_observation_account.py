"""Operation-local actor evidence through the existing typed Gitea GET owner."""

from __future__ import annotations

import json
from typing import Any

from .base import ProviderError
from .observation_credentials import ObservationCredentialError, ObservationCredentials


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate actor field")
        result[key] = value
    return result


def verify_account(
    owner: Any, credentials: ObservationCredentials, *, deadline: float | None = None,
) -> None:
    """Verify once; credentials pin the actual material across subsequent acquisitions."""
    expected = credentials.expected_account
    if expected is None:
        return
    try:
        status, body = owner._curl(
            "GET", owner._api(credentials.api_base, "/user"), credentials,
            read_deadline=deadline,
        )
        if status != 200:
            raise ValueError("actor read failed")
        actor = json.loads(body, object_pairs_hook=_unique)
        login = actor.get("login") if isinstance(actor, dict) else None
        if (
            not isinstance(login, str) or not login or not login.isascii()
            or any(not char.isprintable() or char.isspace() for char in login)
            or login.casefold() != expected.casefold()
        ):
            raise ValueError("actor mismatch")
    except (ProviderError, OSError, ValueError, UnicodeError, RecursionError):
        raise ObservationCredentialError("native observation account verification failed") from None
