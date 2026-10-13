"""Read incumbent session quota without creating or refreshing a client."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

from .models import ModelError, decimal_text, instant_text
from .quota_values import epoch_instant, quantity as _quantity

QUOTA_SCHEMA = "copilot-extensions.session-quota-reading"


class QuotaResult(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


class QuotaApi(Protocol):
    async def get(self, *, timeout: float | None = None) -> QuotaResult: ...


class SessionRpc(Protocol):
    quota: QuotaApi


class QuotaSession(Protocol):
    session_id: str
    rpc: SessionRpc


def _base(
    session_id: str, runtime_version: str, acquired_at: datetime, attribution: str
) -> dict[str, Any]:
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ModelError("quota acquisition time must include a timezone")
    if not isinstance(session_id, str) or len(session_id) > 256:
        raise ModelError("session id must be a string of at most 256 characters")
    if not runtime_version.strip() or len(runtime_version) > 256:
        raise ModelError("runtime version must be non-empty and at most 256 characters")
    return {
        "schema": QUOTA_SCHEMA,
        "version": 1,
        "session_id": session_id,
        "runtime_version": runtime_version,
        "attribution": attribution,
        "acquired_at": instant_text(acquired_at),
        "provider_observed_at": None,
        "freshness": "unknown",
        "availability": "unavailable",
        "quantity_kind": None,
        "has_quota": None,
        "allowance": None,
        "consumption": None,
        "additional_usage": None,
        "remaining": None,
        "overspend": None,
        "reset_at": None,
        "reset_source": None,
        "calculated": None,
        "diagnostics": [],
        "advice": {
            "mode": "advice-only",
            "operator_launch_blocked": False,
            "worker_mutation": False,
            "concurrency": None,
            "reason": "provider observation freshness and rate evidence are unavailable",
        },
    }


def normalize_quota(
    projection: Mapping[str, Any],
    *,
    session_id: str,
    expected_session_id: str,
    runtime_version: str,
    acquired_at: datetime,
    category: str = "premium_interactions",
    session_owned: bool = False,
    provider_id: str | None = None,
    quota_id: str | None = None,
    evaluated_at: datetime | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Allowlist cached SDK quota; never present read time as provider freshness."""
    result = _base(
        session_id, runtime_version, acquired_at,
        "session-owned-rpc" if session_owned else "caller-declared-capture",
    )
    diagnostics: list[str] = result["diagnostics"]
    if not isinstance(expected_session_id, str) or len(expected_session_id) > 256:
        raise ModelError("expected session id must be a string of at most 256 characters")
    if not session_id or not expected_session_id:
        diagnostics.append("requesting-session-context-missing")
        return result
    if session_id != expected_session_id:
        diagnostics.append("requesting-session-mismatch")
        return result
    if not session_owned:
        diagnostics.append("capture-attribution-not-independently-verified")
    if provider_id is not None:
        from .provider_quota import normalize_provider_quota

        return normalize_provider_quota(
            projection, result=result, provider_id=provider_id, quota_id=quota_id,
            category=category, at=evaluated_at or acquired_at,
            max_age_seconds=max_age_seconds, session_owned=session_owned,
        )
    if quota_id is not None:
        raise ModelError("quota id requires an explicit provider id")
    diagnostics.append("cached-provider-state-observation-time-unknown")
    snapshots = projection.get("snapshots")
    if snapshots is not None and not isinstance(snapshots, Mapping):
        raise ModelError("quota.snapshots must be an object")
    snapshot = snapshots.get(category) if isinstance(snapshots, Mapping) else None
    if not isinstance(snapshot, Mapping):
        diagnostics.append("quota-category-unavailable")
        return result
    has_quota = snapshot.get("hasQuota")
    if has_quota is not None and not isinstance(has_quota, bool):
        raise ModelError("quota.hasQuota must be a boolean when provided")
    result["has_quota"] = has_quota
    if has_quota is False:
        diagnostics.append("provider-reports-no-quota-counters-withheld")
        return result
    unlimited = snapshot.get("isUnlimitedEntitlement")
    if not isinstance(unlimited, bool):
        raise ModelError("quota.isUnlimitedEntitlement must be a boolean")
    token_based = snapshot.get("tokenBasedBilling", projection.get("isTbbUser"))
    if not isinstance(token_based, bool):
        diagnostics.append("quantity-kind-unknown")
        return result
    account_token_based = projection.get("isTbbUser")
    if (
        isinstance(account_token_based, bool)
        and account_token_based != token_based
    ):
        diagnostics.append("quantity-kind-conflict")
        return result
    result["quantity_kind"] = "ai-credits" if token_based else "premium-requests"
    if unlimited:
        result["availability"] = "unlimited"
        diagnostics.append("unlimited-entitlement-not-a-finite-budget")
        return result
    allowance = _quantity(snapshot.get("entitlementRequests"), "entitlementRequests")
    consumption = _quantity(snapshot.get("usedRequests"), "usedRequests")
    result.update(
        availability="cached",
        allowance=decimal_text(allowance),
        consumption=decimal_text(consumption),
        remaining=decimal_text(max(allowance - consumption, Decimal(0))),
        overspend=decimal_text(max(consumption - allowance, Decimal(0))),
    )
    if snapshot.get("overage") is not None:
        result["additional_usage"] = decimal_text(_quantity(snapshot["overage"], "overage"))
        diagnostics.append("additional-usage-reported-separately-not-added-to-used")
    epoch_ms = snapshot.get("resetDateEpochMs")
    if epoch_ms is None:
        diagnostics.append("reset-unknown")
    else:
        reset = epoch_instant(epoch_ms, "resetDateEpochMs")
        estimated = snapshot.get("resetDateEstimated", False)
        if not isinstance(estimated, bool):
            raise ModelError("quota.resetDateEstimated must be a boolean")
        if reset <= acquired_at:
            diagnostics.append("reset-past-or-zero-epoch")
        elif estimated:
            diagnostics.append("reset-estimated-not-provider-authority")
        else:
            result["reset_at"] = instant_text(reset)
            result["reset_source"] = "session.quota.get.snapshots.resetDateEpochMs"
    if not token_based:
        diagnostics.append("premium-requests-are-not-ai-credits")
    diagnostics.append("forecast-unavailable-without-provider-freshness-and-rate-evidence")
    return result


async def acquire_session_quota(
    session: QuotaSession,
    *,
    expected_session_id: str,
    runtime_version: str,
    timeout: float = 10,
    category: str = "premium_interactions",
    provider_id: str | None = None,
    quota_id: str | None = None,
    max_age_seconds: int = 300,
) -> dict[str, Any]:
    """Use the caller's existing SDK session; do not resume, refresh, or stop it."""
    if isinstance(timeout, bool) or not 0 < timeout <= 30:
        raise ModelError("quota deadline must be greater than zero and at most 30 seconds")
    now = datetime.now(timezone.utc)
    session_id = getattr(session, "session_id", "")
    if not isinstance(session_id, str):
        raise ModelError("SDK session_id must be a string")
    if not expected_session_id or session_id != expected_session_id:
        return normalize_quota(
            {}, session_id=session_id, expected_session_id=expected_session_id,
            runtime_version=runtime_version, acquired_at=now, session_owned=True,
        )
    quota = getattr(getattr(session, "rpc", None), "quota", None)
    getter = getattr(quota, "get", None)
    if not callable(getter):
        result = _base(session_id, runtime_version, now, "session-owned-rpc")
        result["diagnostics"] = ["session-quota-rpc-unsupported"]
        return result
    try:
        raw = await asyncio.wait_for(getter(timeout=timeout), timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        result = _base(session_id, runtime_version, now, "session-owned-rpc")
        result["availability"] = "error"
        result["diagnostics"] = [
            "quota-acquisition-timeout"
            if isinstance(exc, asyncio.TimeoutError) else "quota-transport-error"
        ]
        return result
    projection = raw.to_dict()
    if not isinstance(projection, Mapping):
        raise ModelError("SDK quota response must serialize to an object")
    return normalize_quota(
        projection, session_id=session_id, expected_session_id=expected_session_id,
        runtime_version=runtime_version, acquired_at=datetime.now(timezone.utc),
        category=category, session_owned=True,
        provider_id=provider_id, quota_id=quota_id, max_age_seconds=max_age_seconds,
    )
