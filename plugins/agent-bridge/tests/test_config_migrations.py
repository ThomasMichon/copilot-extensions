"""Tests for agent-bridge config-schema migration wiring.

Covers: the ``schema_version`` field round-trips through save/load, the loader
migrates a still-old (unmarked) config in memory on read, and the eager
``run_migrations`` stamps the machine-local config.yaml idempotently. The eager
on-disk assertions require the vendored ``config_migrate`` library and skip
cleanly when it is absent.
"""

from __future__ import annotations

import pytest

from agent_bridge import config_migrations
from agent_bridge.config import config_dir, load_config, save_config
from agent_bridge.models import ServiceConfig


@pytest.fixture()
def config_home(tmp_path, monkeypatch):
    d = tmp_path / ".agent-bridge"
    d.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(d))
    return d


def test_schema_version_default_matches_module():
    assert ServiceConfig().schema_version == config_migrations.current_version()


def test_schema_version_round_trips_through_save_load(config_home):
    save_config(ServiceConfig(port=9999))
    text = (config_home / "config.yaml").read_text()
    assert f"schema_version: {config_migrations.current_version()}" in text
    # Reload preserves it.
    assert load_config().schema_version == config_migrations.current_version()


def test_load_unmarked_config_gets_current_version(config_home):
    # A pre-versioning config.yaml (no schema_version) still loads, and the
    # lazy migrate + field default resolve it to the current version.
    (config_home / "config.yaml").write_text("port: 8123\n")
    cfg = load_config()
    assert cfg.port == 8123
    assert cfg.schema_version == config_migrations.current_version()


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_stamps_file_idempotently(config_home):
    cfg_file = config_home / "config.yaml"
    cfg_file.write_text("# bridge config\nport: 8123\n")

    first = config_migrations.run_migrations(cfg_file)
    assert any(r.changed for r in first)
    text = cfg_file.read_text()
    assert "# bridge config" in text  # comment preserved by the textual stamp
    assert f"schema_version: {config_migrations.current_version()}" in text

    second = config_migrations.run_migrations(cfg_file)
    assert not any(r.changed for r in second)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_defaults_to_config_dir(config_home):
    (config_home / "config.yaml").write_text("port: 8123\n")
    results = config_migrations.run_migrations()  # no arg -> config_dir()/config.yaml
    assert any(r.changed for r in results)
    assert config_dir() == config_home
