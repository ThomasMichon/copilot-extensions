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
    # A real v1->v2 transform runs, so the file is reserialized with the marker
    # (the leading comment is not preserved across a shape migration). The custom
    # (non-legacy) port is preserved by the v1->v2 migrator.
    assert f"schema_version: {config_migrations.current_version()}" in text
    assert "port: 8123" in text

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


def test_v1_to_v2_unpins_legacy_default_port():
    # The v1->v2 migrator drops a legacy 9280 (host) / 9281 (WSL) pin so the
    # daemon binds dynamic; the key is removed entirely (unset sentinel).
    m = config_migrations._v1_to_v2_unpin_legacy_default_port
    assert "port" not in m({"port": 9280, "bind": "127.0.0.1"})
    assert "port" not in m({"port": 9281})
    # String-typed legacy value is also unpinned (defensive).
    assert "port" not in m({"port": "9280"})


def test_v1_to_v2_preserves_custom_and_dynamic_port():
    # A deliberately customised port is preserved; the dynamic sentinel is a no-op.
    m = config_migrations._v1_to_v2_unpin_legacy_default_port
    assert m({"port": 9999})["port"] == 9999
    assert m({"port": 0})["port"] == 0
    assert m({"bind": "127.0.0.1"}) == {"bind": "127.0.0.1"}


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_unpins_legacy_port_on_disk(config_home):
    # A legacy pinned config.yaml migrates to the dynamic (unset) default on disk.
    cfg_file = config_home / "config.yaml"
    cfg_file.write_text("port: 9280\nbind: 127.0.0.1\n")

    results = config_migrations.run_migrations(cfg_file)
    assert any(r.changed for r in results)

    cfg = load_config()
    assert cfg.port == 0  # unpinned -> dynamic ephemeral bind
    assert cfg.schema_version == config_migrations.current_version()
