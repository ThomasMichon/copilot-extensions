"""Service-owned maintenance never returns credentials or consults a client cache."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agent_vault import cache, cache_maintenance, config, service, service_cache
from agent_vault.service_cache_store import ServiceCacheStore
from agent_vault.service_connections import VaultConnections

if TYPE_CHECKING:
    from test_service_cache import cached_service as cached_service
else:
    from .test_service_cache import cached_service as cached_service

ServiceFixture = tuple[service.VaultService, cache.PersistentCache, str, list[float]]


def maintain(vault: service.VaultService, **options: Any) -> dict[str, Any]:
    return vault.handle_request({
        "action": "cache-maintenance", "operation": "verify",
        "entry": "entry", "group": "Allowed", **options,
    })


def test_population_and_locked_verification_return_only_service_metadata(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert vault.handle_request({"action": "ping"})["cache_maintenance"] == cache_maintenance.describe()
    populated = maintain(vault, operation="populate")
    assert populated == {
        "ok": True, "contract": cache_maintenance.CONTRACT,
        "version": 1, "present": True, "owner": "selected-service",
    }
    assert ServiceCacheStore(persistent, kpdb, "Allowed").record("entry") is not None
    before = persistent._cache_file.read_bytes()
    clock[0] += 10
    monkeypatch.setattr(
        vault, "ensure_unlocked", lambda *_a, **_kw: pytest.fail("verification tried to unlock"),
    )
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_a: pytest.fail("verification read the backend"),
    )
    assert maintain(vault) == populated
    assert maintain(vault, entry="missing")["present"] is False
    assert not vault.cli.has_password(kpdb)
    assert persistent._cache_file.read_bytes() == before
    assert "fixture-value" not in json.dumps(populated)


def test_current_and_legacy_requests_cross_the_existing_json_transport(
    cached_service: ServiceFixture,
) -> None:
    vault, _persistent, kpdb, _clock = cached_service

    async def exercise() -> None:
        connections = VaultConnections(vault, service.handle_client)
        listener = await asyncio.start_server(connections.callback("tcp"), "127.0.0.1", 0)
        host, port = listener.sockets[0].getsockname()[:2]

        async def send(request: dict[str, Any]) -> dict[str, Any]:
            reader, writer = await asyncio.open_connection(host, port)
            try:
                writer.write((json.dumps({"kpdb": kpdb, **request}) + "\n").encode())
                await writer.drain()
                response = json.loads(await asyncio.wait_for(reader.readline(), timeout=5))
                assert isinstance(response, dict)
                return response
            finally:
                writer.close()
                await writer.wait_closed()

        try:
            metadata = (await send({"action": "ping"}))["cache_maintenance"]
            assert metadata["contract"] == cache_maintenance.CONTRACT
            populated = await send({
                "action": "cache-maintenance", "operation": "populate",
                "entry": "entry", "group": "Allowed",
            })
            assert populated["ok"] and populated["present"]
            assert "fixture-value" not in json.dumps(populated)
            legacy = await send({"action": "get", "entry": "entry", "group": "Allowed"})
            assert legacy["ok"] and legacy["value"] == "fixture-value"
        finally:
            await connections.close([listener], [], lambda: None)
            assert not connections.clients and not connections.workers and not connections.work

    asyncio.run(exercise())


def test_default_group_is_resolved_once_for_the_entire_maintenance_operation(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    calls: list[bool] = []

    def selected() -> config.VaultConfig:
        assert not calls, "maintenance reselected the default group"
        calls.append(True)
        return config.VaultConfig(group="Allowed")

    monkeypatch.setattr(config, "load_config", selected)
    assert maintain(vault, operation="populate", group=None)["present"]
    assert calls == [True]
    assert ServiceCacheStore(persistent, kpdb, "Allowed").record("entry") is not None


@pytest.mark.parametrize("mismatch", ["database", "group", "field", "installation", "freshness"])
def test_verification_retains_binding_and_freshness_without_backend_fallback(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    assert maintain(vault, operation="populate")["ok"]
    before = persistent._cache_file.read_bytes()
    options: dict[str, Any] = {}
    if mismatch == "database":
        options["kpdb"] = str(Path(kpdb).with_name("other.kdbx"))
    elif mismatch == "group":
        options.update(group="Other", entry="Allowed/entry")
    elif mismatch == "field":
        options["field"] = "username"
    elif mismatch == "installation":
        monkeypatch.setattr(config, "installation_id", lambda: "other-cell")
    else:
        clock[0] += 2
        options["max_age_seconds"] = 1
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_a: pytest.fail("verification read a credential"),
    )
    result = maintain(vault, **options)
    assert result["ok"] and result["present"] is False
    assert persistent._cache_file.read_bytes() == before


def test_population_cannot_unlock_or_report_an_unverified_write(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, clock = cached_service
    monkeypatch.setattr(persistent, "_write_store_unlocked", lambda _store: False)
    assert maintain(vault, operation="populate")["code"] == "cache_write_failed"
    clock[0] += 10
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_a: pytest.fail("locked population read the backend"),
    )
    result = maintain(vault, operation="populate", allow_prompt=True)
    assert result["code"] == "needs_unlock" and result["needs_unlock"]
    assert not vault.cli.has_password(kpdb)
    assert vault._request_ctx.require_existing_unlock is False


def test_unsettled_service_revocation_blocks_maintenance_of_a_retained_record(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    assert maintain(vault, operation="populate")["ok"]
    before = persistent._cache_file.read_bytes()
    vault._blocked_service_cache.add(service_cache.database_binding(kpdb))
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_a: pytest.fail("blocked maintenance read a credential"),
    )
    assert maintain(vault)["code"] == "cache_unavailable"
    assert maintain(vault, operation="populate")["code"] == "cache_unavailable"
    assert persistent._cache_file.read_bytes() == before


def test_service_maintenance_preserves_root_namesakes_and_standard_field_aliases(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, kpdb, _clock = cached_service
    monkeypatch.setattr(vault.cli, "get_entry", lambda _db, entry, field: entry + ":" + field)
    for entry in ("/entry", "entry"):
        for field in ("UserName", "Password"):
            assert maintain(vault, operation="populate", entry=entry, field=field)["present"]
            assert maintain(vault, entry=entry, field=field.lower())["present"]
    selected = ServiceCacheStore(persistent, kpdb, "Allowed")
    root = selected.record("/entry", "password")
    grouped = selected.record("entry", "password")
    assert root is not None and root["value"] == "entry:password"
    assert grouped is not None and grouped["value"] == "Allowed/entry:password"
    assert vault.handle_request({"action": "lock", "kpdb": kpdb})["ok"]
    assert maintain(vault)["present"] is False


@pytest.mark.parametrize("options", [
    {"operation": "unknown"}, {"entry": "/"}, {"entry": []},
    {"field": False}, {"group": []}, {"max_age_seconds": True},
    {"max_age_seconds": -1},
])
def test_invalid_maintenance_refuses_before_protected_or_backend_work(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
    options: dict[str, Any],
) -> None:
    vault, _persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(
        ServiceCacheStore, "record", lambda *_a, **_kw: pytest.fail("invalid request read the cache"),
    )
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_a: pytest.fail("invalid request read a credential"),
    )
    assert maintain(vault, **options)["code"] == "invalid_request"


def test_unavailable_protection_and_draining_service_never_claim_success(
    cached_service: ServiceFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, persistent, _kpdb, _clock = cached_service
    monkeypatch.setattr(persistent, "_crypto_available", lambda: False)
    refused = maintain(vault)
    assert refused.get("code") == "cache_unavailable", refused
    vault._runtime_can_work = False
    assert maintain(vault)["error"] == "service_transition"
