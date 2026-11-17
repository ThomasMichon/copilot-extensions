"""Declared targets keep their configuration owner and root-entry identity."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from agent_vault import (
    cache,
    cache_sources,
    cache_targets,
    cache_warm,
    config,
    configuration,
    extensions,
    service,
)
from agent_vault.cache_limits import WorkBudget
from agent_vault.invocation import use_configuration_root
from agent_vault.service_connections import settle_owned

if TYPE_CHECKING:
    from test_service_cache import cached_service as cached_service
else:
    from .test_service_cache import cached_service as cached_service

SelectedFixture = tuple[Path, Path, extensions.ExtensionRegistry]
CacheFixture = tuple[service.VaultService, cache.PersistentCache, str, list[float]]


@pytest.fixture
def target_service(
    cached_service: CacheFixture, monkeypatch: pytest.MonkeyPatch,
) -> CacheFixture:
    monkeypatch.setattr(cache_warm, "get_cache", lambda: cached_service[1])
    return cached_service


async def warm_once(vault: service.VaultService, kpdb: str) -> dict[str, object]:
    runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
    try:
        runtime.tick()
        future = runtime.future
        assert future is not None
        await settle_owned(future)
        return vault._cache_warm.status(kpdb, "Allowed")
    finally:
        await runtime.close()


@pytest.fixture
def selected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SelectedFixture:
    repository, caller = tmp_path / "selected", tmp_path / "foreign"
    repository.mkdir()
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "native.json"))
    monkeypatch.setattr(configuration, "owner_report", lambda _root: None)
    registry = extensions.ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    return repository, caller, registry


def write(root: Path, values: dict[str, object]) -> None:
    (root / ".agent-vault.json").write_text(json.dumps(values), encoding="utf-8")


def test_shared_collection_anchors_manifests_and_preserves_root_namesakes(
    selected: SelectedFixture,
) -> None:
    repository, caller, registry = selected
    write(repository, {
        "cache_entries": [["/Account", "UserName"], "Account"],
        "cache_manifests": ["declared.conf"],
    })
    (repository / "declared.conf").write_text(
        "/Account | username\nGroup/API | password\n", encoding="utf-8",
    )
    (repository / "manual.conf").write_text("/Account | username\n", encoding="utf-8")
    (caller / "manual.conf").write_text("WRONG | password\n", encoding="utf-8")
    write(caller, {"cache_entries": ["WRONG"]})
    registry.register_cache_source(lambda _machine: [("/Account", "UserName"), "Extension"])
    args = SimpleNamespace(entry=["Explicit:username"], manifest="manual.conf", machine=None)

    with use_configuration_root(str(repository)):
        assert cache_targets.command_entries(args) == [
            ("Explicit", "username"), ("/Account", "username"), ("Account", "password"),
            ("Group/API", "password"), ("Extension", "password"),
        ]
        assert Path.cwd() == caller
    assert cache_targets.command_entries(SimpleNamespace()) == [
        ("WRONG", "password"), ("/Account", "username"), ("Extension", "password"),
    ]


def test_absolute_manifest_keeps_its_native_path(
    selected: SelectedFixture, tmp_path: Path,
) -> None:
    repository, _caller, _registry = selected
    manifest = tmp_path / "absolute.conf"
    manifest.write_text("/Native | password\n", encoding="utf-8")
    with use_configuration_root(str(repository)):
        assert cache_targets.command_entries(SimpleNamespace(manifest=str(manifest))) == [
            ("/Native", "password"),
        ]


@pytest.mark.parametrize("policy", [
    {"cache_entries": "not-an-array"},
    {"cache_entries": [["entry", None]]},
    {"cache_entries": [""]},
    {"cache_entries": [{"entry": "not-a-supported-reference"}]},
    {"cache_manifests": [False]},
])
def test_invalid_declaration_refuses_before_extension_targets(
    selected: SelectedFixture, capsys: pytest.CaptureFixture[str], policy: dict[str, object],
) -> None:
    repository, _caller, registry = selected
    write(repository, policy)
    registry.register_cache_source(lambda _machine: pytest.fail("fallback target selected"))
    with use_configuration_root(str(repository)):
        assert cache_targets.command_entries(SimpleNamespace()) is None
    assert "cache targets unavailable" in capsys.readouterr().err


def test_unreadable_declared_manifest_aborts_bounded_collection_before_fallback(
    selected: SelectedFixture,
) -> None:
    repository, _caller, _registry = selected
    write(repository, {"cache_manifests": ["absent.conf"]})
    with use_configuration_root(str(repository)):
        source = cache_targets.configured_source()
    assert source is not None
    with pytest.raises(cache_targets.CacheTargetError, match="manifest"):
        cache_sources.collect(
            [source, lambda _machine: pytest.fail("fallback target selected")], None,
        )


def test_warming_source_is_snapshotted_and_uses_existing_target_budget(
    selected: SelectedFixture,
) -> None:
    repository, _caller, _registry = selected
    write(repository, {"cache_entries": ["/first", "second", "third"]})
    with use_configuration_root(str(repository)):
        source = cache_targets.configured_source()
    write(repository, {"cache_entries": ["different-generation"]})
    assert source is not None
    result = cache_sources.collect([source], None, max_entries=2)
    assert result.entries == [("/first", "password"), ("second", "password")]
    assert result.truncated and result.failed_sources == 0


def test_populate_verify_and_cache_only_reads_agree_on_declared_root_entries(
    selected: SelectedFixture, cached_service: CacheFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_vault import cli, credential_reads

    repository, caller, _registry = selected
    _vault, persistent, _kpdb, _clock = cached_service
    write(repository, {"cache_manifests": ["cache.conf"]})
    (repository / "cache.conf").write_text(
        "/Account | username\n/Account | password\nAccount | password\n", encoding="utf-8",
    )
    monkeypatch.setattr(cache, "get_cache", lambda: persistent)
    monkeypatch.setattr(persistent, "prepare", lambda: {"state": "ready"})
    monkeypatch.setattr(cli, "ensure_service", lambda: True)
    values = {
        ("/Account", "username"): "fixture-root-user",
        ("/Account", "password"): "fixture-root-password",
        ("Account", "password"): "fixture-group-password",
    }

    def read(request: dict[str, Any], **_kwargs: object) -> dict[str, Any]:
        return {
            "ok": True, "value": values[request["entry"], request["field"]], "generation": 1,
        }

    monkeypatch.setattr(cli, "send_command", read)
    prefix = ["--configuration-root", str(repository)]
    assert cli.main([*prefix, "cache-populate"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "ensure_service", lambda: pytest.fail("cache-only path used IPC"))
    assert cli.main([*prefix, "cache-verify", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"present": 3, "total": 3, "missing": []}
    assert cli.main([*prefix, "cache-status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["configured_target_count"] == 3
    for (entry, field), value in values.items():
        result = credential_reads.read_credential(
            SimpleNamespace(entry=entry, field=field, cache_only=True),
        )
        assert (result.code, result.source, result.value) == (0, "cache", value)
    assert Path.cwd() == caller


def test_declared_targets_warm_from_the_captured_repository(
    selected: SelectedFixture, target_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _caller, _registry = selected
    vault, _persistent, kpdb, _clock = target_service
    write(repository, {"cache_entries": ["relative"], "cache_manifests": ["cache.conf"]})
    (repository / "cache.conf").write_text("/Root Account | username\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def read(_database: str, entry: str, field: str) -> str:
        calls.append((entry, field))
        return "synthetic:" + entry

    monkeypatch.setattr(vault.cli, "get_entry", read)
    with use_configuration_root(str(repository)):
        assert vault._cache_warm.enqueue(kpdb, "Allowed").state == "queued"
    result = asyncio.run(warm_once(vault, kpdb))
    assert result["state"] == "complete"
    assert result["target_policy"] == "repository-cache-targets-v1"
    assert (result["selected"], result["cached"]) == (2, 2)
    assert calls == [("Allowed/relative", "password"), ("Root Account", "username")]


def test_unlocks_coalesce_before_policy_discovery_and_resolve_outside_credential_custody(
    selected: SelectedFixture, target_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _caller, _registry = selected
    vault, _persistent, kpdb, _clock = target_service
    write(repository, {"cache_entries": ["entry"]})
    policy_calls: list[bool] = []
    group_calls: list[bool] = []
    original = cache_targets.configured_source

    def source(
        cwd: str | None = None, *, budget: WorkBudget | None = None,
    ) -> Callable[[str | None], Iterator[tuple[str, str]]] | None:
        assert not getattr(vault._credential_lock, "_is_owned")()
        policy_calls.append(True)
        return original(cwd, budget=budget)

    def group_config() -> config.VaultConfig:
        assert not getattr(vault._credential_lock, "_is_owned")()
        group_calls.append(True)
        return config.VaultConfig(group="Allowed")

    monkeypatch.setattr(cache_targets, "configured_source", source)
    monkeypatch.setattr(config, "load_config", group_config)
    with use_configuration_root(str(repository)), vault._credential_lock:
        assert vault._cache_warm.enqueue(kpdb, None).state == "queued"
        assert vault._cache_warm.enqueue(kpdb, None).state == "queued"
        assert vault._cache_warm.enqueue(kpdb, "Allowed").state == "queued"
        assert policy_calls == group_calls == []

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)
        try:
            runtime.tick()
            future = runtime.future
            assert future is not None
            await settle_owned(future)
            assert vault._cache_warm.status(kpdb, None)["state"] == "complete"
            assert vault._cache_warm.enqueue(kpdb, None).state == "complete"
            assert vault._cache_warm.enqueue(kpdb, "Allowed").state == "complete"
            assert policy_calls == group_calls == [True]
            assert len(vault._cache_warm._records) == 1
        finally:
            await runtime.close()

    asyncio.run(exercise())


def test_confirmed_explicit_unlock_invalidates_the_cached_default_group(
    selected: SelectedFixture, target_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _caller, _registry = selected
    vault, _persistent, kpdb, _clock = target_service
    write(repository, {"cache_entries": ["entry"]})
    default_group = ["Allowed"]
    calls: list[str] = []
    monkeypatch.setattr(
        config, "load_config", lambda: config.VaultConfig(group=default_group[0]),
    )

    def read(_database: str, entry: str, _field: str) -> str:
        calls.append(entry)
        return "synthetic:" + entry

    monkeypatch.setattr(vault.cli, "get_entry", read)

    async def exercise() -> None:
        runtime = cache_warm.CacheWarmRuntime(vault, vault._cache_warm, None)

        async def settle() -> None:
            runtime.tick()
            future = runtime.future
            assert future is not None
            await settle_owned(future)

        try:
            with use_configuration_root(str(repository)):
                assert vault._cache_warm.enqueue(kpdb, None).state == "queued"
                await settle()
                assert vault._cache_warm.status(kpdb, None)["state"] == "complete"
                default_group[0] = "Changed"
                assert vault._cache_warm.enqueue(kpdb, "Allowed", confirmed=True).state == "queued"
                await settle()
                assert vault._cache_warm.status(kpdb, None)["reason"] == "context-unavailable"
                assert vault._cache_warm.enqueue(kpdb, None).state == "queued"
                await settle()
                assert vault._cache_warm.status(kpdb, None)["state"] == "complete"
                assert vault._cache_warm.enqueue(kpdb, "Changed").state == "complete"
                assert calls == ["Allowed/entry", "Allowed/entry", "Changed/entry"]
        finally:
            await runtime.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["manual", "warming"])
def test_empty_manifest_entry_refuses_before_extensions_or_credential_reads(
    selected: SelectedFixture, target_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    repository, _caller, _registry = selected
    vault, _persistent, kpdb, _clock = target_service
    write(repository, {"cache_manifests": ["invalid.conf"]})
    (repository / "invalid.conf").write_text("first | password\n | password\n", encoding="utf-8")
    extensions.get_registry().register_cache_source(
        lambda _machine: pytest.fail("invalid manifest reached extension targets"),
    )
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_args: pytest.fail("invalid manifest read a credential"),
    )
    with use_configuration_root(str(repository)):
        if operation == "manual":
            assert cache_targets.command_entries(SimpleNamespace()) is None
            return
        assert vault._cache_warm.enqueue(kpdb, "Allowed").state == "queued"
    result = asyncio.run(warm_once(vault, kpdb))
    assert (result["state"], result["reason"]) == ("failed", "configuration-unavailable")


def test_comment_prefix_consumes_warming_work_but_manual_collection_stays_uncapped(
    selected: SelectedFixture, target_service: CacheFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _caller, _registry = selected
    vault, _persistent, kpdb, _clock = target_service
    write(repository, {"cache_manifests": ["comments.conf"]})
    (repository / "comments.conf").write_text("# prefix\n" * 20 + "entry | password\n", encoding="utf-8")
    extension_calls: list[bool] = []

    def extension(_machine: str | None) -> list[str]:
        extension_calls.append(True)
        return ["extension"]

    extensions.get_registry().register_cache_source(extension)
    monkeypatch.setattr(cache_warm, "MAX_ITEMS", 8)
    monkeypatch.setattr(
        vault.cli, "get_entry", lambda *_args: pytest.fail("unselected credential read"),
    )
    with use_configuration_root(str(repository)):
        assert vault._cache_warm.enqueue(kpdb, "Allowed").state == "queued"
    result = asyncio.run(warm_once(vault, kpdb))
    assert (result["state"], result["selected"], result["truncated"]) == ("partial", 0, True)
    assert extension_calls == []
    with use_configuration_root(str(repository)):
        assert cache_targets.command_entries(SimpleNamespace()) == [
            ("entry", "password"), ("extension", "password"),
        ]
    assert extension_calls == [True]
