"""Opt-in, provider-scoped evidence from the incumbent SDK quota projection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from .models import (
    BudgetConfig, BudgetReading, ModelError, StaticAdapter,
    decimal_text, instant_text, parse_instant,
)
from .posture import build_posture
from .provider_pacing import apply_pacing_advice, estimate_provider_rate
from .quota_values import epoch_instant, quantity


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ModelError(f"{field} must be non-empty text of at most 256 characters")
    return value


def _enum(value: Any, choices: set[str], field: str) -> str | None:
    if value is not None and (not isinstance(value, str) or value not in choices):
        raise ModelError(f"provider {field} is unsupported")
    return value


def _withhold(result: dict[str, Any], diagnostic: str, state: str) -> dict[str, Any]:
    for field in (
        "allowance", "consumption", "remaining", "overspend", "additional_usage",
        "reset_at", "reset_source", "calculated",
    ):
        result[field] = None
    result["availability"] = state
    result["diagnostics"].append(diagnostic)
    return result


def _observation(
    state: Mapping[str, Any], result: dict[str, Any], *,
    at: datetime, max_age_seconds: int, session_owned: bool,
) -> bool:
    raw_observed = state.get("observedAt")
    if raw_observed is None:
        result["diagnostics"].append("provider-observation-time-unknown")
        return False
    observed = parse_instant(raw_observed, "provider observedAt")
    result["provider_observed_at"] = instant_text(observed)
    acquired = parse_instant(result["acquired_at"], "quota acquired_at")
    if observed > acquired:
        _withhold(result, "provider-observation-after-acquisition", "unavailable")
        return False
    if observed > at:
        _withhold(result, "provider-observation-is-in-the-future", "unavailable")
        return False
    if at - observed > timedelta(seconds=max_age_seconds):
        result["freshness"] = "stale"
        _withhold(result, "provider-observation-stale", "stale")
        return False
    result["freshness"] = "fresh" if session_owned else "unverified"
    return True


def _reset(
    metadata: Mapping[str, Any], *, at: datetime, field: str, estimated_field: str
) -> tuple[datetime | None, str | None]:
    raw = metadata.get(field)
    if raw is None:
        return None, "unknown"
    reset = epoch_instant(raw, field)
    estimated = metadata.get(estimated_field, False)
    if not isinstance(estimated, bool):
        raise ModelError(f"{estimated_field} must be a boolean when provided")
    if reset <= at:
        return None, "past-or-zero-epoch"
    if estimated:
        return None, "estimated"
    return reset, None


def _compatibility(
    projection: Mapping[str, Any], state: Mapping[str, Any], category: str,
) -> Mapping[str, Any] | None:
    if state.get("compatibilityKey") != category:
        return None
    snapshots = projection.get("snapshots")
    if snapshots is not None and not isinstance(snapshots, Mapping):
        raise ModelError("quota.snapshots must be an object")
    snapshot = snapshots.get(category) if isinstance(snapshots, Mapping) else None
    if snapshot is not None and not isinstance(snapshot, Mapping):
        raise ModelError("quota compatibility snapshot must be an object")
    return snapshot


def normalize_provider_quota(
    projection: Mapping[str, Any], *, result: dict[str, Any], provider_id: str,
    quota_id: str | None, category: str, at: datetime,
    max_age_seconds: int, session_owned: bool,
) -> dict[str, Any]:
    """Interpret only an explicitly selected provider; never fall back to counters."""
    _identity(provider_id, "provider id")
    if quota_id is not None:
        _identity(quota_id, "quota id")
    if (
        isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int)
        or not 0 <= max_age_seconds <= 86400
    ):
        raise ModelError("provider observation age must be an integer from zero to 86400")
    if at.tzinfo is None or at.utcoffset() is None:
        raise ModelError("provider evaluation time must include a timezone")
    result["evaluated_at"] = instant_text(at)
    states = projection.get("providerQuotas")
    if states is None:
        return _withhold(result, "provider-quota-surface-unavailable", "unavailable")
    if not isinstance(states, list) or not all(isinstance(item, Mapping) for item in states):
        raise ModelError("providerQuotas must be an array of objects")
    matches = [
        state for state in states
        if isinstance(state.get("provider"), Mapping)
        and state["provider"].get("id") == provider_id
        and (quota_id is None or state.get("quotaId") == quota_id)
    ]
    if len(matches) != 1:
        return _withhold(
            result,
            "provider-quota-ambiguous" if matches else "requested-provider-quota-unavailable",
            "unavailable",
        )
    state = matches[0]
    selected_quota = _identity(state.get("quotaId"), "provider quota id")
    evidence = {
        "provider_id": provider_id,
        "quota_id": selected_quota,
        "acquisition_status": _enum(
            state.get("acquisitionStatus"), {"succeeded", "unavailable", "failed"},
            "acquisitionStatus",
        ),
        "observation_kind": _enum(
            state.get("observationKind"), {"account_snapshot", "admission_state"},
            "observationKind",
        ),
        "access_state": _enum(
            state.get("accessState"), {"allowed", "denied", "not_required", "unknown", "unavailable"},
            "accessState",
        ),
        "capacity_state": _enum(
            state.get("capacityState"),
            {"available", "exhausted", "unlimited", "not_required", "not_applicable",
             "unknown", "unavailable"},
            "capacityState",
        ),
        "quantity_kind": _enum(
            state.get("quantityKind"), {"authoritative_budget", "advisory_balance", "none"},
            "quantityKind",
        ),
        "unit": _enum(state.get("unit"), {"ai_credits", "requests", "tokens", "unknown"}, "unit"),
    }
    result["provider_evidence"] = evidence
    diagnostics: list[str] = result["diagnostics"]
    acquisition = state.get("acquisitionStatus")
    if acquisition != "succeeded":
        return _withhold(
            result,
            "provider-acquisition-failed" if acquisition == "failed"
            else "provider-acquisition-unavailable",
            "error" if acquisition == "failed" else "unavailable",
        )
    has_quota = state.get("hasQuota")
    if has_quota is not None and not isinstance(has_quota, bool):
        raise ModelError("provider hasQuota must be a boolean when provided")
    result["has_quota"] = has_quota
    if has_quota is False or state.get("accessState") == "denied":
        return _withhold(result, "provider-reports-no-quota-counters-withheld", "unavailable")
    capacity = state.get("capacityState")
    if capacity == "exhausted":
        return _withhold(result, "provider-capacity-exhausted", "unavailable")
    if state.get("accessState") != "allowed" or capacity not in {"available", "unlimited"}:
        return _withhold(result, "provider-business-state-not-a-finite-budget", "unavailable")
    if state.get("observationKind") != "account_snapshot":
        return _withhold(result, "admission-state-is-not-an-account-budget-read", "unavailable")
    if state.get("quantityKind") != "authoritative_budget":
        return _withhold(result, "provider-quantities-not-authoritative-budget", "unavailable")
    unit = state.get("unit")
    if unit not in {"ai_credits", "requests", "tokens"}:
        return _withhold(result, "provider-quantity-unit-unknown", "unavailable")
    result["quantity_kind"] = {
        "ai_credits": "ai-credits", "requests": "premium-requests", "tokens": "tokens",
    }[unit]
    metadata = state.get("budgetMetadata")
    if not isinstance(metadata, Mapping):
        return _withhold(result, "provider-budget-metadata-unavailable", "unavailable")
    unlimited = metadata.get("unlimited")
    if not isinstance(unlimited, bool):
        raise ModelError("provider budget unlimited must be a boolean")
    if unlimited:
        if not _observation(
            state, result, at=at, max_age_seconds=max_age_seconds, session_owned=session_owned,
        ):
            return result
        result["availability"] = "unlimited"
        diagnostics.append("unlimited-entitlement-not-a-finite-budget")
        return result
    if capacity == "unlimited":
        return _withhold(result, "provider-unlimited-budget-conflict", "contradictory")
    allowance = quantity(metadata.get("entitlement"), "budgetMetadata.entitlement")
    consumed = quantity(metadata.get("consumed"), "budgetMetadata.consumed")
    result.update(
        availability="cached", allowance=decimal_text(allowance),
        consumption=decimal_text(consumed),
        remaining=decimal_text(max(allowance - consumed, Decimal(0))),
        overspend=decimal_text(max(consumed - allowance, Decimal(0))),
    )
    if metadata.get("overage") is not None:
        result["additional_usage"] = decimal_text(
            quantity(metadata["overage"], "budgetMetadata.overage")
        )
    for field, expected in (
        ("entitledQuantity", allowance), ("availableQuantity", allowance - consumed),
    ):
        if state.get(field) is not None:
            independent = quantity(state[field], field, signed=True)
            if independent != expected:
                return _withhold(result, "provider-budget-measurements-conflict", "contradictory")
    snapshot = _compatibility(projection, state, category)
    if snapshot is not None:
        compatibility_quota = snapshot.get("hasQuota")
        if compatibility_quota is not None and not isinstance(compatibility_quota, bool):
            raise ModelError("compatibility hasQuota must be a boolean when provided")
        if compatibility_quota is False:
            return _withhold(result, "compatibility-provider-quota-conflict", "contradictory")
        compatibility_billing = snapshot.get("tokenBasedBilling", projection.get("isTbbUser"))
        if compatibility_billing is not None and not isinstance(compatibility_billing, bool):
            raise ModelError("compatibility tokenBasedBilling must be a boolean when provided")
        if compatibility_billing is None:
            diagnostics.append("compatibility-quantity-unit-unknown")
        else:
            compatibility_unit = "ai_credits" if compatibility_billing else "requests"
            if compatibility_unit != unit:
                return _withhold(result, "compatibility-provider-unit-conflict", "contradictory")
            for field, expected in (("entitlementRequests", allowance), ("usedRequests", consumed)):
                if snapshot.get(field) is not None:
                    if quantity(snapshot[field], field) != expected:
                        return _withhold(result, "compatibility-budget-counters-conflict", "contradictory")
    if not _observation(
        state, result, at=at, max_age_seconds=max_age_seconds, session_owned=session_owned,
    ):
        return result
    observed = parse_instant(result["provider_observed_at"], "provider observedAt")
    reset, reset_diagnostic = _reset(
        metadata, at=at, field="resetAtEpochMs", estimated_field="resetEstimated",
    )
    result["reset_evidence"] = {
        "provider": instant_text(reset) if reset is not None else None,
        "compatibility": None,
    }
    if reset_diagnostic is not None:
        diagnostics.append(f"provider-reset-{reset_diagnostic}")
    if reset_diagnostic == "past-or-zero-epoch" and metadata.get("resetAtEpochMs") != 0:
        return _withhold(result, "provider-budget-period-reset-due", "stale")
    if snapshot is not None:
        try:
            compatibility_reset, compatibility_diagnostic = _reset(
                snapshot, at=at, field="resetDateEpochMs", estimated_field="resetDateEstimated",
            )
        except ModelError:
            compatibility_reset, compatibility_diagnostic = None, "invalid"
        result["reset_evidence"]["compatibility"] = (
            instant_text(compatibility_reset) if compatibility_reset is not None else None
        )
        if compatibility_diagnostic is not None:
            diagnostics.append(f"compatibility-reset-{compatibility_diagnostic}")
        if reset is not None and compatibility_reset is not None and reset != compatibility_reset:
            return _withhold(result, "provider-compatibility-reset-conflict", "contradictory")
    if reset is not None:
        result["reset_at"] = instant_text(reset)
        result["reset_source"] = "providerQuotas.budgetMetadata.resetAtEpochMs"
    if not session_owned:
        diagnostics.append("capture-provider-evidence-not-authenticated")
        return result
    result["availability"] = "available"
    diagnostics.append("account-read-is-not-a-ledger-settlement-watermark")
    if reset is None:
        result["advice"]["reason"] = "an authoritative future reset is unavailable"
        return result
    if unit != "ai_credits":
        diagnostics.append("provider-unit-is-not-an-ai-credit-budget")
        return result
    rate = estimate_provider_rate(
        state, result, consumed=consumed, reset=reset, observed=observed, at=at,
        max_age_seconds=max_age_seconds,
    )
    config = BudgetConfig(adapters=(StaticAdapter(
        adapter_id="session-provider", authority=0,
        reading=BudgetReading(
            source="session-provider-account-snapshot", captured_at=observed,
            freshness_seconds=max_age_seconds, availability="available", error=None,
            allowance=allowance, consumption=consumed, reset_at=reset,
            trailing_rates=(rate,) if rate is not None else None,
        ),
    ),))
    result["calculated"] = build_posture(config, at)["calculated"]
    apply_pacing_advice(result)
    return result
