"""Installed inventory selects the registered catalog among same-named cache clones."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from plugin_resolve.inventory import installed_item

SOURCE = "helper@fixture-market"
REGISTRATION = {"source": {"source": "github", "repo": "example-owner/fixture-market"}}


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def _catalog(cache: Path, directory: str, origin: str, *, declares_helper: bool = True) -> Path:
    root = cache / "marketplaces" / directory
    _write(root / ".claude-plugin" / "marketplace.json", {
        "name": "fixture-market", "owner": {"name": "Example"},
        "plugins": [{"name": "helper", "source": "helper"}] if declares_helper else [],
    })
    _write(root / "helper" / "plugin.json", {"name": "helper", "version": "1.0.0"})
    _write(root / ".git" / "config", f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {origin}\n')
    return root


@pytest.fixture
def inventory(tmp_path: Path) -> tuple[Path, Path]:
    home, cache = tmp_path / "home", tmp_path / "cache"
    _write(home / "settings.json", {"extraKnownMarketplaces": {"fixture-market": REGISTRATION}})
    _write(home / "installed-plugins" / "fixture-market" / "helper" / "plugin.json",
           {"name": "helper", "version": "1.0.0"})
    return home, cache


def test_native_slug_wins_over_stale_same_origin_catalog(
    inventory: tuple[Path, Path], caplog: pytest.LogCaptureFixture,
) -> None:
    home, cache = inventory
    origin = "https://github.com/example-owner/fixture-market.git"
    _catalog(cache, "example-owner--fixture-market", origin)
    native = _catalog(cache, "example-owner-fixture-market", origin)
    with caplog.at_level(logging.WARNING, logger="plugin-resolve"):
        item = installed_item(SOURCE, home, cache)
    assert item["inventory"]["catalog_root"] == str(native.resolve())
    assert "stale_marketplace_cache" in caplog.text
    assert "example-owner--fixture-market" in caplog.text


def test_registered_origin_wins_over_foreign_same_named_catalog(inventory: tuple[Path, Path]) -> None:
    home, cache = inventory
    _catalog(cache, "a-foreign-copy", "https://example.invalid/someone-else/fixture-market.git")
    registered = _catalog(cache, "z-registered", "git@github.com:example-owner/fixture-market.git")
    item = installed_item(SOURCE, home, cache)
    assert item["inventory"]["catalog_root"] == str(registered.resolve())


def test_unattributable_same_named_catalogs_still_refuse(inventory: tuple[Path, Path]) -> None:
    home, cache = inventory
    origin = "https://github.com/example-owner/fixture-market.git"
    _catalog(cache, "copy-one", origin)
    _catalog(cache, "copy-two", origin)
    with pytest.raises(ValueError, match=r"catalog is ambiguous: .*copy-one, copy-two"):
        installed_item(SOURCE, home, cache)


def test_stale_catalog_predating_the_plugin_does_not_block_selection(
    inventory: tuple[Path, Path],
) -> None:
    home, cache = inventory
    origin = "https://github.com/example-owner/fixture-market.git"
    _catalog(cache, "example-owner--fixture-market", origin, declares_helper=False)
    native = _catalog(cache, "example-owner-fixture-market", origin)
    item = installed_item(SOURCE, home, cache)
    assert item["inventory"]["catalog_root"] == str(native.resolve())


def test_github_registration_identity_matches_case_insensitively(
    inventory: tuple[Path, Path],
) -> None:
    home, cache = inventory
    _write(home / "settings.json", {"extraKnownMarketplaces": {"fixture-market": {
        "source": {"source": "github", "repo": "Example-Owner/Fixture-Market"},
    }}})
    origin = "https://github.com/example-owner/fixture-market.git"
    _catalog(cache, "example-owner--fixture-market", origin)
    native = _catalog(cache, "Example-Owner-Fixture-Market", origin)
    item = installed_item(SOURCE, home, cache)
    assert item["inventory"]["catalog_root"] == str(native.resolve())
