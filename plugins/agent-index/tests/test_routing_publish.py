from __future__ import annotations

from agent_index import server
from agent_index.config import Config


def test_publish_and_clear_routing(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_publish(config_dir, **kwargs):
        calls.append(("publish", config_dir, kwargs))

    def fake_clear(config_dir, pid):
        calls.append(("clear", config_dir, {"pid": pid}))
        return True

    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("zdd.routing.publish_active", fake_publish)
    monkeypatch.setattr("zdd.routing.clear_if_owner", fake_clear)

    cfg = Config(host="127.0.0.1", port=0)
    server._publish_routing(cfg, 4321, passive=False)
    server._clear_routing()

    assert calls[0][0] == "publish"
    assert calls[0][1] == tmp_path / "home"
    assert calls[0][2]["bind"] == "127.0.0.1"
    assert calls[0][2]["port"] == 4321
    assert calls[0][2]["demote_existing"] is True
    assert calls[1][0] == "clear"


def test_passive_does_not_self_publish(monkeypatch, tmp_path) -> None:
    calls = []

    def fake_publish(_config_dir, **kwargs):
        calls.append(kwargs)

    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("zdd.routing.publish_active", fake_publish)

    server._publish_routing(Config(host="127.0.0.1", port=0), 4322, passive=True)

    assert calls == []
