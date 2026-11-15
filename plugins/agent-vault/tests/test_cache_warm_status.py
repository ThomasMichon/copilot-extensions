"""Warming diagnostics must not reload owners, infer contexts or echo secrets."""

from __future__ import annotations

import asyncio

import pytest

from agent_vault import cache_warm, extensions
from agent_vault.cache_warm_status import WarmState
from agent_vault.service_status import format_service_status

from .test_cache_warm import CacheFixture, drain, warming_service as warming_service
from .test_service_cache import cached_service as cached_service


@pytest.mark.parametrize("malformed", ["state", "count", "false-complete"])
def test_status_rendering_does_not_echo_private_or_inconsistent_provider_fields(
    malformed: str,
) -> None:
    status = WarmState("complete", selected=1, cached=1).describe()
    if malformed == "state":
        status["state"] = "private diagnostic"
    elif malformed == "count":
        status["cached"] = "private diagnostic"
    else:
        status["cached"], status["remaining"] = 0, 1
    rendered = format_service_status(
        {"pid": 1, "ttl": 10, "cached": 0, "cache_warm": status},
        status="locked",
        transport="fixture",
    )
    assert "cache warm=unknown" in rendered and "private" not in rendered


def test_full_ping_uses_retained_default_warming_context_without_configuration_reload(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    vault._cache_warm.enqueue(kpdb, None, confirmed=True)
    monkeypatch.setattr(
        cache_warm.config,
        "load_config",
        lambda: pytest.fail("ping or warming reloaded configuration"),
    )
    assert asyncio.run(drain(vault, kpdb))["state"] == "complete"
    response = vault.handle_request({"action": "ping"})
    assert response["cache_warm"]["state"] == "complete"
    assert response["cache_warm"]["cached"] == 1
    assert vault._cache_warm.status(kpdb, "Other")["state"] == "idle"


def test_full_ping_does_not_guess_an_explicit_foreign_group_is_the_default(
    warming_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    vault._cache_warm.enqueue(kpdb, "Foreign", confirmed=True)
    asyncio.run(drain(vault, kpdb))
    monkeypatch.setattr(
        cache_warm.config,
        "load_config",
        lambda: pytest.fail("full ping resolved configuration"),
    )
    response = vault.handle_request({"action": "ping"})
    assert response["cache_warm"]["reason"] == "context-unavailable"
    assert response["cache_warm"]["cached"] == 0
    assert vault._cache_warm.status(kpdb, "Foreign")["cached"] == 1
