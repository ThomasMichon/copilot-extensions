from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any
from types import SimpleNamespace

import pytest

from budget_guidance.models import ModelError
from budget_guidance.quota import acquire_session_quota, normalize_quota

AT = datetime(2030, 1, 11, tzinfo=timezone.utc)
START = AT - timedelta(days=10)
RESET = datetime(2030, 2, 1, tzinfo=timezone.utc)


def projection(*, allowance: int = 1000, consumption: int = 500) -> dict[str, Any]:
    return {"providerQuotas": [{
        "provider": {"id": "provider-example"}, "quotaId": "quota-example",
        "acquisitionStatus": "succeeded", "observationKind": "account_snapshot",
        "observedAt": AT.isoformat(), "accessState": "allowed",
        "capacityState": "available", "quantityKind": "authoritative_budget",
        "unit": "ai_credits", "hasQuota": True,
        "budgetMetadata": {
            "entitlement": allowance, "consumed": consumption,
            "unlimited": False, "resetAtEpochMs": int(RESET.timestamp() * 1000),
        },
        "monthlyUsage": {
            "state": "available", "scope": "user", "unit": "ai_credits",
            "consumedQuantity": consumption, "cycleStart": START.isoformat(),
            "queriedAt": AT.isoformat(), "resetOn": RESET.isoformat(),
            "opaque": "sensitive-sentinel",
        },
    }]}


def normalize(raw: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "session_id": "session-example", "expected_session_id": "session-example",
        "runtime_version": "sdk-example", "acquired_at": AT,
        "provider_id": "provider-example", "session_owned": True,
    }
    args.update(kwargs)
    return normalize_quota(raw if raw is not None else projection(), **args)


def test_same_period_average_forecasts_overrun_without_manufacturing_trailing_rates() -> None:
    result = normalize()
    rate = result["rate_evidence"]
    assert rate["availability"] == "available"
    assert rate["kind"] == "cycle-average-estimate"
    assert rate["cycle_start_inferred"] is False
    assert rate["settlement_watermark"] is False
    assert rate["window_days"] == "10"
    assert rate["rate_per_day"] == "50"
    assert rate["queried_at"] == "2030-01-11T00:00:00Z"
    assert result["calculated"]["projection_rate"] == "50"
    assert result["calculated"]["projected_consumption"] == "1550"
    assert result["calculated"]["projected_overspend"] == "550"
    assert result["calculated"]["warning_band"] == "critical"
    assert result["advice"]["projection_kind"] == "cycle-average-estimate"
    assert result["advice"]["autonomous_recommendation"] == "reduce-aggressiveness"
    assert result["advice"]["overrun_warning"] == "estimated-overrun"
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["worker_mutation"] is False
    assert result["advice"]["operator_launch_blocked"] is False
    assert "sensitive-sentinel" not in json.dumps(result)
    assert "cycle-average-is-not-a-measured-trailing-rate" in result["diagnostics"]


@pytest.mark.parametrize("allowance,warning", [(1549, "critical"), (1550, "on-track"), (2500, "on-track")])
def test_current_allowance_controls_exact_overrun_threshold(allowance: int, warning: str) -> None:
    result = normalize(projection(allowance=allowance))
    assert result["allowance"] == str(allowance)
    assert result["calculated"]["warning_band"] == warning
    assert result["calculated"]["projected_consumption"] == "1550"
    if warning == "on-track":
        assert result["advice"]["autonomous_recommendation"] == "maintain-with-caution"
        assert "increased concurrency" in result["advice"]["reason"]


def test_zero_service_consumption_is_a_valid_zero_rate_not_unavailable() -> None:
    result = normalize(projection(consumption=0))
    assert result["rate_evidence"]["rate_per_day"] == "0"
    assert result["calculated"]["projected_consumption"] == "0"
    assert result["advice"]["overrun_warning"] == "none-estimated"
    assert result["advice"]["concurrency"] is None


@pytest.mark.parametrize(
    "field,value,diagnostic",
    [
        ("state", "no_policy", "provider-monthly-usage-not-available"),
        ("state", "unavailable", "provider-monthly-usage-not-available"),
        ("state", "unknown", "provider-monthly-usage-not-available"),
        ("scope", "unknown", "provider-monthly-usage-account-scope-unknown"),
        ("unit", "requests", "provider-monthly-usage-unit-mismatch"),
        ("unit", "tokens", "provider-monthly-usage-unit-mismatch"),
        ("unit", "unknown", "provider-monthly-usage-unit-mismatch"),
        ("consumedQuantity", 501, "provider-monthly-usage-budget-conflict"),
        ("resetOn", (RESET + timedelta(days=1)).isoformat(), "provider-monthly-usage-budget-conflict"),
        ("cycleStart", AT.isoformat(), "provider-monthly-usage-period-order-invalid"),
        ("cycleStart", (AT + timedelta(seconds=1)).isoformat(), "provider-monthly-usage-period-order-invalid"),
        ("queriedAt", RESET.isoformat(), "provider-monthly-usage-period-order-invalid"),
        ("queriedAt", (AT + timedelta(seconds=1)).isoformat(), "provider-monthly-usage-after-account-observation"),
        ("queriedAt", (AT - timedelta(seconds=301)).isoformat(), "provider-monthly-usage-stale"),
    ],
)
def test_incompatible_rate_evidence_is_unavailable_not_a_zero_rate(
    field: str, value: object, diagnostic: str,
) -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"][field] = value
    result = normalize(raw)
    assert result["availability"] == "available"
    assert result["remaining"] == "500"
    assert result["rate_evidence"]["availability"] == "unavailable"
    assert result["rate_evidence"]["diagnostic"] == diagnostic
    assert result["calculated"]["projection_rate"] is None
    assert result["calculated"]["projected_consumption"] is None
    assert result["advice"]["autonomous_recommendation"] == "unavailable"
    assert diagnostic in result["diagnostics"]


@pytest.mark.parametrize("field", ["consumedQuantity", "cycleStart", "queriedAt", "resetOn"])
def test_missing_period_evidence_is_not_inferred(field: str) -> None:
    raw = projection()
    del raw["providerQuotas"][0]["monthlyUsage"][field]
    result = normalize(raw)
    assert result["rate_evidence"]["cycle_start_inferred"] is False
    assert result["rate_evidence"]["availability"] == "unavailable"
    assert result["calculated"]["projection_rate"] is None
    assert result["calculated"]["warning_band"] == "no-rate"


def test_missing_monthly_surface_preserves_existing_no_rate_behavior() -> None:
    raw = projection()
    del raw["providerQuotas"][0]["monthlyUsage"]
    result = normalize(raw)
    assert result["rate_evidence"]["diagnostic"] == "provider-monthly-usage-unavailable"
    assert result["calculated"]["projected_consumption"] is None
    assert result["advice"]["operator_launch_blocked"] is False


@pytest.mark.parametrize("field", ["state", "scope", "unit"])
@pytest.mark.parametrize("explicit_null", [False, True])
def test_missing_monthly_enums_only_withhold_rate(
    field: str, explicit_null: bool,
) -> None:
    raw = projection()
    monthly = raw["providerQuotas"][0]["monthlyUsage"]
    if explicit_null:
        monthly[field] = None
    else:
        del monthly[field]
    result = normalize(raw)
    assert result["availability"] == "available"
    assert result["remaining"] == "500"
    assert result["rate_evidence"][field] == "unknown"
    assert result["rate_evidence"]["availability"] == "unavailable"
    assert result["calculated"]["projection_rate"] is None
    assert result["calculated"]["warning_band"] == "no-rate"
    assert result["advice"]["autonomous_recommendation"] == "unavailable"


def test_empty_monthly_evidence_only_withholds_rate() -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"] = {}
    result = normalize(raw)
    assert result["availability"] == "available"
    assert result["remaining"] == "500"
    assert result["rate_evidence"]["state"] == "unknown"
    assert result["rate_evidence"]["scope"] == "unknown"
    assert result["rate_evidence"]["unit"] == "unknown"
    assert result["calculated"]["projected_consumption"] is None


def test_exact_rate_freshness_boundary_and_distinct_observation_times() -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"]["queriedAt"] = (AT - timedelta(seconds=300)).isoformat()
    result = normalize(raw)
    assert result["rate_evidence"]["availability"] == "available"
    assert result["rate_evidence"]["queried_at"] == "2030-01-10T23:55:00Z"
    assert result["provider_observed_at"] == "2030-01-11T00:00:00Z"
    assert result["rate_evidence"]["settlement_watermark"] is False


@pytest.mark.parametrize("field", ["cycleStart", "queriedAt", "resetOn"])
def test_malformed_monthly_timestamps_are_modeled_errors(field: str) -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"][field] = "sensitive-sentinel"
    with pytest.raises(ModelError, match=f"monthlyUsage.{field}") as exc:
        normalize(raw)
    assert "sensitive-sentinel" not in str(exc.value)


@pytest.mark.parametrize("field", ["state", "scope", "unit"])
def test_unknown_monthly_enums_do_not_echo_opaque_metadata(field: str) -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"][field] = "sensitive-sentinel"
    with pytest.raises(ModelError, match=f"monthlyUsage.{field}") as exc:
        normalize(raw)
    assert "sensitive-sentinel" not in str(exc.value)


@pytest.mark.parametrize("consumption", [-1, True, "500", Decimal("NaN"), 10**19])
def test_malformed_service_consumption_does_not_drive_arithmetic(consumption: object) -> None:
    raw = projection()
    raw["providerQuotas"][0]["monthlyUsage"]["consumedQuantity"] = consumption
    with pytest.raises(ModelError, match="consumedQuantity"):
        normalize(raw)


def test_unrepresentable_cycle_rate_stays_explicitly_unavailable() -> None:
    raw = projection(allowance=10**18, consumption=10**18)
    raw["providerQuotas"][0]["monthlyUsage"]["cycleStart"] = (AT - timedelta(microseconds=1)).isoformat()
    result = normalize(raw)
    assert result["rate_evidence"]["diagnostic"] == "provider-cycle-average-exceeds-numeric-domain"
    assert result["calculated"]["projection_rate"] is None


def test_capture_cannot_self_authenticate_cycle_history() -> None:
    raw = projection()
    raw["session_owned"] = True
    result = normalize(raw, session_owned=False)
    assert result["availability"] == "cached"
    assert result["freshness"] == "unverified"
    assert result["rate_evidence"] is None
    assert result["calculated"] is None
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["worker_mutation"] is False


@pytest.mark.parametrize("change", ["denied", "stale", "unlimited", "no-reset", "requests"])
def test_ineligible_account_evidence_never_enables_pacing(change: str) -> None:
    raw = projection()
    state = raw["providerQuotas"][0]
    if change == "denied":
        state["hasQuota"] = False
    elif change == "stale":
        state["observedAt"] = (AT - timedelta(seconds=301)).isoformat()
    elif change == "unlimited":
        state["budgetMetadata"]["unlimited"] = True
    elif change == "no-reset":
        del state["budgetMetadata"]["resetAtEpochMs"]
    else:
        state["unit"] = "requests"
    result = normalize(raw)
    assert result["rate_evidence"] is None
    assert result["calculated"] is None
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["worker_mutation"] is False
    assert result["advice"]["operator_launch_blocked"] is False


def test_observed_overspend_still_warns_without_a_rate_estimate() -> None:
    raw = projection(consumption=1250)
    del raw["providerQuotas"][0]["monthlyUsage"]
    result = normalize(raw)
    assert result["calculated"]["warning_band"] == "overspent"
    assert result["advice"]["projection_kind"] is None
    assert result["advice"]["overrun_warning"] == "observed-overspend"
    assert result["advice"]["autonomous_recommendation"] == "reduce-aggressiveness"
    assert result["advice"]["operator_launch_blocked"] is False


def test_incumbent_acquisition_adds_advice_using_only_one_existing_quota_rpc() -> None:
    calls: list[float | None] = []

    class Result:
        def to_dict(self) -> dict[str, Any]:
            now = datetime.now(timezone.utc)
            reset = now + timedelta(days=21)
            raw = projection()
            state = raw["providerQuotas"][0]
            state["observedAt"] = now.isoformat()
            state["budgetMetadata"]["resetAtEpochMs"] = int(reset.timestamp() * 1000)
            state["monthlyUsage"].update(
                cycleStart=(now - timedelta(days=10)).isoformat(),
                queriedAt=now.isoformat(),
                resetOn=datetime.fromtimestamp(
                    state["budgetMetadata"]["resetAtEpochMs"] / 1000, timezone.utc,
                ).isoformat(),
            )
            return raw

    class Quota:
        async def get(self, *, timeout: float | None = None) -> Result:
            calls.append(timeout)
            return Result()

    session = SimpleNamespace(session_id="session-example", rpc=SimpleNamespace(quota=Quota()))
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="sdk-example",
        provider_id="provider-example",
    ))
    assert calls == [10]
    assert result["attribution"] == "session-owned-rpc"
    assert result["rate_evidence"]["kind"] == "cycle-average-estimate"
    assert result["advice"]["autonomous_recommendation"] == "reduce-aggressiveness"
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["worker_mutation"] is False
    assert result["advice"]["operator_launch_blocked"] is False
