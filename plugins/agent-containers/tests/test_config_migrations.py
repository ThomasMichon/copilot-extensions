"""Tests for agent-containers config-schema migration wiring.

Covers the machine-local ``containers.yaml`` versioning: the loader migrates in
memory on read (lazy) and still parses a still-old config, and the eager
``run_migrations`` stamps the machine-local file idempotently while leaving a
repo/cwd copy on disk untouched. The eager on-disk assertions require the
vendored ``config_migrate`` library and skip cleanly when it is absent.
"""

from __future__ import annotations

import pytest

from agent_containers import config, config_migrations


def test_load_config_migrates_in_memory(tmp_path, monkeypatch):
    cfg_file = tmp_path / "containers.yaml"
    cfg_file.write_text("exec_user: alice\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg_file))
    loaded = config.load_config()
    assert loaded.exec_user == "alice"
    # Lazy migration never persists: the on-disk file is unchanged by a read.
    assert "schema_version" not in cfg_file.read_text(encoding="utf-8")


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_stamps_machine_local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)
    runtime_cfg = tmp_path / "containers.yaml"
    runtime_cfg.write_text("# fleet config\nexec_user: bob\n", encoding="utf-8")

    first = config_migrations.run_migrations()
    assert any(r.changed for r in first)
    text = runtime_cfg.read_text(encoding="utf-8")
    assert "# fleet config" in text  # comment preserved by the textual stamp
    assert f"schema_version: {config_migrations.current_version()}" in text
    assert "exec_user: bob" in text

    # Second run is a no-op.
    second = config_migrations.run_migrations()
    assert not any(r.changed for r in second)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_missing_file_skips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)
    results = config_migrations.run_migrations()
    assert all(r.skipped for r in results)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_migrate_loaded_stamps_dict():
    out = config_migrations.migrate_loaded({"exec_user": "carol"})
    assert out["schema_version"] == config_migrations.current_version()
    assert out["exec_user"] == "carol"
