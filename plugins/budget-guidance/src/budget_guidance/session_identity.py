"""Credential-free identity checks around an incumbent session quota read."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlsplit

from .models import ModelError
from .quota import QuotaSession, _base, acquire_session_quota

LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]{0,37}[A-Za-z0-9])?")
HOST = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")


def _host(value: Any) -> str:
    if (
        not isinstance(value, str) or len(value) > 300
        or any(ord(character) <= 32 for character in value)
    ):
        raise ModelError("GitHub identity host must be a bounded HTTPS origin")
    host: str | None = None
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        valid = (
            parsed.scheme.lower() == "https" and host is not None
            and HOST.fullmatch(host) is not None
            and ".." not in host and parsed.port in {None, 443}
            and parsed.username is None and parsed.password is None
            and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment
        )
    except ValueError:
        valid = False
    if not valid or host is None:
        raise ModelError("GitHub identity host must be a bounded HTTPS origin")
    return "https://" + host


def _identity() -> dict[str, Any]:
    return {
        "status": "unverified",
        "scope": "session-github-auth",
        "login": None,
        "host": None,
        "quota_binding": "not-atomic",
    }


async def acquire_verified_session_quota(
    session: QuotaSession, *, expected_session_id: str,
    expected_login: str, expected_host: str, runtime_version: str,
    timeout: float = 10, category: str = "premium_interactions",
    provider_id: str | None = None, quota_id: str | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Bracket quota get with status reads; never inspect credential-bearing auth.

    Equal status reads detect observed account changes, not an atomic runtime
    account/quota transaction or the identity of an arbitrary third-party provider.
    The host must supply the existing trusted SDK session.
    """
    if not isinstance(expected_login, str) or LOGIN.fullmatch(expected_login) is None:
        raise ModelError("expected GitHub login must be a valid account handle")
    host = _host(expected_host)
    if (
        isinstance(timeout, bool) or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 30
    ):
        raise ModelError("identity/quota deadline must be greater than zero and at most 30 seconds")
    session_id = getattr(session, "session_id", "")
    if not isinstance(session_id, str):
        raise ModelError("SDK session_id must be a string")
    result = _base(
        session_id, runtime_version, datetime.now(timezone.utc), "session-owned-rpc",
    )
    identity = _identity()
    result["account_identity"] = identity
    if (
        not isinstance(expected_session_id, str) or not expected_session_id
        or len(expected_session_id) > 256 or not session_id
        or session_id != expected_session_id
    ):
        result["diagnostics"].append("requesting-session-missing-or-mismatch")
        return result
    rpc = getattr(session, "rpc", None)
    getter = getattr(getattr(rpc, "git_hub_auth", None), "get_status", None)
    if not callable(getter):
        identity["status"] = "unsupported"
        result["diagnostics"].append("session-github-auth-status-unsupported")
        return result

    def same_session() -> bool:
        if (
            getattr(session, "session_id", None) != expected_session_id
            or getattr(session, "rpc", None) is not rpc
        ):
            identity["status"] = "changed"
            result["diagnostics"].append("requesting-session-changed-during-acquisition")
            return False
        return True

    async def check_identity() -> bool:
        if not same_session():
            return False
        try:
            raw = await getter(timeout=timeout)
        except NotImplementedError:
            identity["status"] = "unsupported"
            result["diagnostics"].append("session-github-auth-status-unsupported")
            return False
        if not same_session():
            return False
        status = raw.to_dict()
        if not isinstance(status, Mapping):
            raise ModelError("session auth status must serialize to an object")
        if status.get("isAuthenticated") is not True:
            identity["status"] = "unavailable"
            result["diagnostics"].append("requesting-account-not-authenticated")
            return False
        login = status.get("login")
        actual_host = status.get("host")
        if not isinstance(login, str) or actual_host is None:
            identity["status"] = "unavailable"
            result["diagnostics"].append("requesting-account-identity-missing")
            return False
        try:
            actual_origin = _host(actual_host)
        except ModelError:
            identity["status"] = "unavailable"
            result["diagnostics"].append("requesting-account-host-invalid")
            return False
        if login.casefold() != expected_login.casefold() or actual_origin != host:
            identity["status"] = "mismatch"
            result["diagnostics"].append("requesting-account-mismatch")
            return False
        return True

    async def acquire() -> dict[str, Any]:
        if not await check_identity():
            return result
        quota = await acquire_session_quota(
            session, expected_session_id=expected_session_id,
            runtime_version=runtime_version, timeout=timeout, category=category,
            provider_id=provider_id, quota_id=quota_id, max_age_seconds=max_age_seconds,
        )
        if not await check_identity():
            return result
        if not same_session():
            return result
        identity.update(status="matched", login=expected_login, host=host)
        quota["account_identity"] = identity
        return quota

    try:
        return await asyncio.wait_for(acquire(), timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        result["availability"] = "error"
        result["diagnostics"].append(
            "identity-quota-acquisition-timeout"
            if isinstance(exc, asyncio.TimeoutError) else "identity-quota-transport-error"
        )
    return result
