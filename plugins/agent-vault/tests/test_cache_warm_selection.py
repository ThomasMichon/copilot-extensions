"""Provider expansion keeps request-scoped warming separate from legacy unlocks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_vault import cache_warm, cache_warm_selection, config, configuration, extensions
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


def selection(root: Path, *, native_default: bool = False) -> dict[str, object]:
    return {
        "contract": cache_warm_selection.CONTRACT,
        "version": 1,
        "root": str(root),
        "native_default": native_default,
    }


@pytest.mark.parametrize("unlock", ["provider", "password", "prompt"])
@pytest.mark.parametrize("group", ["Allowed", None])
def test_request_policy_survives_queue_and_same_database_sources_do_not_coalesce(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    unlock: str, group: str | None,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    resident, first, second = (tmp_path / name for name in ("resident", "first", "second"))
    for root in (resident, first, second):
        root.mkdir()
        (root / ".agent-vault.json").write_text(json.dumps({
            "cache_entries": [root.name],
        }), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "native.json"))
    monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    calls: list[str] = []
    monkeypatch.setattr(
        vault.cli, "get_entry", recording_reader(calls, "fixture"),
    )
    extensions.get_registry().register_cache_source(
        lambda _machine: ["extension-" + Path(
            config.configuration_root(),
        ).name],
    )
    vault.cli.clear_password(kpdb)
    vault._password_set_at.pop(kpdb)
    request: dict[str, object] = {"action": "unlock", "kpdb": kpdb, "group": group}
    if unlock == "password":
        request["password"] = "fixture-master"
    elif unlock == "prompt":
        request["prompt"] = True
        monkeypatch.setattr("agent_vault.service.prompt_password", lambda *_args: "fixture-master")
    else:
        extensions.get_registry().register_unlock_provider(lambda _context: "fixture-master")
    with use_configuration_root(str(resident)):
        result = vault.handle_request({**request, FIELD: selection(first)})
        assert result["ok"] and result["cache_warm"]["state"] == "queued"
        assert vault.handle_request({
            "action": "unlock", "kpdb": kpdb, "group": group, FIELD: selection(second),
        })["ok"]
        first_request = vault._cache_warm._pending[kpdb, group, WarmSelection(str(first), False)]
        deadline = first_request.deadline
        assert vault.handle_request({
            "action": "unlock", "kpdb": kpdb, "group": group, FIELD: selection(first),
        })["ok"]
        assert first_request.deadline == deadline
    assert len(vault._cache_warm._pending) == 2 and calls == []

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            for root in (first, second):
                runtime.tick()
                assert runtime.future is not None
                await settle_owned(runtime.future)
                response = vault.handle_request({
                    "action": "ping", "kpdb": kpdb, "group": group, FIELD: selection(root),
                })
                assert response["cache_warm_selection_versions"] == [1]
                assert response["cache_warm"]["state"] == "complete"
                assert response["cache_warm"]["cached"] == 2
                assert vault.handle_request({
                    "action": "unlock", "kpdb": kpdb, "group": group, FIELD: selection(root),
                })["cache_warm"]["state"] == "complete"
                assert not any(
                    request.selection == WarmSelection(str(root), False)
                    for request in vault._cache_warm._pending.values()
                )
            assert vault._cache_warm.status(kpdb, "Allowed")["state"] == "idle"
        finally:
            await runtime.close()

    asyncio.run(exercise())
    assert calls == ["Allowed/first", "Allowed/extension-first",
                     "Allowed/second", "Allowed/extension-second"]
    assert vault._password_set_at[kpdb] == 1000.0


@pytest.mark.parametrize("bad", [
    None, {}, {"version": 2}, {"root": "relative"}, {"native_default": 1},
    {"root": "x" * 4097}, {"root": "bad\nroot"},
])
def test_invalid_selection_never_falls_back_to_resident_policy_or_fails_unlock(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    bad: object,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    monkeypatch.setattr(vault.cli, "verify_password", lambda *_args: True)
    raw = {**selection(tmp_path), **bad} if isinstance(bad, dict) and bad else bad
    result = vault.handle_request({
        "action": "unlock", "kpdb": kpdb, "password": "fixture-master", FIELD: raw,
    })
    assert result["ok"] and vault.cli.has_password(kpdb)
    assert result["cache_warm"]["state"] == "not-queued"
    assert result["cache_warm"]["reason"] == "configuration-unavailable"
    assert not vault._cache_warm._pending
    # A legacy caller still gets its original response and baseline behavior.
    assert vault.handle_request({"action": "unlock", "kpdb": kpdb}) == {"ok": True}
    assert len(vault._cache_warm._pending) == 1


@pytest.mark.parametrize("change", ["removed-root", "invalid-policy", "false-native-default"])
def test_worker_revalidates_request_selection_before_any_credential_read(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    selected = tmp_path / "selected"
    selected.mkdir()
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "native.json"))
    monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    request = {
        "action": "unlock", "kpdb": kpdb, "group": "Allowed",
        FIELD: selection(selected, native_default=change == "false-native-default"),
    }
    assert vault.handle_request(request)["cache_warm"]["state"] == "queued"
    if change == "removed-root":
        selected.rmdir()
    elif change == "invalid-policy":
        (selected / ".agent-vault.json").write_text('{"cache_entries":false}', encoding="utf-8")
    monkeypatch.setattr(vault.cli, "get_entry", lambda *_args: pytest.fail("read invalid policy"))

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            runtime.tick()
            assert runtime.future is not None
            await settle_owned(runtime.future)
            status = vault.handle_request({**request, "action": "ping"})["cache_warm"]
            assert status["state"] == "failed"
            assert status["reason"] == "configuration-unavailable"
            assert status["cached"] == 0
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_source_contexts_share_capacity_and_evicted_default_groups_remain_bounded(
    warming_service: CacheFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    vault, _persistent, kpdb, _clock = warming_service
    monkeypatch.setattr(cache_warm, "MAX_CONTEXTS", 1)
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    roots = [tmp_path / str(index) for index in range(3)]
    for root in roots:
        root.mkdir()

    def unlock(root: Path) -> dict[str, object]:
        warmed = vault.handle_request({
            "action": "unlock", "kpdb": kpdb, FIELD: selection(root),
        })["cache_warm"]
        assert isinstance(warmed, dict)
        return warmed

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            for index, root in enumerate(roots):
                assert unlock(root)["state"] == "queued"
                if index + 1 < len(roots):
                    refused = unlock(roots[index + 1])
                    assert refused["state"] == "not-queued" and refused["reason"] == "capacity"
                runtime.tick()
                assert runtime.future is not None
                await settle_owned(runtime.future)
                assert len(vault._cache_warm._default_groups) == 1
                assert len(vault._cache_warm._records) == 1
        finally:
            await runtime.close()

    asyncio.run(exercise())
