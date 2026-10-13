from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
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
                "isUnlimitedEntitlement": False,
                "entitlementRequests": 1000,
                "usedRequests": 250,
                "tokenBasedBilling": True,
                "resetDateEpochMs": RESET_MS,
            }
        },
        "token": "sensitive-sentinel",
        "upgradeUrl": "https://example.com/account/private",
        "providerQuotas": [{"private": "sensitive-sentinel"}],
    }


def normalize(raw: dict | None = None, **kwargs: object) -> dict:
    args = {
        "session_id": "session-example",
        "expected_session_id": "session-example",
        "runtime_version": "sdk-example",
        "acquired_at": AT,
    }
    args.update(kwargs)
    return normalize_quota(raw if raw is not None else projection(), **args)


def test_allowlisted_cached_balances_do_not_manufacture_freshness_or_forecast() -> None:
    result = normalize()
    assert result["allowance"] == "1000"
    assert result["consumption"] == "250"
    assert result["remaining"] == "750"
    assert result["quantity_kind"] == "ai-credits"
    assert result["reset_at"] == "2030-02-01T00:00:00Z"
    assert result["availability"] == "cached"
    assert result["provider_observed_at"] is None
    assert result["freshness"] == "unknown"
    assert result["calculated"] is None
    assert result["advice"]["concurrency"] is None
    assert result["advice"]["operator_launch_blocked"] is False
    assert result["advice"]["worker_mutation"] is False
    assert result["attribution"] == "caller-declared-capture"
    assert "sensitive-sentinel" not in json.dumps(result)
    assert "example.com" not in json.dumps(result)


@pytest.mark.parametrize("entitlement", [1000, 2000, 500])
def test_entitlement_is_observed_per_capture_not_remembered(entitlement: int) -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["entitlementRequests"] = entitlement
    assert normalize(raw)["allowance"] == str(entitlement)


@pytest.mark.parametrize("session_id", ["", "wrong-session"])
def test_missing_or_mismatched_requesting_session_has_no_balances(session_id: str) -> None:
    result = normalize(session_id=session_id)
    assert result["availability"] == "unavailable"
    assert result["allowance"] is None
    assert result["consumption"] is None


@pytest.mark.parametrize("reset", [0, RESET_MS - 86400 * 1000 * 60, None])
def test_past_zero_or_missing_reset_never_enters_forecast(reset: int | None) -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["resetDateEpochMs"] = reset
    result = normalize(raw)
    assert result["reset_at"] is None
    assert result["calculated"] is None
    assert any(item.startswith("reset-") for item in result["diagnostics"])


def test_estimated_reset_is_not_silently_promoted_to_provider_fact() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["resetDateEstimated"] = True
    result = normalize(raw)
    assert result["reset_at"] is None
    assert "reset-estimated-not-provider-authority" in result["diagnostics"]


@pytest.mark.parametrize("bad", [-1, True, float("nan"), float("inf"), 10**30, "100"])
def test_invalid_quantities_are_modeled_errors(bad: object) -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["usedRequests"] = bad
    with pytest.raises(ModelError):
        normalize(raw)


def test_unlimited_does_not_interpret_negative_sentinel_as_budget() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"].update(
        isUnlimitedEntitlement=True, entitlementRequests=-1, usedRequests=-1
    )
    result = normalize(raw)
    assert result["availability"] == "unlimited"
    assert result["allowance"] is None
    assert result["calculated"] is None


def test_unknown_conflicting_and_premium_request_units() -> None:
    raw = projection()
    del raw["isTbbUser"]
    del raw["snapshots"]["premium_interactions"]["tokenBasedBilling"]
    assert normalize(raw)["quantity_kind"] is None
    raw = projection()
    raw["isTbbUser"] = False
    assert normalize(raw)["allowance"] is None
    raw["snapshots"]["premium_interactions"]["tokenBasedBilling"] = False
    result = normalize(raw)
    assert result["quantity_kind"] == "premium-requests"
    assert "premium-requests-are-not-ai-credits" in result["diagnostics"]


def test_reset_overflow_fractional_epoch_and_overspend() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["resetDateEpochMs"] = Decimal("1e18")
    with pytest.raises(ModelError, match="out of range"):
        normalize(raw)
    raw["snapshots"]["premium_interactions"]["resetDateEpochMs"] = Decimal("1.5")
    with pytest.raises(ModelError, match="integer"):
        normalize(raw)
    raw = projection()
    raw["snapshots"]["premium_interactions"]["usedRequests"] = 1250
    result = normalize(raw)
    assert result["remaining"] == "0"
    assert result["overspend"] == "250"


def test_missing_category_is_unavailable_not_zero() -> None:
    assert normalize({"snapshots": {}})["consumption"] is None


def test_additional_usage_preserves_provider_units_without_guessing_a_total() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["overage"] = 25
    result = normalize(raw)
    assert result["additional_usage"] == "25"
    assert result["consumption"] == "250"


def test_malformed_snapshot_container_is_not_a_missing_category() -> None:
    with pytest.raises(ModelError, match="snapshots must be an object"):
        normalize({"snapshots": []})


def test_provider_denial_with_optimistic_cached_counters_cannot_report_a_balance() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["hasQuota"] = False
    result = normalize(raw)
    assert result["availability"] == "unavailable"
    assert result["allowance"] is None
    assert result["consumption"] is None
    assert result["remaining"] is None
    assert result["has_quota"] is False
    assert "provider-reports-no-quota-counters-withheld" in result["diagnostics"]


def test_provider_denial_cannot_be_overridden_by_unlimited_entitlement() -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"].update(
        hasQuota=False, isUnlimitedEntitlement=True
    )
    result = normalize(raw)
    assert result["availability"] == "unavailable"
    assert result["remaining"] is None


@pytest.mark.parametrize("flag", [0, 1, "false", {}, []])
def test_provider_quota_flag_must_be_a_boolean(flag: object) -> None:
    raw = projection()
    raw["snapshots"]["premium_interactions"]["hasQuota"] = flag
    with pytest.raises(ModelError, match="hasQuota"):
        normalize(raw)


def test_explicit_provider_quota_and_absent_optional_flag_preserve_cached_semantics() -> None:
    assert normalize()["has_quota"] is None
    raw = projection()
    raw["snapshots"]["premium_interactions"]["hasQuota"] = True
    result = normalize(raw)
    assert result["has_quota"] is True
    assert result["availability"] == "cached"
    assert result["remaining"] == "750"


def test_transport_failures_are_explicit_and_do_not_echo_provider_errors() -> None:
    class Quota:
        async def get(self, *, timeout: float | None = None) -> None:
            raise OSError("sensitive-sentinel")

    session = SimpleNamespace(session_id="session-example", rpc=SimpleNamespace(quota=Quota()))
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="sdk-example"
    ))
    assert result["availability"] == "error"
    assert result["diagnostics"] == ["quota-transport-error"]
    assert result["consumption"] is None
    assert "sensitive-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("deadline", [0, -1, 31, True, float("inf"), float("nan")])
def test_invalid_deadlines_are_rejected_before_rpc(deadline: float) -> None:
    session = SimpleNamespace(session_id="session-example")
    with pytest.raises(ModelError, match="deadline"):
        asyncio.run(acquire_session_quota(
            session, expected_session_id="session-example",
            runtime_version="sdk-example", timeout=deadline,
        ))


def test_live_helper_calls_only_incumbent_get_and_never_refreshes_or_stops() -> None:
    calls: list[float | None] = []

    class Result:
        def to_dict(self) -> dict:
            return deepcopy(projection())

    class Quota:
        async def get(self, *, timeout: float | None = None) -> Result:
            calls.append(timeout)
            return Result()

    session = SimpleNamespace(session_id="session-example", rpc=SimpleNamespace(quota=Quota()))
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="sdk-example"
    ))
    assert calls == [10]
    assert result["attribution"] == "session-owned-rpc"
    assert result["availability"] == "cached"
    assert result["provider_observed_at"] is None

    mismatch = asyncio.run(acquire_session_quota(
        session, expected_session_id="different-session", runtime_version="sdk-example"
    ))
    assert mismatch["allowance"] is None
    assert calls == [10]


def test_unsupported_runtime_and_bounded_timeout_are_explicit() -> None:
    session = SimpleNamespace(session_id="session-example", rpc=SimpleNamespace())
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="old-runtime"
    ))
    assert result["diagnostics"] == ["session-quota-rpc-unsupported"]

    class Quota:
        async def get(self, *, timeout: float | None = None) -> None:
            await asyncio.sleep(1)

    session.rpc.quota = Quota()
    result = asyncio.run(acquire_session_quota(
        session, expected_session_id="session-example", runtime_version="sdk-example",
        timeout=0.001,
    ))
    assert result["availability"] == "error"
    assert result["diagnostics"] == ["quota-acquisition-timeout"]


def test_cli_capture_is_bounded_and_cannot_self_assert_session_rpc_authority(
    tmp_path, capsys
) -> None:
    path = tmp_path / "capture.json"
    capture = {
        "schema": "copilot-extensions.copilot-quota-capture",
        "version": 1,
        "session_id": "session-example",
        "runtime_version": "sdk-example",
        "acquired_at": "2030-01-10T00:00:00Z",
        "projection": projection(),
        "session_owned": True,
    }
    path.write_text(json.dumps(capture), encoding="utf-8")
    args = ["quota", "--capture", str(path), "--session-id", "session-example", "--json"]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["attribution"] == "caller-declared-capture"
    assert result["calculated"] is None
    capture["projection"]["snapshots"]["premium_interactions"]["hasQuota"] = False
    path.write_text(json.dumps(capture), encoding="utf-8")
    assert main(args) == 1
    denied = json.loads(capsys.readouterr().out)
    assert denied["availability"] == "unavailable"
    assert denied["remaining"] is None
    assert main([*args[:-3], "--session-id", "different", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["consumption"] is None
    path.write_bytes(b" " * 65537)
    assert main(args) == 2
    assert "65536" in json.loads(capsys.readouterr().out)["error"]
