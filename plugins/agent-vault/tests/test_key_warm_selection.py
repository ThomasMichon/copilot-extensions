"""Specialized key owners retain caller warming sources without changing keys."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_vault import cache_warm, configuration, extensions, ssh_key
from agent_vault.cache_warm_selection import FIELD, WarmSelection
from agent_vault.invocation import use_configuration_root
from agent_vault.service_connections import settle_owned

if TYPE_CHECKING:
    from test_cache_warm import CacheFixture, recording_reader
    from test_cache_warm import warming_service as warming_service
    from test_service_cache import cached_service as cached_service
else:
    from .test_cache_warm import CacheFixture, recording_reader
    from .test_cache_warm import warming_service as warming_service
    from .test_service_cache import cached_service as cached_service


@pytest.mark.parametrize("action", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
@pytest.mark.parametrize("entry", ["direct", "Allowed/direct"])
def test_key_provider_unlock_preserves_root_anchored_entry_and_selected_warming_group(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    action: str, entry: str,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    resident, selected = tmp_path / "resident", tmp_path / "selected"
    for root in (resident, selected):
        root.mkdir()
        (root / ".agent-vault.json").write_text(json.dumps({
            "cache_entries": [root.name],
        }), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "native.json"))
    monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    value = (
        base64.b64encode(bytes(range(32))).decode("ascii")
        if action == "identity-key-setup" else ssh_key._generate_record()
    )
    reads: list[str] = []

    def read(_kpdb: str, target: str, _deadline: float) -> str:
        reads.append(target)
        return value

    def no_create(*_args: object) -> None:
        pytest.fail("warming replaced or created a key")

    monkeypatch.setattr(vault.cli, "read_identity_key", read)
    monkeypatch.setattr(vault.cli, "create_identity_key", no_create)
    calls: list[str] = []
    monkeypatch.setattr(vault.cli, "get_entry", recording_reader(calls, "fixture"))
    extensions.get_registry().register_unlock_provider(lambda _context: "fixture-master")
    vault.cli.clear_password(kpdb)
    vault._password_set_at.pop(kpdb)
    selection = WarmSelection(str(selected), False)
    request: dict[str, object] = {
        "action": action, "entry": "/" + entry, "kpdb": kpdb, "group": "Allowed",
    }
    with use_configuration_root(str(resident)):
        health = vault.handle_request({"action": "ping", "health_only": True})
        assert action in health["cache_warm_selection_actions"]
        result = vault.handle_request({**request, FIELD: selection.describe()})
    receipt = result.pop("cache_warm")
    if action == "identity-key-setup":
        assert result["ok"] and result["status"] == "reused"
        assert result["value"] == value and type(result["generation"]) is int
    elif action == "ssh-key-prepare":
        assert result == {"ok": True, "status": "reused", **ssh_key._read_record(value)}
        assert value not in json.dumps(result)
    else:
        assert result == {"ok": True, "value": value}
    assert reads == [entry]
    assert receipt["request_selection_version"] == 1 and receipt["state"] == "queued"
    assert list(vault._cache_warm._pending) == [(kpdb, "Allowed", selection)]

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            runtime.tick()
            assert runtime.future is not None
            await settle_owned(runtime.future)
        finally:
            await runtime.close()

    asyncio.run(exercise())
    assert calls == ["Allowed/selected"]


@pytest.mark.parametrize("action", ["identity-key-setup", "ssh-key-prepare", "ssh-key-read"])
def test_key_invalid_selection_and_legacy_requests_preserve_primary_refusal(
    warming_service: CacheFixture, monkeypatch: pytest.MonkeyPatch, action: str,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    monkeypatch.setattr(vault.cli, "read_identity_key", lambda *_args: None)
    request = {"action": action, "entry": "missing", "kpdb": kpdb, "group": ""}
    legacy = vault.handle_request(request)
    assert legacy["ok"] is False and legacy["code"] == "entry_missing"
    assert "cache_warm" not in legacy
    invalid = vault.handle_request({**request, FIELD: {}})
    receipt = invalid.pop("cache_warm")
    assert invalid == legacy
    assert receipt["state"] == "not-queued"
    assert receipt["reason"] == "configuration-unavailable"
    assert receipt["request_selection_version"] == 1
    assert not vault._cache_warm._pending
