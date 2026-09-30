from __future__ import annotations

import json
from pathlib import Path

from zdd import routing
from zdd.cutover import CutoverOrchestrator


def test_cutover_refuses_active_route_under_cutover_lock(tmp_path: Path) -> None:
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=61001,
        pid=101,
        version="old",
    )
    spawned: list[int] = []

    class Routing:
        def reap_stale_active(self, *_a, **_k):
            return None

        def __getattr__(self, name: str):
            return getattr(routing, name)

    orch = CutoverOrchestrator(
        tmp_path,
        bind="127.0.0.1",
        version="new",
        spawn_passive=lambda port: spawned.append(port),
        health_check=lambda _host, _port: True,
        make_client=lambda _base: None,
        pick_free_port=lambda: 61002,
        refuse_old=lambda active: "active route refused" if active else None,
        routing_mod=Routing(),
    )

    res = orch.run(health_timeout=1, drain_timeout=1)

    assert not res.ok
    assert res.error == "active route refused"
    assert res.steps == ["refused: active route refused"]
    assert spawned == []


def test_cutover_refuses_forward_published_during_health_wait(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(
        tmp_path,
        bind="127.0.0.1",
        port=61001,
        pid=101,
        version="old",
    )
    drained: list[str] = []
    handle = type(
        "Handle",
        (),
        {"pid": 202, "terminated": False, "terminate": lambda self: setattr(self, "terminated", True)},
    )()

    class Client:
        def drain(self, **_k):
            drained.append("drain")
            return {"drained": True}

        def shutdown(self):
            drained.append("shutdown")

        def adopt_relay(self):
            return {"adopted": False}

    def health_check(_host, port):
        if port == 61002:
            (tmp_path / "active.json").write_text(
                json.dumps(
                    {
                        "active": {
                            "bind": "127.0.0.1",
                            "port": 62254,
                            "forwarded": True,
                        }
                    }
                ),
                encoding="utf-8",
            )
        return True

    orch = CutoverOrchestrator(
        tmp_path,
        bind="127.0.0.1",
        version="new",
        spawn_passive=lambda _port: handle,
        health_check=health_check,
        make_client=lambda _base: Client(),
        pick_free_port=lambda: 61002,
        refuse_old=lambda active: (
            "forwarded route replaced active"
            if isinstance(active, dict) and active.get("forwarded") is True
            else None
        ),
        sleep=lambda _s: None,
    )

    res = orch.run(health_timeout=1, drain_timeout=1)

    assert not res.ok
    assert res.error == "forwarded route replaced active"
    assert "refusal: terminated new daemon" in res.steps
    assert handle.terminated is True
    assert drained == []
    assert routing.read_table(tmp_path) == {
        "active": {"bind": "127.0.0.1", "port": 62254, "forwarded": True}
    }
