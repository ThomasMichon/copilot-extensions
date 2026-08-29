"""Tests for client routing to the designated indexer (effort ...-engine-daemon, P8).

Adoption records a client's endpoint (to the designated indexer) into machine-local
config; client_url() resolves it. Search degrades lexical-first when the engine is
unreachable (vision §local-first-standalone, §responsive-when-cold).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_index import config
from agent_index.__main__ import cmd_setup


@pytest.fixture(autouse=True)
def _iso(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_INDEX_ROLE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_ENDPOINT", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "boxA")
    monkeypatch.setattr(
        config,
        "repo_root",
        lambda explicit=None: Path(explicit).resolve() if explicit else None,
    )
    from agent_index import capability

    monkeypatch.setattr(
        capability, "detect", lambda: {"cores": 16, "ram_gb": 64.0, "cuda": False}
    )
    return tmp_path


def _args(**kw):
    base = dict(indexer=None, single=False, ssh=None, endpoint=None, repo=None,
                force=False, yes=True, json=True)
    base.update(kw)
    return argparse.Namespace(**base)


# -- endpoint resolution -----------------------------------------------------


def test_configured_endpoint_none_by_default(_iso):
    assert config.configured_endpoint() is None


def test_client_url_prefers_configured_endpoint(_iso, monkeypatch):
    config.set_machine_config({"role": "client", "endpoint": "http://indexer:8420"})
    # even if a local routing url existed, the configured client endpoint wins
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:9999")
    assert config.client_url() == "http://indexer:8420"


def test_client_url_reads_endpoint_from_current_repo(_iso, monkeypatch):
    root = _iso / "repo"
    root.mkdir()
    monkeypatch.setattr(config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(config, "machine_id", lambda: "client")
    config.write_indexer_designation(
        root,
        "host",
        endpoint="http://indexer:8420",
    )

    assert config.client_url() == "http://indexer:8420"


def test_client_local_endpoint_overrides_repo_endpoint(_iso, monkeypatch):
    root = _iso / "repo"
    root.mkdir()
    monkeypatch.setattr(config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(config, "machine_id", lambda: "client")
    config.write_indexer_designation(
        root,
        "host",
        endpoint="http://shared-endpoint:8420",
    )
    config.set_machine_config(
        {"role": "client", "endpoint": "http://local-forward:18420"}
    )

    assert config.client_url() == "http://local-forward:18420"


def test_host_falls_through_to_local(_iso, monkeypatch):
    # No configured endpoint (a host) -> local routing is used.
    config.set_machine_config({"role": "host"})
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:8420")
    assert config.client_url() == "http://127.0.0.1:8420"


def test_unconfigured_ignores_stale_local_routing(_iso, monkeypatch):
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:8420")
    assert config.client_url() is None


def test_unconfigured_repo_ignores_machine_host_role(_iso, monkeypatch):
    root = _iso / "repo"
    root.mkdir()
    config.set_machine_config({"role": "host"})
    monkeypatch.setattr(config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:8420")

    assert config.client_url() is None


def test_host_ignores_stray_configured_endpoint(_iso, monkeypatch):
    # A host with a stale machine-local ``endpoint`` (e.g. a fixed 127.0.0.1:8420
    # left by an old setup) must NOT let it shadow the live zdd routing port, which
    # changes every zero-downtime generation. Regression for #1349 (status/stop
    # probing a dead static port and reporting the running service as down).
    config.set_machine_config({"role": "host", "endpoint": "http://127.0.0.1:8420"})
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:65019")
    assert config.client_url() == "http://127.0.0.1:65019"


# -- setup client routing ----------------------------------------------------


def test_client_setup_records_explicit_endpoint(_iso, monkeypatch):
    repo = _iso / "repo"; repo.mkdir()
    cmd_setup(_args(indexer="boxB", ssh="boxB-wsl", endpoint="http://127.0.0.1:8420",
                    repo=str(repo)))
    monkeypatch.setattr(config, "repo_root", lambda explicit=None: repo)
    assert config.resolve_role() == "client"
    assert config.configured_endpoint() == "http://127.0.0.1:8420"
    assert config.client_url() == "http://127.0.0.1:8420"


def test_client_setup_inherits_repo_endpoint(_iso):
    repo = _iso / "repo"; repo.mkdir()
    # Host designation (from boxB) publishes the shared endpoint into the repo.
    config.write_indexer_designation(repo, "boxB", ssh="boxB-wsl",
                                     endpoint="http://127.0.0.1:8420")
    # A plain client run with no --endpoint inherits it from the repo config.
    cmd_setup(_args(indexer="boxB", repo=str(repo)))
    assert config.configured_endpoint() == "http://127.0.0.1:8420"


def test_host_setup_records_no_client_endpoint(_iso):
    repo = _iso / "repo"; repo.mkdir()
    cmd_setup(_args(single=True, repo=str(repo)))
    assert config.resolve_role() == "host"
    assert config.configured_endpoint() is None  # a host uses its local service


# -- lexical-first degrade ---------------------------------------------------


class _DownEngine:
    def embed_query(self, q):
        from agent_index.engine.client import EngineUnavailableError

        raise EngineUnavailableError("engine down")


class _FakeStore:
    def __init__(self):
        self.fts_called_with = None

    def fts_search(self, query, *, limit=10, **kw):
        self.fts_called_with = (query, limit, kw)
        return ["lexical-hit-1", "lexical-hit-2"]

    def search_all(self, *a, **k):  # pragma: no cover - not reached when engine down
        raise AssertionError("search_all must not run when the engine is down")


def test_search_degrades_to_lexical_when_engine_down():
    from agent_index.search.engine import SearchEngine

    store = _FakeStore()
    eng = SearchEngine({"code": _DownEngine()}, store)
    hits = eng.search("def main", limit=5)
    assert hits == ["lexical-hit-1", "lexical-hit-2"]
    assert store.fts_called_with[0] == "def main"
    assert store.fts_called_with[1] == 5
