"""Tests for the injected Azure-token relay source (test/relay-shim affordance).

Covers the production-inert passthrough contract and the serve/allowlist/expiry
behavior. The build-wiring assertion (injected source placed BEFORE az-login)
lives in test_relay_shim.py, which has the relay_token isolation fixture.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time

from credential_relay.sources.injected_token import (
    DEFAULT_ENV_VAR,
    InjectedTokenSource,
)

ADO = "499b84ac-1321-427f-aa17-267ca6975798"


def _resolve(src: InjectedTokenSource, action: str, fields: dict) -> str | None:
    return asyncio.run(src.resolve(action, fields))


def test_supports_only_get_azure_token():
    src = InjectedTokenSource(allowed_resources=["*"])
    assert src.supports("get-azure-token", {}) is True
    assert src.supports("get-github-token", {}) is False
    assert src.supports("get", {"host": "github.com"}) is False


def test_passthrough_when_env_unset(monkeypatch):
    """Inert without the env var: returns None so routing falls to az-login."""
    monkeypatch.delenv(DEFAULT_ENV_VAR, raising=False)
    src = InjectedTokenSource(allowed_resources=["*"])
    assert _resolve(src, "get-azure-token", {"scope": ADO}) is None


def test_serves_injected_token_when_env_set(monkeypatch):
    monkeypatch.setenv(DEFAULT_ENV_VAR, "injected-bearer-xyz")
    src = InjectedTokenSource(allowed_resources=[ADO])
    out = _resolve(src, "get-azure-token", {"scope": ADO})
    assert out is not None
    assert "protocol=https" in out
    assert "token=injected-bearer-xyz" in out
    assert out.endswith("\n\n")


def test_enforces_resource_allowlist(monkeypatch):
    monkeypatch.setenv(DEFAULT_ENV_VAR, "injected-bearer-xyz")
    src = InjectedTokenSource(allowed_resources=[ADO])
    # A resource off the allowlist is denied even with a token present.
    assert _resolve(
        src, "get-azure-token", {"scope": "https://graph.microsoft.com/.default"}
    ) is None
    # "*" allows any scope.
    star = InjectedTokenSource(allowed_resources=["*"])
    monkeypatch.setenv(DEFAULT_ENV_VAR, "tok")
    assert _resolve(
        star, "get-azure-token", {"scope": "https://graph.microsoft.com/.default"}
    ) is not None


def test_allowlist_matches_resource_and_scope_forms(monkeypatch):
    monkeypatch.setenv(DEFAULT_ENV_VAR, "tok")
    # Allowlist entry as a bare resource GUID matches a "/.default" scope request.
    src = InjectedTokenSource(allowed_resources=[ADO])
    assert _resolve(src, "get-azure-token", {"scope": f"{ADO}/.default"}) is not None


def test_missing_target_is_none(monkeypatch):
    monkeypatch.setenv(DEFAULT_ENV_VAR, "tok")
    src = InjectedTokenSource(allowed_resources=["*"])
    assert _resolve(src, "get-azure-token", {}) is None


def test_custom_env_var(monkeypatch):
    monkeypatch.delenv(DEFAULT_ENV_VAR, raising=False)
    monkeypatch.setenv("MY_ADO_BEARER", "tok")
    src = InjectedTokenSource(allowed_resources=["*"], env_var="MY_ADO_BEARER")
    assert _resolve(src, "get-azure-token", {"scope": ADO}) is not None


def test_includes_expires_on_from_jwt(monkeypatch):
    exp = int(time.time()) + 3600
    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode())
        .rstrip(b"=")
        .decode()
    )
    monkeypatch.setenv(DEFAULT_ENV_VAR, f"aaa.{payload}.bbb")
    src = InjectedTokenSource(allowed_resources=["*"])
    out = _resolve(src, "get-azure-token", {"scope": ADO})
    assert out is not None
    assert f"expires_on={exp}" in out


def test_non_jwt_token_omits_expires_on(monkeypatch):
    monkeypatch.setenv(DEFAULT_ENV_VAR, "not-a-jwt")
    src = InjectedTokenSource(allowed_resources=["*"])
    out = _resolve(src, "get-azure-token", {"scope": ADO})
    assert out is not None
    assert "expires_on=" not in out


def test_build_wires_injected_before_az_login():
    """RelayBuilder.build() places the injected shim AHEAD of az-login so a set
    env var wins, while az-login remains the real fallback."""
    from credential_relay import RelayBuilder

    b = RelayBuilder()
    b.allow_azure_resources([ADO])
    srv = b.build()
    names = [s.name for s in srv.sources]
    assert "injected-azure-token" in names
    assert "az-login" in names
    assert names.index("injected-azure-token") < names.index("az-login")


def test_build_omits_injected_when_azure_disabled():
    """No azure minting requested -> neither azure source is added."""
    from credential_relay import RelayBuilder

    srv = RelayBuilder().build()
    names = [s.name for s in srv.sources]
    assert "injected-azure-token" not in names
    assert "az-login" not in names
