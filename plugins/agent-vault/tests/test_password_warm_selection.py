"""Password transaction receipts retain source without changing internal custody."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_vault import cache_warm, configuration, extensions
from agent_vault.cache_warm_selection import FIELD, WarmSelection
from agent_vault.invocation import use_configuration_root
from agent_vault.service_connections import settle_owned

if TYPE_CHECKING:
    from test_cache_warm import CacheFixture
    from test_cache_warm import warming_service as warming_service
    from test_service_cache import cached_service as cached_service
else:
    from .test_cache_warm import CacheFixture, warming_service as warming_service
    from .test_service_cache import cached_service as cached_service


@pytest.mark.parametrize("outcome", ["success", "backend-refusal", "committed-cache-failure"])
def test_password_provider_unlock_keeps_source_and_original_transaction_outcome(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    vault, persistent, kpdb, _clock = warming_service
    resident, selected = tmp_path / "resident", tmp_path / "selected"
    for root in (resident, selected):
        root.mkdir()
        (root / ".agent-vault.json").write_text(json.dumps({
            "cache_entries": [root.name],
        }), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "native.json"))
    monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    writes: list[tuple[str, str]] = []
    reads: list[str] = []

    def edit(_kpdb: str, entry: str, password: str) -> tuple[bool, str]:
        writes.append((entry, password))
        return (
            (False, "fixture-backend-refusal")
            if outcome == "backend-refusal" else (True, "fixture-updated")
        )

    def read(_kpdb: str, entry: str, _field: str) -> str:
        reads.append(entry)
        return "fixture-value"

    monkeypatch.setattr(vault.cli, "edit_password", edit)
    monkeypatch.setattr(vault.cli, "get_entry", read)
    if outcome == "committed-cache-failure":
        def unavailable(*_args: object, **_kwargs: object) -> int:
            raise RuntimeError("fixture-cache-failure")

        monkeypatch.setattr(vault, "_cache_updated_password", unavailable)
    extensions.get_registry().register_unlock_provider(lambda _context: "fixture-master")
    vault.cli.clear_password(kpdb)
    vault._password_set_at.pop(kpdb)
    selection = WarmSelection(str(selected), False)
    with use_configuration_root(str(resident)):
        health = vault.handle_request({"action": "ping", "health_only": True})
        assert "set-password" in health["cache_warm_selection_actions"]
        result = vault.handle_request({
            "action": "set-password", "entry": "direct", "password": "fixture-new-password",
            "kpdb": kpdb, "group": "Allowed", FIELD: selection.describe(),
        })
    receipt = result.pop("cache_warm")
    assert receipt["request_selection_version"] == 1 and receipt["state"] == "queued"
    assert list(vault._cache_warm._pending) == [(kpdb, "Allowed", selection)]
    assert writes == [("Allowed/direct", "fixture-new-password")]
    assert reads == []
    if outcome == "backend-refusal":
        assert result == {"ok": False, "error": "fixture-backend-refusal"}
        assert persistent.pending_replace("Allowed/direct", "password") is None
    else:
        assert type(result.pop("generation")) is int
        if outcome == "committed-cache-failure":
            assert result == {
                "ok": False, "committed": True, "cache_updated": False,
                "error": "fixture-cache-failure",
            }
            assert persistent.pending_replace("Allowed/direct", "password") is not None
            return
        assert result == {"ok": True, "message": "fixture-updated"}
        assert persistent.pending_replace("Allowed/direct", "password") is None

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            runtime.tick()
            assert runtime.future is not None
            await settle_owned(runtime.future)
        finally:
            await runtime.close()

    asyncio.run(exercise())
    assert reads == ["Allowed/selected"]
