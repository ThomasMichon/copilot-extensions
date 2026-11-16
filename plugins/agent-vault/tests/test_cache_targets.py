"""Declared targets keep their configuration owner and root-entry identity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_vault import cache, cache_sources, cache_targets, configuration, extensions, service
from agent_vault.invocation import use_configuration_root

from .test_service_cache import cached_service as cached_service

SelectedFixture = tuple[Path, Path, extensions.ExtensionRegistry]
CacheFixture = tuple[service.VaultService, cache.PersistentCache, str, list[float]]


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

    def read(request: dict, **_kwargs: object) -> dict:
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
