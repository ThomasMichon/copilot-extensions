"""Queued native-default warming retains its source identity until execution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agent_vault import configuration, configuration_scope, extensions
from agent_vault.cache_warm_selection import CONTRACT, FIELD, WarmSelection
from agent_vault.invocation import use_configuration_root

if TYPE_CHECKING:
    from test_cache_warm import CacheFixture, drain
    from test_cache_warm import warming_service as warming_service
    from test_default_owner import Owners
    from test_default_owner import owners as owners
    from test_service_cache import cached_service as cached_service
else:
    from .test_cache_warm import CacheFixture, drain
    from .test_cache_warm import warming_service as warming_service
    from .test_default_owner import Owners
    from .test_default_owner import owners as owners
    from .test_service_cache import cached_service as cached_service


@pytest.mark.parametrize("selection", ["native-default", "explicit-root", "request-native-default"])
@pytest.mark.parametrize("replacement", ["removed", "protected-field"])
def test_queued_warm_revalidates_only_its_native_default(
    warming_service: CacheFixture,
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
    replacement: str,
) -> None:
    _home, base, project, _native = owners
    vault, _persistent, kpdb, _clock = warming_service
    configuration_scope.bind_service_configuration(str(base), native_default=True)
    peer_calls: list[Path] = []

    def no_peer(root: Path, *, native_default: bool = False) -> None:
        peer_calls.append(root)

    monkeypatch.setattr(configuration, "owner_report", no_peer)
    reads: list[str] = []

    def get_entry(_database: str, entry: str, _field: str) -> str:
        reads.append(entry)
        return "fixture-value"

    monkeypatch.setattr(vault.cli, "get_entry", get_entry)
    extensions.get_registry().register_cache_source(lambda _machine: ["entry"])
    if selection == "request-native-default":
        accepted = vault.handle_request({
            "action": "unlock", "kpdb": kpdb, "group": "Allowed",
            FIELD: {"contract": CONTRACT, "version": 1, "root": str(base), "native_default": True},
        })["cache_warm"]
        assert accepted["state"] == "queued"
    elif selection == "explicit-root":
        with use_configuration_root(str(project)):
            queued = vault._cache_warm.enqueue(kpdb, "Allowed")
        assert queued.state == "queued"
    else:
        queued = vault._cache_warm.enqueue(kpdb, "Allowed")
        assert queued.state == "queued"
    scope = WarmSelection(str(base), True) if selection == "request-native-default" else None
    request = vault._cache_warm._pending[(kpdb, "Allowed", scope)]
    assert request.native_default is (selection != "explicit-root")
    configuration_scope.bind_service_configuration(str(project))
    (base.parent / ".agent-vault.json").write_text(
        '{"enabled":true,"vault":"second","cache_entries":["wrong-ancestor"]}',
        encoding="utf-8",
    )
    selected_file = base / ".agent-vault.json"
    if replacement == "removed":
        selected_file.unlink()
    else:
        selected_file.write_text(json.dumps({
            "enabled": True, "vault": "first", "kpdb": "unexpected-database",
            "cache_entries": ["wrong-replacement"],
        }), encoding="utf-8")
    status = asyncio.run(drain(vault, kpdb))
    if scope is not None:
        status = vault._cache_warm.status(kpdb, "Allowed", selection=scope)
    if selection != "explicit-root":
        assert status["state"] == "failed"
        assert status["reason"] == "configuration-unavailable"
        assert reads == [] and peer_calls == []
    else:
        assert status["state"] == "complete"
        assert reads == ["Allowed/entry"]
