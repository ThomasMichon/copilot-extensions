"""Personal billing report facts never become an invented account budget."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json

import pytest

from budget_guidance.github_usage import GitHubResponse, acquire_github_usage
from budget_guidance.models import ModelError, parse_reading


def _body(value: object) -> bytes:
    return json.dumps(value).encode("utf-8")


def _report(**updates: object) -> dict:
    return {
        "user": "example-user", "timePeriod": {"year": 2030, "month": 1},
        "usageItems": [{
            "unitType": "ai-credits", "grossQuantity": 25.5,
            "discountQuantity": 20, "netQuantity": 5.5,
            "netAmount": 0.055, "opaque": "do-not-render",
        }],
        **updates,
    }


class Client:
    def __init__(self, report: object | None = None, *, status: int = 200) -> None:
        self.responses = [
            GitHubResponse(200, _body({"login": "Example-User", "token": "do-not-render"})),
            GitHubResponse(status, _body(_report() if report is None else report)),
        ]
        self.paths: list[str] = []

    async def get(self, path: str, *, max_bytes: int) -> GitHubResponse:
        assert max_bytes == 65536
        self.paths.append(path)
        return self.responses.pop(0)


def _read(client: Client, **kwargs: object) -> dict:
    return asyncio.run(acquire_github_usage(
        client, expected_login="example-user", year=2030, month=1, **kwargs,
    ))


def test_acquires_exact_account_and_period_without_equating_cost_and_usage() -> None:
    client = Client()
    result = _read(client)
    assert client.paths == [
        "/user", "/users/example-user/settings/billing/ai_credit/usage?year=2030&month=1",
    ]
    assert result["account_verified"] is True
    assert result["availability"] == "available"
    assert result["reported_usage"] == {
        "quantity_kind": "ai-credits", "item_count": 1,
        "grossQuantity": "25.5", "discountQuantity": "20", "netQuantity": "5.5",
    }
    assert result["freshness"] == "unknown"
    assert result["provider_observed_at"] is None
    assert result["requesting_session_account_verified"] is False
    serialized = json.dumps(result)
    assert "do-not-render" not in serialized
    assert "netAmount" not in serialized
    reading = parse_reading(json.loads(
        json.dumps(result["budget_reading"]), parse_int=Decimal,
    ))
    assert reading.availability == "unavailable"
    assert reading.consumption is None
    assert reading.allowance is None
    assert reading.reset_at is None


def test_authentication_mismatch_prevents_report_request() -> None:
    client = Client()
    client.responses[0] = GitHubResponse(200, _body({"login": "other-user"}))
    result = _read(client)
    assert client.paths == ["/user"]
    assert result["availability"] == "error"
    assert result["account_verified"] is False
    assert result["reported_usage"] is None
    assert "other-user" not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_user_failure_prevents_billing_request(status: int) -> None:
    client = Client()
    client.responses[0] = GitHubResponse(status, b"do-not-render")
    result = _read(client)
    assert client.paths == ["/user"]
    assert result["account_verified"] is False
    assert result["availability"] != "available"
    assert result["reported_usage"] is None
    assert "do-not-render" not in json.dumps(result)


@pytest.mark.parametrize("endpoint", [0, 1])
def test_ambiguous_403_remains_an_error_without_claiming_permission_denial(endpoint: int) -> None:
    client = Client()
    client.responses[endpoint] = GitHubResponse(403, b"do-not-render")
    result = _read(client)
    assert len(client.paths) == endpoint + 1
    assert result["availability"] == "error"
    assert result["budget_reading"]["availability"] == "error"
    assert result["diagnostics"] == ["github-forbidden-or-rate-limited"]
    assert result["reported_usage"] is None


@pytest.mark.parametrize("endpoint", [0, 1])
@pytest.mark.parametrize("status", [200, 401, 403, 404, 429, 500])
def test_byte_bound_precedes_status_handling_on_every_endpoint(
    endpoint: int, status: int,
) -> None:
    client = Client()
    client.responses[endpoint] = GitHubResponse(status, b"x" * 65537)
    result = _read(client)
    assert len(client.paths) == endpoint + 1
    assert result["availability"] == "error"
    assert result["diagnostics"] == ["github-response-exceeds-65536-bytes"]
    assert result["reported_usage"] is None
    assert result["budget_reading"]["availability"] == "error"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_http_failures_never_become_zero_or_trigger_retries(status: int) -> None:
    client = Client(status=status)
    result = _read(client)
    assert len(client.paths) == 2
    assert result["availability"] != "available"
    assert result["reported_usage"] is None
    assert result["budget_reading"]["availability"] != "available"


def test_empty_report_is_explicitly_unavailable_not_zero() -> None:
    result = _read(Client(_report(usageItems=[])))
    assert result["availability"] == "unavailable"
    assert result["reported_usage"] is None
    assert result["diagnostics"] == ["github-report-empty-not-zero-consumption"]


@pytest.mark.parametrize("updates", [
    {"user": "other-user"},
    {"timePeriod": {"year": 2030}},
    {"timePeriod": {"year": 2030, "month": 2}},
    {"timePeriod": {"year": 2030, "month": 1, "day": 1}},
    {"timePeriod": {"year": True, "month": 1}},
    {"product": "filtered"},
    {"model": "filtered"},
    {"usageItems": None},
    {"usageItems": [{}] * 257},
    {"usageItems": [{"unitType": "requests"}]},
    {"usageItems": [{"unitType": "credits"}]},
    {"usageItems": [{"unitType": "ai-credits", "grossQuantity": -1}]},
    {"usageItems": [{"unitType": "ai-credits", "grossQuantity": True}]},
    {"usageItems": [{"unitType": "ai-credits", "grossQuantity": float("inf")}]},
])
def test_wrong_scope_units_and_malformed_quantities_withhold_totals(updates: dict) -> None:
    result = _read(Client(_report(**updates)))
    assert result["availability"] == "error"
    assert result["reported_usage"] is None


@pytest.mark.parametrize("body", [
    b"x" * 65537, b'{"user":1,"user":2}', b'{"opaque":"do-not-render",',
    b"[]", b"\xff",
], ids=["oversized-body", "duplicate-user", "truncated-json", "non-object", "invalid-utf8"])
def test_invalid_response_is_bounded_and_redacted(body: bytes) -> None:
    client = Client()
    client.responses[1] = GitHubResponse(200, body)
    result = _read(client)
    assert result["availability"] == "error"
    assert "do-not-render" not in json.dumps(result)
    assert result["reported_usage"] is None


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_non_json_constants_are_rejected_even_in_ignored_metadata(constant: bytes) -> None:
    client = Client()
    client.responses[0] = GitHubResponse(
        200, b'{"login":"example-user","ignored":' + constant + b"}",
    )
    result = _read(client)
    assert client.paths == ["/user"]
    assert result["diagnostics"] == ["github-response-invalid-json"]


def test_multiple_rows_preserve_exact_quantities_and_explicit_zero() -> None:
    row = {"unitType": "ai-credits", "grossQuantity": 0.1, "discountQuantity": 0, "netQuantity": 0.1}
    result = _read(Client(_report(usageItems=[row] * 3)))
    assert result["reported_usage"]["grossQuantity"] == "0.3"
    row["grossQuantity"] = row["netQuantity"] = 0
    result = _read(Client(_report(usageItems=[row])))
    assert result["availability"] == "available"
    assert result["reported_usage"]["grossQuantity"] == "0"


def test_aggregate_precision_and_quantity_bounds_do_not_round_or_expand() -> None:
    client = Client()
    client.responses[1] = GitHubResponse(200, (
        '{"user":"example-user","timePeriod":{"year":2030,"month":1},"usageItems":['
        '{"unitType":"ai-credits","grossQuantity":999999999999999999,'
        '"discountQuantity":0,"netQuantity":999999999999999999},'
        '{"unitType":"ai-credits","grossQuantity":0.000000000000000001,'
        '"discountQuantity":0,"netQuantity":0.000000000000000001}]}'
    ).encode())
    assert _read(client)["reported_usage"]["grossQuantity"] == (
        "999999999999999999.000000000000000001"
    )
    for number in ("1e-99999999", "1e19", "NaN"):
        client = Client()
        client.responses[1] = GitHubResponse(
            200, _body(_report()).replace(b"25.5", number.encode()),
        )
        result = _read(client)
        assert result["availability"] == "error"
        assert result["reported_usage"] is None


def test_deadline_cancels_only_owned_acquisition_and_does_not_stop_client() -> None:
    class SlowClient(Client):
        cancelled = False

        async def get(self, path: str, *, max_bytes: int) -> GitHubResponse:
            try:
                await asyncio.sleep(60)
            finally:
                self.cancelled = True
            return await super().get(path, max_bytes=max_bytes)

    client = SlowClient()
    result = _read(client, timeout=0.01)
    assert client.cancelled
    assert result["availability"] == "error"
    assert result["diagnostics"] == ["github-acquisition-timeout"]


def test_transport_failure_is_redacted_but_unexpected_host_error_propagates() -> None:
    class BrokenClient(Client):
        async def get(self, path: str, *, max_bytes: int) -> GitHubResponse:
            raise OSError("do-not-render")

    assert _read(BrokenClient())["diagnostics"] == ["github-transport-error"]

    class BuggyClient(Client):
        async def get(self, path: str, *, max_bytes: int) -> GitHubResponse:
            raise RuntimeError("host bug")

    with pytest.raises(RuntimeError, match="host bug"):
        _read(BuggyClient())


@pytest.mark.parametrize("kwargs", [
    {"expected_login": "../user"}, {"expected_login": ""},
    {"year": True}, {"month": 13}, {"timeout": True}, {"timeout": 31},
    {"timeout": float("nan")}, {"year": 10**100},
    {"year": 1}, {"year": 999}, {"year": 10000},
])
def test_invalid_invocation_fails_before_network(kwargs: dict) -> None:
    client = Client()
    with pytest.raises(ModelError):
        asyncio.run(acquire_github_usage(client, **{
            "expected_login": "example-user", "year": 2030, "month": 1, **kwargs,
        }))
    assert client.paths == []
