"""Tests for the Azure CLI token credential source."""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from credential_relay.sources.az_login import (
    AzLoginSource,
    _EXPIRY_SAFETY_MARGIN,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_az_response(
    token: str = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.test",
    expires_on: int | str | None = None,
    tenant: str = "test-tenant-id",
) -> str:
    """Build a realistic az CLI JSON response."""
    if expires_on is None:
        expires_on = int(time.time()) + 3600
    data = {
        "accessToken": token,
        "expiresOn": str(expires_on) if isinstance(expires_on, int) else expires_on,
        "expires_on": expires_on if isinstance(expires_on, int) else None,
        "subscription": "sub-id",
        "tenant": tenant,
        "tokenType": "Bearer",
    }
    # Remove None values
    data = {k: v for k, v in data.items() if v is not None}
    return json.dumps(data)


def _mock_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Create a mock async subprocess."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    return proc


# ---------------------------------------------------------------------------
# Basic Behavior
# ---------------------------------------------------------------------------
class TestAzLoginSourceBasic:

    def test_name(self):
        source = AzLoginSource()
        assert source.name == "az-login"

    def test_supports_get_azure_token(self):
        source = AzLoginSource()
        assert source.supports("get-azure-token", {}) is True

    def test_does_not_support_other_actions(self):
        source = AzLoginSource()
        assert source.supports("get", {}) is False
        assert source.supports("get-github-token", {}) is False
        assert source.supports("store", {}) is False
        assert source.supports("fill", {}) is False

    @pytest.mark.asyncio
    async def test_missing_resource_returns_none(self):
        source = AzLoginSource(allowed_resources=["https://management.azure.com/"])
        result = await source.resolve("get-azure-token", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_resource_returns_none(self):
        source = AzLoginSource(allowed_resources=["https://management.azure.com/"])
        result = await source.resolve("get-azure-token", {"resource": ""})
        assert result is None


# ---------------------------------------------------------------------------
# Resource Allowlist
# ---------------------------------------------------------------------------
class TestAzLoginResourceAllowlist:

    @pytest.mark.asyncio
    async def test_disallowed_resource_denied(self):
        source = AzLoginSource(allowed_resources=["https://management.azure.com/"])
        result = await source.resolve(
            "get-azure-token",
            {"resource": "https://graph.microsoft.com/"},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_no_allowed_resources_denies_all(self):
        source = AzLoginSource(allowed_resources=[])
        result = await source.resolve(
            "get-azure-token",
            {"resource": "https://management.azure.com/"},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_normalized_form_allowed(self):
        """Allowlist normalizes trailing slash + /.default (same resource).

        ``https://management.azure.com`` / ``.../`` / ``.../.default`` are the
        same resource in different URL forms, so an allowlist entry in one form
        matches a request in another. (Denied requests short-circuit before az;
        an allowed one would proceed to az, so we only assert it passes the
        allowlist gate by checking a *different* resource is denied below.)
        """
        source = AzLoginSource(
            allowed_resources=["https://management.azure.com/"]
        )
        assert source._is_allowed("https://management.azure.com") is True
        assert source._is_allowed("https://management.azure.com/.default") is True

    @pytest.mark.asyncio
    async def test_different_resource_denied(self):
        """A genuinely different resource is still denied (no glob/prefix)."""
        source = AzLoginSource(
            allowed_resources=["https://management.azure.com/"]
        )
        assert source._is_allowed("https://storage.azure.com/") is False
        result = await source.resolve(
            "get-azure-token",
            {"resource": "https://storage.azure.com/"},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_wildcard_allows_any_scope(self):
        """``*`` permits any scope (the codespace any-AAD-scope policy)."""
        source = AzLoginSource(allowed_resources=["*"])
        assert source._is_allowed("https://storage.azure.com/.default") is True
        assert source._is_allowed("https://anything.example.com/.default") is True


# ---------------------------------------------------------------------------
# Successful Resolution
# ---------------------------------------------------------------------------
class TestAzLoginResolution:

    @pytest.mark.asyncio
    async def test_successful_token_resolution(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        az_output = _make_az_response(
            token="test-token-123",
            tenant="my-tenant",
        )
        proc = _mock_process(stdout=az_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert result is not None
        assert "token=test-token-123" in result
        assert "host=management.azure.com" in result
        assert "protocol=https" in result
        assert "tenant=my-tenant" in result
        assert result.endswith("\n\n")

    @pytest.mark.asyncio
    async def test_resolution_with_tenant_field(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        az_output = _make_az_response()
        proc = _mock_process(stdout=az_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await source.resolve(
                "get-azure-token",
                {"resource": resource, "tenant": "specific-tenant"},
            )

        # Verify --tenant was passed to az CLI
        call_args = mock_exec.call_args
        args = call_args[0] if call_args[0] else call_args.args
        assert "--tenant" in args
        assert "specific-tenant" in args

    @pytest.mark.asyncio
    async def test_scope_field_uses_az_scope(self):
        """A ``scope`` field (official azure-auth-helper contract) -> az --scope."""
        scope = "https://storage.azure.com/.default"
        source = AzLoginSource(allowed_resources=["*"])

        proc = _mock_process(stdout=_make_az_response(token="scoped-tok"))
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await source.resolve("get-azure-token", {"scope": scope})

        call_args = mock_exec.call_args
        args = call_args[0] if call_args[0] else call_args.args
        assert "--scope" in args
        assert scope in args
        assert "--resource" not in args
        assert result is not None and "token=scoped-tok" in result


# ---------------------------------------------------------------------------
# Error Handling
# ---------------------------------------------------------------------------
class TestAzLoginErrors:

    @pytest.mark.asyncio
    async def test_az_cli_not_found(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("az not found"),
        ):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_az_cli_failure(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        proc = _mock_process(
            stderr="ERROR: Please run 'az login'",
            returncode=1,
        )

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_az_cli_timeout(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        async def slow_communicate():
            await asyncio.sleep(999)
            return (b"", b"")

        proc = MagicMock()
        proc.communicate = slow_communicate

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
                timeout=0.01,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_response(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        proc = _mock_process(stdout="not valid json at all")

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_access_token(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        proc = _mock_process(stdout=json.dumps({"accessToken": ""}))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert result is None


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
class TestAzLoginCaching:

    @pytest.mark.asyncio
    async def test_successful_response_is_cached(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        future_expiry = int(time.time()) + 7200  # 2 hours
        az_output = _make_az_response(expires_on=future_expiry)
        proc = _mock_process(stdout=az_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result1 = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )
            result2 = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        # Should only call az CLI once (second call hits cache)
        assert mock_exec.call_count == 1
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_different_resources_have_separate_cache(self):
        resources = [
            "https://management.azure.com/",
            "https://graph.microsoft.com/",
        ]
        source = AzLoginSource(allowed_resources=resources)

        az_output1 = _make_az_response(token="mgmt-token")
        az_output2 = _make_az_response(token="graph-token")

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_process(stdout=az_output1)
            return _mock_process(stdout=az_output2)

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            r1 = await source.resolve(
                "get-azure-token",
                {"resource": resources[0]},
            )
            r2 = await source.resolve(
                "get-azure-token",
                {"resource": resources[1]},
            )

        assert "mgmt-token" in r1
        assert "graph-token" in r2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_different_tenants_have_separate_cache(self):
        resource = "https://management.azure.com/"
        source = AzLoginSource(allowed_resources=[resource])

        az_output1 = _make_az_response(token="tenant1-token", tenant="t1")
        az_output2 = _make_az_response(token="tenant2-token", tenant="t2")

        call_count = 0

        async def mock_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_process(stdout=az_output1)
            return _mock_process(stdout=az_output2)

        with patch("asyncio.create_subprocess_exec", side_effect=mock_exec):
            r1 = await source.resolve(
                "get-azure-token",
                {"resource": resource, "tenant": "t1"},
            )
            r2 = await source.resolve(
                "get-azure-token",
                {"resource": resource, "tenant": "t2"},
            )

        assert "tenant1-token" in r1
        assert "tenant2-token" in r2
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_expired_token_not_served_from_cache(self):
        resource = "https://management.azure.com/"
        # Use a very short TTL override so the cache expires quickly
        source = AzLoginSource(
            allowed_resources=[resource],
            cache_ttl_override=0.01,
        )

        az_output = _make_az_response(token="token-v1")
        proc = _mock_process(stdout=az_output)

        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )
            # Wait for cache to expire
            await asyncio.sleep(0.02)

            az_output2 = _make_az_response(token="token-v2")
            proc2 = _mock_process(stdout=az_output2)
            mock_exec.return_value = proc2

            r2 = await source.resolve(
                "get-azure-token",
                {"resource": resource},
            )

        assert mock_exec.call_count == 2
        assert "token-v2" in r2


# ---------------------------------------------------------------------------
# Expiry Parsing
# ---------------------------------------------------------------------------
class TestAzLoginExpiryParsing:

    def test_epoch_integer_expiry(self):
        source = AzLoginSource()
        future = int(time.time()) + 7200
        result = source._compute_cache_expiry({"expires_on": future})
        assert abs(result - (future - _EXPIRY_SAFETY_MARGIN)) < 2

    def test_epoch_string_expiry(self):
        source = AzLoginSource()
        future = int(time.time()) + 7200
        result = source._compute_cache_expiry({"expiresOn": str(future)})
        assert abs(result - (future - _EXPIRY_SAFETY_MARGIN)) < 2

    def test_iso_datetime_expiry(self):
        source = AzLoginSource()
        result = source._compute_cache_expiry(
            {"expiresOn": "2099-01-15 12:00:00.000000"}
        )
        # Should be far in the future minus safety margin
        assert result > time.time()

    def test_missing_expiry_uses_fallback(self):
        source = AzLoginSource()
        result = source._compute_cache_expiry({})
        # Fallback is ~55 minutes from now (3600 - 300)
        expected = time.time() + 3600 - _EXPIRY_SAFETY_MARGIN
        assert abs(result - expected) < 5

    def test_unparseable_expiry_uses_fallback(self):
        source = AzLoginSource()
        result = source._compute_cache_expiry({"expiresOn": "not-a-date"})
        expected = time.time() + 3600 - _EXPIRY_SAFETY_MARGIN
        assert abs(result - expected) < 5

    def test_ttl_override(self):
        source = AzLoginSource(cache_ttl_override=60.0)
        result = source._compute_cache_expiry(
            {"expires_on": int(time.time()) + 99999}
        )
        expected = time.time() + 60.0
        assert abs(result - expected) < 2
