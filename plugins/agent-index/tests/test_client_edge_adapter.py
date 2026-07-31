from __future__ import annotations

from types import SimpleNamespace

from agent_index import config


def test_client_url_prefers_routing(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(
        "zdd.routing.read_active_endpoint",
        lambda _dir: SimpleNamespace(base_url="http://127.0.0.1:4567"),
    )

    assert config.client_url() == "http://127.0.0.1:4567"


def test_client_url_falls_back_to_rendezvous(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("zdd.routing.read_active_endpoint", lambda _dir: None)
    monkeypatch.setattr(
        config,
        "discovered_endpoint",
        lambda: SimpleNamespace(transport="tcp", tcp_host_port=("127.0.0.1", 9876)),
    )

    assert config.client_url() == "http://127.0.0.1:9876"


def test_client_url_returns_none_without_routing_or_rendezvous(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("zdd.routing.read_active_endpoint", lambda _dir: None)
    monkeypatch.setattr(config, "discovered_endpoint", lambda: None)

    assert config.client_url() is None
