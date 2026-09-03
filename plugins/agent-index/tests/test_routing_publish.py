from __future__ import annotations

from types import SimpleNamespace

import pytest

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


def test_namespaced_startup_cleans_owned_evidence_when_routing_publish_fails(
    monkeypatch,
    tmp_path,
) -> None:
    home = tmp_path / "home"
    run_root = home / "run"
    monkeypatch.setenv("AGENT_INDEX_HOME", str(home))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(run_root))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "cell-a/agent-index")

    app = SimpleNamespace(
        state=SimpleNamespace(
            installation_id="cell-a/agent-index",
            instance_token="instance-token",
            transaction_id=None,
            runtime_version=server.current_runtime_version(),
            promoted=True,
        )
    )
    monkeypatch.setattr(server, "build_app", lambda *, passive=False: app)

    class _Socket:
        closed = False

        @staticmethod
        def getsockname():
            return ("127.0.0.1", 4321)

        def close(self):
            self.closed = True

    sock = _Socket()
    monkeypatch.setattr(server, "_bind_listen_socket", lambda *_args: sock)

    class _Server:
        def __init__(self, _config):
            pass

        def run(self, *, sockets):
            pytest.fail(f"server ran after routing publication failed: {sockets}")

    monkeypatch.setattr("uvicorn.Config", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("uvicorn.Server", _Server)
    monkeypatch.setattr(
        server,
        "_publish_routing",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("routing publish failed")
        ),
    )

    with pytest.raises(RuntimeError, match="routing publish failed"):
        server.serve(Config(host="127.0.0.1", port=0))

    assert sock.closed is True
    assert not (run_root / "endpoint.json").exists()
    assert not (home / "running-version.json").exists()
    assert list((run_root / "instances").glob("*.json")) == []
