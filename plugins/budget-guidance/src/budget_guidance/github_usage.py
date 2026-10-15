"""Bounded personal billing reports, separate from current account entitlement."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, DecimalException, localcontext
import json
import re
from typing import Any, Protocol

from .models import ModelError, _decimal, _strict_object, decimal_text, instant_text

MAX_RESPONSE_BYTES = 65536
MAX_USAGE_ITEMS = 256
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
QUANTITIES = ("grossQuantity", "discountQuantity", "netQuantity")


@dataclass(frozen=True)
class GitHubResponse:
    """One bounded response from the caller's already-authenticated transport."""

    status_code: int
    body: bytes


class GitHubUsageClient(Protocol):
    async def get(self, path: str, *, max_bytes: int) -> GitHubResponse: ...


def _base(login: str, year: int, month: int) -> dict[str, Any]:
    return {
        "schema": "copilot-extensions.github-ai-credit-usage",
        "version": 1,
        "source": "github-rest-ai-credit-usage",
        "attribution": "caller-owned-authenticated-client",
        "requested_login": login,
        "account_verified": False,
        "requesting_session_account_verified": False,
        "period": {"year": year, "month": month},
        "acquired_at": None,
        "provider_observed_at": None,
        "freshness": "unknown",
        "availability": "unavailable",
        "reported_usage": None,
        "diagnostics": [],
        "budget_reading": None,
    }


def _finish(result: dict[str, Any]) -> dict[str, Any]:
    acquired = instant_text(datetime.now(timezone.utc))
    result["acquired_at"] = acquired
    result["budget_reading"] = {
        "schema": "copilot-extensions.budget-reading",
        "version": 1,
        "source": result["source"],
        "captured_at": acquired,
        "freshness_seconds": 0,
        "availability": "error" if result["availability"] == "error" else "unavailable",
        "error": (
            "GitHub acquisition failed: " + ", ".join(result["diagnostics"])
            if result["availability"] == "error"
            else "billing report does not establish current entitlement, reset, or observation time"
        ),
    }
    return result


def _invalid_constant(_value: str) -> None:
    raise ModelError("github-response-invalid-json")


def _decode(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            body.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
            object_pairs_hook=_strict_object, parse_constant=_invalid_constant,
        )
    except (UnicodeError, json.JSONDecodeError, DecimalException, ModelError, RecursionError):
        raise ModelError("github-response-invalid-json") from None
    if not isinstance(value, dict):
        raise ModelError("github-response-not-an-object")
    return value


async def _get(
    client: GitHubUsageClient, path: str, result: dict[str, Any],
) -> dict[str, Any] | None:
    response = await client.get(path, max_bytes=MAX_RESPONSE_BYTES)
    if (
        not isinstance(response, GitHubResponse)
        or type(response.status_code) is not int
        or not isinstance(response.body, bytes)
    ):
        raise TypeError("GitHub transport must return a GitHubResponse")
    if len(response.body) > MAX_RESPONSE_BYTES:
        raise ModelError("github-response-exceeds-65536-bytes")
    if response.status_code != 200:
        reason = {
            401: "github-unauthorized", 403: "github-forbidden-or-rate-limited",
            404: "github-report-unsupported-or-inaccessible",
            429: "github-rate-limited-no-automatic-retry",
        }.get(response.status_code, "github-http-error")
        result["availability"] = "unavailable" if response.status_code in {401, 404} else "error"
        result["diagnostics"].append(reason)
        return None
    return _decode(response.body)


def _report(report: dict[str, Any], result: dict[str, Any]) -> None:
    account = report.get("user")
    if not isinstance(account, str) or account.casefold() != result["requested_login"].casefold():
        raise ModelError("github-report-account-mismatch")
    period = report.get("timePeriod")
    if (
        not isinstance(period, dict) or set(period) != {"year", "month"}
        or any(
            type(period.get(key)) is not Decimal or period[key] != result["period"][key]
            for key in ("year", "month")
        )
        or report.get("product") is not None or report.get("model") is not None
    ):
        raise ModelError("github-report-period-or-filter-mismatch")
    items = report.get("usageItems")
    if not isinstance(items, list) or len(items) > MAX_USAGE_ITEMS:
        raise ModelError("github-report-items-invalid-or-too-many")
    if not items:
        result["diagnostics"].append("github-report-empty-not-zero-consumption")
        return
    totals = _totals(items)
    result["availability"] = "available"
    result["reported_usage"] = {
        "quantity_kind": "ai-credits",
        "item_count": len(items),
        **totals,
    }
    result["diagnostics"].extend([
        "personal-billed-usage-excludes-organization-managed-licenses",
        "report-quantities-not-included-credit-entitlement-or-reset",
        "provider-observation-time-unknown",
        "requesting-session-account-not-independently-verified",
    ])


def _totals(items: list[Any]) -> dict[str, str | None]:
    with localcontext() as context:
        context.prec = 64
        totals = {key: Decimal(0) for key in QUANTITIES}
        for item in items:
            if not isinstance(item, dict) or item.get("unitType") != "ai-credits":
                raise ModelError("github-report-unit-unsupported")
            for key in QUANTITIES:
                try:
                    quantity = _decimal(item.get(key), key)
                    exponent = quantity.as_tuple().exponent
                    if not isinstance(exponent, int) or exponent < -18:
                        raise ModelError("unsupported quantity precision")
                    totals[key] += quantity
                    _decimal(totals[key], key)
                except (ModelError, DecimalException):
                    raise ModelError("github-report-quantity-invalid") from None
        return {key: decimal_text(value) for key, value in totals.items()}


async def acquire_github_usage(
    client: GitHubUsageClient, *, expected_login: str, year: int, month: int,
    timeout: float = 10,
) -> dict[str, Any]:
    """Read an explicit personal billing period through an invocation-owned client.

    The host owns authentication, HTTPS origin, response streaming bounds, and
    cooperative cancellation. No client creation, refresh, retries, or writes.
    """
    if not isinstance(expected_login, str) or LOGIN.fullmatch(expected_login) is None:
        raise ModelError("expected GitHub login must be a valid account handle")
    if type(year) is not int or type(month) is not int:
        raise ModelError("GitHub report year and month must be integers")
    if not 1000 <= year <= 9999:
        raise ModelError("GitHub report year must have four digits")
    try:
        date(year, month, 1)
    except (ValueError, OverflowError) as exc:
        raise ModelError("GitHub report year and month must form a valid period") from exc
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 30:
        raise ModelError("GitHub deadline must be greater than zero and at most 30 seconds")
    result = _base(expected_login, year, month)

    async def acquire() -> None:
        user = await _get(client, "/user", result)
        if user is None:
            return
        login = user.get("login")
        if not isinstance(login, str) or login.casefold() != expected_login.casefold():
            raise ModelError("github-authenticated-account-mismatch")
        result["account_verified"] = True
        report = await _get(
            client,
            f"/users/{expected_login}/settings/billing/ai_credit/usage?year={year}&month={month}",
            result,
        )
        if report is not None:
            _report(report, result)

    try:
        await asyncio.wait_for(acquire(), timeout=timeout)
    except (asyncio.TimeoutError, OSError, ModelError) as exc:
        result["availability"] = "error"
        result["reported_usage"] = None
        result["diagnostics"].append(
            str(exc) if isinstance(exc, ModelError)
            else "github-acquisition-timeout" if isinstance(exc, asyncio.TimeoutError)
            else "github-transport-error"
        )
    return _finish(result)
