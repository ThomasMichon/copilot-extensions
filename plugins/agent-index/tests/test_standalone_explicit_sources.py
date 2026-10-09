"""Opt-in standalone source isolation; the legacy default remains unchanged."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

from agent_index import config


def _inline(monkeypatch, sources):
    monkeypatch.setenv(config.CONFIG_DATA_ENV, base64.urlsafe_b64encode(json.dumps({
        "corpus": {"sources": sources},
    }).encode()).decode())


def test_explicit_sources_never_resolve_repo_or_registry(monkeypatch, tmp_path):
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    sources = [{"name": "git:fixture", "_repo_path": str(tmp_path)}]
    _inline(monkeypatch, sources)
    monkeypatch.setattr(config, "_local_project_roots", lambda: pytest.fail("registry scan"))
    monkeypatch.setattr(config, "_agent_worktrees_home", lambda: pytest.fail("registry read"))
    monkeypatch.setattr(config, "_load_yaml", lambda p: pytest.fail("ambient config read"))
    assert config.repo_root(str(tmp_path)) is None
    assert config.repo_checkout_path("unselected") is None
    assert config.read_corpus_sources() == sources


@pytest.mark.parametrize("sources", [[], None, ["bad"], [{}]])
def test_explicit_sources_must_not_fall_back(monkeypatch, sources):
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    _inline(monkeypatch, sources)
    with pytest.raises(ValueError, match="nonempty"):
        config.read_corpus_sources()


def test_invalid_inline_does_not_scan(monkeypatch):
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    monkeypatch.setenv(config.CONFIG_DATA_ENV, "broken-base64")
    monkeypatch.setattr(config, "_local_project_roots", lambda: pytest.fail("registry scan"))
    with pytest.raises(ValueError):
        config.read_corpus_sources()


def test_legacy_graft_remains_default(monkeypatch, tmp_path):
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.delenv(config.CONFIG_DATA_ENV, raising=False)
    monkeypatch.delenv(config.EFFECTIVE_CONFIG_ENV, raising=False)
    monkeypatch.setattr(config, "repo_root", lambda: None)
    monkeypatch.setattr(config, "_local_project_roots", lambda: {"fixture": tmp_path})
    monkeypatch.setattr(config, "_load_effective_repo_config", lambda root: {
        "corpus": {"sources": [{"name": "git:legacy"}]},
    } if root is not None else {})
    monkeypatch.setattr(config, "_load_yaml", lambda path: {})
    assert config.read_corpus_sources() == [{
        "name": "git:legacy", "_repo_path": str(tmp_path), "_contributed_by": "fixture",
    }]


def test_explicit_runtime_never_dispatches_to_legacy_server_venv(monkeypatch):
    monkeypatch.setenv(config.SOURCE_MODE_ENV, "explicit")
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, sys.executable)
    assert config.server_venv_python() is None


def test_legacy_server_venv_override_is_unchanged(monkeypatch):
    monkeypatch.delenv(config.SOURCE_MODE_ENV, raising=False)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, sys.executable)
    assert config.server_venv_python() == Path(sys.executable)
