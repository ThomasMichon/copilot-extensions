"""Tests for agent-logger config-schema migration wiring.

Covers the machine-local ``config.yaml`` versioning: the loader migrates in
memory on read (lazy) and still resolves a still-old config, and the eager
``run_migrations`` stamps the machine-local file idempotently. The eager on-disk
assertions require the vendored ``config_migrate`` library and skip cleanly when
it is absent.
"""

from __future__ import annotations

import pytest

from agent_logger import config_migrations
from agent_logger.config import load_config


def test_load_config_migrates_in_memory(tmp_path, monkeypatch):
    home = tmp_path / ".agent-logger"
    home.mkdir()
    (home / "config.yaml").write_text("log:\n  voice_pack: neutral\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(home))
    cfg = load_config()
    assert cfg.voice_pack == "neutral"
    # Lazy migration never persists: the on-disk file is untouched by a read.
    assert "schema_version" not in (home / "config.yaml").read_text(encoding="utf-8")


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_stamps_file_idempotently(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("# logger config\nlog:\n  voice_pack: neutral\n", encoding="utf-8")

    first = config_migrations.run_migrations(cfg_file)
    assert any(r.changed for r in first)
    text = cfg_file.read_text(encoding="utf-8")
    assert "# logger config" in text  # comment preserved by the textual stamp
    assert f"schema_version: {config_migrations.current_version()}" in text

    second = config_migrations.run_migrations(cfg_file)
    assert not any(r.changed for r in second)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_missing_file_skips(tmp_path):
    results = config_migrations.run_migrations(tmp_path / "config.yaml")
    assert all(r.skipped for r in results)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_migrate_loaded_stamps_dict():
    out = config_migrations.migrate_loaded({"log": {"voice_pack": "neutral"}})
    assert out["schema_version"] == config_migrations.current_version()
