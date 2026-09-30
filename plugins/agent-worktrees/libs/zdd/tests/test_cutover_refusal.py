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


def _passive(pid: int = 202):
    return type("Handle", (), {"pid": pid, "terminated": False,
                               "terminate": lambda self: setattr(self, "terminated", True)})()


class _Client:
    def drain(self, **_k):
        return {"drained": True}

    def shutdown(self):
        return None

    def adopt_relay(self):
        return {"adopted": False}


def test_without_refuse_old_a_forwarding_stand_ins_own_publish_is_used(tmp_path: Path, monkeypatch) -> None:
    """A caller's routing stand-in that forwards unknown names to zdd.routing
    (agent-index promotes its passive in ``publish_active``) keeps its override."""
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=101, version="old")
    promoted: list[int] = []

    class Promoting:
        def publish_active(self, config_dir, **kw):
            promoted.append(kw["port"])
            return routing.publish_active(config_dir, **kw)

        def __getattr__(self, name: str):
            return getattr(routing, name)

    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="new", spawn_passive=lambda _p: _passive(),
        health_check=lambda _h, _p: True, make_client=lambda _b: _Client(),
        pick_free_port=lambda: 61002, sleep=lambda _s: None, routing_mod=Promoting(),
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok, res.error
    assert promoted == [61002]


def test_without_refuse_old_a_stale_pidless_active_row_is_replaced(tmp_path: Path) -> None:
    """A dead active row with no pid (reap leaves it: pid unknown) used to be
    replaced by the flip; a cutover that didn't ask for guarding still does."""
    (tmp_path / "active.json").write_text(json.dumps(
        {"active": {"bind": "127.0.0.1", "port": 61009}}), encoding="utf-8")
    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="new", spawn_passive=lambda _p: _passive(),
        health_check=lambda _h, _p: True, make_client=lambda _b: _Client(),
        pick_free_port=lambda: 61002, sleep=lambda _s: None,
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert res.ok, res.error
    assert routing.read_table(tmp_path)["active"]["port"] == 61002


def test_refuse_old_with_a_routing_module_that_cant_guard_refuses(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=101, version="old")
    handle = _passive()

    class Forwarding:
        def __getattr__(self, name: str):
            return getattr(routing, name)

    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="new", spawn_passive=lambda _p: handle,
        health_check=lambda _h, _p: True, make_client=lambda _b: _Client(),
        pick_free_port=lambda: 61002, sleep=lambda _s: None, routing_mod=Forwarding(),
        refuse_old=lambda _active: None,
    )
    res = orch.run(health_timeout=1, drain_timeout=1)
    assert not res.ok and "can't publish guarded" in res.error
    assert handle.terminated is True
    assert routing.read_table(tmp_path)["active"]["port"] == 61001

