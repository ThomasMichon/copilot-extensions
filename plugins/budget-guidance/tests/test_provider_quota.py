from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from budget_guidance.cli import main
from budget_guidance.models import ModelError
from budget_guidance.quota import acquire_session_quota, normalize_quota

AT = datetime(2030, 1, 10, tzinfo=timezone.utc)
RESET_MS = int(datetime(2030, 2, 1, tzinfo=timezone.utc).timestamp() * 1000)


def projection() -> dict:
    return {
        "isTbbUser": True,
        "snapshots": {
            "premium_interactions": {
                "isUnlimitedEntitlement": False, "entitlementRequests": 1000,
                "usedRequests": 250, "tokenBasedBilling": True,
                "resetDateEpochMs": RESET_MS, "hasQuota": True,
            }
        },
        "providerQuotas": [{
            "provider": {"id": "provider-example", "label": "sensitive-sentinel"},
            "quotaId": "quota-example", "compatibilityKey": "premium_interactions",
            "acquisitionStatus": "succeeded", "observationKind": "account_snapshot",
            "observedAt": AT.isoformat(), "accessState": "allowed",
            "capacityState": "available", "quantityKind": "authoritative_budget",
            "unit": "ai_credits", "hasQuota": True,
            "entitledQuantity": 1000, "availableQuantity": 750,
            "budgetMetadata": {
                "entitlement": 1000, "consumed": 250, "overage": 0,
                "unlimited": False, "resetAtEpochMs": RESET_MS,
                "resetEstimated": False,
            },
            "reason": "sensitive-sentinel", "source": "https://example.com/private",
            "acquisitionError": "sensitive-sentinel",
        }],
    }


def normalize(raw: dict | None = None, **kwargs: object) -> dict:
    args = {
        "session_id": "session-example", "expected_session_id": "session-example",
        "runtime_version": "sdk-example", "acquired_at": AT,
        "provider_id": "provider-example", "session_owned": True,
    }
    args.update(kwargs)
    return normalize_quota(raw if raw is not None else projection(), **args)


def test_incumbent_authoritative_observation_drives_existing_posture_arithmetic() -> None:
    result = normalize()
    assert result["availability"] == "available"
    assert result["freshness"] == "fresh"
    assert result["provider_observed_at"] == "2030-01-10T00:00:00Z"
    assert result["acquired_at"] == "2030-01-10T00:00:00Z"
    assert result["reset_source"] == "providerQuotas.budgetMetadata.resetAtEpochMs"
    assert result["calculated"]["remaining"] == "750"
    assert result["calculated"]["days_remaining"] == "22"
    assert result["calculated"]["projected_consumption"] is None
    assert result["calculated"]["warning_band"] == "no-rate"
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["worker_mutation"] is False
    assert result["advice"]["operator_launch_blocked"] is False
    assert "sensitive-sentinel" not in json.dumps(result)
    assert "example.com" not in json.dumps(result)


def test_file_capture_cannot_turn_reported_provider_time_into_live_rpc_authority() -> None:
    result = normalize(session_owned=False)
    assert result["attribution"] == "caller-declared-capture"
    assert result["availability"] == "cached"
    assert result["freshness"] == "unverified"
    assert result["provider_observed_at"] is not None
    assert result["calculated"] is None
    assert "capture-provider-evidence-not-authenticated" in result["diagnostics"]


def test_explicit_provider_selection_never_falls_back_to_legacy_counters() -> None:
    raw = projection()
    del raw["providerQuotas"]
    assert normalize(raw)["allowance"] is None
    assert normalize(provider_id="other-provider")["consumption"] is None
    assert normalize(quota_id="other-quota")["remaining"] is None
    raw = projection()
    duplicate = deepcopy(raw["providerQuotas"][0])
    duplicate["quotaId"] = "quota-other"
    raw["providerQuotas"].append(duplicate)
    assert "provider-quota-ambiguous" in normalize(raw)["diagnostics"]
    selected = normalize(raw, quota_id="quota-other")
    assert selected["provider_evidence"]["quota_id"] == "quota-other"
    assert selected["availability"] == "available"


@pytest.mark.parametrize("acquisition", [None, "unavailable", "failed"])
def test_acquisition_failure_does_not_reinterpret_cached_business_state(
    acquisition: str | None,
) -> None:
    raw = projection()
    raw["providerQuotas"][0]["acquisitionStatus"] = acquisition
    result = normalize(raw)
    assert result["availability"] == ("error" if acquisition == "failed" else "unavailable")
    assert result["remaining"] is None
    assert "sensitive-sentinel" not in json.dumps(result)


@pytest.mark.parametrize(
    "field,value",
    [("hasQuota", False), ("accessState", "denied"), ("capacityState", "exhausted")],
)
def test_provider_denial_or_exhaustion_precedes_optimistic_counters(
    field: str, value: object,
) -> None:
    raw = projection()
    raw["providerQuotas"][0][field] = value
    raw["providerQuotas"][0]["budgetMetadata"]["unlimited"] = True
    result = normalize(raw)
    assert result["availability"] == "unavailable"
    assert result["allowance"] is None
    assert result["remaining"] is None


@pytest.mark.parametrize(
    "field,value",
    [("observationKind", "admission_state"), ("quantityKind", "advisory_balance"),
     ("quantityKind", "none"), ("unit", "unknown"), ("accessState", "unknown"),
     ("capacityState", "not_required")],
)
def test_partial_admission_and_advisory_states_do_not_become_budget_measurements(
    field: str, value: str,
) -> None:
    raw = projection()
    raw["providerQuotas"][0][field] = value
    result = normalize(raw)
    assert result["availability"] == "unavailable"
    assert result["remaining"] is None
    assert result["calculated"] is None


def test_unknown_provider_observation_time_is_not_the_acquisition_time() -> None:
    raw = projection()
    del raw["providerQuotas"][0]["observedAt"]
    result = normalize(raw)
    assert result["availability"] == "cached"
    assert result["freshness"] == "unknown"
    assert result["provider_observed_at"] is None
    assert result["calculated"] is None


def test_stale_future_and_exact_boundary_observations() -> None:
    fresh = normalize(evaluated_at=AT + timedelta(seconds=300))
    assert fresh["freshness"] == "fresh"
    stale = normalize(evaluated_at=AT + timedelta(seconds=301))
    assert stale["availability"] == "stale"
    assert stale["remaining"] is None
    future = normalize(evaluated_at=AT - timedelta(seconds=1))
    assert future["availability"] == "unavailable"
    assert future["calculated"] is None


def test_later_evaluation_cannot_validate_an_impossible_capture_observation_time() -> None:
    raw = projection()
    raw["providerQuotas"][0]["observedAt"] = (AT + timedelta(seconds=10)).isoformat()
    result = normalize(raw, evaluated_at=AT + timedelta(seconds=20))
    assert result["availability"] == "unavailable"
    assert result["remaining"] is None
    assert "provider-observation-after-acquisition" in result["diagnostics"]


@pytest.mark.parametrize(
    "observed,evaluated,availability,diagnostic",
    [
        (None, AT, "unavailable", "provider-observation-time-unknown"),
        (AT - timedelta(seconds=301), AT, "stale", "provider-observation-stale"),
        (AT, AT - timedelta(seconds=1), "unavailable", "provider-observation-is-in-the-future"),
        (AT + timedelta(seconds=10), AT + timedelta(seconds=20),
         "unavailable", "provider-observation-after-acquisition"),
    ],
)
def test_unlimited_provider_evidence_requires_valid_observation(
    observed, evaluated, availability, diagnostic,
) -> None:
    raw = projection()
    raw["providerQuotas"][0]["budgetMetadata"] = {"unlimited": True}
    raw["providerQuotas"][0]["observedAt"] = observed.isoformat() if observed else None
    result = normalize(raw, evaluated_at=evaluated)
    assert result["availability"] == availability
    assert diagnostic in result["diagnostics"]
    assert result["provider_observed_at"] == (
        observed.isoformat().replace("+00:00", "Z") if observed else None
    )
    assert result["freshness"] == ("stale" if availability == "stale" else "unknown")
    assert result["remaining"] is None
    assert result["calculated"] is None


@pytest.mark.parametrize("session_owned", [True, False])
def test_unlimited_provider_evidence_preserves_freshness_without_finite_math(
    session_owned: bool,
) -> None:
    raw = projection()
    raw["providerQuotas"][0]["budgetMetadata"] = {"unlimited": True}
    result = normalize(raw, session_owned=session_owned, evaluated_at=AT + timedelta(seconds=300))
    assert result["availability"] == "unlimited"
    assert result["freshness"] == ("fresh" if session_owned else "unverified")
    assert result["provider_observed_at"] == "2030-01-10T00:00:00Z"
    assert result["allowance"] is None
    assert result["calculated"] is None


def test_unlimited_provider_evidence_rejects_malformed_observation() -> None:
    raw = projection()
    raw["providerQuotas"][0]["budgetMetadata"] = {"unlimited": True}
    raw["providerQuotas"][0]["observedAt"] = "invalid-time"
    with pytest.raises(ModelError, match="provider observedAt"):
        normalize(raw)


def test_cli_stale_unlimited_provider_evidence_is_not_success(tmp_path, capsys) -> None:
    raw = projection()
    raw["providerQuotas"][0]["budgetMetadata"] = {"unlimited": True}
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({
        "schema": "copilot-extensions.copilot-quota-capture", "version": 1,
        "session_id": "session-example", "runtime_version": "sdk-example",
        "acquired_at": AT.isoformat(), "projection": raw,
    }), encoding="utf-8")
    assert main([
        "quota", "--capture", str(path), "--session-id", "session-example",
        "--provider-id", "provider-example",
        "--at", (AT + timedelta(seconds=301)).isoformat(), "--json",
    ]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["availability"] == "stale"
    assert result["freshness"] == "stale"
    assert result["calculated"] is None


@pytest.mark.parametrize("reset", [None, 0, RESET_MS - 86400 * 1000 * 60])
def test_missing_or_elapsed_provider_reset_is_not_replaced_by_normalized_reset(
    reset: int | None,
) -> None:
    raw = projection()
    raw["providerQuotas"][0]["budgetMetadata"]["resetAtEpochMs"] = reset
    result = normalize(raw)
    assert result["reset_at"] is None
    assert result["calculated"] is None
    assert any(item.startswith("provider-reset-") for item in result["diagnostics"])
    if reset not in (None, 0):
        assert result["availability"] == "stale"
        assert result["remaining"] is None


def test_real_provider_reset_can_diagnose_but_not_reuse_a_synthetic_old_reset() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["resetDateEpochMs"] = 0
    result = normalize(raw)
    assert result["reset_at"] == "2030-02-01T00:00:00Z"
    assert result["calculated"] is not None
    assert "compatibility-reset-past-or-zero-epoch" in result["diagnostics"]
    raw["providerQuotas"][0]["budgetMetadata"]["resetEstimated"] = True
    result = normalize(raw)
    assert result["reset_at"] is None
    assert result["calculated"] is None


@pytest.mark.parametrize(
    "field,value,diagnostic",
    [("resetDateEpochMs", RESET_MS + 86400000, "provider-compatibility-reset-conflict"),
     ("usedRequests", 200, "compatibility-budget-counters-conflict"),
     ("tokenBasedBilling", False, "compatibility-provider-unit-conflict"),
     ("hasQuota", False, "compatibility-provider-quota-conflict")],
)
def test_compatible_snapshots_cannot_silently_disagree_with_provider_evidence(
    field: str, value: object, diagnostic: str,
) -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"][field] = value
    result = normalize(raw)
    assert result["availability"] == "contradictory"
    assert result["remaining"] is None
    assert result["calculated"] is None
    assert diagnostic in result["diagnostics"]


def test_independent_zero_quantity_does_not_rewrite_business_access() -> None:
    raw = projection()
    raw["providerQuotas"][0]["availableQuantity"] = 0
    result = normalize(raw)
    assert result["availability"] == "contradictory"
    assert result["provider_evidence"]["access_state"] == "allowed"
    assert result["provider_evidence"]["capacity_state"] == "available"
    assert result["remaining"] is None


def test_exhausted_budget_arithmetic_does_not_infer_service_denial() -> None:
    raw = projection()
    raw["providerQuotas"][0]["availableQuantity"] = -250
    raw["providerQuotas"][0]["budgetMetadata"]["consumed"] = 1250
    raw["snapshots"]["premium_interactions"]["usedRequests"] = 1250
    result = normalize(raw)
    assert result["availability"] == "available"
    assert result["provider_evidence"]["access_state"] == "allowed"
    assert result["remaining"] == "0"
    assert result["overspend"] == "250"
    assert result["calculated"]["warning_band"] == "overspent"
    assert result["advice"]["operator_launch_blocked"] is False


def test_unknown_optional_service_quota_flag_is_not_an_invented_true() -> None:
    raw = projection()
    del raw["providerQuotas"][0]["hasQuota"]
    result = normalize(raw)
    assert result["has_quota"] is None
    assert result["availability"] == "available"


@pytest.mark.parametrize("unit", ["requests", "tokens"])
def test_non_credit_provider_units_cannot_drive_ai_credit_pacing(unit: str) -> None:
    raw = projection()
    raw["providerQuotas"][0]["unit"] = unit
    raw["providerQuotas"][0]["compatibilityKey"] = None
    result = normalize(raw)
    assert result["calculated"] is None
    assert result["quantity_kind"] == ("premium-requests" if unit == "requests" else "tokens")


def test_bad_provider_enum_is_a_modeled_error_without_echoing_opaque_metadata() -> None:
    raw = projection()
    raw["providerQuotas"][0]["acquisitionStatus"] = "sensitive-sentinel"
    with pytest.raises(ModelError, match="acquisitionStatus") as exc:
        normalize(raw)
    assert "sensitive-sentinel" not in str(exc.value)


@pytest.mark.parametrize("age", [-1, True, 86401, 0.5])
def test_bad_observation_age_is_rejected(age: object) -> None:
    with pytest.raises(ModelError, match="observation age"):
        normalize(max_age_seconds=age)


def test_existing_sdk_session_can_opt_in_to_a_provider_without_refreshing() -> None:
    calls: list[float | None] = []

    class Result:
        def to_dict(self) -> dict:
            raw = projection()
            raw["providerQuotas"][0]["observedAt"] = datetime.now(timezone.utc).isoformat()
            return raw

    class Quota:
        async def get(self, *, timeout: float | None = None) -> Result:
            calls.append(timeout)
            return Result()

    session = SimpleNamespace(session_id="session-example", rpc=SimpleNamespace(quota=Quota()))
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="sdk-example",
        provider_id="provider-example", quota_id="quota-example",
    ))
    assert calls == [10]
    assert result["availability"] == "available"
    assert result["attribution"] == "session-owned-rpc"
    assert result["calculated"] is not None


def test_cli_provider_evidence_stays_unverified_and_reports_scope_failures(
    tmp_path, capsys,
) -> None:
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({
        "schema": "copilot-extensions.copilot-quota-capture", "version": 1,
        "session_id": "session-example", "runtime_version": "sdk-example",
        "acquired_at": AT.isoformat(), "projection": projection(), "session_owned": True,
    }), encoding="utf-8")
    args = [
        "quota", "--capture", str(path), "--session-id", "session-example",
        "--provider-id", "provider-example", "--at", AT.isoformat(), "--json",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["freshness"] == "unverified"
    assert result["calculated"] is None
    assert main([*args, "--quota-id", "wrong"]) == 1
    assert json.loads(capsys.readouterr().out)["consumption"] is None


def test_capture_parse_failure_does_not_echo_opaque_duplicate_metadata_keys(
    tmp_path, capsys,
) -> None:
    path = tmp_path / "capture.json"
    path.write_text(
        '{"sensitive-sentinel":1,"sensitive-sentinel":2}', encoding="utf-8"
    )
    assert main([
        "quota", "--capture", str(path), "--session-id", "session-example", "--json"
    ]) == 2
    error = json.loads(capsys.readouterr().out)
    assert "sensitive-sentinel" not in error["error"]
    assert "unique keys" in error["error"]
