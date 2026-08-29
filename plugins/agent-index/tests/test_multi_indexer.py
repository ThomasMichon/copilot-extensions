"""Tests for ordered multi-indexer designation + client failover routing.

A multi-indexer deployment declares a plural ``indexers:`` list (primary first) in
the repo config; each machine gets its own ssh alias + endpoint. A machine that is
any listed indexer resolves to ``host``; every other machine is a ``client`` that
routes to the ordered endpoints and uses the **first reachable** one, so a down
primary transparently fails over to a secondary
(vision §adoption-designates-ordered-indexers; SSH-mesh robustness).
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
    monkeypatch.delenv("AGENT_INDEX_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_INDEX_REPO", raising=False)
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


def _author_indexers(repo, indexers):
    config.write_indexers_designation(repo, indexers)


# -- read_indexers / write_indexers_designation ------------------------------


def test_read_indexers_plural_ordered(_iso):
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [
        {"machine": "boxA", "ssh": "boxA-ssh", "endpoint": "http://127.0.0.1:8420"},
        {"machine": "boxB", "ssh": "boxB-ssh", "endpoint": "http://127.0.0.1:8421"},
    ])
    got = config.read_indexers(repo)
    assert [i["machine"] for i in got] == ["boxA", "boxB"]  # order preserved (primary first)
    assert got[0]["endpoint"] == "http://127.0.0.1:8420"


def test_read_indexer_returns_primary_for_plural(_iso):
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [{"machine": "boxA"}, {"machine": "boxB"}])
    # Back-compat singular accessor returns the primary (first) indexer.
    assert config.read_indexer(repo)["machine"] == "boxA"


def test_write_indexers_removes_singular_key(_iso):
    repo = _iso / "repo"; repo.mkdir()
    config.write_indexer_designation(repo, "old")
    _author_indexers(repo, [{"machine": "boxA"}, {"machine": "boxB"}])
    data = config._load_yaml(config.repo_config_path(repo))
    assert "indexer" not in data
    assert [i["machine"] for i in data["indexers"]] == ["boxA", "boxB"]


def test_write_indexers_drops_machineless_entries(_iso):
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [{"machine": "boxA"}, {"ssh": "no-machine"}, {"machine": "boxB"}])
    assert [i["machine"] for i in config.read_indexers(repo)] == ["boxA", "boxB"]


# -- configured_endpoints (machine-local) ------------------------------------


def test_configured_endpoints_plural(_iso):
    config.set_machine_config({"endpoints": ["http://p:8420", "http://s:8421"]})
    assert config.configured_endpoints() == ["http://p:8420", "http://s:8421"]


def test_configured_endpoints_falls_back_to_singular(_iso):
    config.set_machine_config({"endpoint": "http://only:8420"})
    assert config.configured_endpoints() == ["http://only:8420"]


def test_configured_endpoints_empty(_iso):
    assert config.configured_endpoints() == []


# -- client_url ordered failover ---------------------------------------------


def test_client_url_uses_primary_when_reachable(_iso, monkeypatch):
    config.set_machine_config({"role": "client",
                               "endpoints": ["http://p:8420", "http://s:8421"]})
    monkeypatch.setattr(config, "_endpoint_healthy", lambda url, t: True)
    assert config.client_url() == "http://p:8420"


def test_client_url_fails_over_to_secondary(_iso, monkeypatch):
    config.set_machine_config({"role": "client",
                               "endpoints": ["http://p:8420", "http://s:8421"]})
    # Primary down, secondary up -> route to the secondary.
    monkeypatch.setattr(config, "_endpoint_healthy",
                        lambda url, t: url == "http://s:8421")
    assert config.client_url() == "http://s:8421"


def test_client_url_all_down_returns_primary(_iso, monkeypatch):
    config.set_machine_config({"role": "client",
                               "endpoints": ["http://p:8420", "http://s:8421"]})
    monkeypatch.setattr(config, "_endpoint_healthy", lambda url, t: False)
    # None reachable -> deterministic primary so the caller surfaces its error.
    assert config.client_url() == "http://p:8420"


def test_client_url_single_endpoint_skips_probe(_iso, monkeypatch):
    # A single configured endpoint must NOT be health-probed (back-compat): it is
    # returned as-is even if unreachable.
    config.set_machine_config({"role": "client", "endpoints": ["http://only:8420"]})

    def _boom(url, t):  # pragma: no cover - must not be called
        raise AssertionError("single-endpoint path must not probe")

    monkeypatch.setattr(config, "_endpoint_healthy", _boom)
    assert config.client_url() == "http://only:8420"


def test_host_ignores_endpoints_list(_iso, monkeypatch):
    # A host follows its own local routing, never the client endpoints list.
    config.set_machine_config({"role": "host",
                               "endpoints": ["http://p:8420", "http://s:8421"]})
    monkeypatch.setattr(config, "_routing_url", lambda: "http://127.0.0.1:65019")
    assert config.client_url() == "http://127.0.0.1:65019"


# -- cmd_setup against an authored indexers: list ----------------------------


def test_setup_multi_this_machine_is_host(_iso):
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [
        {"machine": "boxA", "ssh": "boxA-ssh", "endpoint": "http://127.0.0.1:8420"},
        {"machine": "boxB", "ssh": "boxB-ssh", "endpoint": "http://127.0.0.1:8421"},
    ])
    # boxA (this machine) is the primary indexer -> host; no client endpoints written.
    rc = cmd_setup(_args(repo=str(repo)))
    assert rc == 0
    assert config.resolve_role() == "host"
    assert config.configured_endpoints() == []  # a host uses its local service


def test_setup_multi_other_machine_is_client_with_failover_list(_iso, monkeypatch):
    repo = _iso / "repo"; repo.mkdir()
    monkeypatch.setenv("AGENT_INDEX_MACHINE", "boxC")  # not a listed indexer
    _author_indexers(repo, [
        {"machine": "boxA", "ssh": "boxA-ssh", "endpoint": "http://a:8420"},
        {"machine": "boxB", "ssh": "boxB-ssh", "endpoint": "http://b:8421"},
    ])
    rc = cmd_setup(_args(repo=str(repo)))
    assert rc == 0
    assert config.resolve_role() == "client"
    # ordered failover list recorded (primary first), plus singular back-compat mirror
    assert config.configured_endpoints() == ["http://a:8420", "http://b:8421"]
    assert config.configured_endpoint() == "http://a:8420"


def test_setup_multi_does_not_rewrite_repo_list(_iso):
    repo = _iso / "repo"; repo.mkdir()
    authored = [{"machine": "boxA", "endpoint": "http://a:8420"},
                {"machine": "boxB", "endpoint": "http://b:8421"}]
    _author_indexers(repo, authored)
    cmd_setup(_args(repo=str(repo)))
    # The operator-authored list is the source of truth -- setup must not rewrite it.
    assert [i["machine"] for i in config.read_indexers(repo)] == ["boxA", "boxB"]


def test_explicit_indexer_flag_still_uses_singular_flow(_iso):
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [{"machine": "boxA"}, {"machine": "boxB"}])
    # An explicit --indexer overrides the authored list and takes the singular path,
    # rewriting the singular designation (back-compat with the single-indexer flow).
    cmd_setup(_args(indexer="boxB", ssh="boxB-ssh", endpoint="http://b:8420",
                    repo=str(repo)))
    data = config._load_yaml(config.repo_config_path(repo))
    assert data["indexer"]["machine"] == "boxB"


# -- hardening (review follow-ups) -------------------------------------------


def test_setup_multi_host_clears_stale_client_endpoints(_iso):
    # A box that was a client (has endpoints) and is re-adopted as a host must not
    # keep stale client routing that could shadow its live local service.
    config.set_machine_config({"role": "client",
                               "endpoints": ["http://old:8420"], "endpoint": "http://old:8420"})
    repo = _iso / "repo"; repo.mkdir()
    _author_indexers(repo, [{"machine": "boxA", "endpoint": "http://a:8420"}])
    cmd_setup(_args(repo=str(repo)))  # boxA (this machine) -> host
    assert config.resolve_role() == "host"
    data = config._load_yaml(config.config_path())
    assert "endpoints" not in data and "endpoint" not in data


def test_read_indexer_machineless_block_is_none(_iso):
    repo = _iso / "repo"; repo.mkdir()
    p = config.repo_config_path(repo)
    p.parent.mkdir(parents=True)
    p.write_text("indexer:\n  ssh: no-machine\n", encoding="utf-8")
    # A malformed indexer: block (no machine) resolves to None, per the contract.
    assert config.read_indexer(repo) is None


def test_set_machine_config_remove(_iso):
    config.set_machine_config({"role": "client", "endpoint": "http://x:8420"})
    config.set_machine_config({"role": "host"}, remove=["endpoint"])
    data = config._load_yaml(config.config_path())
    assert data["role"] == "host" and "endpoint" not in data


def test_route_probe_timeout_default_and_guard(_iso, monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S", raising=False)
    assert config._route_probe_timeout() == 1.5
    monkeypatch.setenv("AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S", "0.4")
    assert config._route_probe_timeout() == 0.4
    for bad in ("banana", "-1", "0", ""):
        monkeypatch.setenv("AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S", bad)
        assert config._route_probe_timeout() == 1.5  # malformed/non-positive -> default


def test_client_url_malformed_timeout_does_not_crash(_iso, monkeypatch):
    config.set_machine_config({"role": "client",
                               "endpoints": ["http://p:8420", "http://s:8421"]})
    monkeypatch.setenv("AGENT_INDEX_ROUTE_PROBE_TIMEOUT_S", "not-a-number")
    monkeypatch.setattr(config, "_endpoint_healthy", lambda url, t: url == "http://s:8421")
    assert config.client_url() == "http://s:8421"  # guarded parse, failover still works
