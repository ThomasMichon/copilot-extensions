"""Tests for agent-codespaces config-schema migration wiring.

Covers the machine-local adoption-manifest (`adopted-repos.yaml`) versioning:
the loader migrates in memory on read (lazy), save stamps the current schema
version so it round-trips, and the eager `run_migrations` stamps the file
idempotently. The eager on-disk assertions require the vendored `config_migrate`
library to be importable; they skip cleanly when it is not.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_codespaces import config, config_migrations


@pytest.fixture()
def runtime(tmp_path, monkeypatch):
    """Point the adoption manifest + runtime dir at a temp location."""
    manifest = tmp_path / "adopted-repos.yaml"
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(config, "ADOPTED_REPOS_FILE", manifest)
    return manifest


def test_save_stamps_schema_version(runtime):
    config.save_adopted_repos([config.AdoptedRepo(path=Path("/tmp/foo"), adopted_at="now")])
    data = yaml.safe_load(runtime.read_text())
    assert data["schema_version"] == config_migrations.current_version()
    assert Path(data["repos"][0]["path"]) == Path("/tmp/foo")


def test_save_load_round_trip(runtime):
    config.save_adopted_repos([config.AdoptedRepo(path=Path("/tmp/bar"))])
    loaded = config.load_adopted_repos()
    assert len(loaded) == 1
    assert loaded[0].path == Path("/tmp/bar")


def test_load_unmarked_manifest_still_loads(runtime):
    # A pre-versioning manifest (no schema_version) must still load.
    runtime.write_text(yaml.safe_dump({"repos": [{"path": "/tmp/legacy"}]}))
    loaded = config.load_adopted_repos()
    assert [r.path for r in loaded] == [Path("/tmp/legacy")]


def test_load_missing_manifest_is_empty(runtime):
    assert config.load_adopted_repos() == []


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_stamps_file_idempotently(runtime):
    runtime.write_text("# adopted repos\nrepos:\n  - path: /tmp/legacy\n")
    first = config_migrations.run_migrations(runtime)
    assert any(r.changed for r in first)
    text = runtime.read_text()
    assert "# adopted repos" in text  # comment preserved by the textual stamp
    assert f"schema_version: {config_migrations.current_version()}" in text
    # Second run is a no-op.
    second = config_migrations.run_migrations(runtime)
    assert not any(r.changed for r in second)


@pytest.mark.skipif(
    not config_migrations.available(),
    reason="vendored config_migrate library not installed in this env",
)
def test_run_migrations_missing_file_skips(runtime):
    results = config_migrations.run_migrations(runtime)
    assert all(r.skipped for r in results)
